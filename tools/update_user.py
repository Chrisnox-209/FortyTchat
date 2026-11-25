#!/usr/bin/env python3
"""Download the official release and replace this user's installed application."""
import fcntl
import os
from pathlib import Path
import subprocess
import sys
import tempfile

SOURCE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOURCE))

from src.features.updates import installed_app
from src.features.versions import version_tuple
from tools.diagnose_session import read_version
from tools.install_user import install_app
from tools.uninstall_user import stop_installed

OFFICIAL_REPOSITORY = 'https://github.com/Chrisnox-209/FortyTchat.git'


class UpdateError(Exception):
    pass


def fetch_release(destination):
    # Ignore local Git URL rewrites, hook templates and protocol overrides.
    env = {key: value for key, value in os.environ.items() if not key.startswith('GIT_')}
    env.update(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull, GIT_TERMINAL_PROMPT='0')
    try:
        subprocess.run([
            'git', '-c', 'protocol.allow=never', '-c', 'protocol.https.allow=always',
            'clone', '--quiet', '--depth=1', '--single-branch', '--branch=main',
            '--no-tags', '--template=', OFFICIAL_REPOSITORY, str(destination),
        ], env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, timeout=90, check=True)
    except FileNotFoundError as exc:
        raise UpdateError('Git est introuvable. Utilise une installation avec Git.') from exc
    except (OSError, subprocess.SubprocessError) as exc:
        raise UpdateError('Téléchargement impossible. Vérifie ta connexion à GitHub, puis réessaie.') from exc


def restart(app):
    process = subprocess.Popen([sys.executable, str(app / 'main.py')], stdin=subprocess.DEVNULL,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               start_new_session=True)
    try:
        if process.wait(timeout=1) != 0:
            raise OSError('Échec du démarrage')
    except subprocess.TimeoutExpired:
        pass  # The graphical application is still running.


def update(app, status):
    if app.resolve() != installed_app().resolve() or not (app / 'main.py').is_file():
        raise UpdateError('La mise à jour automatique requiert la copie installée de FortyTchat.')
    lock_path = app.parent / '.update.lock'
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'r+') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise UpdateError('Une mise à jour est déjà en cours.') from exc

        with tempfile.TemporaryDirectory(prefix='fortytchat-update-') as scratch:
            release = Path(scratch) / 'release'
            status('Téléchargement depuis GitHub…')
            fetch_release(release)
            available = read_version(release / 'src' / '__init__.py')
            current = version_tuple(read_version(app / 'src' / '__init__.py'))
            version = version_tuple(available)
            if current is None or version is None or not (release / 'main.py').is_file():
                raise UpdateError('Impossible de vérifier la version à installer.')
            if version <= current:
                return False
            if (app / 'requirements.txt').read_bytes() != (release / 'requirements.txt').read_bytes():
                raise UpdateError('Cette version demande de nouvelles dépendances. Utilise la commande manuelle.')

            status(f'Installation de FortyTchat {available}…')
            try:
                stop_installed(app)
            except OSError as exc:
                raise UpdateError('FortyTchat n’a pas pu se fermer. Ferme-le depuis son icône puis réessaie.') from exc
            try:
                install_app(release, app.parent)
            except OSError as exc:
                try:
                    restart(app)
                except OSError:
                    raise UpdateError('Installation interrompue. Relance FortyTchat depuis ton lanceur.') from exc
                raise UpdateError('Installation interrompue. La version précédente a été relancée.') from exc
            try:
                restart(app)
            except OSError as exc:
                raise UpdateError('Mise à jour installée. Ouvre FortyTchat depuis ton lanceur.') from exc
            return True


def main():
    from PySide6.QtCore import Qt, QThread, QTimer, Signal
    from PySide6.QtWidgets import QApplication
    from src.ui.theme import apply_theme
    from src.ui.update import UpdateDialog
    from src.config import ConfigStore

    class Worker(QThread):
        progress = Signal(str)
        result = Signal(bool, str)

        def run(self):
            try:
                installed = update(SOURCE, self.progress.emit)
                self.result.emit(installed, 'Tu as déjà la dernière version disponible.')
            except UpdateError as exc:
                self.result.emit(False, str(exc))
            except OSError:
                self.result.emit(False, 'Impossible d’accéder aux fichiers de FortyTchat. Vérifie l’espace disponible.')

    qt = QApplication([sys.argv[0]])
    qt.setApplicationName('FortyTchat')
    apply_theme(qt, ConfigStore().preferences.theme)
    dialog = UpdateDialog()
    dialog.set_busy(True)
    dialog.status.setText('Préparation de la mise à jour…')
    worker = Worker()
    worker.progress.connect(dialog.status.setText, Qt.ConnectionType.QueuedConnection)

    def finish(installed, message):
        dialog.set_busy(False)
        if installed:
            dialog.accept()
        else:
            dialog.status.setText(message)

    worker.result.connect(finish, Qt.ConnectionType.QueuedConnection)
    dialog.show()
    QTimer.singleShot(0, worker.start)
    result = qt.exec()
    worker.wait()
    return result


if __name__ == '__main__':
    sys.exit(main())
