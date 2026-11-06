"""Small, bounded JSON-line protocol. Never deserialize Python objects."""
import asyncio
import base64
import binascii
import json
import re

from ..identity import valid_nickname, session_id, contact_from_caps
from ..avatar_data import avatar_hash, valid_avatar_hash, AvatarError

from ..features.file_transfer import FILE_EVENTS, CHUNK_SIZE, MAX_FILE_SIZE, valid_filename
from ..features.social import SOCIAL_EVENTS, valid_reaction
from ..features.cards import CARD_EVENTS, valid_card
from ..features.receipts import READ_EVENTS, READ_BATCH

VERSION = 1
MAX_FRAME = 32768  # Includes the base64 overhead of the authenticated envelope.
MAX_MESSAGE = 2000
MAX_PEERS = 512
ID = re.compile(r'[a-f0-9]{32}\Z')
EVENTS = {'message', 'wizz', 'break_invite', 'break_reply'} | SOCIAL_EVENTS | CARD_EVENTS | READ_EVENTS


class ProtocolError(ValueError):
    pass


def valid_id(value):
    return isinstance(value, str) and ID.fullmatch(value) is not None


def validate(data):
    if not isinstance(data, dict) or type(data.get('v')) is not int or data['v'] != VERSION:
        raise ProtocolError('Version ou format invalide')
    kind = data.get('type')
    if not isinstance(kind, str):
        raise ProtocolError('Type invalide')
    if kind in ('hello', 'presence', 'bye'):
        if not valid_id(data.get('sid')):
            raise ProtocolError('Session invalide')
        if kind != 'bye':
            if set(data) - {'v', 'type', 'sid', 'nick', 'status', 'port', 'avatar', 'caps', 'avatar_hash', 'contact_id', 'crypto'}:
                raise ProtocolError('Champ de présence inconnu')
            if 'contact_id' in data and not valid_id(data['contact_id']):
                raise ProtocolError('Identifiant de contact invalide')
            if not valid_nickname(data.get('nick')) or data.get('status') not in ('online', 'away', 'dnd'):
                raise ProtocolError('Identité invalide')
            avatar = data.get('avatar', -1)
            if type(avatar) is not int or not -1 <= avatar < 24:
                raise ProtocolError('Avatar invalide')
            if not valid_avatar_hash(data.get('avatar_hash', '')):
                raise ProtocolError('Référence d’image invalide')
            if 'avatar_image' in data:
                raise ProtocolError('Image interdite dans une annonce de présence')
            caps = data.get('caps', [])
            if not isinstance(caps, list) or len(caps) > 8 or any(not isinstance(c, str) or len(c) > 24 for c in caps):
                raise ProtocolError('Capacités invalides')
            if sum(c.startswith('c:') for c in caps) > 1 or any(c.startswith('c:') and not contact_from_caps([c]) for c in caps):
                raise ProtocolError('Identifiant de contact invalide')
            port = data.get('port')
            if type(port) is not int or not 1 <= port <= 65535:
                raise ProtocolError('Port invalide')
    elif kind == 'sealed':
        from ..security import unb64, SecurityError, MAX_PLAINTEXT
        if (set(data) != {'v', 'type', 'id', 'from', 'to', 'channel', 'data'}
                or not all(valid_id(data.get(key)) for key in ('id', 'from', 'to'))
                or data.get('channel') not in ('event', 'stream', 'receipt')):
            raise ProtocolError('Enveloppe chiffrée invalide')
        try:
            raw = unb64(data['data'])
            if not 40 < len(raw) <= MAX_PLAINTEXT + 40:
                raise SecurityError('Taille invalide')
        except SecurityError as exc:
            raise ProtocolError('Enveloppe chiffrée invalide') from exc
    elif kind in ('avatar_image', 'avatar_get'):
        allowed = {'v', 'type', 'sid', 'hash', 'data'} if kind == 'avatar_image' else {'v', 'type', 'sid', 'hash', 'from'}
        if set(data) != allowed:
            raise ProtocolError('Trame d’image invalide')
        if not valid_id(data.get('sid')) or not valid_avatar_hash(data.get('hash')) or not data['hash']:
            raise ProtocolError('Référence d’image invalide')
        if kind == 'avatar_get':
            if not valid_id(data.get('from')):
                raise ProtocolError('Session demandeuse invalide')
        else:
            try:
                if avatar_hash(data.get('data')) != data['hash']:
                    raise AvatarError('Image invalide')
            except (AvatarError, TypeError) as exc:
                raise ProtocolError('Miniature d’avatar invalide') from exc
    elif kind in FILE_EVENTS:
        base = {'v', 'type', 'id', 'from', 'to'}
        fields = {'file_offer': {'name', 'size', 'sha256'},
                  'file_chunk': {'request', 'offset', 'data'}}.get(kind, {'request'})
        if set(data) != base | fields:
            raise ProtocolError('Champs du transfert invalides')
        if not valid_id(data.get('id')) or not valid_id(data.get('from')):
            raise ProtocolError('Identifiant de transfert invalide')
        if data.get('to') != 'general' and not valid_id(data.get('to')):
            raise ProtocolError('Destinataire de transfert invalide')
        if kind != 'file_offer' and data['to'] == 'general':
            raise ProtocolError('Données de fichier privées uniquement')
        if kind == 'file_offer':
            if (not valid_filename(data['name']) or type(data['size']) is not int
                    or not 0 <= data['size'] <= MAX_FILE_SIZE
                    or not isinstance(data['sha256'], str)
                    or not re.fullmatch(r'[a-f0-9]{64}', data['sha256'])):
                raise ProtocolError('Métadonnées de fichier invalides (20 Mo maximum)')
        else:
            if not valid_id(data['request']):
                raise ProtocolError('Référence de fichier invalide')
            if kind == 'file_chunk':
                if (type(data['offset']) is not int or not 0 <= data['offset'] <= MAX_FILE_SIZE
                        or not isinstance(data['data'], str) or not 0 < len(data['data']) <= ((CHUNK_SIZE + 2) // 3) * 4):
                    raise ProtocolError('Bloc de fichier invalide')
                try:
                    raw = base64.b64decode(data['data'], validate=True)
                except (binascii.Error, ValueError) as exc:
                    raise ProtocolError('Encodage de fichier invalide') from exc
                if not 0 < len(raw) <= CHUNK_SIZE:
                    raise ProtocolError('Taille du bloc invalide')
    elif kind in EVENTS:
        if not valid_id(data.get('id')) or not valid_id(data.get('from')):
            raise ProtocolError('Identifiant invalide')
        if data.get('to') != 'general' and not valid_id(data.get('to')):
            raise ProtocolError('Destinataire invalide')
        if kind not in {'message'} | SOCIAL_EVENTS and data['to'] == 'general':
            raise ProtocolError('Action privée uniquement')
        if kind in READ_EVENTS:
            references = data.get('messages')
            if (set(data) != {'v', 'type', 'id', 'from', 'to', 'messages'}
                    or not isinstance(references, list) or not 1 <= len(references) <= READ_BATCH
                    or any(not valid_id(mid) for mid in references) or len(set(references)) != len(references)):
                raise ProtocolError('Confirmation de lecture invalide')
        if kind in CARD_EVENTS:
            fields = {'title', 'text', 'emoji'} | ({'request', 'answer'} if kind == 'card_reply' else set())
            if set(data) != {'v', 'type', 'id', 'from', 'to'} | fields:
                raise ProtocolError('Notification invalide')
            if not valid_card({key: data[key] for key in ('title', 'text', 'emoji')}):
                raise ProtocolError('Titre, texte ou animation de notification invalide')
            if kind == 'card_reply' and (not valid_id(data['request']) or data['answer'] not in ('accepted', 'declined', 'custom', 'dismissed')):
                raise ProtocolError('Réponse de notification invalide')
        if kind in SOCIAL_EVENTS:
            fields = {'message', 'emoji', 'seq'} if kind == 'reaction' else {'active', 'seq'}
            if set(data) != {'v', 'type', 'id', 'from', 'to'} | fields:
                raise ProtocolError('Interaction invalide')
            if type(data['seq']) is not int or not 0 <= data['seq'] <= 2 ** 53 - 1:
                raise ProtocolError('Ordre de l’interaction invalide')
            if kind == 'reaction' and (not valid_id(data['message']) or not valid_reaction(data['emoji'])):
                raise ProtocolError('Réaction invalide')
            if kind == 'typing' and type(data['active']) is not bool:
                raise ProtocolError('Indicateur de saisie invalide')
        if kind == 'message':
            value = data.get('text')
            if (not isinstance(value, str) or not value.strip() or len(value) > MAX_MESSAGE
                    or any(0xd800 <= ord(c) <= 0xdfff for c in value)
                    or len(value.encode('utf-8')) > 8000
                    or any(ord(c) < 32 and c not in '\n\t' for c in value)):
                raise ProtocolError('Message invalide (1 à 2000 caractères)')
            if 'reply_to' in data and not valid_id(data['reply_to']):
                raise ProtocolError('Référence de réponse invalide')
        if kind == 'break_reply':
            if not valid_id(data.get('request')) or data.get('answer') not in ('accepted', 'declined', 'dismissed'):
                raise ProtocolError('Réponse invalide')
    elif kind == 'ack':
        if not valid_id(data.get('id')) or type(data.get('ok')) is not bool:
            raise ProtocolError('Accusé invalide')
        if not isinstance(data.get('reason', ''), str) or len(data.get('reason', '')) > 160:
            raise ProtocolError('Erreur invalide')
    elif kind in ('ping', 'pong'):
        pass
    else:
        raise ProtocolError('Type inconnu')
    return data


def encode(data):
    validate(data)
    try:
        raw = json.dumps(data, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode('utf-8') + b'\n'
    except (ValueError, UnicodeError) as exc:
        raise ProtocolError('Encodage invalide') from exc
    if len(raw) > MAX_FRAME:
        raise ProtocolError('Trame trop longue')
    return raw


def decode(raw):
    if len(raw) > MAX_FRAME:
        raise ProtocolError('Trame trop longue')
    try:
        return validate(json.loads(raw))
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise ProtocolError('Trame invalide') from exc


async def read_frame(reader, timeout=12):
    try:
        raw = await asyncio.wait_for(reader.readline(), timeout)
    except (ValueError, asyncio.LimitOverrunError) as exc:
        raise ProtocolError('Trame trop longue') from exc
    if not raw or not raw.endswith(b'\n'):
        raise ConnectionError('Connexion fermée')
    return decode(raw)


def event(kind, sender, target, **payload):
    return {'v': VERSION, 'type': kind, 'id': session_id(), 'from': sender, 'to': target, **payload}


def ack(message_id, ok=True, reason=''):
    return {'v': VERSION, 'type': 'ack', 'id': message_id, 'ok': ok, 'reason': reason}
