import asyncio
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import pytest
from PySide6.QtWidgets import QApplication
from src.networking.client import ChatClient
from src.ui.session_lock import SessionLockMonitor
from src.ui.private_window import PrivateWindow
from src.features.friends import friend_rows


def test_lock_restores_manual_status_and_preserves_dnd_and_contact():
    client=ChatClient('User42')
    client.preferences(wizz_enabled=True,dnd=False,status='online')
    client.set_screen_locked(True)
    assert client.status=='away' and client.hello()['status']=='away'
    client.set_screen_locked(False);assert client.status=='online'
    client.preferences(wizz_enabled=True,dnd=False,status='away')
    client.set_screen_locked(True);client.set_screen_locked(False)
    assert client.status=='away'
    client.preferences(wizz_enabled=True,dnd=True,status='online')
    client.set_screen_locked(True);assert client.status=='dnd'
    client.set_screen_locked(False);assert client.status=='dnd'
    with pytest.raises(ValueError):client.set_screen_locked('yes')


def test_new_clients_receive_lock_hint_and_keep_messages_available():
    async def run():
        a,b=ChatClient('Ami42',discovery=False,tcp_port=0),ChatClient('Other42',discovery=False,tcp_port=0)
        try:
            await a.start();await b.start();a.observe(b.hello(),'127.0.0.1');b.observe(a.hello(),'127.0.0.1')
            a.set_screen_locked(True)
            for _ in range(400):
                if b.peers[a.sid].locked:break
                await asyncio.sleep(.01)
            assert b.peers[a.sid].locked and b.peers[a.sid].status=='away'
            assert await b.send('message',a.sid,text='Lis ça à ton retour')
            a.set_screen_locked(False)
            for _ in range(400):
                if not b.peers[a.sid].locked:break
                await asyncio.sleep(.01)
            assert not b.peers[a.sid].locked and b.peers[a.sid].status=='online'
        finally:await a.stop();await b.stop()
    asyncio.run(run())


def test_lock_visual_label_friend_menu_and_detector_deduplication():
    qt=QApplication.instance() or QApplication([])
    monitor=SessionLockMonitor();events=[];monitor.changed.connect(events.append)
    monitor.observe(False);monitor.observe(True);monitor.observe(True);monitor.observe(False)
    assert events==[False,True,False]
    peer=dict(sid='a'*32,nick='Ami42',contact_id='b'*32,status='away',locked=True,encrypted=True,caps=['cards'],direct=True,security='unverified')
    view=PrivateWindow(peer['sid'],peer['nick'],[],'assets/icons/fortytchat.svg');view.set_peer(peer)
    assert 'verrouillée' in view.status_label.text() and view.composer.available
    rows=friend_rows({peer['contact_id']:peer['nick']},[peer])
    assert rows[0]['online'] and 'verrouillée' in rows[0]['status']
    monitor.stop();view.close();view.deleteLater();monitor.deleteLater();qt.processEvents()


def test_real_private_dbus_screensaver_read_only_getactive(tmp_path):
    daemon=shutil.which('dbus-daemon')
    if not daemon:pytest.skip('dbus-daemon absent')
    process=subprocess.Popen([daemon,'--session','--nofork','--print-address=1'],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    try:
        address=process.stdout.readline().strip();assert address
        code='''
import time
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QObject,Slot,ClassInfo
from PySide6.QtDBus import QDBusConnection
from src.ui.session_lock import SessionLockMonitor
app=QApplication([])
@ClassInfo({'D-Bus Interface':'org.gnome.ScreenSaver'})
class Saver(QObject):
    active=False
    @Slot(result=bool)
    def GetActive(self):return self.active
bus=QDBusConnection.sessionBus();saver=Saver()
assert bus.registerService('org.gnome.ScreenSaver')
assert bus.registerObject('/org/gnome/ScreenSaver',saver,QDBusConnection.RegisterOption.ExportAllSlots)
monitor=SessionLockMonitor(session_bus=bus,system_bus=bus)
events=[];monitor.changed.connect(events.append);monitor.start(test_bus=True);monitor.timer.stop()
def until(test):
    end=time.monotonic()+3
    while not test():
        app.processEvents();time.sleep(.005)
        assert time.monotonic()<end,events
until(lambda:events==[False])
saver.active=True;monitor.poll();until(lambda:events==[False,True])
saver.active=False;monitor.poll();until(lambda:events==[False,True,False])
monitor.stop()
'''
        result=subprocess.run([sys.executable,'-c',code],cwd=Path(__file__).resolve().parents[1],env={**os.environ,'QT_QPA_PLATFORM':'offscreen','DBUS_SESSION_BUS_ADDRESS':address},capture_output=True,text=True,timeout=15)
        assert result.returncode==0,result.stdout+result.stderr
    finally:
        process.terminate();process.wait(timeout=5)
