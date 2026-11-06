"""Async network core, independent of Qt. All mutable network state lives on one loop."""
import asyncio
import base64
import os
import contextlib
import ssl
import random
import time

from ..features.file_transfer import FileTransfers, FILE_EVENTS, CHUNK_SIZE, disk, fingerprint, open_source
from ..features.break_request import Invitations, BreakRateLimit
from ..features.wizz import Cooldown, SeenMessages, TokenBucket, WIZZ_INTERVAL
from ..features.social import SOCIAL_EVENTS, SocialOrder
from ..identity import session_id, unique_nickname, valid_nickname, contact_capability, contact_from_caps
from ..avatar_data import avatar_hash, avatar_frame
from .discovery import Discovery, PORT
from .interfaces import interfaces
from .diagnostics import socket_error
from .presence import Peer, HEARTBEAT, PEER_TIMEOUT
from .protocol import MAX_FRAME, MAX_PEERS, EVENTS, ProtocolError, ack, encode, event, read_frame


class ChatClient:
    def __init__(self, nickname, callback=None, *, discovery=True, discovery_port=PORT,
                 relay_host='', relay_port=39743, tls=True, tcp_port=39742,
                 heartbeat=25, peer_timeout=90, multicast_group='239.42.42.42',
                 last_wizz_at=0, persist_wizz=None, avatar=-1, avatar_image='', contact_id=None):
        self.sid, self.nickname = session_id(), nickname
        self.status = 'online'
        self.contact_id = contact_id or session_id()
        self.avatar = avatar
        self.avatar_image = avatar_image
        self.avatar_hash = avatar_hash(avatar_image)
        self.avatar_fetches, self.avatar_attempts = {}, {}
        self.avatar_slots = asyncio.Semaphore(4)
        self.avatar_reads = TokenBucket(32, 8)
        self.relay_images = False
        self.relay_files = False
        self.relay_social = False
        self.callback = callback or (lambda kind, data: None)
        self.discovery_enabled, self.discovery_port = discovery, discovery_port
        self.relay_host, self.relay_port, self.tls = relay_host, relay_port, tls
        self.tcp_port, self.heartbeat, self.peer_timeout = tcp_port, heartbeat, peer_timeout
        self.multicast_group = multicast_group
        self.interface = None
        self.errors = []
        self._last_reply = 0.0
        self.persist_wizz = persist_wizz
        self.peers, self.blocked = {}, set()
        self.seen, self.outgoing_invites, self.incoming_invites = SeenMessages(), Invitations(), Invitations()
        self.message_bucket = TokenBucket()
        self.social_bucket, self.typing_bucket = TokenBucket(12, 3), TokenBucket(4, 1)
        self.social_order, self.social_sequence = SocialOrder(), 0
        self.wizz_cooldown = Cooldown(WIZZ_INTERVAL)
        self.outgoing_break_limit, self.incoming_break_limit = BreakRateLimit(), BreakRateLimit()
        remaining = min(WIZZ_INTERVAL, max(0, WIZZ_INTERVAL - (time.time() - last_wizz_at))) if last_wizz_at else 0
        if remaining:
            self.wizz_cooldown.last = time.monotonic() - WIZZ_INTERVAL + remaining
        self.receive_wizz = Cooldown(10)
        self.wizz_enabled, self.dnd = True, False
        self.server, self.discovery, self.relay_writer = None, None, None
        self.tasks, self.connections, self.acks = set(), set(), {}
        self.operations, self.handlers = set(), set()
        self._closing, self._incoming = False, 0
        self._outgoing = asyncio.Semaphore(24)
        self._queued = 0
        self.port = 0
        self.files = FileTransfers(self)

    def emit(self, kind, **data):
        self.callback(kind, data)

    def task(self, coroutine):
        task = asyncio.create_task(coroutine)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        # Consume exceptions without logging frames or message content.
        task.add_done_callback(lambda t: None if t.cancelled() else t.exception())
        return task

    def hello(self, kind='hello'):
        return {'v': 1, 'type': kind, 'sid': self.sid, 'nick': self.nickname,
                'status': self.status, 'port': self.port,
                'caps': ['files', 'wizz', 'pause', 'probe', 'social', 'avatar_image', 'friends', contact_capability(self.contact_id)],
                'avatar': self.avatar, 'avatar_hash': self.avatar_hash}

    def network_state(self):
        return dict(direct=bool(self.discovery), relay=bool(self.relay_writer),
                    port=self.port, udp_port=self.discovery_port, peers=len(self.peers),
                    interface=self.interface.public() if self.interface else None,
                    relay_host=self.relay_host, errors=list(self.errors),
                    tcp_listening=bool(self.server), udp_errors=self.discovery.errors if self.discovery else 0)

    def network_error(self, text):
        if not self.errors or self.errors[-1] != text:
            self.errors = (self.errors + [text])[-6:]
            self.emit('notice', text=text)

    async def _start_local(self):
        choices = await asyncio.to_thread(interfaces)
        selected = choices[0] if choices else None
        if self.discovery and selected != self.interface:
            self.discovery.close()
            self.discovery = None
            for sid, peer in list(self.peers.items()):
                peer.direct_seen = 0
                if not peer.relay:
                    self._remove_peer(sid)
        self.interface = selected
        if self.server is None:
            try:
                self.server = await asyncio.start_server(self._handle_direct, '0.0.0.0', self.tcp_port, limit=MAX_FRAME)
                self.port = self.server.sockets[0].getsockname()[1]
            except OSError as exc:
                self.port = self.tcp_port or 39742
                self.network_error(socket_error(exc, 'TCP', self.tcp_port))
                return
        if self.discovery_enabled and self.discovery is None:
            discovery = Discovery(self.observe, self.interface, self.multicast_group)
            try:
                await discovery.start(self.discovery_port)
                self.discovery = discovery
            except OSError as exc:
                self.network_error(socket_error(exc, 'UDP', self.discovery_port))

    async def start(self):
        await self._start_local()
        self.emit('network', **self.network_state())
        self.task(self._heartbeat())
        if self.relay_host:
            self.task(self._relay_loop())

    def observe(self, data, host, relay=False):
        sid = data['sid']
        if sid == self.sid:
            return
        peer = self.peers.get(sid)
        if peer and not relay and peer.direct and host != peer.host:
            return  # Pin the observed source while this direct route remains alive.
        if data['type'] == 'bye':
            if peer:
                # A LAN goodbye cannot disconnect a relay route.
                if relay:
                    peer.relay = False
                elif peer.host == host:
                    peer.direct_seen = 0
                if not peer.relay and not peer.direct:
                    self._remove_peer(sid)
            return
        if peer is None:
            if len(self.peers) >= MAX_PEERS:
                return
            peer = Peer(sid, data['nick'], timeout=self.peer_timeout, bucket=TokenBucket(), wizz=Cooldown(WIZZ_INTERVAL),
                        social_bucket=TokenBucket(12, 3), typing_bucket=TokenBucket(4, 1))
            self.peers[sid] = peer
            new = True
        else:
            new = False
        previous = (peer.nick, peer.status, peer.relay, peer.avatar, peer.avatar_hash, peer.contact_id)
        if relay or not peer.relay:
            peer.nick, peer.status = data['nick'], data['status']
            peer.caps = tuple(data.get('caps', ()))
            peer.avatar = data.get('avatar', -1)
            peer.contact_id = data.get('contact_id') or contact_from_caps(peer.caps)
            photo_hash = data.get('avatar_hash', '')
            if photo_hash != peer.avatar_hash:
                peer.avatar_hash, peer.avatar_image = photo_hash, ''
                old = self.avatar_fetches.pop(sid, None)
                if old:
                    old.cancel()
                self.avatar_attempts.pop(sid, None)
        if relay:
            peer.relay = True
        else:
            peer.host, peer.port, peer.direct_seen = host, data['port'], time.monotonic()
        if new:
            self.emit('joined', peer=peer.public())
            if self.discovery and not relay and time.monotonic() - self._last_reply > 5:
                self._last_reply = time.monotonic()
                asyncio.get_running_loop().call_later(random.uniform(0.2, 1.5), self._announce)
        if new or previous != (peer.nick, peer.status, peer.relay, peer.avatar, peer.avatar_hash, peer.contact_id):
            self.emit_peers()
        self._queue_avatar(peer)
        # Lexicographically smaller session keeps a contested nickname.
        if peer.nick.casefold() == self.nickname.casefold() and sid < self.sid:
            self.rename(unique_nickname(self.nickname, [p.nick for p in self.peers.values()]))

    def emit_peers(self):
        self.emit('peers', peers=[p.public() for p in self.peers.values()])

    def _remove_peer(self, sid):
        peer = self.peers.pop(sid, None)
        fetch = self.avatar_fetches.pop(sid, None)
        if fetch:
            fetch.cancel()
        self.avatar_attempts.pop(sid, None)
        if peer:
            self.emit('left', peer=peer.public())
            self.emit_peers()

    def _announce(self):
        if self.discovery and not self._closing:
            self.discovery.announce(encode(self.hello()), self.discovery_port)

    async def _heartbeat(self):
        while not self._closing:
            await self._start_local()
            self._announce()
            self.files.prune()
            now = time.monotonic()
            for sid, peer in list(self.peers.items()):
                if not peer.relay and now - peer.direct_seen > self.peer_timeout:
                    self._remove_peer(sid)
                else:
                    self._queue_avatar(peer)
            if self.relay_writer:
                try:
                    self.relay_writer.write(encode({'v': 1, 'type': 'ping'}))
                    await asyncio.wait_for(self.relay_writer.drain(), 3)
                except (OSError, asyncio.TimeoutError):
                    self.relay_writer.close()
            self.emit('network', **self.network_state())
            await asyncio.sleep(max(1, self.heartbeat + random.uniform(-2, 2)))

    def rename(self, nickname):
        if not valid_nickname(nickname):
            raise ValueError('Pseudo : 3 à 28 lettres, chiffres, tirets ou underscores.')
        self.nickname = unique_nickname(nickname, [p.nick for p in self.peers.values()])
        self.emit('identity', nickname=self.nickname)
        self._announce()
        if self.relay_writer:
            self.relay_writer.write(encode(self.hello()))

    def preferences(self, *, wizz_enabled, dnd, status='online', avatar=None, avatar_image=None):
        if avatar is not None:
            if type(avatar) is not int or not -1 <= avatar < 24:
                raise ValueError('Avatar invalide')
            self.avatar = avatar
        if avatar_image is not None:
            self.avatar_hash = avatar_hash(avatar_image)
            self.avatar_image = avatar_image
        self.wizz_enabled, self.dnd = wizz_enabled, dnd
        self.status = 'dnd' if dnd else status
        self._announce()
        if self.relay_writer:
            self.relay_writer.write(encode(self.hello()))
            self._publish_avatar()

    def block(self, sid, blocked=True):
        if blocked:
            self.blocked.add(sid)
            peer = self.peers.get(sid)
            if peer:
                peer.avatar_image = ''
                self.emit_peers()
        else:
            self.blocked.remove(sid) if sid in self.blocked else None
            self.avatar_attempts.pop(sid, None)
            if sid in self.peers:
                self._queue_avatar(self.peers[sid])

    def _publish_avatar(self):
        if self.relay_writer and self.relay_images and self.avatar_image:
            self.relay_writer.write(encode(avatar_frame(self.sid, self.avatar_image)))

    def _receive_avatar(self, frame):
        peer = self.peers.get(frame['sid'])
        if (not peer or peer.sid in self.blocked or frame['hash'] != peer.avatar_hash
                or frame['data'] == peer.avatar_image):
            return
        peer.avatar_image = frame['data']
        self.emit_peers()

    def _queue_avatar(self, peer):
        if (self._closing or not peer.avatar_hash or peer.avatar_image or peer.sid in self.blocked
                or 'avatar_image' not in peer.caps or peer.sid in self.avatar_fetches
                or time.monotonic() - self.avatar_attempts.get(peer.sid, -100) < 3):
            return
        if not peer.direct and not (peer.relay and self.relay_writer and self.relay_images):
            return
        self.avatar_attempts[peer.sid] = time.monotonic()
        task = self.task(self._fetch_avatar(peer.sid, peer.avatar_hash))
        self.avatar_fetches[peer.sid] = task
        def done(finished):
            if self.avatar_fetches.get(peer.sid) is finished:
                self.avatar_fetches.pop(peer.sid, None)
        task.add_done_callback(done)

    async def _fetch_avatar(self, sid, image_hash):
        async with self.avatar_slots:
            peer = self.peers.get(sid)
            if not peer or peer.avatar_hash != image_hash or sid in self.blocked:
                return
            request = {'v': 1, 'type': 'avatar_get', 'from': self.sid, 'sid': sid, 'hash': image_hash}
            if peer.relay and self.relay_writer and self.relay_images:
                self.relay_writer.write(encode(request))
                await asyncio.wait_for(self.relay_writer.drain(), 3)
                return
            if not peer.direct:
                return
            writer = None
            try:
                reader, writer = await asyncio.wait_for(asyncio.open_connection(peer.host, peer.port, limit=MAX_FRAME), 3)
                self.connections.add(writer)
                writer.write(encode(self.hello()) + encode(request))
                await asyncio.wait_for(writer.drain(), 3)
                frame = await read_frame(reader, 4)
                if frame['type'] == 'avatar_image' and frame['sid'] == sid:
                    self._receive_avatar(frame)
            except (OSError, ConnectionError, ProtocolError, asyncio.TimeoutError):
                pass
            finally:
                if writer:
                    writer.close()
                    with contextlib.suppress(OSError):
                        await writer.wait_closed()
                    self.connections.discard(writer)

    def _receive(self, frame):
        sender = frame['from']
        peer = self.peers.get(sender)
        if not peer or sender in self.blocked:
            return False, 'Utilisateur indisponible'
        if frame['to'] not in (self.sid, 'general'):
            return False, 'Destinataire incorrect'
        if frame['id'] in self.seen.ids:
            return True, ''
        kind = frame['type']
        if kind == 'break_invite' and self.incoming_break_limit.remaining(peer.contact_id or sender):
            return False, 'Demandes de pause bloquées pendant 5 minutes'
        if kind in SOCIAL_EVENTS and 'social' not in peer.caps:
            return False, 'Interaction incompatible'
        if kind in SOCIAL_EVENTS and not self.social_order.newer(frame):
            return True, ''
        bucket = (peer.typing_bucket if kind == 'typing' else peer.social_bucket) if kind in SOCIAL_EVENTS else peer.bucket
        if not bucket.allow():
            return False, 'Trop de messages'
        if kind == 'wizz':
            if not self.wizz_enabled or self.dnd or peer.wizz.remaining() or self.receive_wizz.remaining():
                return False, 'Wizz indisponible'
            peer.wizz.take()
            self.receive_wizz.take()
        if kind == 'break_invite':
            if not self.incoming_break_limit.allow(peer.contact_id or sender):
                return False, 'Demandes de pause bloquées pendant 5 minutes'
            if not self.incoming_invites.add(frame['id'], sender):
                return False, 'Invitation indisponible'
        if kind == 'break_reply' and not self.outgoing_invites.consume(frame['request'], sender):
            return False, 'Invitation expirée'
        self.seen.add(frame['id'])
        if kind in SOCIAL_EVENTS:
            self.social_order.remember(frame)
        # Keep only validated fields; never trust a network-provided display name.
        payload = {k: frame[k] for k in ('type', 'id', 'from', 'to', 'text', 'request', 'answer', 'reply_to', 'message', 'emoji', 'active') if k in frame}
        self.emit('event', frame=payload, nickname=peer.nick, avatar=peer.avatar, avatar_image=peer.avatar_image,
                  contact_id=peer.contact_id)
        return True, ''

    async def _handle_direct(self, reader, writer):
        if self._incoming >= 64 or self._closing:
            writer.close()
            return
        self._incoming += 1
        handler = asyncio.current_task()
        self.handlers.add(handler)
        self.connections.add(writer)
        try:
            host = writer.get_extra_info('peername')[0]
            frame = await read_frame(reader, 5)
            if frame['type'] == 'hello':
                self.observe(frame, host)
                frame = await read_frame(reader, 5)
            if frame['type'] == 'ping' and any(p.host == host and p.direct for p in self.peers.values()):
                writer.write(encode({'v': 1, 'type': 'pong'}))
                await asyncio.wait_for(writer.drain(), 3)
                return
            peer = self.peers.get(frame.get('from'))
            if frame['type'] == 'avatar_get':
                if (peer and peer.host == host and peer.direct and frame['sid'] == self.sid
                        and frame['hash'] == self.avatar_hash and self.avatar_image and self.avatar_reads.allow()):
                    writer.write(encode(avatar_frame(self.sid, self.avatar_image)))
                    await asyncio.wait_for(writer.drain(), 3)
                return
            while True:
                if not peer or peer.host != host or not peer.direct:
                    return
                if frame['type'] in FILE_EVENTS:
                    ok, reason = await self.files.receive(frame)
                elif frame['type'] in EVENTS:
                    ok, reason = self._receive(frame)
                else:
                    return
                writer.write(encode(ack(frame['id'], ok, reason)))
                await asyncio.wait_for(writer.drain(), 3)
                if frame['type'] not in FILE_EVENTS or not ok or frame['type'] == 'file_end':
                    return
                frame = await read_frame(reader, 15)
                if frame.get('from') != peer.sid:
                    return
        except (OSError, ConnectionError, ProtocolError, asyncio.TimeoutError):
            pass
        finally:
            writer.close()
            with contextlib.suppress(OSError):
                await writer.wait_closed()
            self.connections.discard(writer)
            self._incoming -= 1
            self.handlers.discard(handler)

    async def _send_direct(self, peer, frame):
        async with self._outgoing:
            for attempt in range(2):
                if self._closing:
                    return False
                writer = None
                try:
                    reader, writer = await asyncio.wait_for(asyncio.open_connection(peer.host, peer.port, limit=MAX_FRAME), 3)
                    self.connections.add(writer)
                    writer.write(encode(self.hello()) + encode(frame))
                    await asyncio.wait_for(writer.drain(), 3)
                    reply = await read_frame(reader, 4)
                    return reply['type'] == 'ack' and reply['id'] == frame['id'] and reply['ok']
                except (OSError, ConnectionError, ProtocolError, asyncio.TimeoutError):
                    if not attempt:
                        await asyncio.sleep(0.3)
                finally:
                    if writer:
                        writer.close()
                        with contextlib.suppress(OSError):
                            await writer.wait_closed()
                        self.connections.discard(writer)
            return False

    async def _send_relay(self, frame):
        if not self.relay_writer:
            return False
        future = asyncio.get_running_loop().create_future()
        self.acks[frame['id']] = future
        try:
            self.relay_writer.write(encode(frame))
            await asyncio.wait_for(self.relay_writer.drain(), 3)
            return await asyncio.wait_for(future, 6)
        except (OSError, asyncio.TimeoutError):
            return False
        finally:
            self.acks.pop(frame['id'], None)

    async def send(self, kind, target, **payload):
        operation = asyncio.current_task()
        self.operations.add(operation)
        try:
            return await self._send(kind, target, **payload)
        finally:
            self.operations.discard(operation)

    async def _send(self, kind, target, **payload):
        if self._closing:
            return None
        if kind in SOCIAL_EVENTS:
            return await self._send_social(kind, target, **payload)
        if self._queued >= 16:
            self.emit('notice', text='Trop de messages en attente. Réessayez dans un instant.')
            return None
        frame = event(kind, self.sid, target, **payload)
        try:
            encode(frame)
        except ProtocolError as exc:
            self.emit('notice', text=str(exc))
            return None
        peers = [p for p in self.peers.values() if p.sid not in self.blocked and (target == 'general' or p.sid == target)]
        if target != 'general' and not peers:
            self.emit('notice', text='Cet utilisateur est hors ligne.')
            return None
        if kind == 'break_invite' and self.outgoing_break_limit.remaining(peers[0].contact_id or target):
            self.emit('notice', text='Pauses bloquées vers cette personne pendant 5 minutes. Les messages restent disponibles.')
            return None
        if not self.message_bucket.allow():
            self.emit('notice', text='Un peu trop rapide… Patientez un instant.')
            return None
        if kind == 'wizz':
            if self.wizz_cooldown.remaining():
                self.emit('notice', text='Un seul Wizz toutes les 42 minutes.')
                return None
            stamp = time.time()
            try:
                if self.persist_wizz:
                    self.persist_wizz(stamp)  # Reserve durably before the network action.
            except OSError:
                self.emit('notice', text='Wizz non envoyé : impossible de conserver le délai anti-spam.')
                return None
            self.wizz_cooldown.take()
            self.emit('wizz_used', timestamp=stamp)
        if kind == 'break_invite':
            if not self.outgoing_break_limit.allow(peers[0].contact_id or target):
                self.emit('notice', text='Trois demandes successives maximum : pauses bloquées vers cette personne pendant 5 minutes.')
                return None
            self.outgoing_invites.add(frame['id'], target)
        if kind == 'break_reply' and not self.incoming_invites.consume(payload.get('request'), target):
            self.emit('notice', text='Cette invitation a expiré.')
            return None
        self.seen.add(frame['id'])
        self.emit('sent', frame=frame)
        self._queued += 1
        try:
            routes = []
            # One relay fanout + direct peers absent from the relay, deduplicated by frame ID.
            if self.relay_writer and (target == 'general' or any(p.relay for p in peers)):
                routes.append(self._send_relay(frame))
            for peer in peers:
                if not (self.relay_writer and peer.relay) and peer.direct:
                    routes.append(self._send_direct(peer, frame))
            results = await asyncio.gather(*routes, return_exceptions=True) if routes else []
            success = bool(results) and all(r is True for r in results)
            self.emit('delivery', id=frame['id'], target=target, ok=success,
                      local_only=not results and target == 'general')
            return frame['id']
        finally:
            self._queued -= 1

    async def _send_social(self, kind, target, **payload):
        if self._queued >= 16:
            return None
        self.social_sequence += 1
        frame = event(kind, self.sid, target, **{**payload, 'seq': self.social_sequence})
        try:
            encode(frame)
        except ProtocolError as exc:
            self.emit('notice', text=str(exc))
            return None
        peers = [peer for peer in self.peers.values()
                 if peer.sid not in self.blocked and 'social' in peer.caps
                 and (target == 'general' or peer.sid == target)]
        if target != 'general' and not peers:
            if kind == 'reaction':
                self.emit('notice', text='Les réactions nécessitent un contact en ligne avec FortyTchat 1.3.0.')
            return None
        bucket = self.typing_bucket if kind == 'typing' else self.social_bucket
        if not bucket.allow():
            if kind == 'reaction':
                self.emit('notice', text='Trop de réactions à la fois. Réessaie dans un instant.')
            return None
        self.seen.add(frame['id'])
        if kind == 'reaction':
            self.emit('sent', frame=frame)
        self._queued += 1
        try:
            routes = []
            relayed = [peer for peer in peers if peer.relay and self.relay_writer and self.relay_social]
            if relayed:
                routes.append(self._send_relay(frame))
            for peer in peers:
                if peer not in relayed and peer.direct:
                    routes.append(self._send_direct(peer, frame))
            results = await asyncio.gather(*routes, return_exceptions=True) if routes else []
            if kind == 'reaction' and peers and (not results or any(result is not True for result in results)):
                self.emit('notice', text='Réaction non confirmée : vérifie que les clients et le relais sont actualisés.')
            return frame['id']
        finally:
            self._queued -= 1

    def file_recipients(self, target):
        return [peer for peer in self.peers.values()
                if peer.sid not in self.blocked and 'files' in peer.caps
                and (target == 'general' or target == peer.sid)
                and (peer.direct or (peer.relay and self.relay_writer and self.relay_files))]

    async def deliver_file(self, frame, peers=None, report=False):
        if self._closing:
            return {} if report else False
        if 'v' not in frame:
            frame = event(frame['type'], self.sid, frame['to'], request=frame['request'])
        peers = self.file_recipients(frame['to']) if peers is None else peers
        routes, groups = [], []
        relayed = [peer for peer in peers if peer.relay and self.relay_writer and self.relay_files]
        if relayed:
            routes.append(self._send_relay(frame))
            groups.append([peer.sid for peer in relayed])
        for peer in peers:
            if peer.direct and peer not in relayed:
                routes.append(self._send_direct(peer, frame))
                groups.append([peer.sid])
        results = await asyncio.gather(*routes, return_exceptions=True) if routes else []
        if report:
            return {sid: result is True for group, result in zip(groups, results) for sid in group}
        return bool(results) and all(result is True for result in results)

    async def _file_operation(self, operation):
        task = asyncio.current_task()
        self.operations.add(task)
        try:
            if not self._closing:
                return await operation
            operation.close()
        finally:
            self.operations.discard(task)

    async def offer_file(self, target, path):
        return await self._file_operation(self.files.offer(target, path))

    async def accept_file(self, request, sender, path):
        return await self._file_operation(self.files.accept(request, sender, path))

    async def decline_file(self, request, sender):
        return await self._file_operation(self.files.decline(request, sender))

    async def cancel_file(self, request, peer='general'):
        return await self._file_operation(self.files.cancel(request, peer))

    async def stream_file(self, offer, sid):
        writer, source = None, None
        try:
            peer = self.peers.get(sid)
            if not peer or sid in self.blocked:
                raise ConnectionError()
            relay = peer.relay and self.relay_writer and self.relay_files
            if not relay:
                if not peer.direct:
                    raise ConnectionError()
                reader, writer = await asyncio.wait_for(asyncio.open_connection(peer.host, peer.port, limit=MAX_FRAME), 3)
                self.connections.add(writer)
                writer.write(encode(self.hello()))
                await asyncio.wait_for(writer.drain(), 3)
            source = await disk(open_source, offer['path'])
            if fingerprint(os.fstat(source.fileno())) != offer['fingerprint']:
                raise ValueError('Le fichier source a été modifié. Propose-le de nouveau.')

            async def transmit(frame):
                if relay:
                    return await self._send_relay(frame)
                writer.write(encode(frame))
                await asyncio.wait_for(writer.drain(), 5)
                reply = await read_frame(reader, 12)
                return reply['type'] == 'ack' and reply['id'] == frame['id'] and reply['ok']

            offset = 0
            self.files.progress(offer, sid, True, amount=0)
            while chunk := await disk(source.read, CHUNK_SIZE):
                frame = event('file_chunk', self.sid, sid, request=offer['id'], offset=offset,
                              data=base64.b64encode(chunk).decode('ascii'))
                if not await transmit(frame):
                    raise ConnectionError()
                offset += len(chunk)
                if relay:
                    await asyncio.sleep(.001)
                self.files.progress(offer, sid, True, amount=offset)
            if relay:
                self.files.receipts[(offer['id'], sid)] = asyncio.get_running_loop().create_future()
            if offset != offer['size'] or not await transmit(event('file_end', self.sid, sid, request=offer['id'])):
                raise ConnectionError()
            if relay and not await asyncio.wait_for(self.files.receipts[(offer['id'], sid)], 20):
                raise ConnectionError()
            self.files.finish(offer, sid)
            self.files.emit('file_complete', offer, sid, True, path='')
        except asyncio.CancelledError:
            raise
        except (OSError, ConnectionError, ValueError, ProtocolError, asyncio.TimeoutError) as exc:
            self.files.emit('file_failed', offer, sid, True,
                            reason=str(exc) if isinstance(exc, ValueError) else 'Transfert interrompu ou fichier refusé.')
            await self.deliver_file(self.files.control('file_cancel', sid, offer['id']))
            self.files.finish(offer, sid)
        finally:
            self.files.receipts.pop((offer['id'], sid), None)
            if source:
                source.close()
            if writer:
                writer.close()
                with contextlib.suppress(OSError):
                    await writer.wait_closed()
                self.connections.discard(writer)
            if offer['active'].get(sid) is asyncio.current_task():
                offer['active'].pop(sid, None)

    async def _relay_loop(self):
        delay = 2
        while not self._closing:
            writer = None
            try:
                context = (ssl.create_default_context() if self.tls is True else self.tls) or None
                reader, writer = await asyncio.wait_for(asyncio.open_connection(
                    self.relay_host, self.relay_port, ssl=context, limit=MAX_FRAME), 4)
                writer.write(encode(self.hello()))
                await writer.drain()
                # The first frame is the server's authoritative hello acknowledgment.
                welcome = await read_frame(reader, 5)
                if welcome['type'] != 'pong':
                    raise ProtocolError('Relais incompatible')
                self.relay_writer = writer
                self.relay_images = isinstance(welcome.get('caps'), list) and 'avatar_image' in welcome['caps']
                self.relay_files = isinstance(welcome.get('caps'), list) and 'files' in welcome['caps']
                self.relay_social = isinstance(welcome.get('caps'), list) and 'social' in welcome['caps']
                self._publish_avatar()
                self.emit('network', **self.network_state())
                delay = 2
                while not self._closing:
                    frame = await read_frame(reader, max(65, self.heartbeat * 3))
                    kind = frame['type']
                    if kind in ('presence', 'bye'):
                        self.observe(frame, '', relay=True)
                    elif kind == 'avatar_image':
                        self._receive_avatar(frame)
                    elif kind == 'ack':
                        future = self.acks.get(frame['id'])
                        if future and not future.done():
                            future.set_result(frame['ok'])
                    elif kind in FILE_EVENTS:
                        ok, _ = await self.files.receive(frame)
                        if not ok and kind in ('file_chunk', 'file_end'):
                            self.task(self.deliver_file(self.files.control('file_cancel', frame['from'], frame['request'])))
                    elif kind in EVENTS:
                        self._receive(frame)
            except (OSError, ConnectionError, ProtocolError, asyncio.TimeoutError):
                if not self._closing:
                    self.network_error('Relais configuré inaccessible ; nouvelle tentative progressive (2 à 60 s).')
            finally:
                self.relay_writer = None
                self.relay_images = False
                self.relay_files = False
                self.relay_social = False
                if writer:
                    writer.close()
                    with contextlib.suppress(OSError):
                        await writer.wait_closed()
                for future in self.acks.values():
                    if not future.done():
                        future.set_result(False)
                for sid, peer in list(self.peers.items()):
                    peer.relay = False
                    if not peer.direct:
                        self._remove_peer(sid)
                self.emit('network', **self.network_state())
            if not self._closing:
                await asyncio.sleep(delay)
                delay = min(60, delay * 2)

    async def diagnose(self):
        rows = []
        choices = await asyncio.to_thread(interfaces)
        rows.append('Interface : ' + (f'{choices[0].name} · {choices[0].address} · {choices[0].subnet}' if choices else 'aucune IPv4 active exploitable'))
        rows.append(f'TCP local : {"écoute active" if self.server else "indisponible"} sur {self.port}')
        rows.append(f'UDP {self.discovery_port} : {"écoute et annonces actives" if self.discovery else "indisponible ou mode relais seul"}')
        self._announce()
        await asyncio.sleep(2)
        rows.append(f'{len(self.peers)} autre(s) utilisateur(s) détecté(s).')
        if self.relay_writer:
            rows.append('Relais : connexion établie, protocole négocié ; chiffrement TLS selon le déploiement.')
        elif self.relay_host:
            rows.append('Relais configuré mais inaccessible actuellement.')
        else:
            rows.append('Aucun relais configuré : une machine autorisée et un nom DNS seraient nécessaires.')
        peer = next((p for p in self.peers.values() if p.direct and p.sid not in self.blocked and 'probe' in p.caps), None)
        if peer:
            writer = None
            try:
                reader, writer = await asyncio.wait_for(asyncio.open_connection(peer.host, peer.port, limit=MAX_FRAME), 3)
                writer.write(encode({'v': 1, 'type': 'ping'}))
                await writer.drain()
                pong = await read_frame(reader, 4)
                rows.append('Connexion TCP et réponse du pair découvert : ' + ('réussies.' if pong['type'] == 'pong' else 'échec.'))
            except (OSError, ConnectionError, ProtocolError, asyncio.TimeoutError):
                rows.append('Le pair a été découvert par UDP, mais son service TCP ne répond pas.')
            finally:
                if writer:
                    writer.close()
                    with contextlib.suppress(OSError):
                        await writer.wait_closed()
        else:
            rows.append('Aucun pair disponible pour un test TCP ; l’accessibilité distante reste non vérifiée.')
        rows.append('Ouvrir un port localement ne prouve pas son accessibilité depuis un autre PC. Aucun scan effectué.')
        self.emit('diagnostic', rows=rows, **self.network_state())
        return rows

    async def stop(self):
        self._closing = True
        if self.discovery:
            self.discovery.announce(encode({'v': 1, 'type': 'bye', 'sid': self.sid}), self.discovery_port)
            self.discovery.close()
        if self.server:
            self.server.close()
            await self.server.wait_closed()
        for writer in list(self.connections):
            writer.close()
        pending = list(self.tasks | self.operations | self.handlers)
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        if self.relay_writer:
            self.relay_writer.close()
        self.files.stop()
        self.peers.clear()
        self.avatar_fetches.clear()
        self.avatar_attempts.clear()
        self.incoming_invites.pending.clear()
        self.outgoing_invites.pending.clear()
