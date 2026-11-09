"""Allowlisted animated cards and explicitly saved personal templates."""
import json
import os
from pathlib import Path
import tempfile
import uuid

CARD_EVENTS = {'card_invite', 'card_reply'}
MAX_TITLE, MAX_TEXT, MAX_TEMPLATES = 80, 400, 12
EMOJIS = (
    ('1f44d', '👍', 'Pouce en haut'), ('1f62d', '😭', 'En larmes'),
    ('1f923', '🤣', 'Mort de rire'), ('1f618', '😘', 'Bisou'),
    ('1f44e', '👎', 'Pouce en bas'), ('1f61b', '😛', 'Tire la langue'),
    ('1f4a9', '💩', 'Caca'), ('274c', '❌', 'Non'), ('2705', '✅', 'Oui'),
    ('1f92f', '🤯', 'Tête qui explose'), ('1f4aa', '💪', 'Motivation'),
)
EMOJI_IDS = frozenset(item[0] for item in EMOJIS)


def card_text(value, maximum, empty=False):
    return (isinstance(value, str) and len(value) <= maximum and (empty or bool(value.strip()))
            and not any(0xD800 <= ord(c) <= 0xDFFF or (ord(c) < 32 and c not in '\n\t') for c in value))


def valid_card(data):
    return (isinstance(data, dict) and set(data) == {'title', 'text', 'emoji'}
            and card_text(data['title'], MAX_TITLE) and '\n' not in data['title']
            and card_text(data['text'], MAX_TEXT, empty=True)
            and isinstance(data['emoji'], str) and data['emoji'] in EMOJI_IDS)



def default_responses():
    return {'buttons': [dict(label='Oui !', answer='accepted', **answer_card('accepted')),
                        dict(label='Non !', answer='declined', **answer_card('declined'))], 'free': True}


def valid_responses(value):
    if (not isinstance(value, dict) or set(value) != {'buttons', 'free'}
            or type(value['free']) is not bool or not isinstance(value['buttons'], list)
            or len(value['buttons']) > 3):
        return False
    labels = set()
    for item in value['buttons']:
        if (not isinstance(item, dict) or set(item) != {'label', 'answer', 'title', 'text', 'emoji'}
                or not card_text(item['label'], 32) or any(c in item['label'] for c in '\n\t')
                or item['answer'] not in ('accepted', 'declined', 'custom')
                or not valid_card({key: item[key] for key in ('title', 'text', 'emoji')})
                or item['label'].strip().casefold() in labels):
            return False
        labels.add(item['label'].strip().casefold())
    return True


def valid_invite(value):
    return (isinstance(value, dict) and not set(value) - {'title', 'text', 'emoji', 'responses', 'close_label'}
            and valid_card({key: value.get(key) for key in ('title', 'text', 'emoji')})
            and ('responses' not in value or valid_responses(value['responses']))
            and ('close_label' not in value or 'responses' in value and valid_close_label(value['close_label'])))


def valid_close_label(value):
    return card_text(value, 32) and not any(c in value for c in '\n\t')


def allowed_reply(policy, frame):
    choice = frame.get('choice')
    if choice is None:
        return policy['free'] and frame['answer'] == 'custom'
    if type(choice) is not int or not 0 <= choice < len(policy['buttons']):
        return False
    selected = policy['buttons'][choice]
    return all(frame.get(key) == selected[key] for key in ('answer', 'title', 'text', 'emoji'))

def answer_card(answer):
    return {'title': 'yessss !!' if answer == 'accepted' else 'Non je bosse !!' if answer == 'declined' else 'Une prochaine fois !',
            'text': '', 'emoji': '1f44d' if answer == 'accepted' else '1f44e' if answer == 'declined' else '274c'}


class CardTemplates:
    def __init__(self, root):
        self.path = Path(root) / 'notification_templates.json'
        self.labels_path = Path(root) / 'notification_close_labels.json'
        self.items, self.error = [], ''
        if self.path.exists():
            try:
                if self.path.stat().st_size > 65536:
                    raise ValueError('Modèles trop volumineux')
                data = json.loads(self.path.read_text(encoding='utf-8'))
                if not isinstance(data, list) or len(data) > MAX_TEMPLATES:
                    raise ValueError('Modèles invalides')
                ids = set()
                for item in data:
                    if (not isinstance(item, dict) or set(item) - {'id', 'kind', 'title', 'text', 'emoji', 'responses'}
                            or not isinstance(item['id'], str) or len(item['id']) != 32
                            or any(c not in '0123456789abcdef' for c in item['id'])
                            or item['id'] in ids or item['kind'] not in ('invite', 'reply')
                            or not (valid_invite if item['kind'] == 'invite' else valid_card)({key: val for key, val in item.items() if key not in ('id', 'kind')})):
                        raise ValueError('Modèle invalide')
                    ids.add(item['id'])
                self.items = data
            except (OSError, ValueError, TypeError, KeyError, RecursionError):
                self.error = 'Les modèles enregistrés sont illisibles. Tu peux créer une nouvelle notification.'
        # Keep the existing template file readable by all previous releases.
        # New display labels live separately; damaged labels do not lose models.
        if self.labels_path.exists():
            try:
                if self.labels_path.stat().st_size > 4096:
                    raise ValueError('Libellés trop volumineux')
                labels = json.loads(self.labels_path.read_text(encoding='utf-8'))
                if (not isinstance(labels, dict) or len(labels) > MAX_TEMPLATES
                        or any(not isinstance(key, str) or len(key) != 32 or any(c not in '0123456789abcdef' for c in key)
                               or not valid_close_label(value) for key, value in labels.items())):
                    raise ValueError('Libellés invalides')
                for item in self.items:
                    if item['kind'] == 'invite' and 'responses' in item and item['id'] in labels:
                        item['close_label'] = labels[item['id']]
            except (OSError, ValueError, TypeError, RecursionError):
                if not self.error:self.error = 'Les libellés de fermeture enregistrés sont illisibles ; « Fermer » sera utilisé.'

    def _write(self, items):
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        encoded = json.dumps([{k:v for k,v in item.items() if k != 'close_label'} for item in items], ensure_ascii=False, separators=(',', ':'))
        if len(encoded.encode('utf-8')) > 65536:
            raise ValueError('Modèles trop volumineux : raccourcis les réponses ou supprime un modèle.')
        fd, name = tempfile.mkstemp(prefix='.notifications-', dir=self.path.parent)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, self.path)
        finally:
            if os.path.exists(name): os.unlink(name)
        labels = {item['id']:item['close_label'] for item in items if 'close_label' in item}
        if labels or self.labels_path.exists():
            fd, name = tempfile.mkstemp(prefix='.notification-labels-', dir=self.path.parent)
            try:
                with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                    json.dump(labels, stream, ensure_ascii=False, separators=(',', ':'))
                    stream.flush();os.fsync(stream.fileno())
                os.replace(name, self.labels_path)
            finally:
                if os.path.exists(name):os.unlink(name)
        self.items, self.error = items, ''

    def save(self, payload, kind, template_id=''):
        if kind not in ('invite', 'reply') or not (valid_invite if kind == 'invite' else valid_card)(payload):
            raise ValueError('Complète le titre, le texte et l’emoji.')
        items = [dict(item) for item in self.items]
        existing = next((i for i, item in enumerate(items) if item['id'] == template_id and item['kind'] == kind), None)
        item = {'id': template_id if existing is not None else uuid.uuid4().hex, 'kind': kind, **payload}
        if existing is not None:
            items[existing] = item
        else:
            if len(items) >= MAX_TEMPLATES:
                raise ValueError('Douze modèles maximum. Modifie ou supprime un modèle existant.')
            items.append(item)
        self._write(items)
        return item['id']

    def remove(self, template_id):
        self._write([item for item in self.items if item['id'] != template_id])
