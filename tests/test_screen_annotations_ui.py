import os
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace
import uuid

import pytest
from PySide6.QtCore import QPointF, QEvent, Qt
from PySide6.QtGui import QImage, QColor, QMouseEvent
from PySide6.QtWidgets import QApplication
from src.ui.screen_share import ScreenWindow, encode_screen_image
from src.app import Application
from src.networking.client import ChatClient
from src.networking.settings import NetworkConfig
from src.features.screen_annotations import FEATURE

ROOT=Path(__file__).resolve().parents[1]
ICON=str(ROOT/'assets/icons/fortytchat.svg')


@pytest.fixture(scope='module')
def qt():
    app=QApplication.instance() or QApplication([]);app.setQuitOnLastWindowClosed(False);return app


def until(qt,check):
    end=time.monotonic()+5
    while not check():
        qt.processEvents();time.sleep(.005)
        assert time.monotonic()<end
    qt.processEvents()


def image():
    value=QImage(1000,500,QImage.Format.Format_RGB32);value.fill(QColor('#254b84'));return encode_screen_image(value)


def mouse(widget,kind,point,button=Qt.MouseButton.NoButton,buttons=Qt.MouseButton.NoButton):
    QApplication.sendEvent(widget,QMouseEvent(kind,QPointF(point),QPointF(point),button,buttons,Qt.KeyboardModifier.NoModifier))


def draw(canvas,start,end):
    mouse(canvas,QEvent.Type.MouseButtonPress,start,Qt.MouseButton.LeftButton,Qt.MouseButton.LeftButton)
    mouse(canvas,QEvent.Type.MouseMove,end,buttons=Qt.MouseButton.LeftButton)
    mouse(canvas,QEvent.Type.MouseButtonRelease,end,Qt.MouseButton.LeftButton)


@pytest.mark.parametrize('tool',['pen','ellipse','rectangle'])
def test_viewer_maps_only_image_area_and_keeps_marks_after_new_frames_and_resize(qt,tool):
    view=ScreenWindow('Alice42',ICON);view.resize(700,700);view.show()
    try:
        view.set_state('a'*32,'active',True);assert not view.canvas.annotating
        view.receive_frame(*image());qt.processEvents()
        canvas=view.canvas;canvas.tool=tool;canvas.color=2;payloads=[]
        canvas.annotation_submitted.connect(payloads.append)
        rect=canvas.image_rect()
        assert rect.top()>0  # Letterboxing is never an annotation target.
        draw(canvas,QPointF(10,2),QPointF(30,3));assert not payloads
        start=QPointF(rect.left()+rect.width()*.2,rect.top()+rect.height()*.2)
        end=QPointF(rect.left()+rect.width()*.8,rect.top()+rect.height()*.8)
        draw(canvas,start,end)
        assert payloads[-1]['action']==tool and payloads[-1]['color']==2
        assert len(view.annotation_layer.board.shapes)==1
        before=tuple(view.annotation_layer.board.shapes)
        mouse(canvas,QEvent.Type.MouseButtonPress,start,Qt.MouseButton.LeftButton,Qt.MouseButton.LeftButton)
        view.receive_frame(*image());assert canvas.drag  # The next video frame must not cancel a drawing.
        canvas.drag=[];view.resize(850,650);qt.processEvents()
        assert tuple(view.annotation_layer.board.shapes)==before
        view.clear_annotations();assert not view.annotation_layer.board.shapes
        assert view.annotation_queue[-1]['action']=='clear'
        view.finish('Terminé');assert not view.annotation_timer.isActive() and not canvas.annotating
        assert not view.annotation_layer.board.shapes and not view.annotation_queue
    finally:view.close();view.deleteLater();qt.processEvents()


def test_pointer_coalesces_moves_and_only_one_network_operation_is_inflight(qt):
    view=ScreenWindow('Alice42',ICON);sent=[];view.annotation_requested.connect(sent.append)
    try:
        view.set_state('a'*32,'active',True);view.receive_frame(*image())
        for i in range(1000):view.pointer_annotation(dict(action='pointer',points=[[i,1000]],color=0))
        assert not view.annotation_queue
        view.flush_annotation();assert len(sent)==1 and sent[0]['points']==[[999,1000]]
        view.flush_annotation();assert len(sent)==1
        view.annotation_done(True)
        view.pointer_annotation(dict(action='hide',points=[],color=0));view.flush_annotation()
        assert sent[-1]['action']=='hide' and view.annotation_layer.board.pointer is None
        view.finish('Terminé');view.annotation_done(True)
        assert not view.annotation_busy and view.annotation_cursor is None
    finally:view.close();view.deleteLater();qt.processEvents()


def test_older_streamer_keeps_video_without_new_tools(qt):
    view=ScreenWindow('Old42',ICON)
    try:
        view.set_state('a'*32,'active',False);view.receive_frame(*image())
        assert not view.canvas.annotating and view.annotation_tools.isHidden()
        assert '2.5.0' in view.annotation_hint.text()
        assert not view.canvas.image.isNull()
    finally:view.close();view.deleteLater();qt.processEvents()


def test_clear_discards_queued_drawings_and_waits_for_viewer_echo(qt):
    viewer=ScreenWindow('Alice42',ICON)
    try:
        viewer.set_state('a'*32,'active',True);viewer.receive_frame(*image())
        rectangle=dict(action='rectangle',points=[[1000,2000],[7000,8000]],color=0)
        clear=dict(action='clear',points=[],color=0)
        viewer.queue_annotation(rectangle);viewer.receive_annotation(clear)
        assert list(viewer.annotation_queue)==[clear] and not viewer.annotation_layer.board.shapes
        viewer.outgoing=True  # Exercise the streamer half without opening a capture device.
        viewer.clear_annotations();assert viewer.annotation_clear_pending
        viewer.receive_annotation(rectangle);assert not viewer.annotation_layer.board.shapes
        viewer.receive_annotation(clear);assert not viewer.annotation_clear_pending
        viewer.receive_annotation(rectangle);assert viewer.annotation_layer.board.shapes
    finally:viewer.close();viewer.deleteLater();qt.processEvents()


def test_wayland_streamer_sees_annotations_in_preview_without_guessing_screen(qt,monkeypatch):
    from PySide6.QtCore import QObject,Signal
    class Source(QObject):
        encoded=Signal(object,int,int);failed=Signal(str);running=False
        @staticmethod
        def availability():return True,''
        def start(self,screen=None):self.running=True
        def stop(self):self.running=False
    monkeypatch.setattr('src.ui.screen_share.QGuiApplication.platformName',lambda:'wayland')
    view=ScreenWindow('Bob42',ICON,outgoing=True,source_factory=Source)
    try:
        view.set_state('a'*32,'active',True);view.frame_ready(*image())
        view.receive_annotation(dict(action='ellipse',points=[[1000,1000],[8000,7000]],color=1))
        assert view.portal and view.overlay is None and not view.canvas.image.isNull()
        assert view.annotation_layer.board.shapes and not view.canvas.annotating
        assert 'aperçu' in view.annotation_hint.text()
        view.finish('Terminé');assert not view.annotation_layer.board.shapes
    finally:view.close();view.deleteLater();qt.processEvents()


def test_real_controller_viewer_sends_shapes_and_receives_streamer_clear(qt,tmp_path,monkeypatch):
    monkeypatch.setenv('XDG_CONFIG_HOME',str(tmp_path/'config'))
    monkeypatch.setattr(NetworkConfig,'load',classmethod(lambda cls,path:NetworkConfig(mode='relay',relay_host='')))
    app=Application(ROOT,SimpleNamespace(dev='annotations-'+uuid.uuid4().hex[:10],background=True,insecure_local_relay=None))
    logs=[];peer=ChatClient('Alice42',lambda k,d:logs.append((k,d)),discovery=False,tcp_port=0)
    try:
        app.worker.submit(peer.start()).result(4);client=app.worker.client
        def announce():client.observe(peer.hello(),'127.0.0.1');peer.observe(client.hello(),'127.0.0.1')
        app.worker.loop.call_soon_threadsafe(announce)
        until(qt,lambda:peer.sid in app.window.peers and FEATURE in app.window.peers[peer.sid].get('features',()))
        share=app.worker.submit(peer.screens.offer(client.sid)).result(4)
        until(qt,lambda:app.screen_prompt is not None);app.screen_prompt.accept_button.click()
        until(qt,lambda:app.screen_window is not None and app.screen_window.annotations_enabled)
        assert app.worker.submit(peer.screens.send_frame(share,*image())).result(4)
        until(qt,lambda:not app.screen_window.canvas.image.isNull())
        view=app.screen_window;qt.processEvents();canvas=view.canvas;canvas.tool='rectangle'
        rect=canvas.image_rect();draw(canvas,QPointF(rect.topLeft())+QPointF(20,20),QPointF(rect.center()))
        until(qt,lambda:any(k=='screen_annotation' for k,d in logs))
        assert [d for k,d in logs if k=='screen_annotation'][-1]['payload']['action']=='rectangle'
        assert app.worker.submit(peer.screens.annotate(share,dict(action='clear',points=[],color=0))).result(4)
        until(qt,lambda:not view.annotation_layer.board.shapes)
        assert app.worker.submit(peer.screens.stop(share)).result(4)
        until(qt,lambda:view.phase=='ready');assert not view.annotation_timer.isActive()
        assert not list(app.store.root.rglob('*annotation*'))
    finally:
        app.worker.submit(peer.stop()).result(4);app.cleanup();app.window.close();app.deleteLater();qt.processEvents()


@pytest.mark.parametrize('scale',['1','1.5','2'])
def test_annotation_tools_fit_scaled_windows(scale):
    code='''
from PySide6.QtWidgets import QApplication,QLabel
from src.ui.screen_share import ScreenWindow
from src.ui.theme import apply_theme
app=QApplication([])
for theme in ('dark','light'):
    apply_theme(app,theme)
    app.setStyleSheet(app.styleSheet()+' QWidget {font-size:20px;}')
    view=ScreenWindow('Alice42','assets/icons/fortytchat.svg')
    view.resize(480,580);view.show();view.set_state('a'*32,'active',True)
    for _ in range(5):app.processEvents()
    for item in (view.annotation_tool,view.annotation_color,view.annotation_clear,view.stop_button):
        assert item.height()>=item.fontMetrics().height()
        assert item.mapTo(view.centralWidget(),item.rect().bottomRight()).x()<view.centralWidget().width()
        assert item.mapTo(view.centralWidget(),item.rect().bottomRight()).y()<view.centralWidget().height()
    view.close();view.deleteLater();app.processEvents()
'''
    result=subprocess.run([sys.executable,'-c',code],cwd=ROOT,env={**os.environ,'QT_QPA_PLATFORM':'offscreen','QT_SCALE_FACTOR':scale},capture_output=True,text=True,timeout=15)
    assert result.returncode==0,result.stdout+result.stderr
