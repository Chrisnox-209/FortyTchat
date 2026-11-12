"""Real D-Bus wire tests on private daemons: never control the user's desktop."""
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def private_bus():
    executable = shutil.which('dbus-daemon')
    if not executable:
        pytest.skip('dbus-daemon absent')
    daemon = subprocess.Popen([executable, '--session', '--nofork', '--print-address=1'],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        address = daemon.stdout.readline().strip()
        assert address
        yield address
    finally:
        daemon.terminate()
        daemon.communicate(timeout=5)


def run_on_bus(address, code):
    result = subprocess.run([sys.executable, '-c', code], cwd=ROOT,
        env={**os.environ, 'QT_QPA_PLATFORM':'offscreen', 'DBUS_SESSION_BUS_ADDRESS':address,
             'XDG_SESSION_ID':'school42'}, capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr


def test_ubuntu_logind_session_lock_is_not_hidden_by_false_screensaver(private_bus):
    run_on_bus(private_bus, '''
import time
from PySide6.QtCore import QObject, Slot, Signal, Property, ClassInfo
from PySide6.QtWidgets import QApplication
from PySide6.QtDBus import QDBusConnection, QDBusObjectPath
from src.ui.session_lock import SessionLockMonitor
app=QApplication([])
@ClassInfo({'D-Bus Interface':'org.gnome.ScreenSaver'})
class Saver(QObject):
    ActiveChanged=Signal(bool)
    @Slot(result=bool)
    def GetActive(self):return False
@ClassInfo({'D-Bus Interface':'org.freedesktop.login1.Manager'})
class Manager(QObject):
    @Slot(str,result=QDBusObjectPath)
    def GetSession(self,name):
        assert name in ('auto','school42')
        return QDBusObjectPath('/org/freedesktop/login1/session/_31')
@ClassInfo({'D-Bus Interface':'org.freedesktop.login1.Session'})
class Session(QObject):
    locked=False
    @Property(bool)
    def LockedHint(self):return self.locked
bus=QDBusConnection.sessionBus()
saver,manager,session=Saver(),Manager(),Session()
assert bus.registerService('org.gnome.ScreenSaver')
assert bus.registerService('org.freedesktop.login1')
flags=QDBusConnection.RegisterOption.ExportAllSlots|QDBusConnection.RegisterOption.ExportAllProperties|QDBusConnection.RegisterOption.ExportAllSignals
assert bus.registerObject('/org/gnome/ScreenSaver',saver,flags)
assert bus.registerObject('/org/freedesktop/login1',manager,flags)
assert bus.registerObject('/org/freedesktop/login1/session/_31',session,flags)
monitor=SessionLockMonitor(session_bus=bus,system_bus=bus)
events=[];monitor.changed.connect(events.append)
monitor.start(test_bus=True);monitor.timer.stop()
def until(test):
    end=time.monotonic()+4
    while not test():
        app.processEvents();time.sleep(.005)
        assert time.monotonic()<end,events
until(lambda:events==[False] and not monitor.busy)
assert monitor.session_path=='/org/freedesktop/login1/session/_31'
# The GNOME proxy keeps replying False, while logind truthfully reports locked.
session.locked=True;monitor.poll();until(lambda:events==[False,True] and not monitor.busy)
# Signal path also re-reads logind, not just the proxy boolean.
session.locked=False;saver.ActiveChanged.emit(False)
until(lambda:events==[False,True,False] and not monitor.busy)
# Losing every source must not invent an unlock.
session.locked=True;monitor.poll();until(lambda:events[-1] and not monitor.busy)
bus.unregisterObject('/org/freedesktop/login1/session/_31')
bus.unregisterObject('/org/gnome/ScreenSaver')
monitor.poll();until(lambda:not monitor.busy)
assert events[-1] is True
monitor.stop()
''')


def test_native_friend_notification_without_notify_send(private_bus):
    if not shutil.which('gdbus'):
        pytest.skip('gdbus absent')
    run_on_bus(private_bus, r'''
import html,time
from PySide6.QtCore import QObject, Slot, ClassInfo
from PySide6.QtWidgets import QApplication
from PySide6.QtDBus import QDBusConnection
from src.ui.notifications import Notifications
app=QApplication([])
@ClassInfo({'D-Bus Interface':'org.freedesktop.Notifications'})
class Daemon(QObject):
    calls=[]
    @Slot(str,'uint',str,str,str,'QStringList','QVariantMap',int,result='uint')
    def Notify(self,name,replaces,icon,title,body,actions,hints,timeout):
        self.calls.append((name,replaces,icon,title,body,actions,hints,timeout))
        return 42
bus=QDBusConnection.sessionBus();daemon=Daemon()
assert bus.registerService('org.freedesktop.Notifications')
assert bus.registerObject('/org/freedesktop/Notifications',daemon,QDBusConnection.RegisterOption.ExportAllSlots)
icon="/tmp/FT ' test.svg"
notifications=Notifications(icon);notifications.executable=None;notifications.headless=False
title="Ami42 est en ligne ' \\ 🙂"
body="Texte ' <b>test</b>\nDeuxième ligne \\"
assert notifications.show(title,body,urgency='normal')
end=time.monotonic()+4
while not daemon.calls or notifications.processes:
    app.processEvents();time.sleep(.005)
    assert time.monotonic()<end,daemon.calls
name,replaces,shown_icon,shown_title,shown_body,actions,hints,timeout=daemon.calls[0]
assert (name,replaces,shown_icon,shown_title,shown_body,actions,timeout)==('FortyTchat',0,icon,title,html.escape(body,quote=False),[],5000)
assert hints['urgency']==1 and hints['suppress-sound'] is True
assert hints['desktop-entry']=='fr.fortytchat.FortyTchat'
assert len(daemon.calls)==1
notifications.stop()
''')


def test_notification_failed_backend_falls_back_without_shell(tmp_path):
    # Both commands are local test files; no personal notification bus is touched.
    log = tmp_path/'arguments.json'
    executable = tmp_path/'notify-test'
    executable.write_text('#!' + sys.executable + '\nimport json,sys\nfrom pathlib import Path\nPath(' + repr(str(log)) + ').write_text(json.dumps(sys.argv[1:]))\n')
    executable.chmod(0o700)
    result = subprocess.run([sys.executable, '-c', '''
import json,time,sys
from pathlib import Path
from PySide6.QtWidgets import QApplication
from src.ui.notifications import Notifications
app=QApplication([])
n=Notifications('/tmp/icon.svg');n.headless=False
n.gdbus='/missing-fortytchat-test-gdbus';n.executable=sys.argv[1]
assert n.show("Ami42 est en ligne",urgency='normal')
end=time.monotonic()+4
while n.processes:
    app.processEvents();time.sleep(.005)
    assert time.monotonic()<end
args=json.loads(Path(sys.argv[2]).read_text())
assert '--urgency=normal' in args and '--hint=boolean:suppress-sound:true' in args
assert args[-2]=='Ami42 est en ligne'
n.stop();assert not n.show('Arrêté')
''',str(executable),str(log)], cwd=ROOT, env={**os.environ,'QT_QPA_PLATFORM':'offscreen'},
        capture_output=True,text=True,timeout=10)
    assert result.returncode == 0, result.stdout + result.stderr
