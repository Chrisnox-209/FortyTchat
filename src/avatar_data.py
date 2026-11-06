"""Bounded avatar metadata, shared by the GUI-free network core and preferences."""
import base64
import binascii
import hashlib
import re

AVATAR_SIZE = 128
MAX_AVATAR_BYTES = 8192
MAX_AVATAR_TEXT = 4 * ((MAX_AVATAR_BYTES + 2) // 3)
HASH = re.compile(r'[a-f0-9]{64}\Z')


class AvatarError(ValueError):
    pass


def valid_avatar_hash(value):
    return isinstance(value, str) and (value == '' or HASH.fullmatch(value) is not None)


def avatar_bytes(value):
    if not isinstance(value, str) or not value or len(value) > MAX_AVATAR_TEXT:
        raise AvatarError('Miniature d’avatar invalide')
    try:
        raw = base64.b64decode(value, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise AvatarError('Miniature d’avatar invalide') from exc
    if len(raw) > MAX_AVATAR_BYTES or not raw.startswith(b'\xff\xd8') or not raw.endswith(b'\xff\xd9'):
        raise AvatarError('Miniature JPEG invalide')
    return raw


def avatar_hash(value):
    if not isinstance(value, str):
        raise AvatarError('Miniature d’avatar invalide')
    return hashlib.sha256(avatar_bytes(value)).hexdigest() if value else ''


def avatar_frame(sid, value):
    return {'v': 1, 'type': 'avatar_image', 'sid': sid, 'hash': avatar_hash(value), 'data': value}
