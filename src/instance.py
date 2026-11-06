"""One process per Unix login session, with a private local activation socket."""
import fcntl
import hashlib
import os
from pathlib import Path
import socket
import stat
import tempfile
import threading


class SingleInstance:
    def __init__(self, callback, profile=''):
        self.callback = callback
        session = os.environ.get('XDG_SESSION_ID') or os.environ.get('WAYLAND_DISPLAY') or os.environ.get('DISPLAY', 'default')
        key = hashlib.sha256((session + '|' + profile).encode()).hexdigest()[:12]
        self.root = Path(tempfile.gettempdir()) / f'fortytchat-{os.getuid()}'
        self.root.mkdir(mode=0o700, exist_ok=True)
        info = self.root.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise OSError('Dossier de session non privé')
        self.path = self.root / f'{key}.sock'
        fd = os.open(self.root / f'{key}.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        self.lock = os.fdopen(fd, 'a')
        self.socket = None
        self.stopped = threading.Event()

    def acquire(self, activate=True):
        try:
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                client.settimeout(1)
                try:
                    client.connect(str(self.path))
                    if activate:
                        client.sendall(b'show')
                except OSError:
                    pass
            return False
        self.path.unlink(missing_ok=True)
        self.socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.socket.bind(str(self.path))
        os.chmod(self.path, 0o600)
        self.socket.listen(4)
        self.socket.settimeout(0.5)
        threading.Thread(target=self._listen, name='FortyTchat activation', daemon=True).start()
        return True

    def _listen(self):
        while not self.stopped.is_set():
            try:
                client, _ = self.socket.accept()
                with client:
                    client.settimeout(0.5)
                    if client.recv(16) == b'show':
                        self.callback()
            except (OSError, TimeoutError):
                pass

    def close(self):
        self.stopped.set()
        if self.socket:
            self.socket.close()
            self.path.unlink(missing_ok=True)
        self.lock.close()
