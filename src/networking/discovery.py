"""One small announcement every ~25s per transport, only on the selected interface."""
import asyncio
import socket

from .protocol import decode, ProtocolError
from ..features.wizz import TokenBucket

GROUP = '239.42.42.42'
PORT = 39741


class Discovery(asyncio.DatagramProtocol):
    def __init__(self, callback, interface=None, group=GROUP):
        self.callback, self.interface, self.group = callback, interface, group
        self.transport, self.errors = None, 0
        self.port = PORT
        self.receive_budget = TokenBucket(512, 128)

    async def start(self, port=PORT):
        self.port = port
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            sock.bind(('', port))
            local = self.interface.address if self.interface else '0.0.0.0'
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP,
                            socket.inet_aton(self.group) + socket.inet_aton(local))
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(local))
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 1)
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_LOOP, 1)
            sock.setblocking(False)
            self.transport, _ = await asyncio.get_running_loop().create_datagram_endpoint(lambda: self, sock=sock)
        except BaseException:
            sock.close()
            raise

    def datagram_received(self, data, addr):
        if len(data) > 1024 or not self.receive_budget.allow():
            return
        try:
            frame = decode(data)
            if frame['type'] in ('hello', 'bye'):
                self.callback(frame, addr[0])
        except ProtocolError:
            pass

    @property
    def destinations(self):
        broadcast = self.interface.broadcast if self.interface else '255.255.255.255'
        return tuple(dict.fromkeys((self.group, broadcast, '255.255.255.255')))

    def announce(self, raw, port=None):
        if self.transport:
            for host in self.destinations:
                try:
                    self.transport.sendto(raw, (host, port or self.port))
                except OSError:
                    self.errors += 1

    def error_received(self, exc):
        self.errors += 1

    def close(self):
        if self.transport:
            self.transport.close()
            self.transport = None
