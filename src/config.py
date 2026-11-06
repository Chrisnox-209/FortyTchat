"""Only allowlisted preferences are serialized. There is no message persistence API."""
from dataclasses import asdict, dataclass, field, fields
import json
import os
from pathlib import Path
import tempfile
import threading

from .identity import random_nickname, valid_nickname, session_id, valid_contact_id
from .avatar_data import avatar_hash, AvatarError


def config_home():
    value = os.environ.get('XDG_CONFIG_HOME', '')
    return Path(value) if value and Path(value).is_absolute() else Path.home() / '.config'


@dataclass
class Preferences:
    nickname: str = ''
    autostart: bool = True
    wizz_enabled: bool = True
    dnd: bool = False
    theme: str = 'dark'
    avatar: int = -1
    avatar_image: str = ''
    last_wizz_at: float = 0.0
    contact_id: str = ''
    friends: dict = field(default_factory=dict)


class ConfigStore:
    def __init__(self, root=None):
        self.lock = threading.RLock()
        self.root = Path(root) if root is not None else config_home() / 'fortytchat'
        self.path = self.root / 'config.json'
        self.first_run = not self.path.exists()
        self.error = ''
        self.preferences = self.load()

    def load(self):
        defaults = Preferences(nickname=random_nickname(), contact_id=session_id())
        if self.first_run:
            return defaults
        try:
            if self.path.stat().st_size > 32768:
                raise ValueError('Configuration trop volumineuse')
            data = json.loads(self.path.read_text(encoding='utf-8'))
            if not isinstance(data, dict):
                raise ValueError('Format incorrect')
            values = {f.name: data[f.name] for f in fields(Preferences) if f.name in data}
            if not valid_nickname(values.get('nickname', '')):
                values['nickname'] = defaults.nickname
            if not valid_contact_id(values.get('contact_id')):
                values['contact_id'] = defaults.contact_id
            contacts = values.get('friends', {})
            values['friends'] = {}
            if isinstance(contacts, dict):
                for contact_id, nickname in contacts.items():
                    if (valid_contact_id(contact_id) and contact_id != values['contact_id']
                            and valid_nickname(nickname) and len(values['friends']) < 256):
                        values['friends'][contact_id] = nickname
            for key in ('autostart', 'wizz_enabled', 'dnd'):
                if type(values.get(key)) is not bool:
                    values[key] = False if key == 'autostart' else getattr(defaults, key)
            if values.get('theme') not in ('dark', 'light'):
                values['theme'] = 'dark'
            if type(values.get('avatar', -1)) is not int or not -1 <= values.get('avatar', -1) < 24:
                values['avatar'] = -1
            try:
                photo = values.get('avatar_image', '')
                if not isinstance(photo, str):
                    raise AvatarError('Image invalide')
                avatar_hash(photo)
            except AvatarError:
                values['avatar_image'] = ''
            stamp = values.get('last_wizz_at', 0)
            if type(stamp) not in (int, float) or not 0 <= stamp < 1e12:
                values['last_wizz_at'] = 0.0
            return Preferences(**values)
        except (OSError, ValueError, TypeError):
            # A damaged existing file never counts as a fresh installation.
            defaults.autostart = False
            self.error = 'Configuration illisible : préférences réinitialisées, démarrage automatique non réactivé.'
            return defaults

    def reserve_wizz(self, stamp):
        with self.lock:
            self.preferences.last_wizz_at = stamp
            self.save()

    def save(self):
        with self.lock:
            self._save()

    def _save(self):
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd, temp = tempfile.mkstemp(prefix='.preferences-', dir=self.root)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                json.dump(asdict(self.preferences), stream, ensure_ascii=False, indent=2, allow_nan=False)
                stream.write('\n')
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp, self.path)
        finally:
            if os.path.exists(temp):
                os.unlink(temp)
