"""Screen UI tests use generated images and fake capture devices only."""
import asyncio
import os
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace
import uuid

import pytest
from PySide6.QtCore import QObject, Signal, Qt
from PySide6.QtGui import QImage,QColor
from PySide6.QtWidgets import QApplication
from src.ui.screen_share import ScreenCaptureSource,ScreenWindow,encode_screen_image,decode_screen_image
from src.ui.private_window import PrivateWindow
from src.features.screen_share import FEATURE,MAX_IMAGE
from src.networking.client import ChatClient
from src.networking.settings import NetworkConfig
from src.app import Application

ROOT=Path(__file__).resolve().parents[1]
ICON=str(ROOT/'assets/icons/fortytchat.svg')


@pytest.fixture(scope='module')
def qt():
    app=QApplication.instance() or QApplication([]);app.setQuitOnLastWindowClosed(False)
    return app


def until(qt,predicate,timeout=7):
    end=time.monotonic()+timeout
    while not predicate():
        qt.processEvents();time.sleep(.005)
        assert time.monotonic()<end,'État du partage Qt non atteint'
    qt.processEvents()


def picture(color='royalblue',width=1024,height=600):
    image=QImage(width,height,QImage.Format.Format_RGB32);image.fill(QColor(color));return image


class FakeCapture(QObject):
    activeChanged=Signal(bool)
    errorOccurred=Signal(object,str)
    def setScreen(self,screen):self.screen=screen
    def start(self):self.started=True;self.activeChanged.emit(True)
    def stop(self):self.started=False;self.activeChanged.emit(False)


class FakeSink(QObject):
    videoFrameChanged=Signal(object)


class FakeSession(QObject):
    def setScreenCapture(self,capture):self.capture=capture
    def setVideoSink(self,sink):self.sink=sink


def capture_factory(created):
    def create(parent):
        backend=FakeSession(parent),FakeCapture(parent),FakeSink(parent)
        created.append(backend);return backend
    return create


def test_screen_codec_scales_and_rejects_wrong_size_or_oversized_images(qt):
    from PySide6.QtCore import QByteArray,QBuffer,QIODevice
    image=picture('tomato',3000,1800);image.setText('private','NOT_FOR_THE_NETWORK')
    data,width,height=encode_screen_image(image)
    assert 0<len(data)<=MAX_IMAGE and width<=1280 and height<=720
    result=decode_screen_image(data,width,height)
    assert not result.isNull() and not result.text('private') and b'NOT_FOR_THE_NETWORK' not in data
    assert decode_screen_image(data,width+1,height).isNull()
    assert decode_screen_image(b'x'*(MAX_IMAGE+1),width,height).isNull()
    assert decode_screen_image(b'not an image',100,100).isNull()
    raw=QByteArray();buffer=QBuffer(raw);buffer.open(QIODevice.OpenModeFlag.WriteOnly);image.save(buffer,'PNG')
    assert decode_screen_image(bytes(raw),3000,1800).isNull()


def test_capture_source_backpressure_and_old_frames_cannot_cross_restarts(qt):
    from PySide6.QtMultimedia import QVideoFrame
    created=[];clock=[10.0]
    source=ScreenCaptureSource(backend_factory=capture_factory(created),clock=lambda:clock[0])
    frames=[];errors=[];source.encoded.connect(lambda *args:frames.append(args));source.failed.connect(errors.append)
    try:
        assert source.start();first=created[-1]
        first[2].videoFrameChanged.emit(QVideoFrame(picture('red')))
        source.stop()  # Queued old pixels must not appear in a new session.
        assert source.start();second=created[-1]
        second[2].videoFrameChanged.emit(QVideoFrame(picture('blue')))
        until(qt,lambda:len(frames)==1)
        assert decode_screen_image(*frames[0]).pixelColor(5,5).blue()>200
        clock[0]+=.3;source.process();assert len(frames)==1
        source.released();source.process();assert len(frames)==2
        source.released();clock[0]+=6;source.process()
        assert errors and not source.running and not source.timer.isActive() and source.latest is None
    finally:source.stop();source.deleteLater();qt.processEvents()


def make_app(qt,tmp_path,monkeypatch):
    monkeypatch.setenv('XDG_CONFIG_HOME',str(tmp_path/'config'))
    monkeypatch.setattr(NetworkConfig,'load',classmethod(lambda cls,path:NetworkConfig(mode='relay',relay_host='')))
    return Application(ROOT,SimpleNamespace(dev='screen-'+uuid.uuid4().hex[:8],background=True,insecure_local_relay=None))


def connect_peer(app,qt):
    logs=[];peer=ChatClient('Alice42',lambda k,d:logs.append((k,d)),discovery=False,tcp_port=0)
    app.worker.submit(peer.start()).result(4);client=app.worker.client
    def announce():client.observe(peer.hello(),'127.0.0.1');peer.observe(client.hello(),'127.0.0.1')
    app.worker.loop.call_soon_threadsafe(announce)
    until(qt,lambda:peer.sid in app.window.peers and FEATURE in app.window.peers[peer.sid].get('features',()))
    return peer,logs


def test_real_sender_ui_starts_after_acceptance_and_tray_stops_capture(qt,tmp_path,monkeypatch):
    from PySide6.QtMultimedia import QVideoFrame
    created=[]
    class Source(ScreenCaptureSource):
        def __init__(self,parent):super().__init__(parent,backend_factory=capture_factory(created))
        @staticmethod
        def availability():return True,''
    monkeypatch.setattr('src.screen_share.ScreenWindow',lambda nick,icon,outgoing=False:ScreenWindow(nick,icon,outgoing=outgoing,source_factory=Source))
    app=make_app(qt,tmp_path,monkeypatch);peer=None
    try:
        peer,logs=connect_peer(app,qt);view=app.open_private(peer.sid)
        assert view.screen_button.isEnabled();view.screen_button.click();qt.processEvents()
        window=app.screen_window;assert window and not created
        window.start_button.click();share=window.share
        until(qt,lambda:peer.screens.state is not None)
        assert share and not created and not window.source.running
        app.worker.submit(peer.screens.answer(share,True)).result(4)
        until(qt,lambda:bool(created) and window.source.running)
        assert app.tray.screen_stop_action.isVisible()
        created[0][2].videoFrameChanged.emit(QVideoFrame(picture('orange')))
        until(qt,lambda:any(k=='screen_frame' for k,d in logs))
        assert window.canvas.image.isNull()  # No recursive local screen preview.
        app.tray.screen_stop_action.trigger()
        assert not window.source.running and window.source.latest is None
        until(qt,lambda:peer.screens.state is None and app.worker.client.screens.state is None)
        assert not app.tray.screen_stop_action.isVisible()
        # Cancel immediately, before the network event has reached the GUI.
        window.start_button.click();cancelled=window.share;window.stop_button.click()
        until(qt,lambda:(peer.sid,cancelled) in app.worker.client.screens.recent)
        qt.processEvents();assert not window.source.running and len(created)==1
        assert window.share==''
    finally:
        if peer:app.worker.submit(peer.stop()).result(4)
        app.cleanup();app.window.close();app.deleteLater();qt.processEvents()


def test_real_receiver_asks_permission_displays_and_clears_images(qt,tmp_path,monkeypatch):
    app=make_app(qt,tmp_path,monkeypatch);peer=None
    try:
        peer,logs=connect_peer(app,qt)
        share=app.worker.submit(peer.screens.offer(app.worker.client.sid)).result(4)
        until(qt,lambda:app.screen_prompt is not None)
        assert app.screen_window is None
        app.screen_prompt.accept_button.click()
        until(qt,lambda:app.screen_window is not None and peer.screens.state.phase=='active')
        data,width,height=encode_screen_image(picture('seagreen'))
        assert app.worker.submit(peer.screens.send_frame(share,data,width,height)).result(4)
        until(qt,lambda:not app.screen_window.canvas.image.isNull())
        window=app.screen_window
        assert window.canvas.image.pixelColor(3,3).green()>70
        window.stop_button.click();assert window.canvas.image.isNull()
        until(qt,lambda:peer.screens.state is None)
        assert not list(app.store.root.rglob('*.jpg')) and not list(app.store.root.rglob('*.png'))
        peer.screens.invites.tokens=2
        share=app.worker.submit(peer.screens.offer(app.worker.client.sid)).result(4)
        until(qt,lambda:app.screen_prompt is not None)
        app.screen_prompt.reject()
        until(qt,lambda:peer.screens.state is None)
        assert app.screen_window is None or app.screen_window.canvas.image.isNull()
    finally:
        if peer:app.worker.submit(peer.stop()).result(4)
        app.cleanup();app.window.close();app.deleteLater();qt.processEvents()


def test_legacy_contact_can_chat_but_screen_button_is_disabled(qt):
    view=PrivateWindow('a'*32,'Old42',[],ICON)
    view.set_peer(dict(sid='a'*32,nick='Old42',status='online',contact_id='b'*32,encrypted=True,caps=['cards'],features=[]))
    assert not view.screen_button.isEnabled() and view.input.isEnabled()
    view.deleteLater();qt.processEvents()


@pytest.mark.parametrize('scale',['1','1.5','2'])
def test_screen_windows_are_readable_at_scales(scale):
    code='''
from PySide6.QtWidgets import QApplication,QLabel
from PySide6.QtCore import QObject,Signal
from src.ui.screen_share import ScreenWindow
from src.ui.theme import apply_theme
app=QApplication([])
class Source(QObject):
    encoded=Signal(object,int,int)
    failed=Signal(str)
    running=False
    @staticmethod
    def availability():return True,''
    def start(self,screen=None):self.running=True
    def stop(self):self.running=False
    def released(self):pass
for theme in ('dark','light'):
    apply_theme(app,theme)
    app.setStyleSheet(app.styleSheet()+' QWidget { font-size:20px; }')
    for outgoing in (False,True):
        view=ScreenWindow('W'*28,'assets/icons/fortytchat.svg',outgoing=outgoing,source_factory=Source)
        view.resize(480,520);view.show();view.set_state('a'*32,'active')
        for _ in range(8):app.processEvents()
        for text in view.findChildren(QLabel):
            if text.wordWrap() and text.text():assert text.height()>=text.heightForWidth(text.width()),text.text()
        assert view.stop_button.height()>=view.stop_button.sizeHint().height()
        assert view.stop_button.geometry().right()<=view.centralWidget().width()
        assert view.stop_button.mapTo(view.centralWidget(),view.stop_button.rect().bottomRight()).y()<view.centralWidget().height()
        view.close();view.deleteLater();app.processEvents()
'''
    result=subprocess.run([sys.executable,'-c',code],cwd=ROOT,
        env={**os.environ,'QT_QPA_PLATFORM':'offscreen','QT_SCALE_FACTOR':scale},capture_output=True,text=True,timeout=20)
    assert result.returncode==0,result.stdout+result.stderr


def test_selected_screen_removal_stops_capture_instead_of_switching_monitor(qt):
    created=[];source=ScreenCaptureSource(backend_factory=capture_factory(created));errors=[]
    source.failed.connect(errors.append)
    try:
        screen=qt.primaryScreen();assert source.start(screen)
        source.screen_removed(object());assert source.running
        source.screen_removed(screen)
        assert errors and not source.running and source.selected_screen is None and source.latest is None
    finally:source.stop();source.deleteLater();qt.processEvents()


def test_wayland_selection_is_left_to_desktop_and_xwayland_is_explained(qt,monkeypatch):
    created=[]
    class Source(ScreenCaptureSource):
        def __init__(self,parent):super().__init__(parent,backend_factory=capture_factory(created))
        @staticmethod
        def availability():return True,''
    monkeypatch.setenv('XDG_SESSION_TYPE','wayland')
    view=ScreenWindow('Alice42',ICON,outgoing=True,source_factory=Source)
    try:
        assert view.portal and view.sources.currentData() is None and not created
        view.set_state('a'*32,'active');assert len(created)==1
        assert not hasattr(created[0][1],'screen')
        monkeypatch.setattr('src.ui.screen_share.QGuiApplication.platformName',lambda:'xcb')
        supported,reason=ScreenCaptureSource.availability()
        assert not supported and 'QT_QPA_PLATFORM=wayland' in reason
    finally:view.close();view.deleteLater();qt.processEvents()
