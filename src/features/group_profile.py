"""Signed group metadata and private persistence. Never stores messages or votes."""
import copy
import time
from nacl.signing import VerifyKey
from nacl.exceptions import BadSignatureError
from ..security import canonical, b64, unb64, SecurityError, read_private, write_private, trust_lock
from ..avatar_data import avatar_hash, AvatarError
from .collaboration import identifier, plain, MAX_GROUPS, MAX_MEMBERS

POPUP_INTERVAL = 42 * 60
MIN_POPUP_MINUTES, MAX_POPUP_MINUTES = 1, 24 * 60
PROFILE_DOMAIN = b'FortyTchat group profile v1\0'
PERMIT_DOMAIN = b'FortyTchat group popup v1\0'


def valid_popup_interval(value):
    return (type(value) is int and MIN_POPUP_MINUTES * 60 <= value <= MAX_POPUP_MINUTES * 60
            and value % 60 == 0)


def popup_interval(profile):
    # Missing on signed profiles created before 2.2.0; do not alter their signature.
    return profile.get('popup_interval', POPUP_INTERVAL)


def popup_remaining(profile, now=None):
    last = profile.get('popup_at', 0)
    return max(0, last + popup_interval(profile) - (time.time() if now is None else now)) if last else 0


def signed(identity, value, domain=PROFILE_DOMAIN):
    value = {k: v for k, v in value.items() if k != 'signature'}
    return {**value, 'signature': b64(identity.signing.sign(domain + canonical(value)).signature)}


def verify(value, key, domain=PROFILE_DOMAIN):
    try:
        clean = {k: v for k, v in value.items() if k != 'signature'}
        VerifyKey(unb64(key, 32)).verify(domain + canonical(clean), unb64(value['signature'], 64))
    except (KeyError, TypeError, ValueError, BadSignatureError) as exc:
        raise SecurityError('Signature du groupe invalide') from exc


def validate_profile(p):
    fields = {'id', 'owner', 'key', 'revision', 'name', 'members', 'admins', 'photo', 'avatar',
              'popup_at', 'popup_id', 'popup_sender', 'signature'}
    if (not isinstance(p, dict) or set(p) - {'popup_interval'} != fields
            or not valid_popup_interval(popup_interval(p)) or not identifier(p['id']) or not identifier(p['owner'])
            or type(p['revision']) is not int or not 1 <= p['revision'] < 2**53
            or not plain(p['name'], 64) or any(c in p['name'] for c in '\n\t')
            or not isinstance(p['members'], list) or not 1 <= len(p['members']) <= MAX_MEMBERS
            or any(not identifier(c) for c in p['members']) or len(set(p['members'])) != len(p['members'])
            or p['owner'] not in p['members'] or not isinstance(p['admins'], list)
            or len(p['admins']) > MAX_MEMBERS or len(set(p['admins'])) != len(p['admins'])
            or any(c not in p['members'] or c == p['owner'] for c in p['admins'])
            or type(p['avatar']) is not int or not -1 <= p['avatar'] < 24
            or type(p['popup_at']) not in (int, float) or not 0 <= p['popup_at'] <= 2**40
            or not (p['popup_id'] == '' or identifier(p['popup_id']))
            or not (p['popup_sender'] == '' or identifier(p['popup_sender']))
            or bool(p['popup_at']) != bool(p['popup_id']) or bool(p['popup_id']) != bool(p['popup_sender'])):
        raise SecurityError('Profil de groupe invalide')
    try:
        avatar_hash(p['photo'])
        verify(p, p['key'])
    except (AvatarError, TypeError) as exc:
        raise SecurityError('Photo du groupe invalide') from exc
    return p


def new_profile(identity, reference, name, members):
    return signed(identity, dict(id=reference, owner=identity.contact_id, key=identity.public, revision=1,
        name=name, members=list(members), admins=[], photo='', avatar=-1,
        popup_at=0, popup_id='', popup_sender='', popup_interval=POPUP_INTERVAL))


def popup_permit(identity, p):
    return signed(identity, dict(room=p['id'], id=p['popup_id'], sender=p['popup_sender'], at=p['popup_at'],
                                 interval=popup_interval(p), revision=p['revision']), PERMIT_DOMAIN)


def validate_permit(permit, profile, sender, now=None):
    now = time.time() if now is None else now
    if (not isinstance(permit, dict) or set(permit) not in ({'room','id','sender','at','signature'},{'room','id','sender','at','interval','revision','signature'})
            or permit['room'] != profile['id'] or not identifier(permit['id']) or permit['sender'] != sender
            or not valid_popup_interval(permit.get('interval',POPUP_INTERVAL))
            or type(permit.get('revision',1)) is not int or not 1 <= permit.get('revision',1) < 2**53
            or type(permit['at']) not in (int, float) or not now - 120 <= permit['at'] <= now + 5):
        raise SecurityError('Autorisation de popup expirée ou invalide')
    verify(permit, profile['key'], PERMIT_DOMAIN)


class GroupStore:
    """CAS metadata revisions and a receiver clock, merged under the profile lock."""
    def __init__(self, identity):
        self.identity, self.root, self.records = identity, identity.root, {}
        self.error = ''
        try:
            self.records = self._read()
        except (OSError, ValueError, TypeError, KeyError, RecursionError):
            self.error = 'Groupes indisponibles : group_profiles.json est illisible. Restaure ce fichier du profil.'

    def _read(self):
        if self.root is None:
            return copy.deepcopy(self.records)
        try:
            data = read_private(self.root / 'group_profiles.json', 1024 * 1024)
        except FileNotFoundError:
            return {}
        if not isinstance(data, dict) or set(data) != {'v', 'groups'} or data['v'] != 1 or not isinstance(data['groups'], dict) or len(data['groups']) > MAX_GROUPS:
            raise SecurityError('Carnet de groupes invalide')
        for gid, record in data['groups'].items():
            p = validate_profile(record['profile'])
            if (gid != p['id'] or record['state'] not in ('pending', 'joined', 'left', 'declined', 'removed')
                    or type(record['seen_at']) not in (int, float) or not 0 <= record['seen_at'] <= 2**40
                    or not isinstance(record['seen_id'], str) or record['seen_id'] and not identifier(record['seen_id'])):
                raise SecurityError('État de groupe invalide')
            key = self.identity.public if p['owner'] == self.identity.contact_id else self.identity.trusted.get(p['owner'], {}).get('key')
            if p['key'] != key:
                raise SecurityError('Clé du créateur de groupe modifiée')
        return data['groups']

    def update(self, operation):
        if self.error:
            raise SecurityError(self.error)
        def apply():
            records = self._read()
            result = operation(records)
            if self.root is not None:
                write_private(self.root / 'group_profiles.json', {'v': 1, 'groups': records})
            self.records = records
            return result
        if self.root is None:
            return apply()
        with trust_lock(self.root):
            return apply()

    def save(self, profile, state, expected=None):
        validate_profile(profile)
        key = self.identity.public if profile['owner'] == self.identity.contact_id else self.identity.trusted.get(profile['owner'], {}).get('key')
        if key != profile['key']: raise SecurityError('Clé du créateur non reconnue')
        def operation(records):
            previous = records.get(profile['id'])
            if expected is not None and (previous['profile']['revision'] if previous else 0) != expected:
                raise SecurityError('Le groupe a changé sur une autre session. Attends sa synchronisation.')
            if previous:
                old = previous['profile']
                legacy_upgrade=('popup_interval' not in old and 'popup_interval' in profile
                                and {k:v for k,v in old.items() if k!='signature'}=={k:v for k,v in profile.items() if k not in ('signature','popup_interval')})
                if profile['owner'] != old['owner'] or profile['key'] != old['key'] or profile['revision'] < old['revision'] or profile['revision'] == old['revision'] and profile != old and not legacy_upgrade:
                    raise SecurityError('Ancien état ou identité de groupe incompatible')
            elif len(records) >= MAX_GROUPS:
                raise SecurityError('32 groupes maximum')
            records[profile['id']] = dict(profile=copy.deepcopy(profile), state=state,
                seen_at=previous['seen_at'] if previous else 0, seen_id=previous['seen_id'] if previous else '')
        self.update(operation)

    def receive_popup(self, reference, permit):
        def operation(records):
            record = records[reference]
            validate_permit(permit, record['profile'], permit['sender'])
            # The signed grant also carries the policy in case it arrives before group_state.
            interval = (permit.get('interval',POPUP_INTERVAL) if permit.get('revision',0) >= record['profile']['revision']
                        else popup_interval(record['profile']))
            if permit['id'] == record['seen_id'] or record['seen_at'] and permit['at'] < record['seen_at'] + interval:
                raise SecurityError(f'Une seule popup de groupe toutes les {interval // 60} minutes')
            record['seen_at'], record['seen_id'] = permit['at'], permit['id']
        self.update(operation)
