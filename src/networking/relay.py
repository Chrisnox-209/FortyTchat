"""Optional authorized relay. RAM-only, bounded queues, no conversation logs."""
import argparse
import asyncio
import contextlib
from dataclasses import dataclass, field
import ssl

from ..features.wizz import TokenBucket
from ..identity import contact_from_caps
from ..security import SecureSession, SecurityError
from .protocol import MAX_FRAME, MAX_PEERS, ProtocolError, ack, encode, read_frame


@dataclass
class Connection:
    hello: dict
    writer: object
    queue: asyncio.Queue = field(default_factory=lambda: asyncio.Queue(MAX_PEERS + 64))
    bucket: TokenBucket = field(default_factory=TokenBucket)
    avatar_image: dict | None = None
    image_updates: TokenBucket = field(default_factory=lambda: TokenBucket(3, 0.1))
    file_bucket: TokenBucket = field(default_factory=lambda: TokenBucket(256, 1024))
    image_requests: TokenBucket = field(default_factory=lambda: TokenBucket(MAX_PEERS, 16))


class RelayServer:
    def __init__(self):
        self.clients, self.handlers = {}, set()
        self.server, self.handshakes, self.closing = None, 0, False

    async def start(self, host='127.0.0.1', port=39743, ssl_context=None):
        self.server = await asyncio.start_server(self.handle, host, port, ssl=ssl_context, limit=MAX_FRAME, backlog=512)
        return self.server.sockets[0].getsockname()[1]

    def put(self, connection, data):
        try:
            connection.queue.put_nowait(encode(data))
            return True
        except asyncio.QueueFull:
            connection.writer.close()
            return False

    def broadcast(self, data, exclude=''):
        for sid, connection in list(self.clients.items()):
            if sid != exclude:
                self.put(connection, data)

    async def pump(self, connection):
        while True:
            raw = await connection.queue.get()
            connection.writer.write(raw)
            await asyncio.wait_for(connection.writer.drain(), 5)

    def route(self, connection, data):
        # Opaque, individually addressed ciphertext only. Consent, file bounds,
        # action limits and identity pins are enforced by the receiving endpoint.
        sid, mid = connection.hello['sid'], data['id']
        if data['type'] != 'sealed' or data['from'] != sid:
            return ack(mid, False, 'Chiffrement de bout en bout requis')
        recipient = self.clients.get(data['to'])
        if recipient is None or data['to'] == sid:
            return ack(mid, False, 'Utilisateur hors ligne')
        if not connection.file_bucket.allow():
            return ack(mid, False, 'Trop de données chiffrées')
        # Transport ACK is deliberately not an endpoint delivery confirmation.
        return ack(mid, self.put(recipient, data), '')

    async def handle(self, reader, writer):
        task = asyncio.current_task()
        self.handlers.add(task)
        connection, pump = None, None
        if self.closing or self.handshakes + len(self.clients) >= MAX_PEERS:
            writer.close()
            self.handlers.discard(task)
            return
        self.handshakes += 1
        handshaking = True
        try:
            hello = await read_frame(reader, 5)
            if hello['type'] != 'hello' or hello['sid'] in self.clients:
                return
            SecureSession.check_presence(hello)
            self.handshakes -= 1
            handshaking = False
            connection = Connection(dict(hello), writer)
            # Roster contains identities only, never previous messages.
            self.put(connection, {'v': 1, 'type': 'pong', 'caps': ['avatar_image', 'files', 'social', 'cards', 'e2ee']})
            for other in self.clients.values():
                self.put(connection, {**other.hello, 'type': 'presence'})
            sid = hello['sid']
            self.clients[sid] = connection
            self.broadcast({**hello, 'type': 'presence'}, exclude=sid)
            pump = asyncio.create_task(self.pump(connection))
            pump.add_done_callback(lambda t: writer.close() if not t.cancelled() else None)
            while not self.closing:
                data = await read_frame(reader, 95)
                kind = data['type']
                if kind == 'hello':
                    SecureSession.check_presence(data)
                    if (data['sid'] != sid or not connection.bucket.allow()
                            or data['crypto']['identity'] != connection.hello['crypto']['identity']
                            or data['crypto']['session'] != connection.hello['crypto']['session']
                            or contact_from_caps(data.get('caps', [])) != contact_from_caps(connection.hello.get('caps', []))):
                        raise ProtocolError('Session incorrecte')
                    if data.get('avatar_hash', '') != connection.hello.get('avatar_hash', ''):
                        connection.avatar_image = None
                    connection.hello = dict(data)
                    self.broadcast({**data, 'type': 'presence'}, exclude=sid)
                elif kind == 'avatar_image':
                    if data['sid'] != sid or data['hash'] != connection.hello.get('avatar_hash', ''):
                        raise ProtocolError('Image liée à une autre session')
                    if connection.avatar_image and connection.avatar_image['hash'] == data['hash']:
                        continue
                    if not connection.image_updates.allow():
                        raise ProtocolError('Trop de changements d’image')
                    connection.avatar_image = dict(data)
                    for other_sid, other in list(self.clients.items()):
                        if other_sid != sid and 'avatar_image' in other.hello.get('caps', []):
                            self.put(other, data)
                elif kind == 'avatar_get':
                    if data['from'] != sid or not connection.image_requests.allow():
                        raise ProtocolError('Demande d’image invalide')
                    owner = self.clients.get(data['sid'])
                    if owner and owner.avatar_image and owner.avatar_image['hash'] == data['hash']:
                        self.put(connection, owner.avatar_image)
                elif kind == 'ping':
                    self.put(connection, {'v': 1, 'type': 'pong'})
                elif kind == 'sealed':
                    self.put(connection, self.route(connection, data))
                else:
                    raise ProtocolError('Type inattendu')
        except (OSError, ConnectionError, ProtocolError, SecurityError, asyncio.TimeoutError):
            pass
        finally:
            if handshaking:
                self.handshakes -= 1
            if connection:
                sid = connection.hello['sid']
                self.clients.pop(sid, None)
                self.broadcast({'v': 1, 'type': 'bye', 'sid': sid})
            if pump:
                pump.cancel()
                await asyncio.gather(pump, return_exceptions=True)
            writer.close()
            with contextlib.suppress(OSError):
                await writer.wait_closed()
            self.handlers.discard(task)

    async def stop(self):
        self.closing = True
        if self.server:
            self.server.close()
        for client in list(self.clients.values()):
            client.writer.close()
        for task in list(self.handlers):
            task.cancel()
        await asyncio.gather(*self.handlers, return_exceptions=True)
        if self.server:
            await self.server.wait_closed()
        self.clients.clear()


async def run(args):
    import resource
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    context = None
    if args.insecure_local:
        if args.host not in ('127.0.0.1', '::1', 'localhost'):
            raise SystemExit('--insecure-local est réservé à la boucle locale')
    else:
        if not args.cert or not args.key:
            raise SystemExit('Fournir --cert et --key pour TLS (ou --insecure-local pour les tests locaux).')
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.load_cert_chain(args.cert, args.key)
    server = RelayServer()
    port = await server.start(args.host, args.port, context)
    print(f'FortyTchat : relais en écoute sur {args.host}:{port}. Aucun historique ni journal de messages.')
    try:
        await server.server.serve_forever()
    finally:
        await server.stop()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Relais FortyTchat, à déployer sur une machine autorisée.')
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=39743)
    parser.add_argument('--cert')
    parser.add_argument('--key')
    parser.add_argument('--insecure-local', action='store_true')
    try:
        asyncio.run(run(parser.parse_args()))
    except KeyboardInterrupt:
        pass
