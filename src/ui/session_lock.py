"""Read-only lock detection for GNOME/logind and other Linux desktops."""
import os
import time

from PySide6.QtCore import QObject, Signal, Slot, SLOT, QTimer
from PySide6.QtWidgets import QApplication


class SessionLockMonitor(QObject):
    changed = Signal(bool)
    diagnostic = Signal(object)
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
        self.generation, self.resolve_after = 0, 0
        self.logind_subscription = ''
        self.readings = {}
        self.watchers, self.subscriptions = set(), []
        self.timer = QTimer(self)
        self.timer.setInterval(2000)
        self.timer.timeout.connect(self.poll)

    def start(self, *, test_bus=False):
        if not self.stopped:
            return
        app = QApplication.instance()
        if isinstance(app, QApplication) and app.platformName() in ('offscreen', 'minimal') and not test_bus:
            return
        try:
            from PySide6.QtDBus import QDBusConnection
        except ImportError:
            return
        self.session_bus = self.session_bus or QDBusConnection.sessionBus()
        self.system_bus = self.system_bus or QDBusConnection.systemBus()
        self.stopped = False
        self.generation += 1
        self.resolve_after = 0
        if self.session_bus.isConnected():
            for service, path, interface in self.SOURCES:
                if self.session_bus.connect(service, path, interface, 'ActiveChanged', self, SLOT('active_changed(bool)')):
                    self.subscriptions.append((service, path, interface))
        self.timer.start()
        self.poll()

    @Slot(bool)
    def active_changed(self, active):
        # Re-read all sources: a proxy reporting False must not hide logind's lock.
        self.refresh()

    def refresh(self):
        if self.busy:
            self.again = True
        else:
            self.poll()

    @Slot()
    def logind_changed(self):
        # The subscription is scoped to our session. Re-read its properties rather
        # than decoding a desktop-dependent QVariant map in the signal handler.
        self.refresh()

    def call(self, bus, service, path, interface, method, args, callback):
        from PySide6.QtDBus import QDBusMessage, QDBusPendingCallWatcher, QDBusVariant
        key = service + ' ' + path + ' ' + method
        if method == 'GetSession': key += ' ' + args[0]
        def record(value, error=''):
            if not error and method in ('GetActive','Get') and type(value) is not bool:
                error = 'Réponse sans état booléen'
            self.readings[key] = {'value':value if type(value) is bool else None, 'error':error[:160]}
            callback(value)
        if not bus or not bus.isConnected():
            record(None, 'Bus indisponible')
            return
        message = QDBusMessage.createMethodCall(service, path, interface, method)
        message.setAutoStartService(False)
        message.setArguments(args)
        watcher = QDBusPendingCallWatcher(bus.asyncCall(message, 800), self)
        self.watchers.add(watcher)
        generation = self.generation

        def finished(call):
            reply = call.reply()
            values = reply.arguments()
            value = values[0] if reply.type() != QDBusMessage.MessageType.ErrorMessage and values else None
            if isinstance(value, QDBusVariant):
                value = value.variant()
            self.watchers.discard(call)
            call.deleteLater()
            if not self.stopped and generation == self.generation:
                record(value, reply.errorName() if reply.type() == QDBusMessage.MessageType.ErrorMessage else '')
        watcher.finished.connect(finished)

    def poll(self):
        if self.stopped or self.busy:
            return
        self.busy = True
        self.readings = {}
        if not self.session_path or time.monotonic() >= self.resolve_after:
            # logind's auto lookup also works for launchers in a systemd user unit,
            # with no XDG_SESSION_ID or a stale inherited one (including Ubuntu 22.04).
            session = os.environ.get('XDG_SESSION_ID', '')
            candidates = ['auto'] + ([session] if session and session != 'auto' else [])
            self.resolve_session(candidates)
        else:
            self.collect()

    def resolve_session(self, candidates):
        if not candidates:
            self.set_session_path('')
            self.resolve_after = time.monotonic() + 30
            self.collect()
            return
        self.call(self.system_bus, 'org.freedesktop.login1', '/org/freedesktop/login1',
                  'org.freedesktop.login1.Manager', 'GetSession', [candidates[0]],
                  lambda value:self.session_resolved(value,candidates[1:]))

    def session_resolved(self, value, remaining=()):
        from PySide6.QtDBus import QDBusObjectPath
        if (isinstance(value, QDBusObjectPath) and value.path().startswith('/org/freedesktop/login1/session/')
                and value.path().rsplit('/',1)[-1] not in ('auto','self','')):
            self.set_session_path(value.path())
            self.resolve_after = time.monotonic() + 30
            self.collect()
        else:self.resolve_session(remaining)

    def set_session_path(self, path):
        if self.logind_subscription == path:
            self.session_path = path
            return
        if self.logind_subscription:
            self.system_bus.disconnect('org.freedesktop.login1',self.logind_subscription,
                'org.freedesktop.DBus.Properties','PropertiesChanged',self,
                SLOT('logind_changed()'))
        self.session_path, self.logind_subscription = path, ''
        if path and self.system_bus and self.system_bus.isConnected():
            if self.system_bus.connect('org.freedesktop.login1',path,'org.freedesktop.DBus.Properties',
                'PropertiesChanged',self,SLOT('logind_changed()')):
                self.logind_subscription = path

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
                self.set_session_path('')  # A login/session daemon may have restarted.
            remaining -= 1
            if remaining:
                return
            self.busy = False
            if results:
                self.observe(any(results))
            self.diagnostic.emit({'locked':self.value,'session_path':self.session_path,
                                  'sources':{key:dict(value) for key,value in self.readings.items()}})
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
        self.generation += 1
        self.timer.stop()
        self.set_session_path('')
        for service, path, interface in self.subscriptions:
            self.session_bus.disconnect(service, path, interface, 'ActiveChanged', self, SLOT('active_changed(bool)'))
        self.subscriptions.clear()
        self.busy = self.again = False
