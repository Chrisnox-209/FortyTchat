"""Deployment defaults; students never need to edit these or enter an address."""
from dataclasses import dataclass
import json
from pathlib import Path


@dataclass(frozen=True)
class NetworkConfig:
    mode: str = 'auto'
    udp_port: int = 39741
    tcp_port: int = 39742
    multicast_group: str = '239.42.42.42'
    heartbeat: float = 25.0
    peer_timeout: float = 90.0
    relay_host: str = ''
    relay_port: int = 39743
    relay_ca: str = ''

    @classmethod
    def load(cls, path):
        path = Path(path)
        if not path.exists():
            return cls()
        if path.stat().st_size > 8192:
            raise ValueError('network.json trop volumineux')
        data = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(data, dict) or set(data) - set(cls.__dataclass_fields__):
            raise ValueError('Clé réseau inconnue')
        value = cls(**data)
        if value.mode not in ('auto', 'lan', 'relay'):
            raise ValueError('Mode réseau : auto, lan ou relay')
        for port in (value.udp_port, value.tcp_port, value.relay_port):
            if type(port) is not int or not 1024 <= port <= 65535:
                raise ValueError('Les ports de production doivent être compris entre 1024 et 65535')
        if type(value.heartbeat) not in (int, float) or not 20 <= value.heartbeat <= 30:
            raise ValueError('Heartbeat : 20 à 30 secondes')
        if type(value.peer_timeout) not in (int, float) or not 75 <= value.peer_timeout <= 180:
            raise ValueError('Expiration : 75 à 180 secondes')
        if any(not isinstance(x, str) for x in (value.relay_host, value.relay_ca, value.multicast_group)):
            raise ValueError('Configuration réseau invalide')
        import ipaddress
        if not ipaddress.IPv4Address(value.multicast_group).is_multicast:
            raise ValueError('Groupe multicast invalide')
        if value.mode == 'relay' and not value.relay_host:
            raise ValueError('Le mode relais nécessite un nom DNS déployé dans network.json')
        return value
