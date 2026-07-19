"""
Camada de segurança: backup de config antes de mexer, diff antes/depois,
confirmação interativa, e rollback best-effort.

Aviso importante sobre rollback (leia antes de confiar cegamente nisso):
- Em Cisco IOS/IOS-XE com suporte a "configure replace", o rollback é
  robusto: reenviamos o arquivo de backup pro equipamento via SCP e
  damos "configure replace ... force", que faz o dispositivo convergir
  a config atual pra igual à do backup (removendo o que foi adicionado).
- Em Huawei VRP e MikroTik RouterOS, não implementamos um mecanismo de
  replace equivalente (VRP tem "rollback configuration to file", mas
  depende de checkpoints configurados previamente, e RouterOS não tem
  isso de forma nativa via CLI simples). Nesses casos, o rollback aqui é
  best-effort: reaplicamos as linhas do backup como configuração — isso
  ADICIONA de volta o que existia, mas comandos que foram removidos por
  engano no meio do caminho podem não ser desfeitos automaticamente.
  Trate como uma rede de segurança adicional, não como garantia.
"""

import difflib
import os
import time

BACKUP_DIR = "backups"


def _timestamp():
    return time.strftime("%Y%m%d-%H%M%S")


def backup_running_config(conn, device_name, profile):
    """Lê a config atual do equipamento e salva localmente antes de qualquer mudança."""
    os.makedirs(BACKUP_DIR, exist_ok=True)
    config_text = conn.send_command(profile["show_running_config"], read_timeout=60)

    filename = f"{device_name}_{_timestamp()}.{profile['backup_extension']}"
    path = os.path.join(BACKUP_DIR, filename)

    with open(path, "w", encoding="utf-8") as f:
        f.write(config_text)

    print(f"[backup] config atual salva em {path}")
    return path, config_text


def confirm(prompt, auto_yes=False):
    if auto_yes:
        print(f"[auto-yes] {prompt} -> sim (--yes)")
        return True
    resposta = input(f"{prompt} [s/N]: ").strip().lower()
    return resposta in ("s", "sim", "y", "yes")


def diff_configs(before_text, after_text, hostname):
    diff = difflib.unified_diff(
        before_text.splitlines(),
        after_text.splitlines(),
        fromfile=f"{hostname}/antes",
        tofile=f"{hostname}/depois",
        lineterm="",
    )
    return "\n".join(diff)


def apply_commands(conn, comandos):
    """Envia os comandos de configuração e devolve o output cru pra log/auditoria."""
    output = conn.send_config_set(comandos)
    return output


def save_to_device(conn, profile):
    if profile["save_config_cmd"]:
        return conn.send_command(profile["save_config_cmd"], read_timeout=30)
    return "(este vendor persiste a config automaticamente, sem comando de save)"


def rollback(conn, profile, backup_path):
    """
    Rollback best-effort. Ver aviso no topo do arquivo sobre as limitações
    reais desse mecanismo em Huawei/MikroTik.
    """
    with open(backup_path, "r", encoding="utf-8") as f:
        backup_lines = [
            line for line in f.read().splitlines()
            if line.strip() and not line.strip().startswith("!") and not line.strip().startswith("#")
        ]

    if profile["supports_config_replace"]:
        print(
            "[rollback] este vendor suporta 'configure replace' de forma nativa, "
            "mas esse fluxo requer transferência do arquivo via SCP para o "
            "equipamento — veja a seção de rollback avançado no README para "
            "habilitar. Aplicando rollback best-effort (reenvio de linhas) por ora."
        )

    print(f"[rollback] reaplicando {len(backup_lines)} linhas do backup {backup_path}...")
    output = conn.send_config_set(backup_lines)
    return output
