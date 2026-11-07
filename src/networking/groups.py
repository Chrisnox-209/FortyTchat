"""Creator-signed group changes, restart sync and one shared popup reservation."""
import asyncio
import copy
from functools import wraps
import time
from ..features.collaboration import CAPABILITY, Room, MAX_GROUPS, MAX_MEMBERS, group_key, group_id, plain
from ..features.group_profile import (GroupStore, new_profile, signed, validate_profile,
    popup_permit, validate_permit, POPUP_INTERVAL)
from ..security import SecurityError
from ..identity import session_id
from .protocol import event, encode, ProtocolError


def group_operation(method):
    @wraps(method)
    async def run(self, *args, **kwargs):
        task = asyncio.current_task()
        self.operations.add(task)
        try:
            if not self._closing: return await method(self, *args, **kwargs)
        finally:
            self.operations.discard(task)
    return run


class GroupNetwork:
    def init_groups(self):
        self.group_store = GroupStore(self.identity)
        self.group_requests = {}
        self.group_syncing = set()
        for gid, record in self.group_store.records.items():
            p = record['profile']
            state = record['state']
            if state == 'joined' and p['owner'] != self.contact_id:
                state = 'restoring'
            room = Room(gid, p['name'], p['owner'], tuple(p['members']), state=state, profile=copy.deepcopy(p))
            if state == 'joined':
                room.joined.add(self.contact_id)
                room.sessions[self.contact_id] = {self.sid}
            self.rooms[gid] = room

    def group_peer(self, cid):
        return next((p for p in self.conversation_peers('general', CAPABILITY) if p.contact_id == cid), None)

    def group_observe(self, peer):
        if not self.rooms: return
        try: asyncio.get_running_loop()
        except RuntimeError: return
        if not peer.secure or CAPABILITY not in peer.caps or peer.sid in self.blocked or peer.sid in self.group_syncing:
            return
        self.group_syncing.add(peer.sid)
        async def sync():
            try:
                owned = [r for r in self.rooms.values() if r.owner == self.contact_id and peer.contact_id in r.members]
                for room in owned:
                    await self._send_to(peer, event('group_state', self.sid, peer.sid, room=room.id, profile=room.profile))
                pending = [r.id for r in self.rooms.values() if r.owner == peer.contact_id]
                if pending:
                    await self._send_to(peer, event('group_sync', self.sid, peer.sid, rooms=pending))
                for room in self.rooms.values():
                    if room.state == 'joined' and peer.contact_id in room.members:
                        await self._send_to(peer, event('group_join', self.sid, peer.sid, room=room.id))
            finally:
                self.group_syncing.discard(peer.sid)
        self.task(sync())

    def persist_room(self, room, expected=None):
        self.group_store.save(room.profile, 'joined' if room.state in ('joining', 'restoring') else room.state, expected)

    @group_operation
    async def create_group(self, name, contacts):
        if (not isinstance(contacts, (list, tuple)) or any(not isinstance(c, str) for c in contacts)
                or not plain(name, 64) or any(c in name for c in '\n\t') or self.contact_id in contacts
                or not 1 <= len(set(contacts)) < MAX_MEMBERS or len(self.rooms) >= MAX_GROUPS):
            self.emit('notice', text='Choisis un nom et 1 à 31 membres (32 groupes maximum).'); return None
        peers = [self.group_peer(cid) for cid in dict.fromkeys(contacts)]
        if not all(peers):
            self.emit('notice', text='Les membres à inviter doivent être connectés avec FortyTchat 2.1.0.'); return None
        gid = session_id()
        profile = new_profile(self.identity, gid, name.strip(), [self.contact_id] + list(dict.fromkeys(contacts)))
        room = Room(gid, profile['name'], self.contact_id, tuple(profile['members']), state='joined',
                    joined={self.contact_id}, sessions={self.contact_id: {self.sid}}, profile=profile)
        try:
            self.persist_room(room, 0)
        except (OSError, ValueError) as exc:
            self.emit('notice', text=str(exc)); return None
        self.rooms[gid] = room
        self.publish_groups()
        self.emit('group_created', group=room.public())
        await asyncio.gather(*(self._send_to(p, event('group_invite', self.sid, p.sid, room=gid, profile=profile)) for p in peers))
        return gid

    async def announce_group(self, room):
        peers = [p for p in self.conversation_peers('general', CAPABILITY) if p.contact_id in room.members]
        await asyncio.gather(*(self._send_to(p, event('group_join', self.sid, p.sid, room=room.id)) for p in peers))

    @group_operation
    async def answer_group(self, reference, accept):
        room = self.rooms.get(reference)
        if not room or room.state not in ('pending', 'joined') or accept and room.state != 'pending':
            return False
        old = room.state
        room.state = 'joining' if accept else 'declined' if old == 'pending' else 'left'
        try:
            self.persist_room(room)
        except (OSError, ValueError) as exc:
            room.state = old; self.emit('notice', text=str(exc)); return False
        if accept:
            room.joined.add(self.contact_id); room.sessions[self.contact_id] = {self.sid}
            await self.announce_group(room)
            room.state = 'joined'
        else:
            room.left.add(self.contact_id); room.joined.discard(self.contact_id)
            peers = [p for p in self.conversation_peers('general', CAPABILITY) if p.contact_id in room.members]
            await asyncio.gather(*(self._send_to(p, event('group_leave', self.sid, p.sid, room=reference)) for p in peers))
        self.prune_group_transfers(room)
        self.publish_groups()
        return True

    def apply_profile(self, profile, peer):
        validate_profile(profile)
        gid, cid = profile['id'], self.contact_id
        room = self.rooms.get(gid)
        if profile['owner'] != peer.contact_id or profile['key'] != peer.identity_key or room and room.owner != peer.contact_id:
            raise SecurityError('Seul le créateur peut valider le groupe')
        record = self.group_store.records.get(gid)
        if record and profile['revision'] < record['profile']['revision']:
            raise SecurityError('Ancien état de groupe')
        if not room and cid not in profile['members']:
            raise SecurityError('Invitation non destinée à ce profil')
        if record and record['state'] == 'removed' and self.contact_id in profile['members'] and profile['revision'] <= record['profile']['revision']:
            raise SecurityError('Nouvelle invitation requise après un retrait')
        previous = record['state'] if record else 'pending'
        state = ('removed' if cid not in profile['members'] else 'pending' if previous == 'removed' else previous)
        self.group_store.save(profile, state)
        if not room:
            room = Room(gid, profile['name'], profile['owner'], tuple(profile['members']))
            self.rooms[gid] = room
        old_members, old_state = set(room.members), room.state
        room.profile = copy.deepcopy(profile)
        room.name, room.members, room.state = profile['name'], tuple(profile['members']), state
        room.joined.intersection_update(room.members)
        room.left.intersection_update(room.members)
        room.left.difference_update(set(room.members) - old_members)
        room.sessions = {c: s for c, s in room.sessions.items() if c in room.members}
        if state == 'joined':
            room.joined.add(cid); room.sessions[cid] = {self.sid}
            if old_state in ('restoring', 'joining') or old_members != set(profile['members']):
                self.task(self.announce_group(room))
        room.joined.add(peer.contact_id)
        room.sessions.setdefault(peer.contact_id, set()).add(peer.sid)
        self.prune_group_transfers(room)
        self.publish_groups()
        if not record or previous == 'removed' and state == 'pending':
            self.emit('group_invitation', group=room.public(), nickname=peer.nick)

    async def broadcast_profile(self, room, extra=()):
        peers = [p for p in self.conversation_peers('general', CAPABILITY) if p.contact_id in set(room.members) | set(extra)]
        await asyncio.gather(*(self._send_to(p, event('group_state', self.sid, p.sid, room=room.id, profile=room.profile)) for p in peers))

    @group_operation
    async def update_group(self, reference, changes):
        room = self.rooms.get(reference)
        if not room or room.state != 'joined': return False
        if room.owner == self.contact_id:
            return await self.author_group_update(room, self.contact_id, room.profile['revision'], changes)
        owner = self.group_peer(room.owner)
        if not owner:
            self.emit('notice', text='Le créateur doit être connecté pour valider les changements du groupe.'); return False
        frame = event('group_update', self.sid, owner.sid, room=reference, revision=room.profile['revision'], changes=changes)
        try: encode(frame)
        except ProtocolError as exc:
            self.emit('notice', text=str(exc)); return False
        ok = await self._send_to(owner, frame)
        if not ok: self.emit('notice', text='Modification du groupe non confirmée. Vérifie ton rôle et sa version.')
        return ok

    async def author_group_update(self, room, actor, revision, changes):
        extra = self.commit_group_update(room, actor, revision, changes)
        if extra is None: return False
        await self.broadcast_profile(room, extra)
        return True

    def commit_group_update(self, room, actor, revision, changes):
        if room.owner != self.contact_id or revision != room.profile['revision'] or actor not in room.members:
            return None
        p = copy.deepcopy(room.profile)
        if actor != room.owner and actor not in p['admins']:
            return None
        if not isinstance(changes, dict) or len(changes) != 1: return None
        action, value = next(iter(changes.items()))
        if action in ('promote', 'demote') and actor != room.owner: return None
        if action in ('remove', 'demote', 'promote') and (value == room.owner or value not in p['members']): return None
        if action == 'remove' and actor != room.owner and value in p['admins']: return None
        before = set(p['members'])
        if action == 'add':
            if value in p['members'] or len(p['members']) >= MAX_MEMBERS or not self.group_peer(value): return None
            p['members'].append(value)
        elif action == 'remove':
            p['members'].remove(value)
            if value in p['admins']: p['admins'].remove(value)
        elif action == 'promote':
            if value not in p['admins']: p['admins'].append(value)
        elif action == 'demote':
            if value in p['admins']: p['admins'].remove(value)
        elif action in ('name', 'photo', 'avatar'):
            p[action] = value
            if action == 'avatar': p['photo'] = ''
        else: return None
        p['revision'] += 1
        p = signed(self.identity, p)
        try: self.group_store.save(p, 'joined' if room.state == 'restoring' else room.state, revision)
        except (OSError, ValueError) as exc:
            self.emit('notice', text=str(exc)); return None
        room.profile, room.name, room.members = p, p['name'], tuple(p['members'])
        room.joined.intersection_update(room.members); room.left.intersection_update(room.members)
        room.sessions = {c:s for c,s in room.sessions.items() if c in room.members}
        self.prune_group_transfers(room)
        self.publish_groups()
        return before - set(room.members)

    def prune_group_transfers(self, room):
        for request, offer in list(self.files.outgoing.items()):
            if offer['to'] != group_key(room.id): continue
            for sid in list(offer['recipients'] - offer['finished']):
                if not self.group_access(self.peers.get(sid), room.id):
                    self.task(self.files.cancel(request, sid))
        for key, offer in list(self.files.incoming.items()):
            if offer.get('group') == room.id and not self.group_access(self.peers.get(key[1]), room.id):
                self.files.fail_incoming(key, 'Transfert arrêté : membre retiré ou groupe quitté.')

    def reserve_group_popup(self, room, actor, batch):
        p = copy.deepcopy(room.profile)
        now = time.time()
        if room.owner != self.contact_id or actor not in room.joined or actor in room.left or actor not in room.members:
            raise SecurityError('Membre non autorisé')
        if now < p['popup_at'] + POPUP_INTERVAL:
            raise SecurityError('Une popup pour tout le groupe toutes les 42 minutes, tous expéditeurs confondus.')
        p.update(revision=p['revision'] + 1, popup_at=now, popup_id=batch, popup_sender=actor)
        p = signed(self.identity, p)
        self.group_store.save(p, 'joined' if room.state == 'restoring' else room.state, room.profile['revision'])
        room.profile = p
        self.publish_groups()
        return popup_permit(self.identity, p)

    async def grant_group_popup(self, room, peer, frame):
        try: permit = self.reserve_group_popup(room, peer.contact_id, frame['batch'])
        except (OSError, ValueError) as exc:
            permit = {}; self.emit('notice', text=str(exc))
        # The persisted reservation is authoritative before any grant leaves this process.
        await self._send_to(peer, event('group_popup_grant', self.sid, peer.sid, room=room.id, request=frame['id'], permit=permit))
        if permit: await self.broadcast_profile(room)

    async def send_group_popup(self, target, **payload):
        room = self.rooms.get(group_id(target))
        if not room or room.state != 'joined': return None
        peers = [p for p in self.conversation_peers(target, 'cards') if 'choices' in p.caps]
        if not peers:
            self.emit('notice', text='Aucun membre du groupe n’est disponible pour recevoir une popup.'); return None
        # Validate user content before spending the shared reservation.
        try: encode(event('card_invite', self.sid, peers[0].sid, **payload))
        except ProtocolError as exc:
            self.emit('notice', text=str(exc)); return None
        self.prune_card_policies()
        if len(self.outgoing_invites.pending) + len(peers) > 100 or len(self.group_requests) >= 4 or not self.message_bucket.allow():
            self.emit('notice', text='Trop de demandes en attente.'); return None
        batch = session_id()
        try:
            if room.owner == self.contact_id:
                permit = self.reserve_group_popup(room, self.contact_id, batch)
                await self.broadcast_profile(room)
            else:
                owner = self.group_peer(room.owner)
                if not owner: raise SecurityError('Le créateur du groupe doit être connecté pour réserver la popup collective.')
                frame = event('group_popup_request', self.sid, owner.sid, room=room.id, batch=batch)
                future = asyncio.get_running_loop().create_future()
                self.group_requests[frame['id']] = (room.id, owner.contact_id, batch, future)
                try:
                    if not await self._send_to(owner, frame): raise SecurityError('Réservation refusée ou délai de 42 minutes en cours.')
                    permit = await asyncio.wait_for(future, 8)
                finally: self.group_requests.pop(frame['id'], None)
                validate_permit(permit, room.profile, self.contact_id)
        except (OSError, ValueError, asyncio.TimeoutError) as exc:
            self.emit('notice', text=str(exc) or 'Réservation de popup expirée.'); return None
        frames, ids = [], []
        for peer in peers:
            # A fresh id per recipient keeps every reply independently consumable.
            frame = event('card_invite', self.sid, peer.sid, **payload, group=room.id, permit=permit)
            encode(frame)
            self.outgoing_invites.add(frame['id'], peer.sid)
            if 'responses' in payload: self.card_policies['outgoing'][frame['id']] = payload['responses']
            ids.append(frame['id'])
            frames.append(frame)
        results = await asyncio.gather(*(self._send_to(p, f) for p, f in zip(peers, frames)), return_exceptions=True)
        self.emit('group_popup_sent', target=target, count=sum(r is True for r in results), total=len(peers), ids=ids)
        return batch

    def receive_group(self, frame, peer):
        kind = frame['type']
        if kind == 'group_sync':
            for gid in frame['rooms']:
                room = self.rooms.get(gid)
                if room and room.owner == self.contact_id and peer.contact_id in self.group_store.records[gid]['profile']['members']:
                    self.task(self._send_to(peer, event('group_state', self.sid, peer.sid, room=gid, profile=room.profile)))
                elif room and room.owner == self.contact_id:
                    # Authenticated owner refusal; no roster or photo is disclosed.
                    self.task(self._send_to(peer, event('group_removed', self.sid, peer.sid, room=gid, revision=room.profile['revision'])))
            return True, ''
        room = self.rooms.get(frame['room'])
        if kind in ('group_invite', 'group_state'):
            try: self.apply_profile(frame['profile'], peer)
            except (OSError, ValueError) as exc: return False, str(exc)[:160]
            return True, ''
        if not room or peer.contact_id not in room.members:
            return False, 'Groupe indisponible'
        if kind == 'group_removed':
            if peer.contact_id != room.owner or frame['revision'] < room.profile['revision']: return False, 'Retrait non autorisé'
            try: self.group_store.save(room.profile, 'removed')
            except (OSError, ValueError) as exc: return False, str(exc)[:160]
            room.state = 'removed'; room.sessions.clear(); self.prune_group_transfers(room); self.publish_groups()
            return True, ''
        if kind == 'group_popup_grant':
            pending = self.group_requests.get(frame['request'])
            if not pending or pending[:2] != (room.id, peer.contact_id) or room.owner != peer.contact_id:
                return False, 'Réservation inconnue'
            permit = frame['permit']
            if permit and permit.get('id') != pending[2]: return False, 'Popup incohérente'
            if not pending[3].done(): pending[3].set_result(permit)
            return True, ''
        if kind in ('group_update', 'group_popup_request'):
            if room.owner != self.contact_id or not self.group_access(peer, room.id): return False, 'Créateur ou membre indisponible'
            if kind == 'group_update':
                if peer.contact_id not in room.profile['admins'] or frame['revision'] != room.profile['revision']: return False, 'Rôle ou version insuffisants'
                extra = self.commit_group_update(room, peer.contact_id, frame['revision'], frame['changes'])
                if extra is None: return False, 'Modification non autorisée'
                self.task(self.broadcast_profile(room, extra))
            else: self.task(self.grant_group_popup(room, peer, frame))
            return True, ''
        if room.state in ('declined', 'left', 'removed', 'pending', 'restoring'):
            return False, 'Groupe non rejoint'
        if kind == 'group_join':
            if peer.contact_id in room.left: return False, 'Ce membre a quitté le groupe'
            sessions = room.sessions.setdefault(peer.contact_id, set())
            fresh = peer.sid not in sessions
            if fresh and len(sessions) >= 4: return False, 'Trop de sessions'
            sessions.add(peer.sid); room.joined.add(peer.contact_id)
            if fresh: self.task(self._send_to(peer, event('group_join', self.sid, peer.sid, room=room.id)))
        else:
            room.left.add(peer.contact_id); room.joined.discard(peer.contact_id)
        self.publish_groups()
        return True, ''
