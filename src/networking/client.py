"""Async network core, independent of Qt. All mutable network state lives on one loop."""
import asyncio
import base64
import os
import contextlib
import ssl
import random
import time
import io

from ..features.file_transfer import FileTransfers, FILE_EVENTS, CHUNK_SIZE, disk, fingerprint, open_source
from ..features.break_request import Invitations, BreakRateLimit
from ..features.wizz import Cooldown, SeenMessages, TokenBucket, WIZZ_INTERVAL
from ..features.social import SOCIAL_EVENTS, SocialOrder
from ..features.cards import CARD_EVENTS, allowed_reply
from ..features.receipts import ReceiptLedger, READ_EVENTS, READ_BATCH
from ..features.versions import modern_caps, room_capable
from .. import __version__
from ..features.collaboration import CAPABILITY, COLLAB_EVENTS, GROUP_EVENTS, group_id, chat_target
from .collaboration import CollaborationNetwork
from ..identity import session_id, unique_nickname, valid_nickname, contact_capability, contact_from_caps
from ..avatar_data import avatar_hash, avatar_frame
from ..security import CryptoIdentity, SecureSession, SecurityError, fingerprint as key_fingerprint
from .discovery import Discovery, PORT
from .interfaces import interfaces
from .diagnostics import socket_error
from .presence import Peer, HEARTBEAT, PEER_TIMEOUT
from .protocol import MAX_FRAME, MAX_PEERS, EVENTS, ProtocolError, ack, encode, event, read_frame
from ..features.screen_share import SCREEN_EVENTS, FEATURE as SCREEN_FEATURE
from ..features.screen_annotations import FEATURE as ANNOTATION_FEATURE
from .screen_share import ScreenSharing


class ChatClient(CollaborationNetwork):
    def __init__(self, nickname, callback=None, *, discovery=True, discovery_port=PORT,
                 relay_host='', relay_port=39743, tls=True, tcp_port=39742,
                 heartbeat=25, peer_timeout=90, multicast_group='239.42.42.42',
                 last_wizz_at=0, persist_wizz=None, avatar=-1, avatar_image='', contact_id=None,
                 security_root=None, crypto_identity=None, blocked_contacts=()):
        self.sid, self.nickname = session_id(), nickname
        self.status = 'online'
        self.manual_status,self.screen_locked='online',False
        self.contact_id = contact_id or session_id()
        self.identity = crypto_identity or CryptoIdentity(self.contact_id, security_root)
        if self.identity.contact_id != self.contact_id:
            raise SecurityError('Identité incompatible avec ce profil')
        self.crypto = SecureSession(self.identity, self.sid)
        self.security_notices = set()
        self.relay_e2ee = False
        self.avatar = avatar
        self.avatar_image = avatar_image
        self.avatar_hash = avatar_hash(avatar_image)
        self.avatar_fetches, self.avatar_attempts = {}, {}
        self.avatar_slots = asyncio.Semaphore(4)
        self.avatar_reads = TokenBucket(32, 8)
        self.relay_images = False
        self.relay_files = False
        self.relay_social = False
        self.relay_cards = False
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
        self.info_attempts={}
        self.info_tasks={}
        self.blocked_contacts = set(blocked_contacts)
        self.init_collaboration()
        self.seen, self.outgoing_invites, self.incoming_invites = SeenMessages(), Invitations(), Invitations()
        self.card_policies = {'incoming': {}, 'outgoing': {}}
        self.message_bucket = TokenBucket()
        self.social_bucket, self.typing_bucket = TokenBucket(12, 3), TokenBucket(4, 1)
        self.receipts, self.receipt_bucket = ReceiptLedger(), TokenBucket(16, 4)
        self.read_receipts = True
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
        self.ack_peers = {}
        self.operations, self.handlers = set(), set()
        self._closing, self._incoming = False, 0
        self._outgoing = asyncio.Semaphore(24)
        self._queued = 0
        self.port = 0
        self.files = FileTransfers(self)
        self.screens = ScreenSharing(self)

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
        return self.crypto.sign_presence({'v': 1, 'type': kind, 'sid': self.sid, 'nick': self.nickname,
                'status': self.status, 'port': self.port, 'contact_id':self.contact_id,
                'caps': ['files', 'choices', 'cards', CAPABILITY, 'social', 'avatar_image', 'receipts', 'rooms2'],
                'avatar': self.avatar, 'avatar_hash': self.avatar_hash})

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
        self.publish_groups()
        if self.group_store.error: self.emit('notice', text=self.group_store.error)
        self.task(self._heartbeat())
        self.task(self.screens.watchdog())
        if self.relay_host:
            self.task(self._relay_loop())

    def observe(self, data, host, relay=False):
        sid = data['sid']
        if sid == self.sid:
            return
        peer = self.peers.get(sid)
        if peer is None and len(self.peers) >= MAX_PEERS:
            return
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
        try:
            cid, identity_key, session_key = self.crypto.check_presence(data)
            if peer and (peer.contact_id != cid or peer.session_key != session_key or peer.identity_key != identity_key):
                return  # A running SID cannot change either key or its contact UUID.
            if cid == self.contact_id and identity_key != self.identity.public:
                raise SecurityError('Une autre clé revendique mon identifiant de profil')
            security = self.identity.remember(cid, identity_key)
        except (SecurityError, OSError):
            if len(self.security_notices) < MAX_PEERS and sid not in self.security_notices:
                self.security_notices.add(sid)
                self.emit('notice', text='Contact refusé : signature absente ou invalide. Tous les clients doivent utiliser FortyTchat 1.5.0 ou plus récent.')
            return
        if peer is None:
            if len(self.peers) >= MAX_PEERS:
                return
            peer = Peer(sid, data['nick'], timeout=self.peer_timeout, bucket=TokenBucket(), wizz=Cooldown(WIZZ_INTERVAL),
                        social_bucket=TokenBucket(12, 3), typing_bucket=TokenBucket(4, 1), receipt_bucket=TokenBucket(16, 4), control_bucket=TokenBucket(96, 8))
            self.peers[sid] = peer
            new = True
        else:
            new = False
        previous = (peer.nick, peer.status, peer.relay, peer.avatar, peer.avatar_hash, peer.contact_id, peer.security)
        peer.identity_key, peer.session_key = identity_key, session_key
        peer.security, peer.secure = security, security != 'changed'
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
        if new or previous != (peer.nick, peer.status, peer.relay, peer.avatar, peer.avatar_hash, peer.contact_id, peer.security):
            if peer.contact_id in self.blocked_contacts:
                self.blocked.add(peer.sid)
                peer.avatar_image = ''
            self.emit_peers()
        if not peer.secure and sid not in self.security_notices and len(self.security_notices) < MAX_PEERS:
            self.security_notices.add(sid)
            self.emit('notice', text=f'La clé de {peer.nick} a changé : échanges bloqués. Compare son empreinte depuis le cadenas de la conversation.')
        self._queue_avatar(peer)
        if new:
            self.group_observe(peer)
            self.queue_client_info(peer)
        # Lexicographically smaller session keeps a contested nickname.
        if peer.nick.casefold() == self.nickname.casefold() and sid < self.sid:
            self.rename(unique_nickname(self.nickname, [p.nick for p in self.peers.values()]))

    def queue_client_info(self,peer,force=False):
        if self._closing or not modern_caps(peer.caps) or not peer.secure or peer.sid in self.blocked:return
        try:asyncio.get_running_loop()
        except RuntimeError:return
        if peer.sid in self.info_tasks:return
        now=time.monotonic()
        if not force and now-self.info_attempts.get(peer.sid,-100)<10:return
        self.info_attempts[peer.sid]=now
        locked=self.screen_locked
        async def publish():
            try:
                await self._send_to(peer,event('client_info',self.sid,peer.sid,version=__version__,features=['poll_final','equals_code','version_notice','screen_lock','close_label',SCREEN_FEATURE,ANNOTATION_FEATURE],locked=locked))
            finally:
                self.info_tasks.pop(peer.sid,None)
                if not self._closing and self.peers.get(peer.sid) is peer and self.screen_locked!=locked:
                    self.queue_client_info(peer,force=True)
        self.info_tasks[peer.sid]=self.task(publish())

    def emit_peers(self):
        self.emit('peers', peers=[p.public() for p in self.peers.values()])

    def verify_contact(self, sid, expected):
        peer = self.peers.get(sid)
        if not peer or not isinstance(expected, str):
            raise SecurityError('Contact hors ligne')
        normalized = ''.join(expected.split()).upper()
        if normalized != ''.join(key_fingerprint(peer.identity_key).split()):
            raise SecurityError('Les empreintes ne correspondent pas : contact non autorisé')
        self.identity.remember(peer.contact_id, peer.identity_key, verified=True, replace=True)
        # Apply the pin to all concurrent sessions of this contact.
        for other in self.peers.values():
            if other.contact_id == peer.contact_id:
                other.secure = other.identity_key == peer.identity_key
                other.security = 'verified' if other.secure else 'changed'
        self.emit_peers()
        self.emit('notice', text='Empreinte vérifiée : identité du contact confirmée.')
        self.emit('security_verified', sid=sid)

    def _remove_peer(self, sid):
        if self.screens.state and self.screens.state.peer==sid:self.screens.close('Contact déconnecté.')
        peer = self.peers.pop(sid, None)
        self.group_syncing.discard(sid)
        self.info_attempts.pop(sid,None)
        info=self.info_tasks.pop(sid,None)
        if info:info.cancel()
        self.blocked.discard(sid)
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
            self.prune_card_policies()
            now = time.monotonic()
            for sid, peer in list(self.peers.items()):
                if not peer.relay and now - peer.direct_seen > self.peer_timeout:
                    self._remove_peer(sid)
                else:
                    self._queue_avatar(peer)
                    self.queue_client_info(peer)
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

    def preferences(self, *, wizz_enabled, dnd, status='online', avatar=None, avatar_image=None, read_receipts=None):
        if read_receipts is not None:
            if type(read_receipts) is not bool:
                raise ValueError('Préférence de lecture invalide')
            self.read_receipts = read_receipts
        if avatar is not None:
            if type(avatar) is not int or not -1 <= avatar < 24:
                raise ValueError('Avatar invalide')
            self.avatar = avatar
        if avatar_image is not None:
            self.avatar_hash = avatar_hash(avatar_image)
            self.avatar_image = avatar_image
        self.wizz_enabled, self.dnd = wizz_enabled, dnd
        self.manual_status=status
        self.status = 'dnd' if dnd else 'away' if self.screen_locked else status
        self._announce()
        if self.relay_writer:
            self.relay_writer.write(encode(self.hello()))
            self._publish_avatar()

    def set_screen_locked(self,locked):
        if type(locked) is not bool:raise ValueError('État de session invalide')
        if self.screen_locked==locked:return
        self.screen_locked=locked
        if locked:self.screens.close('Session verrouillée.',notify=True)
        self.status='dnd' if self.dnd else 'away' if locked else self.manual_status
        self._announce()
        if self.relay_writer:self.relay_writer.write(encode(self.hello()))
        for peer in self.peers.values():self.queue_client_info(peer,force=True)

    def block(self, sid, blocked=True):
        if blocked:
            self.blocked.add(sid)
            if self.screens.state and self.screens.state.peer==sid:self.screens.close('Contact bloqué.')
            for request, offer in list(self.files.outgoing.items()):
                if sid in offer['recipients']:
                    self.task(self.files.cancel(request, sid))
            for key in list(self.files.incoming):
                if key[1] == sid:
                    self.files.fail_incoming(key, 'Transfert arrêté : contact bloqué.')
            peer = self.peers.get(sid)
            if peer:
                peer.avatar_image = ''
                self.emit_peers()
        else:
            self.blocked.remove(sid) if sid in self.blocked else None
            self.avatar_attempts.pop(sid, None)
            if sid in self.peers:
                self._queue_avatar(self.peers[sid])

    def block_contact(self, contact_id, blocked=True):
        from ..identity import valid_contact_id
        if not valid_contact_id(contact_id) or contact_id == self.contact_id:
            raise ValueError('Contact invalide')
        if blocked:
            self.blocked_contacts.add(contact_id)
        else:
            self.blocked_contacts.discard(contact_id)
        for peer in list(self.peers.values()):
            if peer.contact_id == contact_id:
                self.block(peer.sid, blocked)

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
        self.prune_card_policies()
        sender = frame['from']
        peer = self.peers.get(sender)
        if not peer or not peer.secure or sender in self.blocked:
            return False, 'Utilisateur indisponible'
        if frame['to'] not in (self.sid, 'general'):
            return False, 'Destinataire incorrect'
        if frame['id'] in self.seen.ids:
            return True, ''
        kind = frame['type']
        if kind in SCREEN_EVENTS:return self.screens.receive(peer,frame)
        if kind=='client_info':
            if not modern_caps(peer.caps) or not peer.control_bucket.allow():return False,'Information de version indisponible'
            first=not peer.version
            peer.version,peer.features=frame['version'],tuple(frame['features'])
            peer.locked=frame.get('locked',False)
            if first:self.queue_client_info(peer,force=True)
            self.seen.add(frame['id']);self.emit('peer_version',version=peer.version,peer=peer.sid);self.emit_peers()
            return True,''
        if frame.get('group') and not self.group_access(peer, frame['group']):
            return False, 'Membre de groupe non autorisé'
        if kind in COLLAB_EVENTS:
            if not room_capable(peer.caps) or not (peer.control_bucket if kind in GROUP_EVENTS else peer.social_bucket).allow():
                return False, 'Collaboration indisponible ou trop rapide'
            ok, reason = self.receive_collaboration(frame, peer)
            if ok:
                self.seen.add(frame['id'])
            return ok, reason
        if kind in READ_EVENTS:
            if 'receipts' not in peer.caps or not peer.receipt_bucket.allow():
                return False, 'Confirmation de lecture indisponible'
            self.seen.add(frame['id'])
            for reference, target in self.receipts.read(sender, frame['messages']):
                self.emit('message_read', id=reference, target=target, peer=sender)
            return True, ''
        if kind in ('break_invite', 'card_invite') and not frame.get('group') and self.incoming_break_limit.remaining(peer.contact_id or sender):
            return False, self.incoming_break_limit.notice(peer.contact_id or sender)
        if kind in CARD_EVENTS and 'cards' not in peer.caps:
            return False, 'Notifications personnalisées incompatibles'
        if ('responses' in frame or 'choice' in frame) and 'choices' not in peer.caps:
            return False, 'Choix de réponse incompatibles'
        if kind == 'card_invite' and self.dnd:
            return False, 'Ne pas déranger'
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
        if kind in ('break_invite', 'card_invite'):
            if frame.get('group'):
                from ..features.group_profile import validate_permit
                try:
                    validate_permit(frame['permit'], self.rooms[frame['group']].profile, peer.contact_id)
                    self.group_store.receive_popup(frame['group'], frame['permit'])
                except (OSError, ValueError, KeyError) as exc: return False, str(exc)[:160]
            elif not self.incoming_break_limit.allow(peer.contact_id or sender):
                return False, self.incoming_break_limit.notice(peer.contact_id or sender)
            if not self.incoming_invites.add(frame['id'], sender):
                return False, 'Invitation indisponible'
            if 'responses' in frame:
                self.card_policies['incoming'][frame['id']] = frame['responses']
        if kind in ('break_reply', 'card_reply'):
            policy = self.card_policies['outgoing'].get(frame['request'])
            if (policy is not None and (kind != 'card_reply' or not allowed_reply(policy, frame))
                    or policy is None and 'choice' in frame):
                return False, 'Cette réponse ne fait pas partie des choix proposés'
        if kind in ('break_reply', 'card_reply') and not self.outgoing_invites.consume(frame['request'], sender):
            return False, 'Invitation expirée'
        if kind in ('break_reply', 'card_reply'):
            self.card_policies['outgoing'].pop(frame['request'], None)
        self.seen.add(frame['id'])
        if kind in SOCIAL_EVENTS:
            self.social_order.remember(frame)
        if kind == 'message':
            self.receipts.received(frame)
        # Keep only validated fields; never trust a network-provided display name.
        payload = {k: frame[k] for k in ('type', 'id', 'from', 'to', 'group', 'text', 'request', 'answer', 'reply_to', 'message', 'emoji', 'active', 'title', 'responses', 'choice', 'mentions', 'close_label') if k in frame}
        self.emit('event', frame=payload, nickname=peer.nick, avatar=peer.avatar, avatar_image=peer.avatar_image,
                  contact_id=peer.contact_id)
        if kind == 'message':
            self.remember_message(frame, chat_target(frame, self.sid))
        return True, ''

    async def _receive_secure(self, outer):
        peer = self.peers.get(outer.get('from'))
        if not peer or peer.sid in self.blocked:
            raise SecurityError('Contact indisponible')
        frame = self.crypto.open(peer, outer)
        kind = frame['type']
        if kind == 'ack':
            future = self.acks.get(frame['id'])
            if self.ack_peers.get(frame['id']) == peer.sid and future and not future.done():
                future.set_result(frame['ok'])
            return frame, None
        if kind in SCREEN_EVENTS:
            ok,reason=self.screens.receive(peer,frame)
        elif kind in FILE_EVENTS:
            ok, reason = await self.files.receive(frame)
            if not ok and kind in ('file_chunk', 'file_end'):
                self.task(self.deliver_file(self.files.control('file_cancel', frame['from'], frame['request'])))
        elif kind in EVENTS:
            ok, reason = self._receive(frame)
        else:
            raise SecurityError('Contenu chiffré inattendu')
        receipt = self.crypto.seal(peer, ack(outer['id'], ok, reason), 'receipt')
        return frame, receipt

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
            hello_sid = None
            if frame['type'] == 'hello':
                hello_sid = frame['sid']
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
                if (not peer or peer.host != host or not peer.direct or frame['type'] != 'sealed'
                        or frame.get('from') != peer.sid or hello_sid != peer.sid):
                    return  # Plaintext application events are never accepted on a socket.
                inner, receipt = await self._receive_secure(frame)
                if receipt is None:
                    return
                writer.write(encode(receipt))
                await asyncio.wait_for(writer.drain(), 3)
                if inner['type']=='screen_chunk':
                    if not self.screens.accepts(peer.sid,inner['share']):return
                elif inner['type'] not in FILE_EVENTS or inner['type'] == 'file_end':
                    return
                frame = await read_frame(reader, 15)
        except (OSError, ConnectionError, ProtocolError, SecurityError, asyncio.TimeoutError):
            pass
        finally:
            writer.close()
            with contextlib.suppress(OSError):
                await writer.wait_closed()
            self.connections.discard(writer)
            self._incoming -= 1
            self.handlers.discard(handler)

    async def _send_direct(self, peer, frame):
        original = frame
        async with self._outgoing:
            for attempt in range(2):
                if self._closing or peer.sid in self.blocked or (original.get('group') and not self.group_access(peer, original['group'])):
                    return False
                writer = None
                try:
                    frame = self.crypto.seal(peer, original)
                    reader, writer = await asyncio.wait_for(asyncio.open_connection(peer.host, peer.port, limit=MAX_FRAME), 3)
                    self.connections.add(writer)
                    if peer.sid in self.blocked or (original.get('group') and not self.group_access(peer, original['group'])):
                        return False
                    writer.write(encode(self.hello()) + encode(frame))
                    await asyncio.wait_for(writer.drain(), 3)
                    reply = await read_frame(reader, 4)
                    reply = self.crypto.open(peer, reply)
                    return reply['type'] == 'ack' and reply['id'] == frame['id'] and reply['ok']
                except (OSError, ConnectionError, ProtocolError, SecurityError, asyncio.TimeoutError):
                    if not attempt:
                        await asyncio.sleep(0.3)
                finally:
                    if writer:
                        writer.close()
                        with contextlib.suppress(OSError):
                            await writer.wait_closed()
                        self.connections.discard(writer)
            return False

    async def _send_relay(self, frame, peer=None):
        peer = peer or self.peers.get(frame.get('to'))
        if (not self.relay_writer or not self.relay_e2ee or not peer or not peer.secure or peer.sid in self.blocked
                or frame.get('from') != self.sid):
            return False
        frame = self.crypto.seal(peer, frame, 'stream' if frame['type'] in ('file_chunk','screen_chunk') else 'event')
        future = asyncio.get_running_loop().create_future()
        self.acks[frame['id']] = future
        self.ack_peers[frame['id']] = peer.sid
        try:
            self.relay_writer.write(encode(frame))
            await asyncio.wait_for(self.relay_writer.drain(), 3)
            return await asyncio.wait_for(future, 6)
        except (OSError, asyncio.TimeoutError):
            return False
        finally:
            self.acks.pop(frame['id'], None)
            self.ack_peers.pop(frame['id'], None)

    async def send(self, kind, target, **payload):
        operation = asyncio.current_task()
        self.operations.add(operation)
        try:
            return await self._send(kind, target, **payload)
        finally:
            self.operations.discard(operation)

    async def _send(self, kind, target, **payload):
        self.prune_card_policies()
        if self._closing:
            return None
        if kind in SCREEN_EVENTS:return None  # ScreenSharing enforces consent and capability negotiation.
        if kind in READ_EVENTS:
            return None  # Only mark_read() may confirm messages actually received.
        if kind in COLLAB_EVENTS:
            return await self.send_collaboration(kind, target, **payload)
        if kind == 'card_invite' and group_id(target):
            return await self.send_group_popup(target, **payload)
        if kind in SOCIAL_EVENTS:
            return await self._send_social(kind, target, **payload)
        if group_id(target) and (not self.rooms.get(group_id(target)) or self.rooms[group_id(target)].state != 'joined'):
            self.emit('notice', text='Rejoins le groupe avant d’envoyer un message.')
            return None
        if self._queued >= 16:
            self.emit('notice', text='Trop de messages en attente. Réessayez dans un instant.')
            return None
        if kind == 'message':
            from ..features.text import mention_ids
            payload['mentions'] = mention_ids(payload.get('text', ''), [p.public() for p in self.conversation_peers(target)])
        frame = event(kind, self.sid, target, **payload)
        try:
            encode({**frame, 'to': self.sid, 'group': group_id(target)} if group_id(target) else frame)
        except ProtocolError as exc:
            self.emit('notice', text=str(exc))
            return None
        peers = self.conversation_peers(target)
        if target == 'general' and any(not p.secure and p.sid not in self.blocked for p in self.peers.values()):
            self.emit('notice', text='Salon général : les contacts dont la clé a changé sont exclus de cet envoi. Vérifie leurs empreintes pour les autoriser.')
        if kind in CARD_EVENTS:
            peers = [p for p in peers if 'cards' in p.caps and (p.direct or (p.relay and self.relay_writer and self.relay_cards))]
            if 'responses' in frame or 'choice' in frame:
                peers = [p for p in peers if 'choices' in p.caps]
        if target != 'general' and not group_id(target) and not peers:
            self.emit('notice', text='Choix de réponse indisponibles : actualise les deux clients en 1.7.0.' if 'responses' in frame or 'choice' in frame else 'Notifications animées indisponibles : actualise les clients et le relais en 1.5.0.'
                      if kind in CARD_EVENTS else 'Contact indisponible ou clé modifiée : vérifie le cadenas de la conversation.')
            return None
        if kind in ('break_invite', 'card_invite') and self.outgoing_break_limit.remaining(peers[0].contact_id or target):
            self.emit('notice', text=self.outgoing_break_limit.notice(peers[0].contact_id or target))
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
        if kind in ('break_invite', 'card_invite'):
            if not self.outgoing_break_limit.allow(peers[0].contact_id or target):
                self.emit('notice', text=self.outgoing_break_limit.notice(peers[0].contact_id or target))
                return None
            if not self.outgoing_invites.add(frame['id'], target):
                self.emit('notice', text='Trop de demandes sans réponse. Patiente un instant.')
                return None
            if 'responses' in frame:
                self.card_policies['outgoing'][frame['id']] = frame['responses']
        if kind in ('break_reply', 'card_reply'):
            policy = self.card_policies['incoming'].get(payload.get('request'))
            if (policy is not None and (kind != 'card_reply' or not allowed_reply(policy, frame))
                    or policy is None and 'choice' in frame):
                self.emit('notice', text='Cette réponse ne fait pas partie des choix proposés.')
                return None
        if kind in ('break_reply', 'card_reply') and not self.incoming_invites.consume(payload.get('request'), target):
            self.emit('notice', text='Cette invitation a expiré.')
            return None
        if kind in ('break_reply', 'card_reply'):
            self.card_policies['incoming'].pop(payload.get('request'), None)
        self.seen.add(frame['id'])
        if kind == 'message':
            self.receipts.sent(frame, peers)
        self.emit('sent', frame=frame, recipients=[{'sid': p.sid, 'nick': p.nick, 'reads': 'receipts' in p.caps} for p in peers])
        if kind == 'message':
            self.remember_message(frame, target, peers)
        self._queued += 1
        try:
            routes = [self._deliver_message(peer, frame) if kind == 'message' else self._send_to(peer, frame) for peer in peers]
            results = await asyncio.gather(*routes, return_exceptions=True) if routes else []
            success = bool(results) and all(r is True for r in results)
            self.emit('delivery', id=frame['id'], target=target, ok=success,
                      local_only=not results and (target == 'general' or bool(group_id(target))))
            return frame['id']
        finally:
            self._queued -= 1

    async def _deliver_message(self, peer, frame):
        try:
            ok = await self._send_to(peer, frame)
        except Exception:
            ok = False
        self.emit('message_delivery', id=frame['id'], target=frame['to'], peer=peer.sid, ok=ok)
        return ok

    def prune_card_policies(self):
        for name, invitations in (('incoming', self.incoming_invites), ('outgoing', self.outgoing_invites)):
            invitations.prune()
            self.card_policies[name] = {key: value for key, value in self.card_policies[name].items()
                                        if key in invitations.pending}

    async def mark_read(self, references):
        operation = asyncio.current_task()
        self.operations.add(operation)
        try:
            if self._closing or not self.read_receipts:
                return
            groups = {}
            for mid, item in self.receipts.eligible(references):
                peer = self.peers.get(item['sender'])
                if (peer and peer.secure and peer.sid not in self.blocked and 'receipts' in peer.caps
                        and (peer.direct or peer.relay and self.relay_writer and self.relay_e2ee)):
                    groups.setdefault(peer.sid, []).append(mid)
            plans = []
            for sid, ids in groups.items():
                for offset in range(0, len(ids), READ_BATCH):
                    if not self.receipt_bucket.allow():
                        break
                    batch = ids[offset:offset + READ_BATCH]
                    for mid in batch:
                        self.receipts.incoming[mid]['pending'] = True
                    plans.append((sid, batch))
            async def deliver(sid, batch):
                ok = False
                try:
                    frame = event('read_receipt', self.sid, sid, messages=batch)
                    ok = await self._send_to(self.peers[sid], frame)
                except (OSError, ValueError, KeyError, ConnectionError):
                    pass
                finally:
                    self.receipts.finish(batch, ok)
            if plans:
                await asyncio.gather(*(deliver(sid, batch) for sid, batch in plans))
        finally:
            self.operations.discard(operation)

    async def _send_social(self, kind, target, **payload):
        if self._queued >= 16:
            return None
        if group_id(target) and (not self.rooms.get(group_id(target)) or self.rooms[group_id(target)].state != 'joined'):
            return None
        self.social_sequence += 1
        frame = event(kind, self.sid, target, **{**payload, 'seq': self.social_sequence})
        try:
            encode({**frame, 'to': self.sid, 'group': group_id(target)} if group_id(target) else frame)
        except ProtocolError as exc:
            self.emit('notice', text=str(exc))
            return None
        peers = self.conversation_peers(target, 'social')
        if target != 'general' and not group_id(target) and not peers:
            if kind == 'reaction':
                self.emit('notice', text='Les réactions nécessitent un contact en ligne avec FortyTchat 1.5.0.')
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
            routes = [self._send_to(peer, frame) for peer in peers]
            results = await asyncio.gather(*routes, return_exceptions=True) if routes else []
            if kind == 'reaction' and peers and (not results or any(result is not True for result in results)):
                self.emit('notice', text='Réaction non confirmée : vérifie que les clients et le relais sont actualisés.')
            return frame['id']
        finally:
            self._queued -= 1

    def file_recipients(self, target):
        return [peer for peer in self.conversation_peers(target, 'files')
                if peer.direct or (peer.relay and self.relay_writer and self.relay_files)]

    async def _send_to(self, peer, frame):
        if not peer.secure or peer.sid in self.blocked:
            return False
        frame = self.wire_frame(frame, peer)
        if frame.get('group') and not self.group_access(peer, frame['group']):
            return False
        if peer.relay and self.relay_writer and self.relay_e2ee:
            return await self._send_relay(frame, peer)
        if peer.direct:
            return await self._send_direct(peer, frame)
        return False

    async def deliver_file(self, frame, peers=None, report=False):
        if self._closing:
            return {} if report else False
        if 'v' not in frame:
            frame = event(frame['type'], self.sid, frame['to'], request=frame['request'])
        peers = self.file_recipients(frame['to']) if peers is None else peers
        routes = [self._send_to(peer, frame) for peer in peers]
        groups = [[peer.sid] for peer in peers]
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

    async def offer_queued_file(self, target, path, **options):
        async def offer():
            from ..features.file_transfer import MAX_OUTGOING
            while not self._closing:
                self.files.prune()
                if len(self.files.outgoing) + self.files.reserved < MAX_OUTGOING:
                    return await self.files.offer(target, path, **options)
                await asyncio.sleep(.25)
        return await self._file_operation(offer())

    async def offer_file(self, target, path=None, **options):
        return await self._file_operation(self.files.offer(target, path, **options))

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
            if not peer or not peer.secure or sid in self.blocked:
                raise ConnectionError()
            relay = peer.relay and self.relay_writer and self.relay_files
            if not relay:
                if not peer.direct:
                    raise ConnectionError()
                reader, writer = await asyncio.wait_for(asyncio.open_connection(peer.host, peer.port, limit=MAX_FRAME), 3)
                self.connections.add(writer)
                writer.write(encode(self.hello()))
                await asyncio.wait_for(writer.drain(), 3)
            source = io.BytesIO(offer['memory']) if 'memory' in offer else await disk(open_source, offer['path'])
            if 'memory' not in offer and fingerprint(os.fstat(source.fileno())) != offer['fingerprint']:
                raise ValueError('Le fichier source a été modifié. Propose-le de nouveau.')

            async def transmit(frame):
                if sid in self.blocked or group_id(offer['to']) and not self.group_access(peer, group_id(offer['to'])):
                    return False
                if relay:
                    return await self._send_relay(frame, peer)
                frame = self.crypto.seal(peer, frame, 'stream' if frame['type'] == 'file_chunk' else 'event')
                writer.write(encode(frame))
                await asyncio.wait_for(writer.drain(), 5)
                reply = self.crypto.open(peer, await read_frame(reader, 12))
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
                if welcome['type'] != 'pong' or 'e2ee' not in welcome.get('caps', []):
                    raise ProtocolError('Relais incompatible')
                self.relay_e2ee = True
                self.relay_writer = writer
                self.relay_images = isinstance(welcome.get('caps'), list) and 'avatar_image' in welcome['caps']
                self.relay_files = isinstance(welcome.get('caps'), list) and 'files' in welcome['caps']
                self.relay_social = isinstance(welcome.get('caps'), list) and 'social' in welcome['caps']
                self.relay_cards = isinstance(welcome.get('caps'), list) and 'cards' in welcome['caps']
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
                        # The relay can reject a route but cannot confirm reception.
                        future = self.acks.get(frame['id'])
                        if not frame['ok'] and future and not future.done():
                            future.set_result(False)
                    elif kind == 'sealed':
                        try:
                            _, receipt = await self._receive_secure(frame)
                            if receipt:
                                writer.write(encode(receipt))
                                await asyncio.wait_for(writer.drain(), 3)
                        except SecurityError:
                            pass  # Altered/unauthenticated data never reaches the UI.
                    elif kind in EVENTS | FILE_EVENTS:
                        raise ProtocolError('Relais non chiffré refusé')
            except (OSError, ConnectionError, ProtocolError, asyncio.TimeoutError):
                if not self._closing:
                    self.network_error('Relais inaccessible ou incompatible (1.5.0 minimum) ; nouvelle tentative progressive (2 à 60 s).')
            finally:
                self.relay_e2ee = False
                self.relay_writer = None
                self.relay_images = False
                self.relay_files = False
                self.relay_social = False
                self.relay_cards = False
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
        peer = next((p for p in self.peers.values() if p.direct and p.sid not in self.blocked and ('probe' in p.caps or 'rooms' in p.caps or 'rooms2' in p.caps or 'rooms3' in p.caps or CAPABILITY in p.caps)), None)
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
        self.screens.close('Application fermée.')
        if self.discovery:
            self.discovery.announce(encode({'v': 1, 'type': 'bye', 'sid': self.sid}), self.discovery_port)
            self.discovery.close()
        if self.server:
            self.server.close()
        writers = set(self.connections)
        if self.relay_writer:
            writers.add(self.relay_writer)
        for writer in writers:
            writer.close()
        pending = list(self.tasks | self.operations | self.handlers)
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        await asyncio.gather(*(writer.wait_closed() for writer in writers), return_exceptions=True)
        if self.server:
            await self.server.wait_closed()
        self.files.stop()
        self.peers.clear()
        self.avatar_fetches.clear()
        self.avatar_attempts.clear()
        self.incoming_invites.pending.clear()
        self.outgoing_invites.pending.clear()
        self.card_policies['incoming'].clear()
        self.card_policies['outgoing'].clear()
        self.receipts.clear()
        self.rooms.clear()
        self.book.items.clear()
        self.book.pending.clear()
        self.polls.items.clear()
        self.crypto.private = None
