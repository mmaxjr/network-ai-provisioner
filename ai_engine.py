"""
Motor de IA: transforma um pedido em linguagem natural + contexto do
equipamento num plano de comandos de configuração, e depois avalia se a
aplicação funcionou olhando o output dos comandos de verificação.

Duas chamadas de IA por operação:
  1. plan_configuration()   -> gera os comandos a partir da intenção
  2. assess_verification()  -> interpreta o resultado depois de aplicar

Usa Groq (gratuito) por padrão, mas o cliente é isolado numa função só
(get_ai_client) pra trocar de provedor sem mexer no resto do código.
"""

import json
import os

from groq import Groq

GROQ_MODEL = "llama-3.3-70b-versatile"


def get_ai_client():
    return Groq(api_key=os.environ["GROQ_API_KEY"])


PLAN_SYSTEM_PROMPT = """Você é um engenheiro de redes sênior, extremamente
cauteloso, especialista em {vendor_label}. Sua tarefa é transformar um
pedido em linguagem natural num plano de comandos de configuração exatos,
usando a sintaxe correta do fabricante.

Regras importantes:
1. Use como referência o "cheat sheet" de sintaxe abaixo. Adapte os
   placeholders (IPs, AS numbers, interfaces, máscaras) com os valores
   que o usuário forneceu.
2. Se faltar alguma informação essencial para gerar o comando com
   segurança (ex: número de AS do vizinho, interface a usar, máscara),
   NÃO invente o valor. Liste em "faltam_informacoes" e deixe "comandos"
   vazio.
3. Sempre inclua "comandos_verificacao": comandos de leitura (show/display,
   nunca de configuração) que permitam confirmar se a mudança funcionou.
4. Avalie riscos reais: interromper conectividade de gerência, afetar
   tráfego de produção, remover configuração existente, etc.
5. Nunca inclua comandos de "reload"/"reboot" ou que apaguem a config
   inteira do equipamento.

Cheat sheet de sintaxe ({vendor_label}):
{cheat_sheet}

Responda SOMENTE em JSON, no formato:
{{
  "faltam_informacoes": ["..."],
  "comandos": ["linha 1", "linha 2", "..."],
  "explicacao": "o que essa configuração faz e por quê, em 3-5 frases",
  "riscos": [{{"descricao": "...", "severidade": "baixa|media|alta"}}],
  "comandos_verificacao": ["..."],
  "reversivel_automaticamente": true
}}
"""


def plan_configuration(vendor_label, cheat_sheet, current_context, user_intent):
    """
    current_context: dict com informações já coletadas do equipamento
    (ex: trecho relevante do running-config, lista de interfaces, se já
    existe processo de BGP configurado) — ajuda a IA a não sugerir algo
    conflitante com o que já existe.
    """
    client = get_ai_client()

    system_prompt = PLAN_SYSTEM_PROMPT.format(vendor_label=vendor_label, cheat_sheet=cheat_sheet)
    user_prompt = f"""Contexto atual do equipamento:
{json.dumps(current_context, indent=2, ensure_ascii=False)}

Pedido do usuário:
\"\"\"{user_intent}\"\"\"
"""

    completion = client.chat.completions.create(
        model=GROQ_MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.1,
        response_format={"type": "json_object"},
    )
    return json.loads(completion.choices[0].message.content)


ASSESS_SYSTEM_PROMPT = """Você é um engenheiro de redes revisando o
resultado de uma mudança de configuração que acabou de ser aplicada.
Analise o output dos comandos de verificação e diga se a mudança
funcionou como esperado.

Responda SOMENTE em JSON:
{
  "sucesso": true,
  "observacoes": "2-4 frases explicando o que os dados mostram",
  "recomenda_rollback": false
}
"""


def assess_verification(explicacao_original, comandos_verificacao, outputs):
    client = get_ai_client()

    pares = "\n\n".join(
        f"$ {cmd}\n{out}" for cmd, out in zip(comandos_verificacao, outputs)
    )
    user_prompt = f"""O que foi configurado: {explicacao_original}

Output dos comandos de verificação após aplicar:

{pares}
"""

    completion = client.chat.completions.create(
        model=GROQ_MODEL,
        messages=[
            {"role": "system", "content": ASSESS_SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.1,
        response_format={"type": "json_object"},
    )
    return json.loads(completion.choices[0].message.content)
