"""Display names are not credentials. Session IDs never survive an app restart."""
import re
import secrets
import uuid
import base64

NICK_RE = re.compile(r"[A-Za-z0-9_\-]{3,28}\Z")
CONTACT_RE = re.compile(r'[a-f0-9]{32}\Z')
ADJECTIVES = ('Pixel', 'Silent', 'Cosmic', 'Neon', 'Blue', 'Lucky', 'Turbo', 'Velvet', 'Lunar', 'Happy', 'Cyber', 'Chill')
ANIMALS = ('Fox', 'Panda', 'Otter', 'Cat', 'Penguin', 'Koala', 'Lynx', 'Owl', 'Gecko', 'Manta', 'Tiger', 'Axolotl')


def valid_nickname(value):
    return isinstance(value, str) and NICK_RE.fullmatch(value) is not None


def random_nickname():
    return f'{secrets.choice(ADJECTIVES)}{secrets.choice(ANIMALS)}{secrets.randbelow(10000):04d}'


def session_id():
    return uuid.uuid4().hex


def valid_contact_id(value):
    return isinstance(value, str) and CONTACT_RE.fullmatch(value) is not None


def contact_capability(contact_id):
    """Compact v1 extension, so older clients can still parse the same presence frame."""
    return 'c:' + base64.urlsafe_b64encode(bytes.fromhex(contact_id)).decode('ascii').rstrip('=')


def contact_from_caps(caps):
    for value in caps:
        if not isinstance(value, str) or not value.startswith('c:'):
            continue
        try:
            raw = base64.b64decode(value[2:] + '==', altchars=b'-_', validate=True)
        except (ValueError, TypeError):
            return ''
        if len(raw) == 16 and contact_capability(raw.hex()) == value:
            return raw.hex()
        return ''
    return ''


def unique_nickname(preferred, occupied):
    """Deterministic suffixing converges even when several peers join together."""
    taken = {n.casefold() for n in occupied}
    if preferred.casefold() not in taken:
        return preferred
    for number in range(2, 10002):
        suffix = f'_{number}'
        candidate = preferred[:28-len(suffix)] + suffix
        if candidate.casefold() not in taken:
            return candidate
    return random_nickname()
