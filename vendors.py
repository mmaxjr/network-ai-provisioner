"""
Perfis de vendor: tudo que muda de fabricante pra fabricante fica
centralizado aqui — tipo de dispositivo pro netmiko, comando pra ver a
config atual, comando de salvar, extensão do arquivo de backup, e um
"cheat sheet" de sintaxe que é injetado no prompt da IA pra reduzir
alucinação de comando (a IA erra muito menos quando já recebe o padrão
de sintaxe correto do fabricante em vez de "lembrar" sozinha).
"""

VENDOR_PROFILES = {
    "cisco_ios": {
        "netmiko_device_type": "cisco_ios",
        "show_running_config": "show running-config",
        "save_config_cmd": "write memory",
        "backup_extension": "cfg",
        "supports_config_replace": True,
        "cheat_sheet": """
# Cisco IOS — padrões de sintaxe

## BGP básico
router bgp <AS_LOCAL>
 neighbor <IP_VIZINHO> remote-as <AS_REMOTO>
 neighbor <IP_VIZINHO> description <TEXTO>
 network <REDE> mask <MASCARA>
!

## Verificação de BGP
show ip bgp summary
show ip bgp neighbors <IP_VIZINHO>

## CGNAT (NAT com overload de pool compartilhado, ex.: RFC6598 100.64.0.0/10)
ip nat pool CGNPOOL <IP_INICIO> <IP_FIM> prefix-length <MASCARA_CIDR>
ip access-list standard CGNAT-ACL
 permit <REDE_INTERNA> <WILDCARD>
ip nat inside source list CGNAT-ACL pool CGNPOOL overload
interface <INTERFACE_INTERNA>
 ip nat inside
interface <INTERFACE_EXTERNA>
 ip nat outside
!

## Verificação de NAT
show ip nat translations
show ip nat statistics
""",
    },
    "cisco_xe": {
        "netmiko_device_type": "cisco_xe",
        "show_running_config": "show running-config",
        "save_config_cmd": "write memory",
        "backup_extension": "cfg",
        "supports_config_replace": True,
        "cheat_sheet": """
# Cisco IOS-XE — mesma sintaxe geral do IOS clássico para BGP/NAT.
# Em plataformas ASR1000 com licença CGN, prefira "nat64" ou
# "ip nat pool ... prefix-length" para escala de CGNAT real.
""",
    },
    "huawei_vrp": {
        "netmiko_device_type": "huawei",
        "show_running_config": "display current-configuration",
        "save_config_cmd": "save",
        "backup_extension": "cfg",
        "supports_config_replace": False,
        "cheat_sheet": """
# Huawei VRP — padrões de sintaxe

## Entrar em modo de configuração
system-view

## BGP básico
bgp <AS_LOCAL>
 peer <IP_VIZINHO> as-number <AS_REMOTO>
 peer <IP_VIZINHO> description <TEXTO>
 network <REDE> <MASCARA>
 quit

## Verificação de BGP
display bgp peer
display bgp routing-table

## NAT (conceito equivalente a CGNAT, via NAT Address-Group + easy-ip/NAT ALG)
nat address-group <NOME> <IP_INICIO> <IP_FIM>
acl number <NUMERO_ACL>
 rule permit source <REDE_INTERNA> <WILDCARD>
interface <INTERFACE_EXTERNA>
 nat outbound <NUMERO_ACL> address-group <NOME>
 quit

## Verificação de NAT
display nat address-group
display nat outbound
""",
    },
    "mikrotik_routeros": {
        "netmiko_device_type": "mikrotik_routeros",
        "show_running_config": "/export",
        "save_config_cmd": None,  # RouterOS não tem "write memory", a config já é persistente
        "backup_extension": "rsc",
        "supports_config_replace": False,
        "cheat_sheet": """
# MikroTik RouterOS — padrões de sintaxe

## BGP básico (RouterOS 7+, usa o pacote /routing/bgp)
/routing bgp template
add name=default as=<AS_LOCAL> router-id=<ROUTER_ID>
/routing bgp connection
add name=<NOME_PEER> remote.address=<IP_VIZINHO> remote.as=<AS_REMOTO> \\
    local.role=ebgp templates=default

## Verificação de BGP
/routing bgp session print
/routing bgp advertisements print

## NAT / CGNAT (masquerade com pool compartilhado)
/ip pool add name=cgnat-pool ranges=<IP_INICIO>-<IP_FIM>
/ip firewall nat add chain=srcnat src-address=<REDE_INTERNA> \\
    action=src-nat to-addresses=<IP_INICIO>-<IP_FIM>

## Verificação de NAT
/ip firewall connection print
""",
    },
}


def get_profile(device_type_alias):
    if device_type_alias not in VENDOR_PROFILES:
        raise ValueError(
            f"Vendor '{device_type_alias}' não suportado. "
            f"Opções: {', '.join(VENDOR_PROFILES)}"
        )
    return VENDOR_PROFILES[device_type_alias]
