"""Manage exactly one XDG desktop entry, never a system setting."""
import configparser
import os
from pathlib import Path
import sys
import tempfile

from .config import config_home

ENTRY_NAME = 'fr.fortytchat.FortyTchat.desktop'


def desktop_quote(value):
    # Desktop Entry string unescaping happens before Exec argument unquoting.
    if '\n' in value or '\r' in value or '\x00' in value:
        raise ValueError('Chemin invalide')
    value = value.replace('%', '%%')
    value = ''.join('\\' + c if c in '\\"`$' else c for c in value)
    return '"' + value.replace('\\', '\\\\') + '"'


class Autostart:
    def __init__(self, project_root, config_root=None, interpreter=None):
        self.project_root = Path(project_root).absolute()
        self.path = (Path(config_root) if config_root is not None else config_home()) / 'autostart' / ENTRY_NAME
        # Do not resolve symlinks: a venv's python must remain the venv interpreter.
        self.interpreter = os.path.abspath(interpreter or sys.executable)

    def enabled(self):
        try:
            parser = configparser.ConfigParser(interpolation=None, strict=False)
            parser.read(self.path, encoding='utf-8')
            entry = parser['Desktop Entry']
            return (entry.get('Type') == 'Application' and bool(entry.get('Exec'))
                    and entry.get('Hidden', 'false').lower() != 'true'
                    and entry.get('X-GNOME-Autostart-enabled', 'true').lower() != 'false')
        except (OSError, KeyError, configparser.Error):
            return False

    def set_enabled(self, enabled):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        command = ' '.join(desktop_quote(s) for s in (self.interpreter, str(self.project_root / 'main.py'), '--background'))
        content = '\n'.join([
            '[Desktop Entry]', 'Type=Application', 'Version=1.0', 'Name=FortyTchat',
            'Comment=Messagerie éphémère du cluster', f'Exec={command}',
            'Terminal=false', f'Hidden={str(not enabled).lower()}',
            f'X-GNOME-Autostart-enabled={str(enabled).lower()}',
            'X-GNOME-Autostart-Delay=5', 'Categories=Network;InstantMessaging;', '',
        ])
        fd, temp = tempfile.mkstemp(prefix='.fortytchat-', dir=self.path.parent)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                stream.write(content)
            os.replace(temp, self.path)
        finally:
            if os.path.exists(temp):
                os.unlink(temp)

    def initialize(self, store):
        """Only a genuine first run can enable. External disable/removal wins later."""
        if store.first_run and not self.path.exists():
            self.set_enabled(store.preferences.autostart)
        elif store.preferences.autostart and self.enabled():
            self.set_enabled(True)  # Refresh absolute paths after relocating the app.
        store.preferences.autostart = self.enabled()
