#!/usr/bin/env python3
"""Remove this user's installed FortyTchat only; preserve profile unless --purge."""
import argparse
import os
from pathlib import Path
import shutil
import signal
import shlex
import sys
import time

SOURCE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOURCE))
from src.autostart import ENTRY_NAME
from src.config import ConfigStore, config_home


def data_home():
    value = os.environ.get('XDG_DATA_HOME', '')
    return Path(value) if value and Path(value).is_absolute() else Path.home() / '.local/share'


def installed_processes(app_dir):
    """Match an exact script path and owner; never use a broad process-name kill."""
    target = (app_dir / 'main.py').resolve()
    found = []
    for proc in Path('/proc').iterdir():
        if not proc.name.isdigit() or int(proc.name) == os.getpid():
            continue
        try:
            if proc.stat().st_uid != os.getuid():
                continue
            argv = [s.decode(errors='surrogateescape') for s in (proc / 'cmdline').read_bytes().split(b'\0') if s]
            executable = Path(os.readlink(proc / 'exe')).name
            if not executable.startswith('python') or len(argv) < 2 or argv[1] in ('-c', '-m'):
                continue
            script = next((value for value in argv[1:3] if not value.startswith('-')), '')
            path = Path(script)
            if not path.is_absolute():
                path = Path(os.readlink(proc / 'cwd')) / path
            if path.resolve() != target:
                continue
            start = (proc / 'stat').read_text().rsplit(')', 1)[1].split()[19]
            found.append((int(proc.name), start))
        except (OSError, ValueError, IndexError):
            continue
    return found


def same_process(pid, start):
    try:
        fields = Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()
        return fields[0] != 'Z' and fields[19] == start
    except (OSError, IndexError):
        return False


def stop_installed(app_dir):
    processes = installed_processes(app_dir)
    for pid, start in processes:
        if same_process(pid, start):
            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
    end = time.monotonic() + 10
    while any(same_process(pid, start) for pid, start in processes):
        if time.monotonic() >= end:
            raise OSError('Fermez complètement FortyTchat, puis relancez la désinstallation.')
        time.sleep(0.1)
    return len(processes)


def remove_tree(path):
    if path.is_symlink():
        path.unlink()
    elif path.exists():
        shutil.rmtree(path)


def uninstall(args):
    data = data_home()
    root = data / 'fortytchat'
    # Gracefully stop installed processes before removing their files. --purge is explicit.
    stopped = stop_installed(root / 'app')
    (config_home() / 'autostart' / ENTRY_NAME).unlink(missing_ok=True)
    (data / 'applications' / ENTRY_NAME).unlink(missing_ok=True)
    launcher = (Path(args.bin_dir).absolute() if args.bin_dir else Path.home() / '.local/bin') / 'fortytchat'
    if launcher.exists() or launcher.is_symlink():
        # Avoid deleting an unrelated executable placed under the same name.
        try:
            tokens = shlex.split(launcher.read_text(encoding='utf-8'), comments=True)
            own = len(tokens) >= 3 and tokens[0] == 'exec' and Path(tokens[2]) == root / 'app' / 'main.py'
        except (OSError, UnicodeError, ValueError):
            own = False
        if own:
            launcher.unlink()
    remove_tree(root)
    prefs = config_home() / 'fortytchat'
    if args.purge:
        remove_tree(prefs)
        remove_tree(config_home() / 'fortytchat-dev')
    elif (prefs / 'config.json').is_file():
        store = ConfigStore(prefs)
        store.preferences.autostart = False
        store.save()
    print(f'FortyTchat désinstallé. {stopped} instance(s) installée(s) arrêtée(s).')
    print('Profil, amis et photo supprimés.' if args.purge else 'Profil, amis et photo conservés. Le démarrage automatique est supprimé.')
    return 0


def main():
    parser = argparse.ArgumentParser(description='Désinstaller FortyTchat de cet espace utilisateur, sans sudo.')
    parser.add_argument('--purge', action='store_true', help='Supprimer aussi le profil, les amis, la photo et les profils de développement')
    parser.add_argument('--bin-dir', help='Dossier du lanceur personnalisé, si utilisé à l’installation')
    args = parser.parse_args()
    try:
        return uninstall(args)
    except OSError as exc:
        print(f'Désinstallation interrompue : {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
