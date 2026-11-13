"""Silent native notifications, including desktops without libnotify-bin."""
import html
from pathlib import Path
import shutil
from PySide6.QtCore import QObject, QProcess, QTimer
from PySide6.QtWidgets import QApplication

NOTIFICATION_ICONS = {'friend': 'friend.png', 'message': 'message.png', 'group': 'group.png', 'poll': 'poll.png'}


def variant_string(value):
    """One GVariant string argument, never shell code."""
    return "'" + value.replace('\\', '\\\\').replace("'", "\\'").replace('\n', '\\n').replace('\r', '\\r').replace('\t', '\\t') + "'"


class Notifications(QObject):
    def __init__(self, icon, parent=None):
        super().__init__(parent)
        self.executable, self.gdbus = shutil.which('notify-send'), shutil.which('gdbus')
        self.icon, self.processes, self.stopped = icon, set(), False
        self.headless = QApplication.platformName() in ('offscreen', 'minimal')

    def show(self, title, body='Ouvre FortyTchat pour consulter la conversation.', *, urgency='low', kind='default'):
        if urgency not in ('low', 'normal'):
            raise ValueError('Priorité de notification invalide')
        if kind != 'default' and kind not in NOTIFICATION_ICONS:
            raise ValueError('Type de notification invalide')
        if self.stopped or self.headless or len(self.processes) >= 3:
            return False
        icon = self.icon
        image_uri = ''
        if kind in NOTIFICATION_ICONS:
            path = Path(self.icon).parent / 'notifications' / NOTIFICATION_ICONS[kind]
            if path.is_file():
                icon = str(path.resolve());image_uri = path.resolve().as_uri()
        body = html.escape(body, quote=False)
        backends = []
        if self.gdbus:
            hints = "{'urgency': <byte " + ('1' if urgency == 'normal' else '0') + ">, 'suppress-sound': <true>, 'desktop-entry': <'fr.fortytchat.FortyTchat'>"
            # GNOME can use the desktop entry for the app icon. image-path keeps
            # the event's distinct image visible in the banner itself as well.
            if image_uri:hints += ", 'image-path': <" + variant_string(image_uri) + ">"
            hints += '}'
            backends.append((self.gdbus, ['call', '--session', '--timeout', '2',
                '--dest', 'org.freedesktop.Notifications', '--object-path', '/org/freedesktop/Notifications',
                '--method', 'org.freedesktop.Notifications.Notify', variant_string('FortyTchat'), '0',
                variant_string(icon), variant_string(title), variant_string(body), '[]', hints, '5000']))
        if self.executable:
            arguments = ['--app-name=FortyTchat', '--hint=boolean:suppress-sound:true',
                '--hint=string:desktop-entry:fr.fortytchat.FortyTchat', '--urgency=' + urgency,
                '--expire-time=5000', '--icon', icon]
            if image_uri:arguments.append('--hint=string:image-path:' + image_uri)
            backends.append((self.executable, arguments + ['--', title, body]))
        if not backends:
            return False
        self.start_backend(backends)
        return True

    def start_backend(self, backends):
        if self.stopped:
            return
        executable, arguments = backends[0]
        process = QProcess(self)
        process.setProgram(executable)
        process.setArguments(arguments)
        process.setStandardOutputFile('/dev/null')
        process.setStandardErrorFile('/dev/null')
        self.processes.add(process)
        timeout = QTimer(process)
        timeout.setSingleShot(True)
        timeout.timeout.connect(process.kill)

        def done(ok=False):
            if process not in self.processes:
                return
            timeout.stop()
            self.processes.discard(process)
            process.deleteLater()
            if not ok and len(backends) > 1:
                self.start_backend(backends[1:])
        process.finished.connect(lambda code, status: done(code == 0 and status == QProcess.ExitStatus.NormalExit))
        process.errorOccurred.connect(lambda error: done() if error == QProcess.ProcessError.FailedToStart else None)
        process.start()
        timeout.start(2500)

    def stop(self):
        self.stopped = True
        for process in list(self.processes):
            process.kill()
