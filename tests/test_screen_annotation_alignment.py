"""Round-trip annotation alignment including docks, panels, DPI and old viewers."""
import io
import os
from pathlib import Path
import selectors
import shutil
import subprocess
import sys
import tarfile

import pytest
from PySide6.QtCore import QPoint, QPointF, QRect, QRectF
from PySide6.QtWidgets import QApplication
from src.ui.screen_annotations import AnnotationLayer, AnnotationOverlay, mapped

ROOT=Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('screen_rect,origin',[(QRect(0,0,1920,1080),QPoint(64,24)),
    (QRect(1920,0,2560,1440),QPoint(1984,24)),(QRect(-1280,-200,1280,1024),QPoint(-1216,-176))])
def test_overlay_uses_captured_screen_not_its_moved_or_resized_workarea(screen_rect,origin):
    app=QApplication.instance() or QApplication([])
    layer=AnnotationLayer();overlay=AnnotationOverlay(layer,app.primaryScreen())
    class Screen:
        def geometry(self):return screen_rect
    original=overlay.target_screen
    try:
        overlay.target_screen=Screen()
        overlay.move(origin);overlay.resize(screen_rect.width()-64,screen_rect.height()-24)
        actual=overlay.mapToGlobal(QPoint(0,0))
        for point in ((0,0),(1000,1500),(5000,5000),(10000,10000)):
            local=mapped(point,overlay.annotation_rect())
            global_point=local+QPointF(actual)
            expected=QPointF(screen_rect.left()+point[0]*(screen_rect.width()-1)/10000,
                            screen_rect.top()+point[1]*(screen_rect.height()-1)/10000)
            assert (global_point-expected).manhattanLength()<.001
    finally:
        overlay.target_screen=original;overlay.close();overlay.deleteLater();layer.deleteLater();app.processEvents()


@pytest.fixture(scope='module')
def old_viewer(tmp_path_factory):
    repo=Path(os.environ.get('FORTYTCHAT_COMPAT_REPO',ROOT))
    archive=subprocess.run(['git','archive','6f537a3','src'],cwd=repo,capture_output=True)
    if archive.returncode:pytest.skip('Archive 2.5.2 absente : comparaison native avec ancien client non exécutée')
    root=tmp_path_factory.mktemp('alignment-legacy');package=root/'legacy_alignment';package.mkdir()
    with tarfile.open(fileobj=io.BytesIO(archive.stdout)) as source:
        for member in source.getmembers():
            parts=Path(member.name).parts
            if member.isfile() and parts[0]=='src' and '..' not in parts:
                target=package.joinpath(*parts[1:]);target.parent.mkdir(parents=True,exist_ok=True)
                target.write_bytes(source.extractfile(member).read())
    return root


@pytest.mark.parametrize('scale',['1','1.25','1.5','2'])
@pytest.mark.parametrize('legacy_viewer',[False,True])
def test_native_panel_workarea_reproduces_old_offset_and_corrects_new_overlay(tmp_path,old_viewer,scale,legacy_viewer):
    executable=os.environ.get('FORTYTCHAT_XVFB') or shutil.which('Xvfb')
    if not executable:pytest.skip('Xvfb absent : aucun bureau personnel utilisé')
    server=subprocess.Popen([executable,'-displayfd','1','-screen','0','1280x720x24','-nolisten','tcp','-ac'],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    manager=None
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(server.stdout,selectors.EVENT_READ);assert selector.select(5)
        display=server.stdout.readline().strip();assert display.isdecimal()
        env={**os.environ,'QT_QPA_PLATFORM':'xcb','DISPLAY':':'+display,'XDG_SESSION_TYPE':'x11',
             'QT_SCALE_FACTOR':scale,'FORTYTCHAT_ISOLATED_X11':'1',
             'DBUS_SESSION_BUS_ADDRESS':'unix:path=/tmp/fortytchat-test-no-session-bus'}
        manager=subprocess.Popen([sys.executable,str(ROOT/'tests/x11_workarea_manager.py')],env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        with selectors.DefaultSelector() as selector:
            selector.register(manager.stdout,selectors.EVENT_READ);assert selector.select(5)
        assert manager.stdout.readline().strip()=='ready'
        code=r'''
import sys,time
from PySide6.QtCore import QPoint,QPointF,QRectF,Qt,QEvent
from PySide6.QtGui import QImage,QColor,QPainter,QMouseEvent
from PySide6.QtWidgets import QApplication,QWidget
sys.path.insert(0,sys.argv[1])
from legacy_alignment.ui.screen_annotations import AnnotationOverlay as OldOverlay
from legacy_alignment.ui.screen_share import ScreenCanvas as OldCanvas
from src.ui.screen_annotations import AnnotationLayer,AnnotationOverlay,mapped
from src.ui.screen_share import ScreenCanvas,encode_screen_image
app=QApplication([]);screen=app.primaryScreen()
def until(test):
    end=time.monotonic()+4
    while not test():
        app.processEvents();time.sleep(.005)
        assert time.monotonic()<end
def settle():
    for _ in range(20):app.processEvents();time.sleep(.005)
# The manager must really constrain a normal window before testing the fix.
control=QWidget();control.setGeometry(screen.geometry());control.show()
until(lambda:control.x()>0 and control.width()<screen.geometry().width())
control.hide()
backdrop=QWidget(None,Qt.WindowType.FramelessWindowHint|Qt.WindowType.X11BypassWindowManagerHint)
backdrop.setStyleSheet('background:#254b84;');backdrop.setGeometry(screen.geometry());backdrop.show()
layer=AnnotationLayer();viewer_layer=AnnotationLayer()
canvas=OldCanvas() if sys.argv[2]=='1' else ScreenCanvas()
canvas.resize(1000,850)
image=QImage(1280,720,QImage.Format.Format_RGB32);image.fill(QColor('#254b84'))
assert canvas.set_frame(*encode_screen_image(image))
canvas.configure(viewer_layer,True);canvas.tool='rectangle'
packets=[]
def submit(payload):packets.append(payload);layer.apply(payload)
canvas.annotation_submitted.connect(submit)
area=canvas.image_rect()
assert area.top()>0
# Choose a location inside the left dock area plus a second point in the desktop.
start=QPointF(area.left()+area.width()*.025,area.top()+area.height()*.08)
end=QPointF(area.left()+area.width()*.35,area.top()+area.height()*.48)
def mouse(kind,position,button,buttons):
    app.sendEvent(canvas,QMouseEvent(kind,position,position,button,buttons,Qt.KeyboardModifier.NoModifier))
mouse(QEvent.Type.MouseButtonPress,start,Qt.MouseButton.LeftButton,Qt.MouseButton.LeftButton)
mouse(QEvent.Type.MouseMove,end,Qt.MouseButton.NoButton,Qt.MouseButton.LeftButton)
mouse(QEvent.Type.MouseButtonRelease,end,Qt.MouseButton.LeftButton,Qt.MouseButton.NoButton)
assert len(packets)==1
point=packets[0]['points'][0]
expected=mapped(point,QRectF(screen.geometry()))
old=OldOverlay(layer,screen)
until(lambda:old.x()>0 and old.width()<screen.geometry().width())
wrong=mapped(point,QRectF(old.rect()))+QPointF(old.mapToGlobal(QPoint(0,0)))
assert (wrong-expected).manhattanLength()>15,(wrong,expected)
old.close();old.deleteLater();settle()
overlay=AnnotationOverlay(layer,screen);settle()
assert overlay.geometry()==screen.geometry(),(overlay.geometry(),screen.geometry())
assert overlay.windowFlags() & Qt.WindowType.WindowTransparentForInput
assert overlay.windowFlags() & Qt.WindowType.WindowDoesNotAcceptFocus
assert overlay.windowFlags() & Qt.WindowType.X11BypassWindowManagerHint
actual=mapped(point,overlay.annotation_rect())+QPointF(overlay.mapToGlobal(QPoint(0,0)))
assert (actual-expected).manhattanLength()<.01,(actual,expected)
captured=screen.grabWindow(0).toImage()
ratio=captured.width()/screen.geometry().width()
x,y=round(expected.x()*ratio),round(expected.y()*ratio)
assert any(captured.pixelColor(px,py).red()>200 and captured.pixelColor(px,py).blue()>100
           for px in range(max(0,x-3),min(captured.width(),x+4))
           for py in range(max(0,y-3),min(captured.height(),y+4))),(x,y,ratio)
assert captured.save(sys.argv[3])
# The annotated frame must return to the exact original place in the viewer too.
returned=ScreenCanvas();returned.resize(canvas.size())
assert returned.set_frame(*encode_screen_image(captured))
position=mapped(point,QRectF(returned.image_rect()))
assert (position-start).manhattanLength()<2
layer.clear();settle();assert not overlay.isVisible()
overlay.close();backdrop.close();canvas.close();returned.close();control.close();app.processEvents()
'''
        result=subprocess.run([sys.executable,'-c',code,str(old_viewer),'1' if legacy_viewer else '0',str(tmp_path/'aligned.png')],cwd=ROOT,env=env,capture_output=True,text=True,timeout=15)
        assert result.returncode==0,result.stdout+result.stderr
    finally:
        if manager:manager.terminate();manager.communicate(timeout=5)
        server.terminate();server.communicate(timeout=5)
