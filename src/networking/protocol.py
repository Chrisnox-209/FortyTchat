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
from ..features.cards import CARD_EVENTS, valid_card, valid_responses
from ..features.receipts import READ_EVENTS, READ_BATCH
from ..features.collaboration import COLLAB_EVENTS, GROUP_EVENTS, CHANGE_EVENTS, POLL_EVENTS, MAX_MEMBERS, MAX_VOTERS, plain

VERSION = 1
MAX_FRAME = 32768  # Includes the base64 overhead of the authenticated envelope.
MAX_MESSAGE = 2000
MAX_PEERS = 512
ID = re.compile(r'[a-f0-9]{32}\Z')
EVENTS = {'client_info', 'message', 'wizz', 'break_invite', 'break_reply'} | SOCIAL_EVENTS | CARD_EVENTS | READ_EVENTS | COLLAB_EVENTS


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
        if kind == 'file_offer':
            fields |= set(data) & {'group', 'preview'}
        if set(data) != base | fields:
            raise ProtocolError('Champs du transfert invalides')
        if not valid_id(data.get('id')) or not valid_id(data.get('from')):
            raise ProtocolError('Identifiant de transfert invalide')
        if data.get('to') != 'general' and not valid_id(data.get('to')):
            raise ProtocolError('Destinataire de transfert invalide')
        if kind != 'file_offer' and data['to'] == 'general':
            raise ProtocolError('Données de fichier privées uniquement')
        if kind == 'file_offer':
            validate_context(data)
            if 'preview' in data:
                try:
                    if not data['preview']:
                        raise AvatarError('Aperçu vide')
                    avatar_hash(data['preview'])
                except (AvatarError, TypeError) as exc:
                    raise ProtocolError('Aperçu invalide') from exc
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
        if ('responses' in data and kind != 'card_invite') or ('choice' in data and kind != 'card_reply') or ('close_label' in data and kind != 'card_invite'):
            raise ProtocolError('Choix de réponse réservé aux notifications personnalisées')
        if not valid_id(data.get('id')) or not valid_id(data.get('from')):
            raise ProtocolError('Identifiant invalide')
        if data.get('to') != 'general' and not valid_id(data.get('to')):
            raise ProtocolError('Destinataire invalide')
        if kind not in {'message'} | SOCIAL_EVENTS | CHANGE_EVENTS | POLL_EVENTS and data['to'] == 'general':
            raise ProtocolError('Action privée uniquement')
        validate_context(data)
        if kind in COLLAB_EVENTS:
            base = {'v', 'type', 'id', 'from', 'to'}
            fields = {'group_invite': ({'room','name','members'} if 'members' in data else {'room', 'profile'}), 'group_state': {'room', 'profile'},
                      'group_update': {'room', 'revision', 'changes'}, 'group_removed': {'room', 'revision'}, 'group_sync': {'rooms'},
                      'group_popup_request': {'room', 'batch'}, 'group_popup_grant': {'room', 'request', 'permit'}, 'group_join': {'room'}, 'group_leave': {'room'},
                      'message_edit': {'message', 'seq', 'text'}, 'message_delete': {'message', 'seq'},
                      'poll_create': {'question', 'options', 'audience'}, 'poll_vote': {'poll', 'option', 'seq'}}[kind]
            if kind not in GROUP_EVENTS:
                fields |= set(data) & {'group'}
            if kind == 'poll_create': fields |= set(data) & {'multiple'}
            if kind == 'poll_vote' and 'options' in data: fields=(fields-{'option'})|{'options'}
            if kind == 'message_edit': fields |= set(data) & {'mentions'}
            if set(data) != base | fields:
                raise ProtocolError('Champs de collaboration invalides')
            if kind in GROUP_EVENTS:
                from ..features.group_profile import validate_profile
                from ..security import SecurityError
                if kind == 'group_sync':
                    if not isinstance(data['rooms'], list) or not 1 <= len(data['rooms']) <= 32 or any(not valid_id(g) for g in data['rooms']):
                        raise ProtocolError('Synchronisation de groupe invalide')
                elif not valid_id(data['room']):
                    raise ProtocolError('Groupe invalide')
                if kind=='group_invite' and 'members' in data:
                    members=data['members']
                    if not plain(data['name'],64) or not isinstance(members,list) or not 2<=len(members)<=MAX_MEMBERS or any(not valid_id(cid) for cid in members) or len(set(members))!=len(members):raise ProtocolError('Invitation ancienne invalide')
                if kind in ('group_invite', 'group_state') and 'profile' in data:
                    try:
                        if validate_profile(data['profile'])['id'] != data['room']:
                            raise SecurityError('Groupe incohérent')
                    except (ValueError, TypeError, KeyError) as exc:
                        raise ProtocolError('Profil de groupe invalide') from exc
                if kind == 'group_removed' and (type(data['revision']) is not int or not 1 <= data['revision'] < 2**53):
                    raise ProtocolError('Révision invalide')
                if kind == 'group_update':
                    changes = data['changes']
                    if (type(data['revision']) is not int or not 1 <= data['revision'] < 2**53
                            or not isinstance(changes, dict) or not 1 <= len(changes) <= 1
                            or set(changes) - {'name', 'photo', 'avatar', 'add', 'remove', 'promote', 'demote', 'popup_interval'}):
                        raise ProtocolError('Modification de groupe invalide')
                    key, value = next(iter(changes.items()))
                    if key == 'popup_interval':
                        from ..features.group_profile import valid_popup_interval
                        if not valid_popup_interval(value): raise ProtocolError('Délai des popups invalide (1 à 1440 minutes)')
                    if key == 'name' and (not plain(value, 64) or any(c in value for c in '\n\t')):
                        raise ProtocolError('Nom du groupe invalide')
                    if key in ('add', 'remove', 'promote', 'demote') and not valid_id(value):
                        raise ProtocolError('Membre invalide')
                    if key == 'avatar' and (type(value) is not int or not -1 <= value < 24):
                        raise ProtocolError('Avatar invalide')
                    if key == 'photo':
                        try: avatar_hash(value)
                        except (ValueError, TypeError) as exc: raise ProtocolError('Photo invalide') from exc
                if kind == 'group_popup_request' and not valid_id(data['batch']):
                    raise ProtocolError('Popup de groupe invalide')
                if kind == 'group_popup_grant':
                    permit = data['permit']
                    if not valid_id(data['request']) or not isinstance(permit, dict) or (permit and len(permit) not in (5,7)):
                        raise ProtocolError('Autorisation invalide')
            if kind in CHANGE_EVENTS and not valid_id(data['message']):
                raise ProtocolError('Message à modifier invalide')
            if kind in CHANGE_EVENTS | {'poll_vote'} and (type(data['seq']) is not int or not 0 <= data['seq'] < 2 ** 53):
                raise ProtocolError('Ordre invalide')
            if kind == 'message_edit' and (not plain(data['text'], MAX_MESSAGE) or len(data['text'].encode('utf-8')) > 8000):
                raise ProtocolError('Message modifié invalide')
            if kind == 'poll_create':
                if 'multiple' in data and type(data['multiple']) is not bool:raise ProtocolError('Mode du sondage invalide')
                options, audience = data['options'], data['audience']
                if (not plain(data['question'], 200) or not isinstance(options, list) or not 2 <= len(options) <= 8
                        or any(not plain(option, 80) or any(c in option for c in '\n\t') for option in options)
                        or len({o.strip().casefold() for o in options}) != len(options)
                        or not isinstance(audience, list) or not 1 <= len(audience) <= MAX_VOTERS
                        or any(not valid_id(sid) for sid in audience) or len(set(audience)) != len(audience)
                        or data['from'] not in audience):
                    raise ProtocolError('Sondage invalide (2 à 8 choix, 256 participants maximum)')
            if kind == 'poll_vote':
                if not valid_id(data['poll']): raise ProtocolError('Sondage invalide')
                if 'options' in data:
                    choices=data['options']
                    if not isinstance(choices,list) or len(choices)>8 or any(type(i) is not int or not 0<=i<8 for i in choices) or len(set(choices))!=len(choices):raise ProtocolError('Vote invalide')
                elif type(data['option']) is not int or not -1<=data['option']<8:raise ProtocolError('Vote invalide')
        if kind=='client_info':
            from ..features.versions import version_tuple
            features=data.get('features')
            if (set(data)-{'locked'}!={'v','type','id','from','to','version','features'} or 'locked' in data and type(data['locked']) is not bool or not version_tuple(data.get('version'))
                    or not isinstance(features,list) or len(features)>16 or any(not isinstance(f,str) or len(f)>32 for f in features)):
                raise ProtocolError('Version annoncée invalide')
        if kind in READ_EVENTS:
            references = data.get('messages')
            if (set(data) != {'v', 'type', 'id', 'from', 'to', 'messages'}
                    or not isinstance(references, list) or not 1 <= len(references) <= READ_BATCH
                    or any(not valid_id(mid) for mid in references) or len(set(references)) != len(references)):
                raise ProtocolError('Confirmation de lecture invalide')
        if kind in CARD_EVENTS:
            fields = {'title', 'text', 'emoji'} | ({'request', 'answer'} if kind == 'card_reply' else set())
            if kind == 'card_invite' and 'group' in data:
                fields |= {'group', 'permit'}
                if not isinstance(data.get('permit'), dict) or len(data['permit']) not in (5,7):
                    raise ProtocolError('Autorisation de popup requise')
            if kind == 'card_invite' and 'responses' in data:
                fields.add('responses')
                if not valid_responses(data['responses']):
                    raise ProtocolError('Choix de réponse invalides')
            if kind == 'card_invite' and 'close_label' in data:
                from ..features.cards import valid_close_label
                fields.add('close_label')
                if 'responses' not in data or not valid_close_label(data['close_label']):
                    raise ProtocolError('Bouton de fermeture invalide')
            if kind == 'card_reply' and 'choice' in data:
                fields.add('choice')
                if type(data['choice']) is not int or not 0 <= data['choice'] < 3:
                    raise ProtocolError('Choix de réponse invalide')
            if set(data) != {'v', 'type', 'id', 'from', 'to'} | fields:
                raise ProtocolError('Notification invalide')
            if not valid_card({key: data[key] for key in ('title', 'text', 'emoji')}):
                raise ProtocolError('Titre, texte ou animation de notification invalide')
            if kind == 'card_reply' and (not valid_id(data['request']) or data['answer'] not in ('accepted', 'declined', 'custom', 'dismissed')):
                raise ProtocolError('Réponse de notification invalide')
        if kind in SOCIAL_EVENTS:
            fields = {'message', 'emoji', 'seq'} if kind == 'reaction' else {'active', 'seq'}
            fields |= set(data) & {'group'}
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
        if 'mentions' in data:
            mentions = data['mentions']
            if kind not in ('message', 'message_edit') or not isinstance(mentions, list) or len(mentions) > 32 or any(not valid_id(cid) for cid in mentions) or len(set(mentions)) != len(mentions):
                raise ProtocolError('Mentions invalides')
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


def validate_context(data):
    if 'group' in data and (not valid_id(data['group']) or data.get('to') == 'general'
                            or data['type'] not in {'message', 'reaction', 'typing', 'file_offer', 'card_invite'} | CHANGE_EVENTS | POLL_EVENTS):
        raise ProtocolError('Contexte de groupe invalide')


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
