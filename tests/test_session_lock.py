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
from src.networking.relay import RelayServer
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


@pytest.mark.parametrize('relay', [False, True])
def test_new_clients_receive_lock_hint_and_keep_messages_available(relay):
    async def run():
        server=RelayServer() if relay else None
        options=dict(relay_host='127.0.0.1',relay_port=await server.start(port=0),tls=False) if relay else {}
        a,b=ChatClient('Ami42',discovery=False,tcp_port=0,**options),ChatClient('Other42',discovery=False,tcp_port=0,**options)
        try:
            await a.start();await b.start()
            if relay:
                for _ in range(400):
                    if a.sid in b.peers and b.sid in a.peers:break
                    await asyncio.sleep(.01)
                assert a.sid in b.peers and b.sid in a.peers
            else:a.observe(b.hello(),'127.0.0.1');b.observe(a.hello(),'127.0.0.1')
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
        finally:
            await a.stop();await b.stop()
            if server:await server.stop()
    asyncio.run(run())


def test_lock_visual_label_friend_menu_and_detector_deduplication():
    qt=QApplication.instance() or QApplication([])
    monitor=SessionLockMonitor();events=[];monitor.changed.connect(events.append)
    monitor.observe(False);monitor.observe(True);monitor.observe(True);monitor.observe(False)
    assert events==[False,True,False]
    peer=dict(sid='a'*32,nick='Ami42',contact_id='b'*32,status='away',locked=True,encrypted=True,caps=['cards'],direct=True,security='unverified')
    view=PrivateWindow(peer['sid'],peer['nick'],[],'assets/icons/fortytchat.svg');view.set_peer(peer)
    assert 'verrouillée' in view.status_label.text() and view.composer.available
    assert view.avatar.locked and view.avatar.online
    rows=friend_rows({peer['contact_id']:peer['nick']},[peer])
    assert rows[0]['online'] and 'verrouillée' in rows[0]['status']
    monitor.stop();view.close();view.deleteLater();monitor.deleteLater();qt.processEvents()


def test_lock_broadcast_repairs_a_lost_update_and_serializes_fast_unlock():
    async def run():
        a,b=ChatClient('Ami42',discovery=False,tcp_port=0),ChatClient('Other42',discovery=False,tcp_port=0)
        original=a._send_to
        locked_calls=[];gate=asyncio.Event();finished=asyncio.Event()
        try:
            await a.start();await b.start();a.observe(b.hello(),'127.0.0.1');b.observe(a.hello(),'127.0.0.1')
            for _ in range(400):
                if b.peers[a.sid].version and not a.info_tasks:break
                await asyncio.sleep(.01)
            async def drop(peer,frame):
                if frame['type']=='client_info':return False
                return await original(peer,frame)
            a._send_to=drop;a.set_screen_locked(True)
            for _ in range(400):
                if not a.info_tasks:break
                await asyncio.sleep(.01)
            assert not b.peers[a.sid].locked
            # A later heartbeat resends lock state even when the version is known.
            a._send_to=original;a.info_attempts.clear();a.queue_client_info(a.peers[b.sid])
            for _ in range(400):
                if b.peers[a.sid].locked and not a.info_tasks:break
                await asyncio.sleep(.01)
            assert b.peers[a.sid].locked
            async def slow(peer,frame):
                if frame['type']=='client_info':
                    locked_calls.append(frame['locked'])
                    if not frame['locked']:
                        gate.set();await finished.wait()
                return await original(peer,frame)
            a._send_to=slow;a.set_screen_locked(False)
            await asyncio.wait_for(gate.wait(),2)
            # A new lock while the unlock packet is in flight must go afterwards.
            a.set_screen_locked(True);finished.set()
            for _ in range(400):
                if locked_calls==[False,True] and b.peers[a.sid].locked and not a.info_tasks:break
                await asyncio.sleep(.01)
            assert locked_calls==[False,True] and b.peers[a.sid].locked
        finally:finished.set();await a.stop();await b.stop()
    asyncio.run(run())


def test_avatar_lock_replaces_green_dot_for_illustration_and_photo():
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QColor, QImage, QPainter
    from src.ui.widgets import paint_avatar
    from src.ui.avatar_image import encode_photo
    from src.ui.theme import colors
    from src.ui.sidebar import Sidebar
    qt=QApplication.instance() or QApplication([])
    image=QImage(100,100,QImage.Format.Format_ARGB32);image.fill(QColor('navy'))
    # Both avatar paths share exactly the same vector presence marker.
    photo=encode_photo(image)
    for data in ('',photo):
        for locked in (False,True):
            image.fill(Qt.GlobalColor.transparent);painter=QPainter(image)
            paint_avatar(painter,image.rect(),'Ami42',True,image_data=data,locked=locked);painter.end()
            green=QColor(colors()['online']).rgb()
            count=sum(image.pixel(x,y)==green for x in range(70,100) for y in range(70,100))
            assert (count==0) if locked else (count>30)
    sidebar=Sidebar('Moi42',friends={'b'*32:'Ami42'})
    sidebar.peers={'a'*32:dict(sid='a'*32,contact_id='b'*32,nick='Ami42',status='away',locked=True)}
    sidebar.refresh()
    rows=[sidebar.list.item(i).data(Qt.ItemDataRole.UserRole) for i in range(sidebar.list.count())]
    assert next(row for row in rows if row.get('sid')=='a'*32)['locked']
    sidebar.deleteLater();qt.processEvents()


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
        process.terminate();process.communicate(timeout=5)
