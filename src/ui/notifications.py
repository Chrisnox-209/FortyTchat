"""Silent notifications. Message bodies are never sent to a notification daemon."""
import shutil
from PySide6.QtCore import QObject, QProcess


class Notifications(QObject):
    def __init__(self, icon, parent=None):
        super().__init__(parent)
        self.executable, self.icon, self.processes = shutil.which('notify-send'), icon, set()

    def show(self, title, body='Ouvre FortyTchat pour consulter la conversation.'):
        if not self.executable or len(self.processes) >= 3:
            return
        process = QProcess(self)
        process.setProgram(self.executable)
        process.setArguments(['--app-name=FortyTchat', '--hint=boolean:suppress-sound:true',
                              '--urgency=low', '--expire-time=5000', '--icon', self.icon, '--', title, body])
        process.setStandardOutputFile('/dev/null')
        process.setStandardErrorFile('/dev/null')
        self.processes.add(process)
        def done(*args):
            self.processes.discard(process)
            process.deleteLater()
        process.finished.connect(done)
        process.errorOccurred.connect(done)
        process.start()

    def stop(self):
        for process in list(self.processes):
            process.kill()
