"""Bounded, opt-in file transfers. Only user-approved downloads reach disk."""
import asyncio
import base64
import hashlib
import os
from pathlib import Path
import stat
import tempfile
import time

MAX_FILE_SIZE = 20 * 1024 * 1024
CHUNK_SIZE = 6 * 1024
OFFER_LIFETIME = 600
TRANSFER_TIMEOUT = 60
MAX_OUTGOING = 8
MAX_INCOMING = 32
MAX_ACTIVE = 4
FILE_EVENTS = {'file_offer', 'file_accept', 'file_decline', 'file_chunk', 'file_end', 'file_cancel', 'file_done'}


def valid_filename(name):
    return (isinstance(name, str) and 0 < len(name) <= 200 and name not in ('.', '..')
            and not any(c in '/\\' or ord(c) < 32 or 0xD800 <= ord(c) <= 0xDFFF for c in name)
            and len(name.encode('utf-8')) <= 255)


def open_source(path):
    flags = os.O_RDONLY | getattr(os, 'O_NONBLOCK', 0) | getattr(os, 'O_NOFOLLOW', 0)
    fd = os.open(path, flags)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError('Choisis un fichier ordinaire de 20 Mo maximum.')
        return os.fdopen(fd, 'rb')
    except BaseException:
        os.close(fd)
        raise


def prepare_source(path):
    path = Path(path).expanduser().resolve(strict=True)
    with open_source(path) as source:
        before = os.fstat(source.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_size > MAX_FILE_SIZE:
            raise ValueError('Choisis un fichier ordinaire de 20 Mo maximum.')
        digest = hashlib.sha256()
        count = 0
        while chunk := source.read(128 * 1024):
            count += len(chunk)
            if count > MAX_FILE_SIZE or count > before.st_size:
                raise ValueError('Le fichier a changé pendant sa préparation.')
            digest.update(chunk)
        after = os.fstat(source.fileno())
        if fingerprint(before) != fingerprint(after):
            raise ValueError('Le fichier a changé pendant sa préparation.')
    name = path.name
    if not valid_filename(name):
        raise ValueError('Le nom du fichier est trop long ou contient des caractères interdits.')
    return {'path': path, 'name': name, 'size': before.st_size, 'sha256': digest.hexdigest(),
            'fingerprint': fingerprint(before)}


def fingerprint(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)


async def disk(function, *args):
    # Finish an in-flight disk operation before cancellation closes its handle.
    task = asyncio.create_task(asyncio.to_thread(function, *args))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        await task
        raise


def write_all(handle, raw):
    view = memoryview(raw)
    written = 0
    while written < len(view):
        count = handle.write(view[written:])
        if count is None or count <= 0:
            raise OSError('Écriture du fichier interrompue.')
        written += count


class FileTransfers:
    def __init__(self, client):
        self.client = client
        self.outgoing, self.incoming = {}, {}
        self.reserved = 0
        self.receipts = {}

    def emit(self, kind, offer, peer, outgoing=False, **extra):
        self.client.emit(kind, id=offer['id'], peer=peer, target=offer['to'], name=offer['name'],
                         outgoing=outgoing, **extra)

    async def offer(self, target, path=None, *, preview='', memory=None, name='capture.png'):
        self.prune()
        if len(self.outgoing) + self.reserved >= MAX_OUTGOING:
            self.client.emit('notice', text='Huit fichiers proposés simultanément maximum.')
            return
        peers = self.client.file_recipients(target)
        if not peers:
            self.client.emit('notice', text='Aucun destinataire disponible avec le partage de fichiers. Installez la même version de FortyTchat.')
            return
        self.reserved += 1
        try:
            if memory is None:
                source = await disk(prepare_source, path)
            else:
                if not isinstance(memory, bytes) or not 0 < len(memory) <= MAX_FILE_SIZE or not valid_filename(name):
                    raise ValueError('Capture trop grande ou invalide (20 Mio maximum).')
                source = dict(memory=memory, name=name, size=len(memory), sha256=hashlib.sha256(memory).hexdigest())
            if self.client._closing:
                return
            from ..networking.protocol import event
            frame = event('file_offer', self.client.sid, target,
                          **{key: source[key] for key in ('name', 'size', 'sha256')})
            if preview:
                from ..avatar_data import avatar_hash
                avatar_hash(preview)
                frame['preview'] = preview
            source.update(frame)
            source.update(expires=time.monotonic() + OFFER_LIFETIME,
                          recipients={p.sid for p in peers}, active={}, finished=set())
            self.outgoing[frame['id']] = source
            self.client.emit('file_offer', **frame, nickname=self.client.nickname, outgoing=True,
                             recipients=sorted(source['recipients']))
            self.client.remember_message(frame, target, peers)
            deliveries = await self.client.deliver_file(frame, peers, report=True)
            for sid, delivered in deliveries.items():
                if not delivered:
                    self.finish(source, sid)
                    self.emit('file_failed', source, sid, True, reason='Offre non remise : destinataire déconnecté.')
            return frame['id']
        except (OSError, ValueError) as exc:
            self.client.emit('notice', text=str(exc))
        finally:
            self.reserved -= 1

    def prune(self):
        now = time.monotonic()
        for key, offer in list(self.outgoing.items()):
            if offer['expires'] <= now:
                for task in offer['active'].values():
                    task.cancel()
                self.emit('file_failed', offer, offer['to'], True, reason='Proposition de fichier expirée.')
                self.outgoing.pop(key, None)
        for key, offer in list(self.incoming.items()):
            expiry = offer['updated'] + TRANSFER_TIMEOUT if offer.get('file') else offer['expires']
            if expiry <= now:
                self.fail_incoming(key, 'Proposition ou transfert expiré.')

    async def receive(self, frame):
        # Separate direct connections can deliver simultaneous chunks (or a retry).
        # Keep offset validation, disk writes, digest updates and final publication
        # ordered for each accepted offer, without blocking unrelated transfers.
        key = (frame.get('request'), frame.get('from'))
        offer = self.incoming.get(key)
        if offer is not None and frame['type'] in ('file_chunk', 'file_end'):
            lock = offer.setdefault('lock', asyncio.Lock())
            async with lock:
                if self.incoming.get(key) is not offer:
                    return False, 'Transfert annulé ou expiré'
                return await self._receive_unlocked(frame)
        return await self._receive_unlocked(frame)

    async def _receive_unlocked(self, frame):
        sender, kind = frame['from'], frame['type']
        peer = self.client.peers.get(sender)
        if not peer or sender in self.client.blocked or 'files' not in peer.caps:
            return False, 'Partage de fichiers indisponible'
        if frame['to'] not in (self.client.sid, 'general'):
            return False, 'Destinataire incorrect'
        if frame['id'] in self.client.seen.ids:
            return True, ''
        self.prune()
        if kind == 'file_offer':
            if frame.get('group') and not self.client.group_access(peer, frame['group']):
                return False, 'Groupe indisponible'
            existing = self.incoming.get((frame['id'], sender))
            if existing:
                fields = ('from', 'to', 'name', 'size', 'sha256', 'group', 'preview')
                return (True, '') if all(existing.get(key) == frame.get(key) for key in fields) else (False, 'Offre de fichier incohérente')
            if len(self.incoming) >= MAX_INCOMING or not peer.bucket.allow():
                return False, 'Trop de fichiers proposés'
            offer = dict(frame, expires=time.monotonic() + OFFER_LIFETIME, updated=time.monotonic())
            self.incoming[(frame['id'], sender)] = offer
            self.client.emit('file_offer', **frame, nickname=peer.nick, outgoing=False)
            from .collaboration import chat_target
            self.client.remember_message(frame, chat_target(frame, self.client.sid))
        elif kind == 'file_accept':
            offer = self.outgoing.get(frame['request'])
            from .collaboration import group_id
            if offer and group_id(offer['to']) and not self.client.group_access(peer, group_id(offer['to'])):
                return False, 'Membre retiré du groupe'
            if not offer or sender not in offer['recipients'] or sender in offer['finished']:
                return False, 'Proposition expirée'
            if sender not in offer['active']:
                if sum(len(item['active']) for item in self.outgoing.values()) >= MAX_ACTIVE:
                    return False, 'Trop de transferts actifs'
                task = self.client.task(self.client.stream_file(offer, sender))
                offer['active'][sender] = task
        elif kind == 'file_decline':
            offer = self.outgoing.get(frame['request'])
            if offer and sender in offer['recipients']:
                task = offer['active'].pop(sender, None)
                if task:
                    task.cancel()
                self.finish(offer, sender)
                self.emit('file_declined', offer, sender, True)
        elif kind == 'file_done':
            receipt = self.receipts.get((frame['request'], sender))
            if receipt and not receipt.done():
                receipt.set_result(True)
        elif kind == 'file_cancel':
            key = (frame['request'], sender)
            if key in self.incoming:
                self.fail_incoming(key, 'Transfert annulé par l’expéditeur.')
            else:
                offer = self.outgoing.get(frame['request'])
                if offer and sender in offer['recipients']:
                    task = offer['active'].pop(sender, None)
                    if task:
                        task.cancel()
                    self.finish(offer, sender)
                    self.emit('file_failed', offer, sender, True, reason='Transfert annulé par le destinataire.')
        elif kind in ('file_chunk', 'file_end'):
            key = (frame['request'], sender)
            offer = self.incoming.get(key)
            if offer and offer.get('group') and not self.client.group_access(peer, offer['group']):
                self.fail_incoming(key, 'Membre retiré du groupe.')
                return False, 'Groupe indisponible'
            if not offer or not offer.get('file'):
                return False, 'Ce fichier n’a pas été accepté'
            if kind == 'file_chunk':
                raw = base64.b64decode(frame['data'], validate=True)
                if frame['offset'] != offer['received'] or offer['received'] + len(raw) > offer['size']:
                    self.fail_incoming(key, 'Longueur ou ordre du fichier invalide.')
                    return False, 'Bloc invalide'
                offer['io_busy'] = True
                try:
                    await disk(write_all, offer['file'], raw)
                except (OSError, ValueError):
                    self.fail_incoming(key, 'Écriture du fichier impossible.')
                    return False, 'Écriture impossible'
                finally:
                    offer['io_busy'] = False
                    if offer.pop('cleanup_pending', False):
                        self.cleanup(offer)
                if self.incoming.get(key) is not offer:
                    return False, 'Transfert annulé ou expiré'
                offer['digest'].update(raw)
                offer['received'] += len(raw)
                offer['updated'] = time.monotonic()
                self.progress(offer, sender)
            else:
                if offer['received'] != offer['size'] or offer['digest'].hexdigest() != offer['sha256']:
                    self.fail_incoming(key, 'Le fichier reçu est incomplet ou son empreinte est incorrecte.')
                    return False, 'Fichier incomplet ou corrompu'
                try:
                    offer['file'].close()
                    offer['file'] = None
                    # Never overwrite an unrelated file created while transfer was running.
                    os.link(offer['temporary'], offer['destination'])
                    os.unlink(offer['temporary'])
                except OSError:
                    self.fail_incoming(key, 'Enregistrement impossible ; choisis un nom libre.')
                    return False, 'Enregistrement impossible'
                self.emit('file_complete', offer, sender, path=str(offer['destination']))
                self.incoming.pop(key, None)
                self.client.task(self.client.deliver_file(self.control('file_done', sender, frame['request'])))
        self.client.seen.add(frame['id'])
        return True, ''

    def finish(self, offer, peer):
        offer['finished'].add(peer)
        if offer['finished'] >= offer['recipients']:
            self.outgoing.pop(offer['id'], None)

    def progress(self, offer, peer, outgoing=False, amount=None):
        now = time.monotonic()
        amount = offer.get('received', 0) if amount is None else amount
        key = ('progress', peer, outgoing)
        if now - offer.get(key, 0) >= .15 or amount == offer['size']:
            offer[key] = now
            self.emit('file_progress', offer, peer, outgoing, received=amount, total=offer['size'])

    async def accept(self, request, sender, path):
        self.prune()
        key = (request, sender)
        offer = self.incoming.get(key)
        if not offer or offer.get('file'):
            return
        if offer.get('group') and not self.client.group_access(self.client.peers.get(sender), offer['group']):
            self.fail_incoming(key, 'Groupe indisponible.'); return
        if sum(bool(item.get('file')) for item in self.incoming.values()) >= MAX_ACTIVE:
            self.emit('file_deferred', offer, sender, reason='Quatre téléchargements simultanés maximum. Réessaie à la fin d’un transfert.')
            return
        temporary = None
        try:
            destination = Path(path).expanduser().absolute()
            if destination.exists() or destination.is_symlink():
                raise ValueError('Choisis un nom de fichier libre : aucun fichier existant ne sera remplacé.')
            fd, temporary = tempfile.mkstemp(prefix='.fortytchat-', suffix='.part', dir=destination.parent)
            offer.update(file=os.fdopen(fd, 'wb', buffering=0), temporary=temporary,
                         destination=destination, received=0, digest=hashlib.sha256(), updated=time.monotonic())
            frame = self.control('file_accept', sender, request)
            if not await self.client.deliver_file(frame):
                self.fail_incoming(key, 'Expéditeur déconnecté ou occupé.')
            elif self.incoming.get(key) is offer:
                self.progress(offer, sender)
        except (OSError, ValueError) as exc:
            if temporary and 'temporary' not in offer:
                os.unlink(temporary)
            self.cleanup(offer)
            for field in ('file', 'temporary', 'destination', 'received', 'digest'):
                offer.pop(field, None)
            self.emit('file_deferred', offer, sender, reason=str(exc))

    async def decline(self, request, sender):
        offer = self.incoming.pop((request, sender), None)
        if offer:
            self.cleanup(offer)
            self.emit('file_declined', offer, sender)
            await self.client.deliver_file(self.control('file_decline', sender, request))

    async def cancel(self, request, peer):
        offer = self.outgoing.get(request)
        if offer and (peer == 'general' or peer in offer['recipients']):
            peers = (offer['recipients'] - offer['finished']) if peer == 'general' else ({peer} - offer['finished'])
            for sid in peers:
                task = offer['active'].pop(sid, None)
                if task:
                    task.cancel()
                self.finish(offer, sid)
                self.emit('file_failed', offer, sid, True, reason='Transfert annulé.')
                await self.client.deliver_file(self.control('file_cancel', sid, request))
            if peer == 'general':
                self.outgoing.pop(request, None)
            return
        key = (request, peer)
        incoming = self.incoming.get(key)
        if incoming:
            self.fail_incoming(key, 'Transfert annulé.')
            await self.client.deliver_file(self.control('file_cancel', peer, request))

    @staticmethod
    def control(kind, target, request):
        # Caller fills sender without trusting a UI-provided identity.
        return {'type': kind, 'to': target, 'request': request}

    def fail_incoming(self, key, reason):
        offer = self.incoming.pop(key, None)
        if offer:
            self.cleanup(offer)
            self.emit('file_failed', offer, key[1], reason=reason)

    @staticmethod
    def cleanup(offer):
        if offer.get('io_busy'):
            offer['cleanup_pending'] = True
            return
        if offer.get('file'):
            offer['file'].close()
            offer['file'] = None
        temporary = offer.get('temporary')
        if temporary:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass

    def stop(self):
        for offer in self.incoming.values():
            self.cleanup(offer)
        self.incoming.clear()
        self.outgoing.clear()
