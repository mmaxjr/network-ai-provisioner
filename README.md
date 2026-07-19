# Network AI Provisioner

> ⚠️ **Projeto em fase de teste/desenvolvimento — não use em produção.**
> Este código ainda não passou por revisão de segurança nem testes em
> equipamentos reais além dos exemplos do README. Ele aplica configuração
> de verdade via SSH; um erro de sintaxe, de parâmetro ou de julgamento
> da IA pode derrubar conectividade ou serviço. Use só em laboratório
> (GNS3, EVE-NG, equipamento de teste isolado) até validar cada parte do
> fluxo (backup, confirmação, rollback) no seu ambiente. Veja também a
> seção "Limitações importantes do rollback" mais abaixo.
>
> **Nota sobre nomes de arquivo:** se você baixou/clonou este repositório
> e viu nomes truncados tipo `INVENT~1.PY`, `AI_ENG~1.PY`, `SESSIO~1.PY`,
> `REQUIR~1.TXT` ou `INVENT~1.YAM`, isso é um nome curto estilo DOS (8.3)
> gerado por alguma etapa de sincronização/upload — não é assim que os
> arquivos foram criados. Os nomes corretos são:
> `inventory.py`, `ai_engine.py`, `session_logger.py`, `requirements.txt`
> e `inventory.example.yaml`, respectivamente. Renomeie antes de rodar,
> já que `main.py` importa esses módulos pelo nome exato
> (`import ai_engine`, `from inventory import ...`, etc.).

Ferramenta de linha de comando que conecta via SSH em equipamentos de
rede (Cisco IOS/IOS-XE, Huawei VRP, MikroTik RouterOS), recebe um pedido
em **linguagem natural** — "configura BGP entre o IP X e Y", "configura
CGNAT nesse Cisco" — e usa uma IA gratuita (Groq / Llama 3.3) pra gerar
os comandos corretos na sintaxe do fabricante, aplicar com segurança, e
verificar se funcionou. Se não funcionou, oferece reverter.

Este é o projeto mais complexo da série (depois do NetFlow+IA e dos
scripts de firewall/config/Zabbix) porque ele **muda configuração de
verdade em equipamento de produção** — então quase todo o código aqui
é sobre reduzir o risco disso dar errado, não sobre a parte "mágica" da
IA em si.

## Por que isso é arriscado e como o projeto trata isso

Pedir pra uma IA gerar comando de rede e aplicar direto no equipamento é
perigoso por três motivos, e o projeto ataca os três:

1. **A IA pode alucinar sintaxe ou usar valor errado.** Mitigação: cada
   vendor tem um "cheat sheet" de sintaxe correta (`vendors.py`) que é
   injetado no prompt, e a IA é instruída a nunca inventar parâmetro que
   falta — ela deve listar em `faltam_informacoes` e não gerar comando
   nenhum nesse caso.
2. **Mesmo com sintaxe certa, a mudança pode ter efeito colateral não
   previsto** (ex: um ACL mal escrito bloqueia a própria sessão SSH de
   gerência). Mitigação: backup automático do running-config antes de
   qualquer mudança, confirmação manual obrigatória (a menos que você
   passe `--yes` explicitamente), e uma segunda chamada de IA que analisa
   o resultado depois de aplicar.
3. **Se der errado, alguém precisa conseguir voltar atrás rápido.**
   Mitigação: `safety.rollback()` reaplica o backup — de forma robusta em
   Cisco (que suporta `configure replace`) e best-effort nos demais
   vendors (veja a seção de limitações abaixo, isso é importante).

## Arquitetura — visão geral do fluxo

```
 usuário                  main.py                    equipamento (SSH)
 ┌─────────┐   pedido    ┌──────────────────────┐   comandos   ┌─────────┐
 │"configura│ ──────────▶│ 1. conecta (netmiko) │──────────────▶│ Cisco/  │
 │ BGP..."  │             │ 2. coleta contexto   │◀──────────────│ Huawei/ │
 └─────────┘             │ 3. IA gera plano      │   show/display│ Mikrotik│
                          │ 4. mostra + confirma  │              └─────────┘
                          │ 5. backup da config   │
                          │ 6. aplica comandos    │
                          │ 7. roda verificação   │
                          │ 8. IA avalia resultado│
                          │ 9. rollback se falhou │
                          └──────────┬────────────┘
                                     │
                                     ▼
                     logs/<device>_<timestamp>.jsonl + .log
```

## Os módulos, um por um

### `vendors.py` — perfis de fabricante

Um dicionário `VENDOR_PROFILES` com, para cada vendor suportado
(`cisco_ios`, `cisco_xe`, `huawei_vrp`, `mikrotik_routeros`):

- `netmiko_device_type`: o driver do netmiko a usar na conexão SSH
- `show_running_config`: comando pra ler a config atual (`show
  running-config` no Cisco, `display current-configuration` no Huawei,
  `/export` no RouterOS)
- `save_config_cmd`: comando pra persistir a config (`write memory` no
  Cisco, `save` no Huawei; RouterOS não precisa, já é persistente)
- `supports_config_replace`: se o vendor tem um mecanismo nativo de
  "substituir a config inteira por um arquivo" (só o Cisco, aqui)
- `cheat_sheet`: um texto com a sintaxe correta de BGP e NAT/CGNAT pra
  aquele vendor especificamente — isso é o que impede a IA de, por
  exemplo, tentar usar sintaxe de Cisco num Huawei.

### `inventory.py` — inventário e conexão

Lê `inventory.yaml` (uma lista de equipamentos com host, usuário, senha,
tipo) e expõe:

- `get_device(nome)` — pega um `Device` do inventário pelo nome
- `DeviceConnection` — context manager que abre a conexão SSH via
  `netmiko.ConnectHandler` no `__enter__` e desconecta no `__exit__`,
  então o código principal nunca esquece de fechar a sessão.

A senha pode vir do YAML (não recomendado em produção) ou de uma
variável de ambiente `NETAI_PASSWORD_<NOME_DO_DISPOSITIVO_EM_MAIUSCULO>`,
o que permite manter `inventory.yaml` fora do controle de segredo.

### `context.py` — o que a IA vê do equipamento antes de responder

Antes de gerar qualquer comando, o script roda comandos de leitura
(nunca de escrita) pra descobrir o estado atual relevante: interfaces
existentes, se já tem BGP configurado, se já tem NAT configurado. Isso
vai no prompt da IA como contexto — sem isso, a IA poderia sugerir criar
um processo BGP que já existe, ou usar uma interface que não existe
nesse equipamento.

Cada output é limitado a 3000 caracteres antes de entrar no prompt
(`context[label][:3000]`), pra não estourar o limite de tokens nem gastar
demais numa config gigante — só o suficiente pra dar contexto.

### `ai_engine.py` — as duas chamadas de IA

**`plan_configuration()`** — chamada 1. Recebe o cheat sheet do vendor, o
contexto coletado, e o pedido do usuário. O prompt de sistema
(`PLAN_SYSTEM_PROMPT`) instrui a IA a:

- adaptar os placeholders do cheat sheet com os valores do pedido
- nunca inventar parâmetro que falta (listar em `faltam_informacoes` e
  deixar `comandos` vazio nesse caso)
- sempre devolver `comandos_verificacao` (comandos de leitura) junto
- nunca incluir `reload`/`reboot` ou comando que apague a config inteira
- avaliar riscos reais (interromper gerência, afetar produção, etc.)

Retorna um JSON estruturado (comandos, explicação, riscos, comandos de
verificação, se é reversível automaticamente).

**`assess_verification()`** — chamada 2, só acontece depois que os
comandos já foram aplicados de verdade. Pega o output real dos comandos
de verificação (ex: `show ip bgp summary` depois de configurar BGP) e
pergunta pra IA se aquilo indica sucesso ou problema. Retorna
`{"sucesso": bool, "observacoes": "...", "recomenda_rollback": bool}`.

### `safety.py` — a parte que existe pra te proteger de você mesmo (ou da IA)

- `backup_running_config()` — lê e salva a config atual em
  `backups/<device>_<timestamp>.<ext>` **antes** de qualquer alteração.
- `confirm()` — pede confirmação explícita no terminal antes de aplicar
  qualquer coisa, a menos que `--yes` seja passado.
- `apply_commands()` — só um wrapper em `conn.send_config_set()`.
- `diff_configs()` — gera um diff unificado (`difflib`) entre a config de
  antes e depois, útil pra auditoria.
- `rollback()` — reaplica as linhas do backup. **Leia a seção de
  limitações abaixo, isso é importante.**

### `session_logger.py` — trilha de auditoria

Cada operação gera dois arquivos em `logs/`: um `.jsonl` (uma linha JSON
por evento — pedido, plano, backup, aplicação, verificação, rollback) e
um `.log` em texto legível. Isso é o que torna esse tipo de automação
defensável numa auditoria: dá pra reconstruir exatamente o que a IA
sugeriu, o que foi de fato aplicado, e o que a verificação encontrou.

### `main.py` — orquestração

Dois modos:

- `configure` — um pedido único, de ponta a ponta (conecta, gera plano,
  confirma, aplica, verifica, desconecta).
- `shell` — sessão interativa: conecta uma vez e aceita vários pedidos em
  sequência, até você digitar `sair`.

## Instalação

```bash
pip install -r requirements.txt
# netmiko, groq, pyyaml

export GROQ_API_KEY="sua_chave"   # console.groq.com/keys

cp inventory.example.yaml inventory.yaml
# edite com seus equipamentos reais
```

## Exemplos de uso

### BGP num switch Huawei

```bash
python main.py configure \
  --device switch-huawei-01 \
  --intent "configurar BGP entre o IP 200.1.1.1 (AS local 65001) e o vizinho 200.1.1.2 (AS remoto 65002), com descrição 'peering-transito-A'"
```

O que acontece:

1. Conecta via SSH no `switch-huawei-01` (definido no inventory.yaml)
2. Roda `display ip interface brief`, `display current-configuration
   configuration bgp` etc. pra ver o que já existe
3. Manda pro Groq: cheat sheet de BGP do Huawei + esse contexto + o pedido
4. A IA devolve algo como:
   ```json
   {
     "faltam_informacoes": [],
     "comandos": [
       "system-view",
       "bgp 65001",
       "peer 200.1.1.2 as-number 65002",
       "peer 200.1.1.2 description peering-transito-A",
       "quit"
     ],
     "explicacao": "Cria o processo BGP 65001 e adiciona o vizinho 200.1.1.2 como peer eBGP do AS 65002...",
     "riscos": [{"descricao": "Se o AS remoto estiver incorreto, a sessão BGP não estabelece", "severidade": "baixa"}],
     "comandos_verificacao": ["display bgp peer"],
     "reversivel_automaticamente": true
   }
   ```
5. Mostra o plano, pede confirmação
6. Faz backup do `display current-configuration`
7. Aplica os comandos
8. Roda `display bgp peer` e manda o resultado de volta pra IA avaliar
9. Salva a config (`save`) se tudo indicar sucesso

### CGNAT num Cisco

```bash
python main.py configure \
  --device roteador-cisco-01 \
  --intent "configurar CGNAT usando o pool 100.64.0.1 a 100.64.0.254 /24 para a rede interna 10.0.0.0/8, interface interna GigabitEthernet0/0/0 e externa GigabitEthernet0/0/1"
```

### Sessão interativa

```bash
python main.py shell --device roteador-cisco-01
roteador-cisco-01> configurar BGP com o vizinho 203.0.113.1 AS 65010
[...]
roteador-cisco-01> agora adiciona a rede 172.16.0.0/24 ao BGP
[...]
roteador-cisco-01> sair
```

### Testando sem aplicar nada (recomendado na primeira vez)

```bash
python main.py configure --device switch-huawei-01 --intent "..." --dry-run
```

Mostra o plano gerado pela IA, mas não conecta pra aplicar nada — só
usa a conexão pra coletar contexto de leitura.

## Flags importantes

| Flag | Efeito |
|---|---|
| `--dry-run` | Gera e mostra o plano, mas não aplica nada |
| `--yes` | Pula a confirmação manual (⚠ use com cautela, nunca em produção sem revisar antes) |
| `--no-verify` | Pula a etapa de verificação pós-aplicação |
| `--auto-rollback` | Se a verificação indicar falha, reverte sem perguntar |

## Limitações importantes do rollback (leia antes de confiar)

- **Cisco IOS/IOS-XE**: suportam `configure replace <arquivo> force`, que
  é a forma robusta de rollback (o equipamento reconcilia a config atual
  pra ficar idêntica à do arquivo, inclusive removendo o que foi
  adicionado). Este projeto **não implementa a transferência via SCP do
  arquivo de backup de volta pro equipamento** por padrão — isso requer
  SCP habilitado no dispositivo e mais uma dependência (`netmiko`
  tem suporte via `file_transfer`, mas fica de fora aqui pra manter o
  projeto mais simples). O rollback default reenvia as linhas do backup
  como comandos de configuração, o que é aditivo — funciona bem pra
  desfazer a maioria das mudanças, mas não é uma garantia formal de
  estado idêntico ao anterior.
- **Huawei VRP**: tem `rollback configuration to file`, mas depende de
  checkpoints configurados previamente no equipamento — não é algo que
  dá pra improvisar de fora sem preparo prévio do dispositivo. O
  rollback aqui também é best-effort (reenvio de linhas).
- **MikroTik RouterOS**: não tem um mecanismo de "replace" via CLI simples
  equivalente. Mesma limitação.

**Recomendação prática**: em equipamentos críticos, sempre rode primeiro
com `--dry-run`, revise o plano gerado manualmente, e só depois rode sem
essa flag. Trate o rollback automático como uma rede de segurança, não
como uma garantia formal — para isso, cada vendor tem mecanismos próprios
de configuração agendada com auto-revert (ex: Cisco `configure terminal
... reload in 5` antes de mudanças de acesso remoto) que valem a pena
combinar com esta ferramenta em cenários de produção real.

## Status do projeto

Projeto de estudo/portfólio, propositalmente mais robusto que os
anteriores da série porque lida com mudança real de configuração.
Próximos passos possíveis:

- Implementar rollback via SCP + `configure replace` de verdade para
  Cisco (mecanismo mais robusto que o reenvio de linhas)
- Suporte a mais fabricantes (Juniper JunOS, Arista EOS)
- Um modo "plano em lote": aplicar o mesmo tipo de mudança em vários
  equipamentos do inventário de uma vez, com aprovação por item
- Integração com um sistema de tickets (abrir uma mudança formal antes
  de aplicar, referenciar o ticket no log)
