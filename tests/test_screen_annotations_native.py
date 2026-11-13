"""Input transparency is tested only on a new, isolated virtual X11 display."""
import os
from pathlib import Path
import selectors
import shutil
import subprocess
import sys
import pytest


def test_native_overlay_is_visible_and_local_clicks_reach_the_streamer_app(tmp_path):
    executable=os.environ.get('FORTYTCHAT_XVFB') or shutil.which('Xvfb')
    if not executable:pytest.skip('Xvfb absent : aucun essai sur le bureau personnel')
    from ctypes.util import find_library
    if not all(find_library(name) for name in ('X11','Xtst')):pytest.skip('Bibliothèques X11 de test absentes')
    server=subprocess.Popen([executable,'-displayfd','1','-screen','0','1024x768x24','-nolisten','tcp','-ac'],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(server.stdout,selectors.EVENT_READ);assert selector.select(5)
        number=server.stdout.readline().strip();assert number.isdecimal()
        code='''
import ctypes
from ctypes.util import find_library
import sys,time
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication,QPushButton
from src.ui.screen_annotations import AnnotationLayer,AnnotationOverlay
app=QApplication([])
backdrop=QPushButton('L’application du streamer reste cliquable')
backdrop.setStyleSheet('background:#254b84;color:white;font-size:25px;border:0;')
backdrop.setGeometry(app.primaryScreen().geometry());backdrop.show()
clicks=[];backdrop.clicked.connect(lambda:clicks.append(True))
for _ in range(20):app.processEvents();time.sleep(.01)
screen=app.primaryScreen();layer=AnnotationLayer();overlay=AnnotationOverlay(layer,screen)
try:
    assert AnnotationOverlay.supported()
    assert overlay.windowFlags() & Qt.WindowType.WindowTransparentForInput
    assert overlay.windowFlags() & Qt.WindowType.WindowDoesNotAcceptFocus
    layer.apply(dict(action='rectangle',points=[[1000,1000],[9000,9000]],color=0))
    layer.apply(dict(action='ellipse',points=[[1500,2000],[4500,7000]],color=1))
    layer.apply(dict(action='pen',points=[[2000,8000],[7000,8000]],color=3,width=12))
    layer.apply(dict(action='pointer',points=[[5000,5000]],color=0))
    for _ in range(20):app.processEvents();time.sleep(.01)
    assert overlay.isVisible() and overlay.geometry()==screen.geometry()
    captured=screen.grabWindow(0).toImage()
    assert captured.pixelColor(10,10).blue()>100  # The rest of the desktop is visible even without a compositor.
    assert any(captured.pixelColor(x,y).red()>200 for x in range(510,540) for y in range(380,415))
    assert captured.pixelColor(500,618).name()=='#a5e46b'  # Thick strokes must not be clipped by the X11 mask.
    assert captured.save(sys.argv[1])
    # Only this isolated X server receives the following synthetic local click.
    xlib=ctypes.CDLL(find_library('X11'));xtst=ctypes.CDLL(find_library('Xtst'))
    xlib.XOpenDisplay.argtypes=[ctypes.c_char_p];xlib.XOpenDisplay.restype=ctypes.c_void_p
    display=xlib.XOpenDisplay(None);assert display
    xlib.XSync.argtypes=[ctypes.c_void_p,ctypes.c_int]
    xlib.XCloseDisplay.argtypes=[ctypes.c_void_p]
    xtst.XTestFakeMotionEvent.argtypes=[ctypes.c_void_p,ctypes.c_int,ctypes.c_int,ctypes.c_int,ctypes.c_ulong]
    xtst.XTestFakeButtonEvent.argtypes=[ctypes.c_void_p,ctypes.c_uint,ctypes.c_int,ctypes.c_ulong]
    try:
        xtst.XTestFakeMotionEvent(display,-1,518,395,0)
        xtst.XTestFakeButtonEvent(display,1,1,0);xtst.XTestFakeButtonEvent(display,1,0,0);xlib.XSync(display,False)
        end=time.monotonic()+3
        while not clicks and time.monotonic()<end:app.processEvents();time.sleep(.01)
        assert clicks==[True],clicks
    finally:xlib.XCloseDisplay(display)
    layer.clear();app.processEvents();assert not overlay.isVisible()
finally:overlay.close();backdrop.close();app.processEvents()
'''
        screenshot=tmp_path/'streamer-overlay.png'
        result=subprocess.run([sys.executable,'-c',code,str(screenshot)],cwd=Path(__file__).resolve().parents[1],
            env={**os.environ,'QT_QPA_PLATFORM':'xcb','DISPLAY':':'+number,'XDG_SESSION_TYPE':'x11',
                 'DBUS_SESSION_BUS_ADDRESS':'unix:path=/tmp/fortytchat-test-no-session-bus'},capture_output=True,text=True,timeout=15)
        assert result.returncode==0,result.stdout+result.stderr
        assert screenshot.is_file()
    finally:server.terminate();server.communicate(timeout=5)
