#!/usr/bin/env python3
"""FortyTchat: python3 main.py, no sudo and no manual network setup."""
import argparse
import json
import os
from pathlib import Path
import re
import signal
import sys
from src import __version__


def main():
    parser = argparse.ArgumentParser(description='FortyTchat — messagerie locale et éphémère pour le cluster.')
    parser.add_argument('--background', action='store_true', help='Démarrer dans la barre si elle est disponible')
    parser.add_argument('--dev', metavar='PROFIL', help='Profil isolé, port TCP dynamique, aucun autostart modifié')
    parser.add_argument('--insecure-local-relay', type=int, metavar='PORT', help='Relais de test sur 127.0.0.1, exige --dev')
    diagnostics = parser.add_mutually_exclusive_group()
    diagnostics.add_argument('--diagnose', action='store_true', help='Inspecter les interfaces et tester les ports localement, sans GUI')
    diagnostics.add_argument('--diagnose-lock', type=int, nargs='?', const=60, metavar='SECONDES',
                             help='Observer le verrouillage en lecture seule, sans tchat (5 à 120 s ; défaut 60)')
    parser.add_argument('--version', action='version', version=f'FortyTchat {__version__}')
    args = parser.parse_args()
    if args.diagnose_lock is not None and not 5 <= args.diagnose_lock <= 120:
        parser.error('--diagnose-lock : durée comprise entre 5 et 120 secondes')
    if args.dev and not re.fullmatch(r'[A-Za-z0-9_-]{1,32}', args.dev):
        parser.error('Profil : 1 à 32 lettres, chiffres, tirets ou underscores')
    if args.insecure_local_relay and (not args.dev or not 1024 <= args.insecure_local_relay <= 65535):
        parser.error('--insecure-local-relay exige --dev et un port entre 1024 et 65535')
    root = Path(__file__).resolve().parent
    if args.diagnose_lock is not None:
        from src.features.lock_diagnostics import run_lock_diagnostic
        return run_lock_diagnostic(args.diagnose_lock)
    if args.diagnose:
        from src.networking.settings import NetworkConfig
        from src.networking.diagnostics import probe_ports
        try:
            config = NetworkConfig.load(root / 'network.json')
            print(json.dumps(probe_ports(config.udp_port, config.tcp_port), ensure_ascii=False, indent=2))
            return 0
        except (OSError, ValueError, TypeError):
            print('Configuration réseau illisible. Vérifiez network.json.', file=sys.stderr)
            return 1
    try:
        import PySide6
        import nacl
        from PySide6.QtCore import QTimer
        from PySide6.QtWidgets import QApplication
    except ImportError:
        print('Dépendance manquante (PySide6 ou PyNaCl). Installez les dépendances dans un venv : python3 -m pip install -r requirements.txt', file=sys.stderr)
        return 1
    if sys.version_info >= (3, 14) and tuple(map(int, PySide6.__version__.split('.')[:2])) < (6, 12):
        print('Python 3.14 nécessite PySide6 6.12 ou plus récent. Lancez sh ./install.sh pour installer un environnement compatible.', file=sys.stderr)
        return 1
    # Avoid automatic creation of core dumps containing transient messages.
    import resource
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    qt = QApplication([sys.argv[0]])
    qt.setApplicationName('FortyTchat')
    qt.setOrganizationName('FortyTchat')
    qt.setDesktopFileName('fr.fortytchat.FortyTchat')
    qt.setQuitOnLastWindowClosed(False)
    from src.app import Application, AlreadyRunning
    from src.security import SecurityError
    try:
        app = Application(root, args)
    except AlreadyRunning:
        return 0
    except SecurityError as exc:
        from PySide6.QtWidgets import QMessageBox
        QMessageBox.critical(None, 'Identité de chiffrement inaccessible', str(exc))
        return 1
    except OSError:
        print('Impossible d’initialiser la session utilisateur FortyTchat.', file=sys.stderr)
        return 1
    signal.signal(signal.SIGINT, lambda *_: app.quit())
    signal.signal(signal.SIGTERM, lambda *_: app.quit())
    timer = QTimer()
    timer.timeout.connect(lambda: None)
    timer.start(250)
    qt.commitDataRequest.connect(lambda manager: app.quit())
    return qt.exec()


if __name__ == '__main__':
    sys.exit(main())
