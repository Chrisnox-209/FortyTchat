"""Room fan-out and authenticated message changes, inside the existing E2EE envelopes."""
import asyncio
from ..features.collaboration import (CAPABILITY, GROUP_EVENTS, CHANGE_EVENTS, POLL_EVENTS,
    MAX_GROUPS, MAX_MEMBERS, MAX_VOTERS, Room, MessageBook, PollBook, group_key, group_id, chat_target, plain)
from ..features.versions import modern_caps, room_capable
from ..identity import session_id
from .groups import GroupNetwork


class CollaborationNetwork(GroupNetwork):
    def init_collaboration(self):
        self.rooms, self.book, self.polls = {}, MessageBook(), PollBook()
        self.change_sequence = 0
        self.init_groups()

    def publish_groups(self):
        self.emit('groups', groups=[room.public() for room in self.rooms.values()])

    def group_access(self, peer, reference):
        room = self.rooms.get(reference)
        return bool(room and room.state in ('joining', 'joined') and peer and room_capable(peer.caps)
                    and peer.contact_id in room.members and peer.contact_id in room.joined and peer.contact_id not in room.left
                    and peer.sid in room.sessions.get(peer.contact_id, ()))

    def conversation_peers(self, target, capability=None):
        reference = group_id(target)
        return [peer for peer in self.peers.values()
                if peer.secure and peer.sid not in self.blocked
                and (not capability or (room_capable(peer.caps) if capability==CAPABILITY else capability in peer.caps))
                and (self.group_access(peer, reference) if reference else target == 'general' or target == peer.sid)]

    def wire_frame(self, frame, peer):
        if frame['type'] == 'card_invite' and 'close_label' in frame and 'close_label' not in peer.features:
            frame = {key:value for key,value in frame.items() if key != 'close_label'}
        reference = group_id(frame['to'])
        if reference:
            frame = {**frame, 'to': peer.sid, 'group': reference}
        if 'preview' in frame and not room_capable(peer.caps):
            frame={k:v for k,v in frame.items() if k!='preview'}
        if frame['type'] in ('group_invite','group_state') and 'profile' in frame:
            from ..features.group_profile import signed
            profile=frame['profile']
            if 'rooms3' not in peer.caps and 'rooms2' in peer.caps:
                frame={**frame,'profile':signed(self.identity,{k:v for k,v in profile.items() if k!='popup_interval'})}
            elif 'rooms' in peer.caps and not any(c in peer.caps for c in ('rooms2','rooms3')):
                frame={k:v for k,v in frame.items() if k not in ('profile',)}
                frame.update(type='group_invite',name=profile['name'],members=profile['members'])
        if frame['type']=='card_invite' and frame.get('group'):
            length=len(frame['permit'])
            supported=modern_caps(peer.caps) or length==7 and 'rooms3' in peer.caps or length==5 and 'rooms2' in peer.caps and 'rooms3' not in peer.caps
            if not supported:
                frame={k:v for k,v in frame.items() if k not in ('group','permit')}
        return frame

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
            if modern_caps(peer.caps) and frame.get('multiple'):return False,'Sondage définitif à choix unique requis'
            self.polls.create(frame, target, final=modern_caps(peer.caps))
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
            if kind == 'message_edit':
                from ..features.text import mention_ids
                payload['mentions'] = mention_ids(payload.get('text', ''), [p.public() for p in peers])
        elif kind == 'poll_create':
            if payload.get('multiple'):
                self.emit('notice',text='Les nouveaux sondages proposent une seule réponse.');return None
            payload.pop('multiple',None)
            if any(CAPABILITY not in p.caps for p in self.conversation_peers(target)):
                self.emit('notice', text='Certains contacts doivent mettre à jour FortyTchat pour voir ce sondage.')
            if any(not modern_caps(p.caps) for p in peers):self.emit('notice',text='Ce sondage définitif sera visible sur les versions récentes. Les échanges avec les anciennes versions continuent.')
            peers = sorted([p for p in peers if modern_caps(p.caps)], key=lambda p: p.sid)[:MAX_VOTERS - 1]
            payload['audience'] = [self.sid] + [p.sid for p in peers]
        elif kind == 'poll_vote':
            poll = self.polls.items.get(payload.get('poll'))
            if not poll or poll['chat'] != target:
                self.emit('notice', text='Sondage indisponible.')
                return None
            if self.sid in poll['votes']:
                self.emit('notice',text='Ton vote est définitif.');return None
            peers = [p for p in peers if p.sid in poll['audience']]
            if poll.get('multiple') and 'option' in payload:payload['options']=[payload.pop('option')]
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
