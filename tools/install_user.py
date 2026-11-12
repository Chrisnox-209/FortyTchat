#!/usr/bin/env python3
"""User-scoped installer. Copies only application files; never modifies the system."""
import argparse
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import time

SOURCE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOURCE))
from src.autostart import Autostart, desktop_quote, ENTRY_NAME
from src.config import ConfigStore, config_home
from src.instance import SingleInstance
from src import __version__


def atomic_write(path, text, mode=0o600):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.fortytchat-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            stream.write(text)
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def install(args):
    value = os.environ.get('XDG_DATA_HOME', '')
    data_home = Path(value) if value and Path(value).is_absolute() else Path.home() / '.local' / 'share'
    destination = data_home / 'fortytchat'
    destination.mkdir(parents=True, exist_ok=True, mode=0o700)
    interpreter = Path(sys.executable).absolute()
    if args.use_current_python:
        subprocess.run([str(interpreter), '-c',
                        'import sys, PySide6, PySide6.QtWidgets, nacl; '
                        'version=tuple(map(int, PySide6.__version__.split(".")[:2])); '
                        'sys.exit("Python 3.14 nécessite PySide6 6.12 ou plus récent. Relancez install.sh sans --use-current-python.") '
                        'if sys.version_info >= (3,14) and version < (6,12) else None'], check=True)
    else:
        venv = destination / 'venv'
        if not (venv / 'bin' / 'python').exists():
            print('Création de l’environnement Python personnel…', flush=True)
            subprocess.run([str(interpreter), '-m', 'venv', str(venv)], check=True)
        interpreter = venv / 'bin' / 'python'
        print('Installation des dépendances dans le dossier utilisateur…', flush=True)
        subprocess.run([str(interpreter), '-m', 'pip', 'install', '--disable-pip-version-check',
                        '-r', str(SOURCE / 'requirements.txt')], check=True)
    app_dir = destination / 'app'
    if SOURCE != app_dir:
        stage = Path(tempfile.mkdtemp(prefix='.app-', dir=destination))
        backup = destination / '.previous-app'
        try:
            # Explicit allowlist excludes working copies, credentials and personal files.
            for name in ('src', 'assets', 'docs', 'tools', 'tests'):
                source_dir = SOURCE / name
                if source_dir.is_dir():
                    shutil.copytree(source_dir, stage / name,
                                    ignore=shutil.ignore_patterns('__pycache__', '.pytest_cache'), symlinks=False)
            for name in ('main.py', 'install.sh', 'uninstall.sh', 'diagnostic-session.sh', 'requirements.txt', 'requirements-dev.txt', 'README.md', 'network.example.json'):
                source_file = SOURCE / name
                if source_file.is_file():
                    shutil.copy2(source_file, stage / name)
            deployment = SOURCE / 'network.json'
            if not deployment.is_file():
                deployment = app_dir / 'network.json'
            if deployment.is_file():
                shutil.copy2(deployment, stage / 'network.json')
            elif (stage / 'network.example.json').is_file():
                shutil.copy2(stage / 'network.example.json', stage / 'network.json')
            if backup.exists():
                shutil.rmtree(backup)
            if app_dir.exists():
                app_dir.rename(backup)
            try:
                stage.rename(app_dir)
            except OSError:
                if backup.exists():
                    backup.rename(app_dir)
                raise
            if backup.exists():
                shutil.rmtree(backup)
        finally:
            if stage.exists():
                shutil.rmtree(stage)
    config = ConfigStore()
    autostart = Autostart(app_dir, interpreter=str(interpreter))
    autostart.initialize(config)
    config.save()
    bin_dir = Path(args.bin_dir).absolute() if args.bin_dir else Path.home() / '.local' / 'bin'
    launcher = bin_dir / 'fortytchat'
    command = [str(interpreter), str(app_dir / 'main.py')]
    atomic_write(launcher, '#!/bin/sh\nexec ' + ' '.join(map(shlex.quote, command)) + ' "$@"\n', 0o755)
    entry = '\n'.join([
        '[Desktop Entry]', 'Type=Application', 'Version=1.0', 'Name=FortyTchat',
        'Comment=Le cluster, en direct. Messagerie locale et éphémère.',
        'Exec=' + ' '.join(map(desktop_quote, command)),
        'Icon=' + str(app_dir / 'assets' / 'icons' / 'fortytchat.svg'),
        'Terminal=false', 'Categories=Network;InstantMessaging;',
        'StartupNotify=false', 'StartupWMClass=FortyTchat', '',
    ])
    atomic_write(data_home / 'applications' / ENTRY_NAME, entry, 0o644)
    print(f'FortyTchat {__version__} installé : {app_dir}')
    print(f'Lanceur : {launcher}')
    print('Démarrage de session : ' + ('activé, lancement réduit.' if autostart.enabled() else 'désactivé, choix précédent conservé.'))
    if not args.no_launch:
        guard = SingleInstance(lambda: None)
        try:
            already_open = not guard.acquire(activate=False)
        finally:
            guard.close()
        if already_open:
            print('FortyTchat est déjà ouvert. La mise à jour est installée ; quittez complètement l’application puis relancez le lanceur pour charger la nouvelle version.')
            return 0
        process = subprocess.Popen(command + ['--background'], stdin=subprocess.DEVNULL,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                   start_new_session=True)
        time.sleep(1)
        if process.poll() not in (None, 0):
            print(f'Le lancement graphique a échoué. Essayez {shlex.quote(str(launcher))} dans un terminal.', file=sys.stderr)
            return 1
        print('FortyTchat démarre réduit : icône dans la barre si disponible, sinon dans le dock.')
    print('L’ordre des icônes (notamment à côté de Discord) dépend de GNOME et de ses extensions.')
    return 0


def main():
    parser = argparse.ArgumentParser(description='Installation utilisateur de FortyTchat, sans sudo.')
    parser.add_argument('--no-launch', action='store_true', help='Installer sans démarrer immédiatement')
    parser.add_argument('--use-current-python', action='store_true', help='Utiliser ce Python si PySide6 y est déjà installé, sans téléchargement')
    parser.add_argument('--bin-dir', help='Dossier du lanceur (par défaut ~/.local/bin)')
    args = parser.parse_args()
    if sys.version_info < (3, 10):
        parser.error('Python 3.10 ou plus récent est nécessaire.')
    try:
        return install(args)
    except (OSError, subprocess.CalledProcessError, ValueError):
        print('Installation incomplète. Vérifiez l’espace, l’accès réseau et la disponibilité de python3 -m venv.\n'
              'Aucun sudo ni changement système effectué. Si PySide6 est déjà installé, utilisez --use-current-python.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
