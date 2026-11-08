"""Read-only desktop lock detection. No session controls or activity recording."""
from PySide6.QtCore import QObject, Signal, QTimer
from PySide6.QtWidgets import QApplication


class SessionLockMonitor(QObject):
    changed = Signal(bool)
    SOURCES = [('org.gnome.ScreenSaver','/org/gnome/ScreenSaver','org.gnome.ScreenSaver','GetActive',[]),
               ('org.freedesktop.ScreenSaver','/ScreenSaver','org.freedesktop.ScreenSaver','GetActive',[]),
               ('org.cinnamon.ScreenSaver','/org/cinnamon/ScreenSaver','org.cinnamon.ScreenSaver','GetActive',[])]

    def __init__(self,parent=None,*,session_bus=None,system_bus=None):
        super().__init__(parent)
        self.session_bus,self.system_bus=session_bus,system_bus
        self.value,self.busy,self.stopped=None,False,True
        self.watchers=set()
        self.timer=QTimer(self);self.timer.setInterval(3000);self.timer.timeout.connect(self.poll)

    def start(self,*,test_bus=False):
        app=QApplication.instance()
        if app and app.platformName() in ('offscreen','minimal') and not test_bus:return
        try:
            from PySide6.QtDBus import QDBusConnection
        except ImportError:return
        self.session_bus=self.session_bus or QDBusConnection.sessionBus()
        self.system_bus=self.system_bus or QDBusConnection.systemBus()
        self.stopped=False;self.timer.start();self.poll()

    def poll(self):
        if self.stopped or self.busy:return
        self.busy=True
        sources=[(self.session_bus,*source) for source in self.SOURCES]
        sources.append((self.system_bus,'org.freedesktop.login1','/org/freedesktop/login1/session/auto',
                        'org.freedesktop.DBus.Properties','Get',['org.freedesktop.login1.Session','LockedHint']))
        self.query(sources)

    def query(self,sources):
        if self.stopped or not sources:self.busy=False;return
        from PySide6.QtDBus import QDBusMessage,QDBusPendingCallWatcher,QDBusVariant
        bus,service,path,interface,method,args=sources[0]
        if not bus or not bus.isConnected():self.query(sources[1:]);return
        message=QDBusMessage.createMethodCall(service,path,interface,method);message.setArguments(args)
        watcher=QDBusPendingCallWatcher(bus.asyncCall(message,1000),self);self.watchers.add(watcher)
        def finished(call):
            reply=call.reply();values=reply.arguments()
            value=values[0] if reply.type()!=QDBusMessage.MessageType.ErrorMessage and values else None
            if isinstance(value,QDBusVariant):value=value.variant()
            self.watchers.discard(call);call.deleteLater()
            if self.stopped:self.busy=False;return
            if type(value) is bool:
                self.busy=False;self.observe(value)
            else:self.query(sources[1:])
        watcher.finished.connect(finished)

    def observe(self,locked):
        if type(locked) is bool and locked!=self.value:
            self.value=locked;self.changed.emit(locked)

    def stop(self):
        self.stopped=True;self.timer.stop()
