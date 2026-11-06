"""Linux interface discovery using read-only ioctls, no external command or root."""
from dataclasses import asdict, dataclass
import fcntl
import ipaddress
from pathlib import Path
import socket
import struct

EXCLUDED = ('lo', 'docker', 'veth', 'virbr', 'br-', 'tun', 'tap', 'wg', 'tailscale', 'zt', 'sit', 'vmnet')


@dataclass(frozen=True)
class Interface:
    name: str
    address: str
    netmask: str
    broadcast: str
    subnet: str
    physical: bool

    def public(self):
        return asdict(self)


def interface_record(name, address, netmask, active=True, physical=True):
    if not active or name.startswith(EXCLUDED):
        return None
    address_obj = ipaddress.IPv4Address(address)
    if address_obj.is_loopback or address_obj.is_unspecified or address_obj.is_link_local:
        return None
    subnet = ipaddress.IPv4Network(f'{address}/{netmask}', strict=False)
    return Interface(name, address, netmask, str(subnet.broadcast_address), str(subnet), physical)


def default_interface():
    try:
        for line in Path('/proc/net/route').read_text().splitlines()[1:]:
            parts = line.split()
            if len(parts) > 3 and parts[1] == '00000000' and int(parts[3], 16) & 1:
                return parts[0]
    except (OSError, ValueError):
        pass
    return ''


def interfaces():
    result = []
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    except OSError:
        return []
    with sock:
        for _, name in socket.if_nameindex():
            if name.startswith(EXCLUDED):
                continue
            request = struct.pack('256s', name.encode()[:15])
            try:
                flags = struct.unpack_from('H', fcntl.ioctl(sock, 0x8913, request), 16)[0]
                address = socket.inet_ntoa(fcntl.ioctl(sock, 0x8915, request)[20:24])
                mask = socket.inet_ntoa(fcntl.ioctl(sock, 0x891b, request)[20:24])
                item = interface_record(name, address, mask, bool(flags & 1 and flags & 64),
                                        Path('/sys/class/net', name, 'device').exists())
                if item:
                    result.append(item)
            except (OSError, ValueError):
                continue
    # Real physical links first. A container's active eth0 may be used as a labelled fallback.
    preferred = default_interface()
    return sorted(result, key=lambda i: (not i.physical, i.name != preferred, i.name))
