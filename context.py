"""
Coleta de contexto do equipamento antes de pedir o plano pra IA.

A ideia é dar pra IA um retrato mínimo e relevante do que já existe no
equipamento (sem mandar o running-config inteiro, que pode ser enorme e
caro em tokens) — só os pedaços que ajudam a não gerar algo conflitante:
interfaces existentes, se já tem BGP configurado, se já tem NAT/ACL.
"""

import re

# comandos de leitura por vendor pra descobrir o que já existe
CONTEXT_COMMANDS = {
    "cisco_ios": {
        "interfaces": "show ip interface brief",
        "bgp_existente": "show running-config | section router bgp",
        "nat_existente": "show running-config | include ip nat",
    },
    "cisco_xe": {
        "interfaces": "show ip interface brief",
        "bgp_existente": "show running-config | section router bgp",
        "nat_existente": "show running-config | include ip nat",
    },
    "huawei_vrp": {
        "interfaces": "display ip interface brief",
        "bgp_existente": "display current-configuration configuration bgp",
        "nat_existente": "display current-configuration configuration nat",
    },
    "mikrotik_routeros": {
        "interfaces": "/ip address print",
        "bgp_existente": "/routing bgp connection print",
        "nat_existente": "/ip firewall nat print",
    },
}


def collect_context(conn, device_type_alias):
    commands = CONTEXT_COMMANDS.get(device_type_alias, {})
    context = {}

    for label, cmd in commands.items():
        try:
            output = conn.send_command(cmd, read_timeout=30)
        except Exception as exc:  # comando pode não existir/aplicar nesse equipamento
            output = f"(não foi possível coletar: {exc})"
        # limita tamanho pra não explodir o prompt da IA
        context[label] = output[:3000]

    return context


def extract_existing_interfaces(context_text):
    """Extrai nomes de interface de um output tipo 'show ip interface brief'."""
    return re.findall(r"^(\S+(?:Ethernet|Vlan|eth|ether)\S*)", context_text, re.MULTILINE | re.IGNORECASE)
