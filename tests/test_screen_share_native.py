"""Optional real Qt screen capture, exclusively on a new isolated Xvfb display."""
import os
from pathlib import Path
import selectors
import shutil
import subprocess
import sys
import pytest


def test_native_x11_capture_encrypted_delivery_and_decoding_on_virtual_screen():
    executable=os.environ.get('FORTYTCHAT_XVFB') or shutil.which('Xvfb')
    if not executable:pytest.skip('Xvfb absent : capture native non testée sur le bureau personnel')
    server=subprocess.Popen([executable,'-displayfd','1','-screen','0','1024x768x24','-nolisten','tcp','-ac'],
                            stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(server.stdout,selectors.EVENT_READ)
            assert selector.select(5),'Écran virtuel non démarré'
        number=server.stdout.readline().strip();assert number.isdecimal()
        code='''
import time
from PySide6.QtCore import QObject,Signal,Slot,Qt
from PySide6.QtWidgets import QApplication,QWidget,QVBoxLayout,QLabel
from src.ui.screen_share import ScreenCaptureSource,decode_screen_image
from src.networking.worker import NetworkWorker
from src.features.screen_share import FEATURE
app=QApplication([])
window=QWidget();window.setStyleSheet('background:#254b84; color:white; font-size:32px;')
box=QVBoxLayout(window);box.addWidget(QLabel('FortyTchat : écran virtuel de test'))
window.showFullScreen()
source=ScreenCaptureSource();errors=[];source.failed.connect(errors.append)
a=NetworkWorker('Alice42',discovery=False,tcp_port=0)
b=NetworkWorker('Bob42',discovery=False,tcp_port=0)
class Bridge(QObject):
    release=Signal()
    def __init__(self):super().__init__();self.received=[]
    @Slot(str,object)
    def receive_network(self,kind,data):
        if kind=='screen_frame':
            image=decode_screen_image(data['data'],data['width'],data['height'])
            assert not image.isNull()
            assert image.pixelColor(15,15).blue()>image.pixelColor(15,15).red()+20
            self.received.append(image.size())
bridge=Bridge();bridge.release.connect(source.released,Qt.ConnectionType.QueuedConnection)
b.received.connect(bridge.receive_network)
def until(test,seconds=10):
    end=time.monotonic()+seconds
    while not test():
        app.processEvents();time.sleep(.005)
        assert time.monotonic()<end,errors
try:
    a.start().result(4);b.start().result(4)
    a.call('observe',b.client.hello(),'127.0.0.1');b.call('observe',a.client.hello(),'127.0.0.1')
    until(lambda:b.client.sid in a.client.peers and FEATURE in a.client.peers[b.client.sid].features)
    share=a.offer_screen(b.client.sid).result(4)
    assert share and not source.running
    assert b.answer_screen(share,True).result(4)
    def transmit(data,width,height):
        future=a.screen_frame(share,data,width,height)
        future.add_done_callback(lambda result:bridge.release.emit())
    source.encoded.connect(transmit)
    assert source.start(app.primaryScreen())
    until(lambda:len(bridge.received)>=3)
    source.stop();b.stop_screen(share).result(4)
    until(lambda:a.client.screens.state is None)
    assert source.latest is None and not source.timer.isActive()
    print('Capture X11, chiffrement, TCP et décodage vérifiés :',bridge.received)
finally:
    source.stop();a.stop();b.stop();window.close();app.processEvents()
'''
        result=subprocess.run([sys.executable,'-c',code],cwd=Path(__file__).resolve().parents[1],
            env={**os.environ,'QT_QPA_PLATFORM':'xcb','DISPLAY':':'+number,'XDG_SESSION_TYPE':'x11',
                 'DBUS_SESSION_BUS_ADDRESS':'unix:path=/tmp/fortytchat-test-no-session-bus'},
            text=True,capture_output=True,timeout=25)
        assert result.returncode==0,result.stdout+result.stderr
    finally:
        server.terminate();server.communicate(timeout=5)
