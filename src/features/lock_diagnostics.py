"""Explicit, read-only lock probe; no chat, profile, or desktop control."""
import json
import os
import signal
import sys


def run_lock_diagnostic(seconds=60):
    try:
        import PySide6
        if sys.version_info>=(3,14) and tuple(map(int,PySide6.__version__.split('.')[:2]))<(6,12):
            print('Python 3.14 nécessite PySide6 6.12 ou plus récent. Relance install.sh.',file=sys.stderr)
            return 1
        from PySide6.QtCore import QCoreApplication, QTimer
        from PySide6 import QtDBus
        from ..ui.session_lock import SessionLockMonitor
    except ImportError:
        print('Le diagnostic nécessite PySide6 et QtDBus. Relance install.sh.',file=sys.stderr)
        return 1
    app=QCoreApplication([sys.argv[0]])
    monitor=SessionLockMonitor()
    print('Diagnostic de verrouillage FortyTchat — lecture seule.',flush=True)
    print(f'Pendant {seconds} secondes : verrouille le poste comme d’habitude, puis déverrouille-le.',flush=True)
    print('Aucun verrouillage automatique, aucune connexion au tchat, aucun fichier de profil modifié.',flush=True)
    print(json.dumps({'bureau':os.environ.get('XDG_CURRENT_DESKTOP',''),
                      'session':os.environ.get('XDG_SESSION_TYPE',''),
                      'xdg_session_id_present':bool(os.environ.get('XDG_SESSION_ID'))},ensure_ascii=False),flush=True)
    previous=[None]
    import time
    started=time.monotonic()
    def report(data):
        # Print changes only, keeping the latest probe readable in a terminal.
        value=json.dumps(data,ensure_ascii=False,sort_keys=True)
        if value!=previous[0]:
            previous[0]=value;print(f'[+{time.monotonic()-started:.1f}s] '+value,flush=True)
    monitor.diagnostic.connect(report)
    old_handlers={name:signal.getsignal(name) for name in (signal.SIGINT,signal.SIGTERM)}
    for name in old_handlers:signal.signal(name,lambda *_:app.quit())
    heartbeat=QTimer();heartbeat.timeout.connect(lambda:None);heartbeat.start(250)
    QTimer.singleShot(seconds*1000,app.quit)
    try:
        monitor.start();app.exec()
    finally:
        monitor.stop()
        for name,handler in old_handlers.items():signal.signal(name,handler)
    print('Diagnostic terminé.',flush=True)
    return 0
