"""E2EE using libsodium's authenticated Box and signed, per-run public keys.

Only the Ed25519 identity and public trust pins persist. Session encryption keys
are generated in memory; no plaintext fallback or history/key archive exists.
"""
import base64
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile

from nacl.exceptions import CryptoError
from nacl.public import Box, PrivateKey, PublicKey
from nacl.signing import SigningKey, VerifyKey

from .identity import contact_from_caps, session_id, valid_contact_id

SCHEME = 'ft-box-1'
MAX_TRUST = 4096
MAX_PLAINTEXT = 16384
REPLAY_WINDOW = 4096
MAX_SESSIONS = 8192


class SecurityError(ValueError):
    pass


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False,
                      allow_nan=False).encode('utf-8')


def b64(value):
    return base64.b64encode(bytes(value)).decode('ascii')


def unb64(value, length=None, maximum=24000):
    if not isinstance(value, str) or len(value) > maximum:
        raise SecurityError('Clé ou enveloppe invalide')
    try:
        raw = base64.b64decode(value, validate=True)
    except (ValueError, TypeError) as exc:
        raise SecurityError('Encodage cryptographique invalide') from exc
    if length is not None and len(raw) != length:
        raise SecurityError('Longueur de clé invalide')
    if b64(raw) != value:
        raise SecurityError('Encodage cryptographique non canonique')
    return raw


def fingerprint(public):
    digest = hashlib.sha256(unb64(public, 32)).hexdigest().upper()
    return ' '.join(digest[i:i+4] for i in range(0, 64, 4))


def read_private(path, maximum):
    # Refuse symlinks, insecure permissions and damaged files; never silently reset.
    fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
    with os.fdopen(fd, 'r', encoding='utf-8') as stream:
        info = os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_size > maximum
                or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077):
            raise SecurityError('Fichier de sécurité non privé ou invalide (permissions 600 requises)')
        return json.load(stream)


def write_private(path, value, create=False):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix='.security-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(value, stream, ensure_ascii=True, allow_nan=False)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        if create:
            os.link(temp, path)  # Atomic first creation; cannot overwrite an identity.
        else:
            os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def trust_records(pins):
    if (not isinstance(pins, dict) or set(pins) != {'v', 'contacts'} or pins['v'] != 1
            or not isinstance(pins['contacts'], dict) or len(pins['contacts']) > MAX_TRUST):
        raise SecurityError('Carnet de clés invalide')
    for cid, record in pins['contacts'].items():
        if (not valid_contact_id(cid) or not isinstance(record, dict)
                or set(record) != {'key', 'verified'} or type(record['verified']) is not bool):
            raise SecurityError('Contact cryptographique invalide')
        unb64(record['key'], 32)
    return pins['contacts']


@contextmanager
def trust_lock(root):
    fd = os.open(root / '.security.lock', os.O_RDWR | os.O_CREAT | getattr(os, 'O_NOFOLLOW', 0), 0o600)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
            raise SecurityError('Verrou du carnet de clés non privé')
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


class CryptoIdentity:
    def __init__(self, contact_id, root=None):
        if not valid_contact_id(contact_id):
            raise SecurityError('Identifiant de profil invalide')
        self.contact_id = contact_id
        self.root = Path(root) if root is not None else None
        self.trusted = {}
        try:
            if self.root is None:
                self.signing = SigningKey.generate()
                return
            identity, trust = self.root / 'identity.json', self.root / 'trusted_contacts.json'
            created = False
            try:
                data = read_private(identity, 2048)
            except FileNotFoundError:
                if trust.exists():
                    raise SecurityError('Identité absente : restaure identity.json avec ton profil')
                signing = SigningKey.generate()
                data = {'v': 1, 'contact_id': contact_id, 'seed': b64(signing)}
                write_private(identity, data, create=True)
                created = True
            if set(data) != {'v', 'contact_id', 'seed'} or data['v'] != 1 or data['contact_id'] != contact_id:
                raise SecurityError('Identité incompatible avec ce profil')
            self.signing = SigningKey(unb64(data['seed'], 32))
            try:
                pins = read_private(trust, 1024 * 1024)
            except FileNotFoundError:
                if not created:
                    raise SecurityError('Carnet de clés absent : restaure trusted_contacts.json avec ton profil')
                pins = {'v': 1, 'contacts': {}}
                write_private(trust, pins, create=True)
            self.trusted = trust_records(pins)
        except (OSError, ValueError, TypeError, KeyError, RecursionError) as exc:
            raise SecurityError('Identité ou carnet de clés illisible. Restaure les fichiers du profil ; aucune clé n’a été réinitialisée.') from exc

    @property
    def public(self):
        return b64(self.signing.verify_key)

    def remember(self, contact_id, public, verified=False, replace=False):
        if not valid_contact_id(contact_id):
            raise SecurityError('Identifiant de contact invalide')
        unb64(public, 32)
        if self.root is not None:
            # Shared home directories can host concurrent profiles on two PCs.
            # Reload under a short lock so a stale process cannot erase newer pins.
            with trust_lock(self.root):
                try:
                    self.trusted = trust_records(read_private(self.root / 'trusted_contacts.json', 1024 * 1024))
                except (OSError, ValueError, TypeError, KeyError, RecursionError) as exc:
                    raise SecurityError('Carnet de clés illisible : contact refusé') from exc
                return self._remember(contact_id, public, verified, replace)
        return self._remember(contact_id, public, verified, replace)

    def _remember(self, contact_id, public, verified, replace):
        record = self.trusted.get(contact_id)
        if record and record['key'] != public and not replace:
            return 'changed'
        if not record and len(self.trusted) >= MAX_TRUST:
            raise SecurityError('Carnet de clés plein : contact refusé')
        updated = {**self.trusted, contact_id: {'key': public,
                   'verified': verified or bool(record and record['key'] == public and record['verified'])}}
        if self.root is not None and updated != self.trusted:
            write_private(self.root / 'trusted_contacts.json', {'v': 1, 'contacts': updated})
        self.trusted = updated
        return 'verified' if updated[contact_id]['verified'] else 'unverified'


class SecureSession:
    def __init__(self, identity, sid):
        self.identity, self.sid = identity, sid
        self.private = PrivateKey.generate()
        self.sent, self.received = {}, {}

    def sign_presence(self, presence):
        frame = dict(presence)
        frame['crypto'] = {'scheme': SCHEME, 'identity': self.identity.public,
                           'session': b64(self.private.public_key)}
        frame['crypto']['signature'] = b64(self.identity.signing.sign(self.presence_bytes(frame)).signature)
        return frame

    @staticmethod
    def presence_bytes(frame):
        # A relay changes hello to presence without changing the signed identity.
        clean = {key: value for key, value in frame.items() if key != 'type'}
        clean['crypto'] = {key: value for key, value in frame['crypto'].items() if key != 'signature'}
        return b'FortyTchat signed presence v1\0' + canonical(clean)

    @classmethod
    def check_presence(cls, frame):
        try:
            crypto = frame['crypto']
            if set(crypto) != {'scheme', 'identity', 'session', 'signature'} or crypto['scheme'] != SCHEME:
                raise SecurityError('Chiffrement incompatible : FortyTchat 1.5.0 requis')
            public, session = unb64(crypto['identity'], 32), unb64(crypto['session'], 32)
            VerifyKey(public).verify(cls.presence_bytes(frame), unb64(crypto['signature'], 64))
            # Reject low-order keys before adding a contact or displaying a lock.
            Box(PrivateKey.generate(), PublicKey(session))
            cid = frame.get('contact_id') or contact_from_caps(frame.get('caps', []))
            if not valid_contact_id(cid):
                raise SecurityError('Identité de contact absente')
            return cid, crypto['identity'], crypto['session']
        except (CryptoError, ValueError, TypeError, KeyError, RecursionError) as exc:
            raise SecurityError('Identité cryptographique absente ou signature invalide') from exc

    def seal(self, peer, frame, channel='event'):
        from .networking.protocol import encode
        if not peer.secure:
            raise SecurityError('Clé du contact modifiée : vérifie son empreinte avant de discuter')
        outer = {'v': 1, 'type': 'sealed', 'id': session_id(), 'from': self.sid,
                 'to': peer.sid, 'channel': channel}
        if peer.sid not in self.sent and len(self.sent) >= MAX_SESSIONS:
            raise SecurityError('Limite de sessions atteinte : relance FortyTchat')
        seq = self.sent.get(peer.sid, 0) + 1
        self.sent[peer.sid] = seq
        if seq >= 2 ** 53:
            raise SecurityError('Session de chiffrement à renouveler')
        # Header binding prevents substitution, reflection, cross-recipient replay.
        plain = canonical({**{k: outer[k] for k in ('id', 'from', 'to', 'channel')}, 'seq': seq, 'frame': frame})
        encode(frame)
        if len(plain) > MAX_PLAINTEXT:
            raise SecurityError('Message chiffré trop volumineux')
        outer['data'] = b64(Box(self.private, PublicKey(unb64(peer.session_key, 32))).encrypt(plain))
        return outer

    def open(self, peer, outer):
        from .networking.protocol import decode
        if (not peer.secure or outer.get('type') != 'sealed' or outer.get('from') != peer.sid
                or outer.get('to') != self.sid):
            raise SecurityError('Enveloppe non authentifiée')
        try:
            raw = Box(self.private, PublicKey(unb64(peer.session_key, 32))).decrypt(unb64(outer['data']))
            if len(raw) > MAX_PLAINTEXT:
                raise SecurityError('Message chiffré trop volumineux')
            packet = json.loads(raw)
            if (set(packet) != {'id', 'from', 'to', 'channel', 'seq', 'frame'}
                    or any(packet[key] != outer[key] for key in ('id', 'from', 'to', 'channel'))):
                raise SecurityError('En-tête chiffré incohérent')
            frame = decode(canonical(packet['frame']))
            if frame['type'] == 'ack':
                if outer['channel'] != 'receipt':
                    raise SecurityError('Accusé incohérent')
            elif (outer['channel'] == 'receipt' or frame.get('from') != peer.sid
                  or frame.get('to') not in (self.sid, 'general')):
                raise SecurityError('Expéditeur ou destinataire incohérent')
            seq = packet['seq']
            if type(seq) is not int or not 0 < seq < 2 ** 53:
                raise SecurityError('Compteur de session invalide')
            if peer.sid not in self.received and len(self.received) >= MAX_SESSIONS:
                raise SecurityError('Limite de sessions atteinte : relance FortyTchat')
            high, bits = self.received.get(peer.sid, (0, 0))
            if seq > high:
                shift = seq - high
                bits = 0 if shift >= REPLAY_WINDOW else (bits << shift) & ((1 << REPLAY_WINDOW) - 1)
                high, bits = seq, bits | 1
            else:
                distance = high - seq
                if distance >= REPLAY_WINDOW or bits & (1 << distance):
                    raise SecurityError('Enveloppe rejouée ou trop ancienne')
                bits |= 1 << distance
            self.received[peer.sid] = high, bits
            return frame
        except (CryptoError, ValueError, TypeError, KeyError, RecursionError) as exc:
            raise SecurityError('Message chiffré altéré ou clés incompatibles') from exc
