"""
Log de sessão: cada operação (plano gerado, comandos aplicados,
verificação, rollback) fica registrada em dois formatos:
  - um arquivo .jsonl (uma linha JSON por evento) pra auditoria/parsing
  - um arquivo .log humano, pra leitura rápida

Isso é o que faz esse tipo de automação ser aceitável em produção: tudo
que a IA sugeriu e tudo que foi de fato executado no equipamento fica
rastreável depois.
"""

import json
import os
import time

LOG_DIR = "logs"


def _now():
    return time.strftime("%Y-%m-%d %H:%M:%S")


class SessionLogger:
    def __init__(self, device_name):
        os.makedirs(LOG_DIR, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        self.jsonl_path = os.path.join(LOG_DIR, f"{device_name}_{stamp}.jsonl")
        self.text_path = os.path.join(LOG_DIR, f"{device_name}_{stamp}.log")

    def _write(self, event_type, data):
        record = {"timestamp": _now(), "tipo": event_type, **data}
        with open(self.jsonl_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        with open(self.text_path, "a", encoding="utf-8") as f:
            f.write(f"[{record['timestamp']}] {event_type.upper()}\n")
            for k, v in data.items():
                f.write(f"  {k}: {v}\n")
            f.write("\n")

    def log_intent(self, device_name, user_intent):
        self._write("pedido_usuario", {"dispositivo": device_name, "pedido": user_intent})

    def log_plan(self, plan):
        self._write("plano_gerado", {"plano": plan})

    def log_backup(self, path):
        self._write("backup_criado", {"arquivo": path})

    def log_apply(self, comandos, output):
        self._write("comandos_aplicados", {"comandos": comandos, "output": output})

    def log_verification(self, comandos_verificacao, outputs, assessment):
        self._write(
            "verificacao",
            {
                "comandos_verificacao": comandos_verificacao,
                "outputs": outputs,
                "avaliacao_ia": assessment,
            },
        )

    def log_rollback(self, output):
        self._write("rollback_executado", {"output": output})

    def log_abort(self, motivo):
        self._write("abortado", {"motivo": motivo})
