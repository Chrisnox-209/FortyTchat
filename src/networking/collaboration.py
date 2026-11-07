"""Room fan-out and authenticated message changes, inside the existing E2EE envelopes."""
import asyncio
from ..features.collaboration import (CAPABILITY, GROUP_EVENTS, CHANGE_EVENTS, POLL_EVENTS,
    MAX_GROUPS, MAX_MEMBERS, MAX_VOTERS, Room, MessageBook, PollBook, group_key, group_id, chat_target, plain)
from ..identity import session_id


class CollaborationNetwork:
    def init_collaboration(self):
        self.rooms, self.book, self.polls = {}, MessageBook(), PollBook()
        self.change_sequence = 0

    def publish_groups(self):
        self.emit('groups', groups=[room.public() for room in self.rooms.values()])

    def group_access(self, peer, reference):
        room = self.rooms.get(reference)
        return bool(room and room.state in ('joining', 'joined') and peer and CAPABILITY in peer.caps
                    and peer.contact_id in room.joined and peer.contact_id not in room.left
                    and peer.sid in room.sessions.get(peer.contact_id, ()))

    def conversation_peers(self, target, capability=None):
        reference = group_id(target)
        return [peer for peer in self.peers.values()
                if peer.secure and peer.sid not in self.blocked
                and (not capability or capability in peer.caps)
                and (self.group_access(peer, reference) if reference else target == 'general' or target == peer.sid)]

    def wire_frame(self, frame, peer):
        reference = group_id(frame['to'])
        if reference:
            frame = {**frame, 'to': peer.sid, 'group': reference}
        if 'preview' in frame and CAPABILITY not in peer.caps:
            frame = {key: value for key, value in frame.items() if key != 'preview'}
        return frame

    async def create_group(self, name, contacts):
        from .protocol import event, encode, ProtocolError
        if not isinstance(contacts, (list, tuple)) or any(not isinstance(cid, str) for cid in contacts):
            return None
        unique = list(dict.fromkeys(contacts))
        if (not plain(name, 64) or any(c in name for c in '\n\t') or not 1 <= len(unique) < MAX_MEMBERS or self.contact_id in unique
                or len(self.rooms) >= MAX_GROUPS):
            self.emit('notice', text='Choisis un nom et 1 à 31 personnes (32 groupes maximum).')
            return None
        peers = []
        for cid in unique:
            candidates = [p for p in self.conversation_peers('general', CAPABILITY) if p.contact_id == cid]
            if not candidates:
                self.emit('notice', text='Chaque membre doit être connecté avec FortyTchat 2.0.0 et autorisé.')
                return None
            peers.append(sorted(candidates, key=lambda p: p.sid)[0])
        if not self.message_bucket.allow():
            self.emit('notice', text='Patiente un instant avant de créer un groupe.')
            return None
        reference = session_id()
        room = Room(reference, name.strip(), self.contact_id, tuple([self.contact_id] + unique),
                    state='joined', joined={self.contact_id})
        room.sessions[self.contact_id] = {self.sid}
        self.rooms[reference] = room
        self.publish_groups()
        self.emit('group_created', group=room.public())
        frames = [event('group_invite', self.sid, peer.sid, room=reference, name=room.name, members=list(room.members)) for peer in peers]
        try:
            for frame in frames: encode(frame)
        except ProtocolError:
            self.rooms.pop(reference, None); self.publish_groups()
            return None
        results = await asyncio.gather(*(self._send_to(peer, frame) for peer, frame in zip(peers, frames)), return_exceptions=True)
        if any(ok is not True for ok in results):
            self.emit('notice', text='Certaines invitations de groupe n’ont pas été remises.')
        return reference

    async def answer_group(self, reference, accept):
        from .protocol import event
        room = self.rooms.get(reference)
        if not room or room.state not in ('pending', 'joined'):
            return False
        if accept and room.state != 'pending':
            return False
        room.state = 'joining' if accept else ('declined' if room.state == 'pending' else 'left')
        if accept:
            room.joined.add(self.contact_id)
            room.sessions[self.contact_id] = {self.sid}
        else:
            room.left.add(self.contact_id)
            room.joined.discard(self.contact_id)
        self.publish_groups()
        peers = [p for p in self.conversation_peers('general', CAPABILITY) if p.contact_id in room.members]
        kind = 'group_join' if accept else 'group_leave'
        results = await asyncio.gather(*(self._send_to(p, event(kind, self.sid, p.sid, room=reference)) for p in peers), return_exceptions=True)
        if accept:
            room.state = 'joined'
            self.publish_groups()
        if any(result is not True for result in results):
            self.emit('notice', text='Présence de groupe non confirmée chez certains membres.')
        return True

    def receive_group(self, frame, peer):
        reference = frame['room']
        room = self.rooms.get(reference)
        if frame['type'] == 'group_invite':
            members = tuple(frame['members'])
            if self.contact_id not in members or peer.contact_id not in members:
                return False, 'Invitation incohérente'
            if room:
                return (room.owner == peer.contact_id and room.members == members and room.name == frame['name'], 'Groupe déjà connu')
            if len(self.rooms) >= MAX_GROUPS:
                return False, 'Trop de groupes'
            room = Room(reference, frame['name'], peer.contact_id, members, joined={peer.contact_id})
            room.sessions[peer.contact_id] = {peer.sid}
            self.rooms[reference] = room
            self.publish_groups()
            self.emit('group_invitation', group=room.public(), nickname=peer.nick)
            return True, ''
        if not room or peer.contact_id not in room.members or room.state in ('declined', 'left'):
            return False, 'Groupe indisponible'
        if frame['type'] == 'group_join':
            if peer.contact_id in room.left:
                return False, 'Ce membre a quitté le groupe'
            room.joined.add(peer.contact_id)
            sessions = room.sessions.setdefault(peer.contact_id, set())
            if len(sessions) >= 4 and peer.sid not in sessions:
                return False, 'Trop de sessions pour ce membre'
            sessions.add(peer.sid)
        else:
            room.left.add(peer.contact_id)
            room.joined.discard(peer.contact_id)
        self.publish_groups()
        return True, ''

    def publish_change(self, frame, chat):
        if frame['type'] == 'message_delete':
            reference = frame['message']
            if reference in self.files.outgoing:
                self.task(self.files.cancel(reference, 'general'))
            incoming = (reference, frame['from'])
            if incoming in self.files.incoming:
                self.files.fail_incoming(incoming, 'Fichier retiré par son expéditeur.')
        if frame['type'] == 'message_delete' and frame['message'] in self.polls.items:
            self.polls.items[frame['message']]['deleted'] = True
        self.emit('message_changed', target=chat, frame=dict(frame))

    def remember_message(self, frame, target, peers=()):
        for change in self.book.remember(frame, target, [p.sid for p in peers]):
            self.publish_change(change, target)

    def receive_collaboration(self, frame, peer):
        kind = frame['type']
        target = chat_target(frame, self.sid)
        if kind in GROUP_EVENTS:
            return self.receive_group(frame, peer)
        if kind in CHANGE_EVENTS:
            outcome = self.book.change(frame, target)
            if outcome == 'refused':
                return False, 'Seul l’auteur peut modifier ce message dans cette conversation'
            if outcome == 'apply':
                self.publish_change(frame, target)
            return True, ''
        if kind == 'poll_create':
            if self.sid not in frame['audience']:
                return False, 'Ce sondage ne t’est pas destiné'
            if not frame.get('group') and frame['to'] != 'general':
                return False, 'Sondages dans le général ou les groupes uniquement'
            self.polls.create(frame, target)
            self.emit('poll_created', target=target, poll=self.polls.public(frame['id']), nickname=peer.nick,
                      avatar=peer.avatar, avatar_image=peer.avatar_image, outgoing=False)
            self.remember_message(frame, target)
            return True, ''
        if not self.polls.vote(frame, target):
            return False, 'Sondage inconnu ou vote non autorisé'
        self.emit('poll_updated', target=target, poll=self.polls.public(frame['poll']))
        return True, ''

    async def send_collaboration(self, kind, target, **payload):
        from .protocol import event, encode, ProtocolError
        if kind in GROUP_EVENTS:
            return None  # Room control uses create_group()/answer_group(), never arbitrary UI payloads.
        if self._queued >= 16:
            self.emit('notice', text='Trop d’actions en attente.')
            return None
        if target != 'general' and not group_id(target) and kind in POLL_EVENTS:
            self.emit('notice', text='Les sondages sont disponibles dans le général et les groupes.')
            return None
        peers = self.conversation_peers(target, CAPABILITY)
        if group_id(target) and (not self.rooms.get(group_id(target)) or self.rooms[group_id(target)].state != 'joined'):
            self.emit('notice', text='Rejoins le groupe avant d’envoyer une action.')
            return None
        self.change_sequence += 1
        if kind in CHANGE_EVENTS:
            item = self.book.items.get(payload.get('message'))
            if not item or item['author'] != self.sid or item['chat'] != target or item['deleted']:
                self.emit('notice', text='Ce message ne peut plus être modifié.')
                return None
            peers = [p for p in peers if p.sid in item['recipients']]
            if item['recipients'] - {p.sid for p in peers}:
                self.emit('notice', text='Des contacts absents ou des clients anciens peuvent conserver le message original. Les changements ne sont pas renvoyés après reconnexion.')
            payload['seq'] = self.change_sequence
        elif kind == 'poll_create':
            if any(CAPABILITY not in p.caps for p in self.conversation_peers(target)):
                self.emit('notice', text='Certains contacts doivent mettre à jour FortyTchat pour voir ce sondage.')
            peers = sorted(peers, key=lambda p: p.sid)[:MAX_VOTERS - 1]
            payload['audience'] = [self.sid] + [p.sid for p in peers]
        elif kind == 'poll_vote':
            poll = self.polls.items.get(payload.get('poll'))
            if not poll or poll['chat'] != target:
                self.emit('notice', text='Sondage indisponible.')
                return None
            peers = [p for p in peers if p.sid in poll['audience']]
            payload['seq'] = self.change_sequence
        wire_target = peers[0].sid if group_id(target) and peers else self.sid if group_id(target) else target
        frame = event(kind, self.sid, wire_target, **payload, **({'group': group_id(target)} if group_id(target) else {}))
        try:
            encode(frame)
        except ProtocolError as exc:
            self.emit('notice', text=str(exc)); return None
        if not self.social_bucket.allow():
            self.emit('notice', text='Trop d’actions à la fois. Patiente un instant.'); return None
        if kind in CHANGE_EVENTS:
            if self.book.change(frame, target) != 'apply':
                return None
            self.publish_change(frame, target)
        elif kind == 'poll_create':
            self.polls.create(frame, target)
            self.emit('poll_created', target=target, poll=self.polls.public(frame['id']), nickname=self.nickname,
                      avatar=self.avatar, avatar_image=self.avatar_image, outgoing=True)
            self.remember_message(frame, target, peers)
        else:
            if not self.polls.vote(frame, target):
                return None
            self.emit('poll_updated', target=target, poll=self.polls.public(frame['poll']))
        self.seen.add(frame['id'])
        self._queued += 1
        try:
            frames = [{**frame, 'to': p.sid} if group_id(target) else frame for p in peers]
            results = await asyncio.gather(*(self._send_to(p, f) for p, f in zip(peers, frames)), return_exceptions=True)
            if any(ok is not True for ok in results):
                self.emit('notice', text='Action non confirmée chez certains destinataires.')
            return frame['id']
        finally:
            self._queued -= 1
