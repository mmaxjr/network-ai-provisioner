"""
Network AI Provisioner
=======================

CLI que conecta via SSH num equipamento de rede (Cisco IOS/IOS-XE,
Huawei VRP, MikroTik RouterOS), recebe um pedido em linguagem natural
("configurar BGP entre 200.1.1.1 e 200.1.1.2, AS 65001 e 65002") e usa
uma IA gratuita (Groq) pra gerar, aplicar e verificar a configuração —
com backup automático, confirmação antes de aplicar, e rollback em caso
de falha.

Requisitos:
    pip install netmiko groq pyyaml

Uso — um pedido único:
    python main.py configure --device switch-huawei-01 \\
        --intent "configurar BGP entre o IP 200.1.1.1 (AS 65001, local) e o vizinho 200.1.1.2 (AS 65002)"

Uso — shell interativo (fica conectado, aceita vários pedidos em sequência):
    python main.py shell --device roteador-cisco-01

Flags úteis:
    --dry-run     mostra o plano gerado pela IA mas não aplica nada
    --yes         não pergunta confirmação (cuidado em produção)
    --no-verify   pula a etapa de verificação pós-aplicação
    --auto-rollback   se a verificação indicar falha, reverte sem perguntar

Groq API key gratuita em: https://console.groq.com/keys
"""

import argparse
import sys

import ai_engine
import context as ctx
import safety
from inventory import DeviceConnection, get_device
from session_logger import SessionLogger
from vendors import get_profile

VENDOR_LABELS = {
    "cisco_ios": "Cisco IOS",
    "cisco_xe": "Cisco IOS-XE",
    "huawei_vrp": "Huawei VRP",
    "mikrotik_routeros": "MikroTik RouterOS",
}


def print_plan(plan):
    print("\n" + "=" * 70)
    print("PLANO GERADO PELA IA")
    print("=" * 70)

    if plan.get("faltam_informacoes"):
        print("\n⚠ Faltam informações para prosseguir com segurança:")
        for item in plan["faltam_informacoes"]:
            print(f"  - {item}")
        return

    print("\nComandos a aplicar:")
    for cmd in plan["comandos"]:
        print(f"  {cmd}")

    print(f"\nExplicação: {plan['explicacao']}")

    if plan.get("riscos"):
        print("\nRiscos identificados:")
        for r in plan["riscos"]:
            print(f"  [{r['severidade'].upper()}] {r['descricao']}")

    print("\nComandos de verificação após aplicar:")
    for cmd in plan["comandos_verificacao"]:
        print(f"  {cmd}")
    print("=" * 70 + "\n")


def run_one_shot(device_name, user_intent, args):
    device = get_device(device_name, args.inventory)
    profile = device.profile
    vendor_label = VENDOR_LABELS.get(device.device_type_alias, device.device_type_alias)
    logger = SessionLogger(device_name)
    logger.log_intent(device_name, user_intent)

    with DeviceConnection(device) as conn:
        print("[contexto] coletando estado atual do equipamento (interfaces, BGP/NAT existentes)...")
        current_context = ctx.collect_context(conn, device.device_type_alias)

        print("[ia] gerando plano de configuração...")
        plan = ai_engine.plan_configuration(
            vendor_label=vendor_label,
            cheat_sheet=profile["cheat_sheet"],
            current_context=current_context,
            user_intent=user_intent,
        )
        logger.log_plan(plan)
        print_plan(plan)

        if plan.get("faltam_informacoes"):
            print("Refaça o pedido incluindo essas informações.")
            logger.log_abort("faltam_informacoes")
            return

        if args.dry_run:
            print("[dry-run] nada foi aplicado no equipamento.")
            return

        if not safety.confirm("Aplicar esses comandos no equipamento?", auto_yes=args.yes):
            print("Cancelado pelo usuário.")
            logger.log_abort("cancelado_pelo_usuario")
            return

        backup_path, before_config = safety.backup_running_config(conn, device_name, profile)
        logger.log_backup(backup_path)

        print("[aplicar] enviando comandos de configuração...")
        apply_output = safety.apply_commands(conn, plan["comandos"])
        logger.log_apply(plan["comandos"], apply_output)
        print(apply_output)

        if not args.no_verify:
            print("[verificar] rodando comandos de verificação...")
            outputs = [conn.send_command(cmd, read_timeout=30) for cmd in plan["comandos_verificacao"]]
            assessment = ai_engine.assess_verification(
                plan["explicacao"], plan["comandos_verificacao"], outputs
            )
            logger.log_verification(plan["comandos_verificacao"], outputs, assessment)

            print(f"\n[avaliação da IA] sucesso={assessment['sucesso']}")
            print(f"  {assessment['observacoes']}")

            if not assessment["sucesso"]:
                should_rollback = args.auto_rollback or safety.confirm(
                    "A verificação indica problema. Reverter para o backup?",
                    auto_yes=False,
                )
                if should_rollback:
                    rollback_output = safety.rollback(conn, profile, backup_path)
                    logger.log_rollback(rollback_output)
                    print("[rollback] concluído. Revise o equipamento manualmente antes de tentar de novo.")
                    return

        save_output = safety.save_to_device(conn, profile)
        print(f"[salvar] {save_output}")

    print(f"\nLog completo desta operação em: {logger.text_path}")


def run_shell(device_name, args):
    device = get_device(device_name, args.inventory)
    profile = device.profile
    vendor_label = VENDOR_LABELS.get(device.device_type_alias, device.device_type_alias)

    print(f"Conectado a {device_name} ({vendor_label}). Digite o pedido em linguagem natural.")
    print("Digite 'sair' para encerrar.\n")

    with DeviceConnection(device) as conn:
        while True:
            try:
                user_intent = input(f"{device_name}> ").strip()
            except (EOFError, KeyboardInterrupt):
                break

            if user_intent.lower() in ("sair", "exit", "quit"):
                break
            if not user_intent:
                continue

            logger = SessionLogger(device_name)
            logger.log_intent(device_name, user_intent)

            current_context = ctx.collect_context(conn, device.device_type_alias)
            plan = ai_engine.plan_configuration(
                vendor_label=vendor_label,
                cheat_sheet=profile["cheat_sheet"],
                current_context=current_context,
                user_intent=user_intent,
            )
            logger.log_plan(plan)
            print_plan(plan)

            if plan.get("faltam_informacoes"):
                continue
            if args.dry_run:
                continue
            if not safety.confirm("Aplicar esses comandos no equipamento?", auto_yes=args.yes):
                logger.log_abort("cancelado_pelo_usuario")
                continue

            backup_path, _ = safety.backup_running_config(conn, device_name, profile)
            logger.log_backup(backup_path)

            apply_output = safety.apply_commands(conn, plan["comandos"])
            logger.log_apply(plan["comandos"], apply_output)
            print(apply_output)

            if not args.no_verify:
                outputs = [conn.send_command(cmd, read_timeout=30) for cmd in plan["comandos_verificacao"]]
                assessment = ai_engine.assess_verification(
                    plan["explicacao"], plan["comandos_verificacao"], outputs
                )
                logger.log_verification(plan["comandos_verificacao"], outputs, assessment)
                print(f"[avaliação da IA] sucesso={assessment['sucesso']} — {assessment['observacoes']}")


def main():
    parser = argparse.ArgumentParser(description="Network AI Provisioner")
    parser.add_argument("--inventory", default="inventory.yaml")
    parser.add_argument("--dry-run", action="store_true", help="Gera o plano mas não aplica nada")
    parser.add_argument("--yes", action="store_true", help="Não pede confirmação antes de aplicar")
    parser.add_argument("--no-verify", action="store_true", help="Pula a verificação pós-aplicação")
    parser.add_argument("--auto-rollback", action="store_true", help="Reverte automaticamente se a verificação falhar")

    subparsers = parser.add_subparsers(dest="modo", required=True)

    p_configure = subparsers.add_parser("configure", help="Aplica um único pedido de configuração")
    p_configure.add_argument("--device", required=True, help="Nome do dispositivo no inventory.yaml")
    p_configure.add_argument("--intent", required=True, help="Pedido em linguagem natural")

    p_shell = subparsers.add_parser("shell", help="Sessão interativa com um dispositivo")
    p_shell.add_argument("--device", required=True, help="Nome do dispositivo no inventory.yaml")

    args = parser.parse_args()

    try:
        if args.modo == "configure":
            run_one_shot(args.device, args.intent, args)
        elif args.modo == "shell":
            run_shell(args.device, args)
    except Exception as exc:
        print(f"\nErro: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
