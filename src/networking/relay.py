"""Optional authorized relay. RAM-only, bounded queues, no conversation logs."""
import argparse
import base64
import asyncio
import contextlib
from dataclasses import dataclass, field
import ssl
import time

from ..features.file_transfer import FILE_EVENTS, OFFER_LIFETIME
from ..features.break_request import Invitations, BreakRateLimit
from ..features.wizz import Cooldown, SeenMessages, TokenBucket, WIZZ_INTERVAL
from ..features.social import SOCIAL_EVENTS, SocialOrder
from ..identity import contact_from_caps
from .protocol import MAX_FRAME, MAX_PEERS, EVENTS, ProtocolError, ack, encode, read_frame


@dataclass
class Connection:
    hello: dict
    writer: object
    queue: asyncio.Queue = field(default_factory=lambda: asyncio.Queue(MAX_PEERS + 64))
    bucket: TokenBucket = field(default_factory=TokenBucket)
    wizz: Cooldown = field(default_factory=lambda: Cooldown(WIZZ_INTERVAL))
    invitations: Invitations = field(default_factory=Invitations)
    seen: SeenMessages = field(default_factory=lambda: SeenMessages(512))
    avatar_image: dict | None = None
    image_updates: TokenBucket = field(default_factory=lambda: TokenBucket(3, 0.1))
    file_bucket: TokenBucket = field(default_factory=lambda: TokenBucket(256, 1024))
    social_bucket: TokenBucket = field(default_factory=lambda: TokenBucket(12, 3))
    typing_bucket: TokenBucket = field(default_factory=lambda: TokenBucket(4, 1))
    social_order: SocialOrder = field(default_factory=SocialOrder)
    image_requests: TokenBucket = field(default_factory=lambda: TokenBucket(MAX_PEERS, 16))


class RelayServer:
    def __init__(self):
        self.clients, self.handlers = {}, set()
        self.wizz_sessions = {}
        self.break_limit = BreakRateLimit(maximum=8192)
        self.file_offers = {}
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
        sid = connection.hello['sid']
        mid = data['id']
        if data['from'] != sid:
            return ack(mid, False, 'Session incorrecte')
        if mid in connection.seen.ids:
            return ack(mid)
        target = data['to']
        recipient = self.clients.get(target)
        if target != 'general' and (recipient is None or target == sid):
            return ack(mid, False, 'Utilisateur hors ligne')
        kind = data['type']
        if kind in SOCIAL_EVENTS:
            if 'social' not in connection.hello.get('caps', ()):
                return ack(mid, False, 'Interaction incompatible')
            if not connection.social_order.newer(data):
                return ack(mid)
            bucket = connection.typing_bucket if kind == 'typing' else connection.social_bucket
            if not bucket.allow():
                return ack(mid, False, 'Trop d’interactions')
            if target == 'general':
                for other_sid, other in list(self.clients.items()):
                    if other_sid != sid and 'social' in other.hello.get('caps', ()):
                        self.put(other, data)
                success = True
            else:
                success = 'social' in recipient.hello.get('caps', ()) and self.put(recipient, data)
            connection.seen.add(mid)
            connection.social_order.remember(data)
            return ack(mid, success, '')
        if kind == 'break_invite':
            sender_contact = contact_from_caps(connection.hello.get('caps', ())) or sid
            target_contact = contact_from_caps(recipient.hello.get('caps', ())) or target
            if self.break_limit.remaining((sender_contact, target_contact)):
                return ack(mid, False, 'Demandes de pause bloquées pendant 5 minutes')
        if not connection.bucket.allow():
            return ack(mid, False, 'Trop de messages')
        if kind == 'wizz':
            if not connection.wizz.take():
                return ack(mid, False, 'Un Wizz toutes les 42 minutes')
            self.wizz_sessions[sid] = (time.monotonic(), connection.wizz)
        if kind == 'break_invite':
            sender_contact = contact_from_caps(connection.hello.get('caps', ())) or sid
            target_contact = contact_from_caps(recipient.hello.get('caps', ())) or target
            if not self.break_limit.allow((sender_contact, target_contact)):
                return ack(mid, False, 'Demandes de pause bloquées pendant 5 minutes')
            connection.invitations.add(mid, target)
        if kind == 'break_reply' and not recipient.invitations.consume(data['request'], sid):
            return ack(mid, False, 'Invitation expirée')
        connection.seen.add(mid)
        if target == 'general':
            self.broadcast(data, exclude=sid)
            return ack(mid)
        return ack(mid, self.put(recipient, data), '')

    def route_file(self, connection, data):
        sid, mid, target, kind = connection.hello['sid'], data['id'], data['to'], data['type']
        if data['from'] != sid or 'files' not in connection.hello.get('caps', ()):
            return ack(mid, False, 'Session ou capacité incorrecte')
        if mid in connection.seen.ids:
            return ack(mid)
        if not connection.file_bucket.allow():
            return ack(mid, False, 'Trop de données de fichier')
        now = time.monotonic()
        self.file_offers = {key: value for key, value in self.file_offers.items() if value['expires'] > now}
        if kind == 'file_offer':
            if (sid, mid) in self.file_offers:
                return ack(mid)
            if (not connection.bucket.allow() or len(self.file_offers) >= 256
                    or sum(owner == sid for owner, request in self.file_offers) >= 8):
                return ack(mid, False, 'Trop de fichiers proposés')
            recipients = {other_sid for other_sid, other in self.clients.items()
                          if other_sid != sid and (target == 'general' or other_sid == target)
                          and 'files' in other.hello.get('caps', ())}
            if not recipients:
                return ack(mid, False, 'Destinataire incompatible ou hors ligne')
            self.file_offers[(sid, mid)] = dict(size=data['size'], recipients=recipients,
                                               active={}, expires=now + OFFER_LIFETIME)
            delivered = {other for other in recipients if self.put(self.clients[other], data)}
            self.file_offers[(sid, mid)]['recipients'] = delivered
            success = bool(delivered)
            if not delivered:
                self.file_offers.pop((sid, mid), None)
        else:
            recipient = self.clients.get(target)
            if not recipient or target == sid or 'files' not in recipient.hello.get('caps', ()):
                return ack(mid, False, 'Destinataire incompatible ou hors ligne')
            receiver_control = kind in ('file_accept', 'file_decline', 'file_done')
            key = (target, data['request']) if receiver_control else (sid, data['request'])
            if kind == 'file_cancel' and key not in self.file_offers:
                key = (target, data['request'])
                receiver_control = True
            offer = self.file_offers.get(key)
            receiver = sid if receiver_control else target
            if not offer or receiver not in offer['recipients']:
                return ack(mid, False, 'Proposition de fichier expirée')
            active = offer['active']
            if kind == 'file_accept':
                active.setdefault(receiver, [0, False])
            elif kind == 'file_chunk':
                state = active.get(receiver)
                if not state or state[1] or data['offset'] != state[0]:
                    return ack(mid, False, 'Fichier non accepté ou bloc incorrect')
                length = len(base64.b64decode(data['data'], validate=True))
                if state[0] + length > offer['size']:
                    return ack(mid, False, 'Fichier trop long')
                state[0] += length
            elif kind == 'file_end':
                state = active.get(receiver)
                if not state or state[0] != offer['size']:
                    return ack(mid, False, 'Fichier incomplet')
                state[1] = True
            elif kind == 'file_done':
                state = active.get(receiver)
                if not state or not state[1]:
                    return ack(mid, False, 'Fichier incomplet')
                offer['recipients'].discard(receiver)
                active.pop(receiver, None)
            elif kind in ('file_decline', 'file_cancel'):
                offer['recipients'].discard(receiver)
                active.pop(receiver, None)
            success = self.put(recipient, data)
            if not offer['recipients']:
                self.file_offers.pop(key, None)
        connection.seen.add(mid)
        return ack(mid, success, '')

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
            self.handshakes -= 1
            handshaking = False
            now = time.monotonic()
            self.wizz_sessions = {sid: pair for sid, pair in self.wizz_sessions.items()
                                  if now - pair[0] < WIZZ_INTERVAL or sid in self.clients}
            if hello['sid'] not in self.wizz_sessions and len(self.wizz_sessions) >= 8192:
                return
            _, gate = self.wizz_sessions.get(hello['sid'], (now, Cooldown(WIZZ_INTERVAL)))
            self.wizz_sessions[hello['sid']] = (now, gate)
            connection = Connection(dict(hello), writer, wizz=gate)
            # Roster contains identities only, never previous messages.
            self.put(connection, {'v': 1, 'type': 'pong', 'caps': ['avatar_image', 'files', 'social']})
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
                    if data['sid'] != sid or not connection.bucket.allow():
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
                elif kind in FILE_EVENTS:
                    self.put(connection, self.route_file(connection, data))
                elif kind in EVENTS:
                    self.put(connection, self.route(connection, data))
                else:
                    raise ProtocolError('Type inattendu')
        except (OSError, ConnectionError, ProtocolError, asyncio.TimeoutError):
            pass
        finally:
            if handshaking:
                self.handshakes -= 1
            if connection:
                sid = connection.hello['sid']
                self.clients.pop(sid, None)
                for key, offer in list(self.file_offers.items()):
                    if key[0] == sid:
                        self.file_offers.pop(key, None)
                    else:
                        offer['recipients'].discard(sid)
                        offer['active'].pop(sid, None)
                        if not offer['recipients']:
                            self.file_offers.pop(key, None)
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
            await self.server.wait_closed()
        for client in list(self.clients.values()):
            client.writer.close()
        for task in list(self.handlers):
            task.cancel()
        await asyncio.gather(*self.handlers, return_exceptions=True)
        self.clients.clear()
        self.wizz_sessions.clear()
        self.file_offers.clear()


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
