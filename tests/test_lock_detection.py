"""Lock lookup and diagnostics are read-only; all servers are private test buses."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import pytest
from PySide6.QtWidgets import QApplication
from PySide6.QtDBus import QDBusObjectPath
from src.ui.session_lock import SessionLockMonitor

ROOT=Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('environment,automatic', [('',True),('stale42',True),('school42',False)])
def test_lookup_uses_current_session_then_environment_fallback(monkeypatch,environment,automatic):
    app=QApplication.instance() or QApplication([])
    monkeypatch.setenv('XDG_SESSION_ID',environment)
    monitor=SessionLockMonitor();monitor.stopped=False;calls=[];reports=[]
    monitor.diagnostic.connect(reports.append)
    def call(bus,service,path,interface,method,args,callback):
        calls.append((method,args))
        if method=='GetSession':
            callback(QDBusObjectPath('/org/freedesktop/login1/session/_35') if (args[0]=='auto' and automatic or args[0]=='school42') else None)
        else:callback(True if method=='Get' else False)
    monkeypatch.setattr(monitor,'call',call)
    monitor.poll()
    assert calls[0]==('GetSession',['auto'])
    assert monitor.session_path.endswith('/_35') and monitor.value is True
    assert reports[-1]['locked'] is True and not monitor.busy
    if not automatic:assert calls[1]==('GetSession',['school42'])
    monitor.stop();monitor.deleteLater();app.processEvents()


def test_unknown_sources_do_not_invent_an_unlock(monkeypatch):
    app=QApplication.instance() or QApplication([])
    monitor=SessionLockMonitor();monitor.stopped=False;monitor.value=True;reports=[]
    monitor.diagnostic.connect(reports.append)
    monkeypatch.setattr(monitor,'call',lambda *args:args[-1](None))
    monitor.poll()
    assert monitor.value is True and reports[-1]['locked'] is True
    assert not monitor.busy
    monitor.stop();monitor.deleteLater();app.processEvents()


@pytest.fixture
def private_bus():
    daemon=shutil.which('dbus-daemon')
    if not daemon:pytest.skip('dbus-daemon absent')
    process=subprocess.Popen([daemon,'--session','--nofork','--print-address=1'],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    try:yield process.stdout.readline().strip()
    finally:process.terminate();process.communicate(timeout=5)


def run_on_bus(address,code,tmp_path):
    result=subprocess.run([sys.executable,'-c',code],cwd=ROOT,
        env={**os.environ,'QT_QPA_PLATFORM':'offscreen','DBUS_SESSION_BUS_ADDRESS':address,
             'DBUS_SYSTEM_BUS_ADDRESS':address,'XDG_SESSION_ID':'',
             'XDG_CONFIG_HOME':str(tmp_path/'config'),'XDG_DATA_HOME':str(tmp_path/'data')},
        capture_output=True,text=True,timeout=20)
    assert result.returncode==0,result.stdout+result.stderr


def test_real_dbus_without_session_id_drives_remote_gui_cadenas_and_unlock(private_bus,tmp_path):
    run_on_bus(private_bus, r'''
import time,uuid
from pathlib import Path
from types import SimpleNamespace
from PySide6.QtCore import QObject,Slot,Property,ClassInfo,QMetaType
from PySide6.QtDBus import QDBusConnection,QDBusObjectPath,QDBusMessage,QDBusVariant,QDBusArgument
from PySide6.QtWidgets import QApplication
from src.app import Application
from src.networking.settings import NetworkConfig
from src.ui.session_lock import SessionLockMonitor
qt=QApplication([]);qt.setQuitOnLastWindowClosed(False)
@ClassInfo({'D-Bus Interface':'org.freedesktop.login1.Manager'})
class Manager(QObject):
    @Slot(str,result=QDBusObjectPath)
    def GetSession(self,name):
        assert name=='auto'
        return QDBusObjectPath('/org/freedesktop/login1/session/_35')
@ClassInfo({'D-Bus Interface':'org.freedesktop.login1.Session'})
class Session(QObject):
    locked=False
    @Property(bool)
    def LockedHint(self):return self.locked
bus=QDBusConnection.sessionBus();manager=Manager();session=Session()
assert bus.registerService('org.freedesktop.login1')
flags=QDBusConnection.RegisterOption.ExportAllSlots|QDBusConnection.RegisterOption.ExportAllProperties
assert bus.registerObject('/org/freedesktop/login1',manager,flags)
assert bus.registerObject('/org/freedesktop/login1/session/_35',session,flags)
NetworkConfig.load=classmethod(lambda cls,path:NetworkConfig(mode='relay',relay_host=''))
root=Path.cwd()
apps=[Application(root,SimpleNamespace(dev='lock-'+uuid.uuid4().hex[:10],background=True,insecure_local_relay=None)) for _ in range(2)]
a,b=apps
def until(check):
    end=time.monotonic()+5
    while not check():
        qt.processEvents();time.sleep(.005)
        assert time.monotonic()<end
    qt.processEvents()
monitor=SessionLockMonitor(session_bus=bus,system_bus=bus);reports=[]
monitor.diagnostic.connect(reports.append);monitor.changed.connect(a.screen_lock_changed)
try:
    until(lambda:all(app.worker.client.port>0 for app in apps))
    a.worker.loop.call_soon_threadsafe(a.worker.client.observe,b.worker.client.hello(),'127.0.0.1')
    b.worker.loop.call_soon_threadsafe(b.worker.client.observe,a.worker.client.hello(),'127.0.0.1')
    until(lambda:a.worker.client.sid in b.window.peers)
    sid=a.worker.client.sid;direct=b.open_private(sid)
    monitor.start(test_bus=True);monitor.timer.stop()
    until(lambda:monitor.value is False and not monitor.busy)
    assert monitor.session_path.endswith('/_35') and monitor.logind_subscription==monitor.session_path
    session.locked=True
    change=QDBusMessage.createSignal(monitor.session_path,'org.freedesktop.DBus.Properties','PropertiesChanged')
    invalidated=QDBusArgument();invalidated.beginArray(QMetaType.Type.QString);invalidated.endArray()
    change.setArguments(['org.freedesktop.login1.Session',{'LockedHint':QDBusVariant(True)},invalidated])
    assert bus.send(change)
    until(lambda:direct.avatar.locked and b.window.peers[sid]['locked'])
    assert a.screen_locked and 'verrouillée' in direct.status_label.text()
    session.locked=False
    change.setArguments(['org.freedesktop.login1.Session',{'LockedHint':QDBusVariant(False)},invalidated])
    assert bus.send(change)
    until(lambda:not direct.avatar.locked and not b.window.peers[sid]['locked'])
    assert not a.screen_locked and direct.avatar.online
    assert reports[-1]['locked'] is False
finally:
    monitor.stop()
    for app in apps:app.cleanup();app.window.close();app.deleteLater()
    qt.processEvents()
''',tmp_path)


@pytest.mark.parametrize('report',[False,True])
def test_diagnostic_cli_and_shell_report_observe_bus_without_chat_or_profile_changes(private_bus,tmp_path,report):
    run_on_bus(private_bus, 'REPORT_MODE='+repr(report)+'\n'+r'''
import os,subprocess,sys,time
from pathlib import Path
from PySide6.QtCore import QCoreApplication,QObject,Slot,ClassInfo
from PySide6.QtDBus import QDBusConnection
qt=QCoreApplication([])
@ClassInfo({'D-Bus Interface':'org.gnome.ScreenSaver'})
class Saver(QObject):
    active=False
    @Slot(result=bool)
    def GetActive(self):return self.active
saver=Saver();bus=QDBusConnection.sessionBus()
assert bus.registerService('org.gnome.ScreenSaver')
assert bus.registerObject('/org/gnome/ScreenSaver',saver,QDBusConnection.RegisterOption.ExportAllSlots)
path=Path(os.environ['XDG_DATA_HOME']).parent/'rapport avec espaces.txt'
command=['sh','diagnostic-session.sh','--duration','5','--output',str(path)] if REPORT_MODE else [sys.executable,'main.py','--diagnose-lock','5']
child=subprocess.Popen(command,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,
    env={**os.environ,'FORTYTCHAT_PYTHON':sys.executable,'FORTYTCHAT_PRIVATE_TEST_SENTINEL':'NEVER_COLLECT_THIS_SECRET'})
start=time.monotonic()
while child.poll() is None:
    qt.processEvents();time.sleep(.01)
    if time.monotonic()-start>1:saver.active=True
    assert time.monotonic()-start<10
out,error=child.communicate(timeout=2)
assert child.returncode==0,(out,error)
if REPORT_MODE:
    assert str(path) in out
    out=path.read_text()
    assert path.stat().st_mode & 0o777 == 0o600
    assert 'NEVER_COLLECT_THIS_SECRET' not in out
    assert 'fortytchat_script' in out and 'systeme' in out and 'pyside6' in out
assert '"locked": false' in out and '"locked": true' in out,out
assert 'org.gnome.ScreenSaver' in out and 'org.freedesktop' in out
assert 'Diagnostic terminé.' in out
assert not Path(os.environ['XDG_CONFIG_HOME']).exists()
assert not Path(os.environ['XDG_DATA_HOME']).exists()
''',tmp_path)


def test_report_never_overwrites_an_existing_file(tmp_path):
    path=tmp_path/'existing.txt';path.write_text('KEEP THIS FILE')
    result=subprocess.run([sys.executable,'tools/diagnose_session.py','--output',str(path)],cwd=ROOT,capture_output=True,text=True,timeout=5)
    assert result.returncode==1 and path.read_text()=='KEEP THIS FILE'


@pytest.mark.parametrize('value',['0','121','invalid'])
def test_diagnostic_duration_is_bounded(value):
    result=subprocess.run([sys.executable,'main.py','--diagnose-lock',value],cwd=ROOT,capture_output=True,text=True,timeout=5)
    assert result.returncode==2
