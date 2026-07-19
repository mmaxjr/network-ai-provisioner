"""
Inventário de equipamentos + wrapper de conexão SSH (netmiko).

O inventário é um YAML simples com uma lista de dispositivos. Cada um
tem um "device_type" que é o *alias* usado em vendors.py (não confundir
com o device_type do netmiko em si — o alias pode ter granularidade
diferente, ex: "cisco_ios" vs "cisco_xe" mesmo sendo os dois netmiko
"cisco_ios" por baixo).
"""

import os

import yaml
from netmiko import ConnectHandler

from vendors import get_profile


class Device:
    def __init__(self, name, raw):
        self.name = name
        self.host = raw["host"]
        self.device_type_alias = raw["device_type"]
        self.username = raw["username"]
        self.password = raw.get("password") or os.environ.get(
            f"NETAI_PASSWORD_{name.upper()}", ""
        )
        self.secret = raw.get("secret", "")  # enable password (Cisco)
        self.port = raw.get("port", 22)
        self.profile = get_profile(self.device_type_alias)

    def netmiko_params(self):
        return {
            "device_type": self.profile["netmiko_device_type"],
            "host": self.host,
            "username": self.username,
            "password": self.password,
            "secret": self.secret,
            "port": self.port,
            "fast_cli": False,
        }


def load_inventory(path="inventory.yaml"):
    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or []
    return {item["name"]: Device(item["name"], item) for item in raw}


def get_device(name, inventory_path="inventory.yaml"):
    inventory = load_inventory(inventory_path)
    if name not in inventory:
        disponiveis = ", ".join(inventory) or "(nenhum)"
        raise KeyError(f"Dispositivo '{name}' não encontrado no inventário. Disponíveis: {disponiveis}")
    return inventory[name]


class DeviceConnection:
    """Context manager fino em cima do netmiko ConnectHandler."""

    def __init__(self, device: Device):
        self.device = device
        self.conn = None

    def __enter__(self):
        print(f"[conexao] conectando via SSH em {self.device.name} ({self.device.host})...")
        self.conn = ConnectHandler(**self.device.netmiko_params())
        if self.device.secret:
            self.conn.enable()
        return self.conn

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.conn:
            self.conn.disconnect()
        print(f"[conexao] desconectado de {self.device.name}")
