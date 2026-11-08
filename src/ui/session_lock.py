"""Read-only lock detection for GNOME/logind and other Linux desktops."""
import os

from PySide6.QtCore import QObject, Signal, Slot, SLOT, QTimer
from PySide6.QtWidgets import QApplication


class SessionLockMonitor(QObject):
    changed = Signal(bool)
    SOURCES = [('org.gnome.ScreenSaver', '/org/gnome/ScreenSaver', 'org.gnome.ScreenSaver'),
               ('org.freedesktop.ScreenSaver', '/ScreenSaver', 'org.freedesktop.ScreenSaver'),
               ('org.freedesktop.ScreenSaver', '/org/freedesktop/ScreenSaver', 'org.freedesktop.ScreenSaver'),
               ('org.cinnamon.ScreenSaver', '/org/cinnamon/ScreenSaver', 'org.cinnamon.ScreenSaver'),
               ('org.mate.ScreenSaver', '/org/mate/ScreenSaver', 'org.mate.ScreenSaver'),
               ('org.xfce.ScreenSaver', '/org/xfce/ScreenSaver', 'org.xfce.ScreenSaver')]

    def __init__(self, parent=None, *, session_bus=None, system_bus=None):
        super().__init__(parent)
        self.session_bus, self.system_bus = session_bus, system_bus
        self.value, self.busy, self.stopped = None, False, True
        self.again, self.session_path = False, ''
        self.watchers, self.subscriptions = set(), []
        self.timer = QTimer(self)
        self.timer.setInterval(2000)
        self.timer.timeout.connect(self.poll)

    def start(self, *, test_bus=False):
        if not self.stopped:
            return
        app = QApplication.instance()
        if app and app.platformName() in ('offscreen', 'minimal') and not test_bus:
            return
        try:
            from PySide6.QtDBus import QDBusConnection
        except ImportError:
            return
        self.session_bus = self.session_bus or QDBusConnection.sessionBus()
        self.system_bus = self.system_bus or QDBusConnection.systemBus()
        self.stopped = False
        if self.session_bus.isConnected():
            for service, path, interface in self.SOURCES:
                if self.session_bus.connect(service, path, interface, 'ActiveChanged', self, SLOT('active_changed(bool)')):
                    self.subscriptions.append((service, path, interface))
        self.timer.start()
        self.poll()

    @Slot(bool)
    def active_changed(self, active):
        # Re-read all sources: a proxy reporting False must not hide logind's lock.
        if self.busy:
            self.again = True
        else:
            self.poll()

    def call(self, bus, service, path, interface, method, args, callback):
        from PySide6.QtDBus import QDBusMessage, QDBusPendingCallWatcher, QDBusVariant
        if not bus or not bus.isConnected():
            callback(None)
            return
        message = QDBusMessage.createMethodCall(service, path, interface, method)
        message.setAutoStartService(False)
        message.setArguments(args)
        watcher = QDBusPendingCallWatcher(bus.asyncCall(message, 800), self)
        self.watchers.add(watcher)

        def finished(call):
            reply = call.reply()
            values = reply.arguments()
            value = values[0] if reply.type() != QDBusMessage.MessageType.ErrorMessage and values else None
            if isinstance(value, QDBusVariant):
                value = value.variant()
            self.watchers.discard(call)
            call.deleteLater()
            if not self.stopped:
                callback(value)
        watcher.finished.connect(finished)

    def poll(self):
        if self.stopped or self.busy:
            return
        self.busy = True
        session = os.environ.get('XDG_SESSION_ID', '')
        if not self.session_path and session:
            self.call(self.system_bus, 'org.freedesktop.login1', '/org/freedesktop/login1',
                      'org.freedesktop.login1.Manager', 'GetSession', [session], self.session_resolved)
        else:
            self.collect()

    def session_resolved(self, value):
        from PySide6.QtDBus import QDBusObjectPath
        if isinstance(value, QDBusObjectPath) and value.path().startswith('/org/freedesktop/login1/session/'):
            self.session_path = value.path()
        self.collect()

    def collect(self):
        sources = [(self.session_bus, service, path, interface, 'GetActive', [])
                   for service, path, interface in self.SOURCES]
        sources.append((self.system_bus, 'org.freedesktop.login1',
                        self.session_path or '/org/freedesktop/login1/session/auto',
                        'org.freedesktop.DBus.Properties', 'Get',
                        ['org.freedesktop.login1.Session', 'LockedHint']))
        remaining, results = len(sources), []

        def answer(value, logind=False):
            nonlocal remaining
            if type(value) is bool:
                results.append(value)
            elif logind:
                self.session_path = ''  # A login/session daemon may have restarted.
            remaining -= 1
            if remaining:
                return
            self.busy = False
            if results:
                self.observe(any(results))
            if self.again:
                self.again = False
                QTimer.singleShot(0, self.poll)

        # Parallel probes keep missing services from delaying the other sources.
        for index, source in enumerate(sources):
            self.call(*source, lambda value, last=index == len(sources)-1: answer(value, last))

    def observe(self, locked):
        if type(locked) is bool and locked != self.value:
            self.value = locked
            self.changed.emit(locked)

    def stop(self):
        self.stopped = True
        self.timer.stop()
        for service, path, interface in self.subscriptions:
            self.session_bus.disconnect(service, path, interface, 'ActiveChanged', self, SLOT('active_changed(bool)'))
        self.subscriptions.clear()
        self.busy = self.again = False
