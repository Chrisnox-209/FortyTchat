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


def answer_card(answer):
    return {'title': 'yessss !!' if answer == 'accepted' else 'Non je bosse !!' if answer == 'declined' else 'Une prochaine fois !',
            'text': '', 'emoji': '1f44d' if answer == 'accepted' else '1f44e' if answer == 'declined' else '274c'}


class CardTemplates:
    def __init__(self, root):
        self.path = Path(root) / 'notification_templates.json'
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
                    if (not isinstance(item, dict) or set(item) != {'id', 'kind', 'title', 'text', 'emoji'}
                            or not isinstance(item['id'], str) or len(item['id']) != 32
                            or any(c not in '0123456789abcdef' for c in item['id'])
                            or item['id'] in ids or item['kind'] not in ('invite', 'reply')
                            or not valid_card({key: item[key] for key in ('title', 'text', 'emoji')})):
                        raise ValueError('Modèle invalide')
                    ids.add(item['id'])
                self.items = data
            except (OSError, ValueError, TypeError, RecursionError):
                self.error = 'Les modèles enregistrés sont illisibles. Tu peux créer une nouvelle notification.'

    def _write(self, items):
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd, name = tempfile.mkstemp(prefix='.notifications-', dir=self.path.parent)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                json.dump(items, stream, ensure_ascii=False, separators=(',', ':'))
            os.replace(name, self.path)
        finally:
            if os.path.exists(name): os.unlink(name)
        self.items, self.error = items, ''

    def save(self, payload, kind, template_id=''):
        if not valid_card(payload) or kind not in ('invite', 'reply'):
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
