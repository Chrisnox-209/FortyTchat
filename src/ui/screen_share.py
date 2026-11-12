"""Native screen chooser/capture and RAM-only JPEG viewing. Never captures on import."""
import os
import time
from PySide6.QtCore import Qt, Signal, QObject, QTimer, QSize, QRect, QByteArray, QBuffer, QIODevice
from PySide6.QtGui import QImage, QImageReader, QPainter, QColor, QIcon, QGuiApplication, QShortcut, QKeySequence
from PySide6.QtWidgets import (QApplication, QWidget, QDialog, QMainWindow, QVBoxLayout,
                               QHBoxLayout, QComboBox, QSizePolicy)
from .widgets import label, button
from ..features.screen_share import MAX_IMAGE, MAX_WIDTH, MAX_HEIGHT, FPS


def encode_screen_image(image):
    if image.isNull():return None
    for width,height in [(MAX_WIDTH,MAX_HEIGHT),(960,540),(640,360)]:
        scaled=image.scaled(width,height,Qt.AspectRatioMode.KeepAspectRatio,Qt.TransformationMode.SmoothTransformation)
        # Fresh pixels exclude image metadata from the outgoing frame.
        clean=QImage(scaled.size(),QImage.Format.Format_RGB888);clean.fill(Qt.GlobalColor.black)
        painter=QPainter(clean);painter.drawImage(0,0,scaled);painter.end()
        for quality in (75,55,35):
            data=QByteArray();buffer=QBuffer(data);buffer.open(QIODevice.OpenModeFlag.WriteOnly)
            if clean.save(buffer,'JPEG',quality) and 0<len(data)<=MAX_IMAGE:
                return bytes(data),clean.width(),clean.height()
    return None


def decode_screen_image(data,width,height):
    if (not isinstance(data,bytes) or not 0<len(data)<=MAX_IMAGE
            or type(width) is not int or type(height) is not int
            or not 0<width<=MAX_WIDTH or not 0<height<=MAX_HEIGHT):return QImage()
    buffer=QBuffer();buffer.setData(QByteArray(data));buffer.open(QIODevice.OpenModeFlag.ReadOnly)
    reader=QImageReader(buffer,b'JPEG')
    if reader.size()!=QSize(width,height):return QImage()
    image=reader.read()
    return image if not image.isNull() and image.size()==QSize(width,height) else QImage()


class ScreenCanvas(QWidget):
    def __init__(self,parent=None):
        super().__init__(parent)
        self.image=QImage();self.placeholder='En attente du partage…'
        self.setMinimumSize(240,150)
        self.setSizePolicy(QSizePolicy.Policy.Expanding,QSizePolicy.Policy.Expanding)
        self.setAccessibleName('Écran partagé, lecture seule')

    def sizeHint(self):return QSize(800,450)

    def set_frame(self,data,width,height):
        image=decode_screen_image(data,width,height)
        if image.isNull():return False
        self.image=image;self.update();return True

    def clear(self,text='Partage terminé.'):
        self.image=QImage();self.placeholder=text;self.update()

    def paintEvent(self,event):
        painter=QPainter(self);painter.fillRect(self.rect(),QColor('#101217'))
        if self.image.isNull():
            painter.setPen(QColor('#a0a6b1'))
            painter.drawText(self.rect().adjusted(20,20,-20,-20),Qt.AlignmentFlag.AlignCenter|Qt.TextFlag.TextWordWrap,self.placeholder)
        else:
            size=self.image.size().scaled(self.size(),Qt.AspectRatioMode.KeepAspectRatio)
            target=QRect((self.width()-size.width())//2,(self.height()-size.height())//2,size.width(),size.height())
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            painter.drawImage(target,self.image)


class ScreenCaptureSource(QObject):
    encoded = Signal(object,int,int)
    failed = Signal(str)

    def __init__(self,parent=None,*,backend_factory=None,clock=None):
        super().__init__(parent)
        self.factory=backend_factory;self.clock=clock or time.monotonic
        self.running=self.inflight=self.seen_active=False
        self.session=self.capture=self.sink=self.latest=None
        self.next_frame=0
        self.generation=0
        self.last_input=0
        self.selected_screen=None
        QGuiApplication.instance().screenRemoved.connect(self.screen_removed)
        self.timer=QTimer(self);self.timer.setInterval(round(1000/FPS));self.timer.timeout.connect(self.process)

    @staticmethod
    def availability():
        platform=QGuiApplication.platformName()
        if platform in ('offscreen','minimal'):return False,'La capture nécessite une session graphique.'
        if os.environ.get('XDG_SESSION_TYPE')=='wayland' and platform=='xcb':
            return False,'Pour partager sous Wayland, relance FortyTchat avec QT_QPA_PLATFORM=wayland.'
        try:from PySide6.QtMultimedia import QScreenCapture,QMediaCaptureSession,QVideoSink
        except ImportError:return False,'Relance install.sh pour installer le module de partage d’écran.'
        return True,''

    def start(self,screen=None):
        if self.running:return True
        if self.factory is None:
            available,reason=self.availability()
            if not available:self.failed.emit(reason);return False
        try:
            self.generation+=1;generation=self.generation;self.latest=None
            self.selected_screen=screen
            if self.factory:self.session,self.capture,self.sink=self.factory(self)
            else:
                from PySide6.QtMultimedia import QScreenCapture,QMediaCaptureSession,QVideoSink
                self.session=QMediaCaptureSession(self);self.capture=QScreenCapture(self);self.sink=QVideoSink(self)
            if screen is not None:self.capture.setScreen(screen)
            self.session.setScreenCapture(self.capture);self.session.setVideoSink(self.sink)
            self.sink.videoFrameChanged.connect(lambda frame:self.remember(frame) if self.generation==generation else None,Qt.ConnectionType.QueuedConnection)
            self.capture.errorOccurred.connect(lambda code,message:self.error(code,message) if self.generation==generation else None,Qt.ConnectionType.QueuedConnection)
            self.capture.activeChanged.connect(lambda active:self.active_changed(active) if self.generation==generation else None,Qt.ConnectionType.QueuedConnection)
            self.running=True;self.inflight=False;self.seen_active=False;self.next_frame=0
            self.timer.start();self.capture.start();return True
        except (RuntimeError,OSError,ImportError) as exc:
            self.stop();self.failed.emit('Capture indisponible. Vérifie les autorisations de partage de ton bureau.');return False

    def remember(self,frame):
        if self.running and frame.isValid():
            from PySide6.QtMultimedia import QVideoFrame
            self.last_input=self.clock()
            self.latest=QVideoFrame(frame)
            if not self.next_frame:self.process()

    def process(self):
        if not self.running or self.inflight or self.latest is None or self.clock()<self.next_frame:return
        if self.clock()-self.last_input>5:
            self.error(None,'Capture sans nouvelles images');return
        image=self.latest.toImage()
        encoded=encode_screen_image(image)
        if not encoded:return
        self.inflight=True;self.next_frame=self.clock()+1/FPS
        self.encoded.emit(*encoded)

    def released(self):self.inflight=False

    def error(self,code,message):
        if self.running:
            self.stop();self.failed.emit('Le bureau a refusé ou interrompu la capture d’écran. Vérifie ses autorisations de partage.')

    def active_changed(self,active):
        if active:self.seen_active=True
        elif self.running and self.seen_active:
            self.stop();self.failed.emit('Le partage a été arrêté depuis le bureau.')

    def screen_removed(self,screen):
        if self.running and self.selected_screen is screen:
            self.stop();self.failed.emit('L’écran sélectionné a été débranché.')

    def stop(self):
        self.generation+=1
        self.running=False;self.timer.stop();self.inflight=False;self.latest=None
        self.selected_screen=None
        capture,session,sink=self.capture,self.session,self.sink
        self.capture=self.session=self.sink=None
        if capture:capture.stop()
        if session:session.setScreenCapture(None);session.setVideoSink(None)
        for item in (sink,capture,session):
            if item:item.deleteLater()


class ScreenWindow(QMainWindow):
    requested = Signal()
    stopped = Signal()
    closed = Signal()
    encoded = Signal(object,int,int)
    capture_failed = Signal(str)

    def __init__(self,nickname,icon_path,*,outgoing=False,source_factory=ScreenCaptureSource):
        super().__init__()
        self.nickname=nickname;self.outgoing=outgoing;self.share='';self.phase='ready'
        self.setWindowTitle(('Partager avec ' if outgoing else 'Écran de ')+nickname+' · FortyTchat')
        self.setWindowIcon(QIcon(icon_path));self.resize(620 if outgoing else 880,440 if outgoing else 640);self.setMinimumSize(420,360)
        root=QWidget();self.setCentralWidget(root);box=QVBoxLayout(root);box.setContentsMargins(18,18,18,18);box.setSpacing(12)
        self.heading=label('Partager mon écran' if outgoing else f'Écran de {nickname}','title',True);box.addWidget(self.heading)
        self.status=label('Choisis ton écran. La capture attendra l’accord de ton ami.' if outgoing else 'En attente des premières images…','caption',True);box.addWidget(self.status)
        self.sources=QComboBox();self.sources.setMinimumHeight(40)
        self.portal='wayland' in QGuiApplication.platformName() or os.environ.get('XDG_SESSION_TYPE')=='wayland'
        if self.portal:self.sources.addItem('Choisir l’écran dans la fenêtre du bureau',None)
        else:
            for index,screen in enumerate(QGuiApplication.screens(),1):
                self.sources.addItem(f'Écran {index} · {screen.size().width()} × {screen.size().height()}',screen)
        self.sources.setVisible(outgoing);box.addWidget(self.sources)
        self.canvas=ScreenCanvas();self.canvas.clear('Aucune capture avant ton lancement et l’accord du destinataire.' if outgoing else 'En attente des premières images…');box.addWidget(self.canvas,1)
        row=QHBoxLayout()
        self.fullscreen=button('Plein écran','soft');self.fullscreen.clicked.connect(self.toggle_fullscreen);row.addWidget(self.fullscreen)
        self.fullscreen.setVisible(not outgoing)
        self.start_button=button('Partager','primary');self.start_button.setVisible(outgoing)
        self.start_button.clicked.connect(self.requested);row.addWidget(self.start_button)
        self.stop_button=button('Fermer','soft');self.stop_button.clicked.connect(self.stop_or_close);row.addWidget(self.stop_button)
        box.addLayout(row);box.addWidget(label('Chiffré de bout en bout · lecture seule · images non enregistrées','caption',True))
        self.source=source_factory(self) if outgoing else None
        if self.source:
            self.source.encoded.connect(self.frame_ready);self.source.failed.connect(self.capture_failed)
            available,reason=self.source.availability()
            self.start_button.setEnabled(available and self.sources.count()>0)
            if not available:self.status.setText(reason)
        QShortcut(QKeySequence('Escape'),self,activated=lambda:self.showNormal() if self.isFullScreen() else None)

    def toggle_fullscreen(self):
        self.showNormal() if self.isFullScreen() else self.showFullScreen()

    def set_state(self,share,phase):
        self.share=share;self.phase=phase;self.sources.setEnabled(phase=='ready')
        self.start_button.setVisible(self.outgoing and phase=='ready')
        self.start_button.setEnabled(phase=='ready' and self.source is not None and self.source.availability()[0])
        self.stop_button.setText('Arrêter' if phase=='active' else 'Annuler' if phase=='waiting' else 'Fermer')
        self.stop_button.setToolTip('Arrêter le partage d’écran' if phase=='active' else '')
        self.stop_button.setStyleSheet('background:#9f3349; color:white; border:1px solid #ce596e;' if phase=='active' else '')
        if phase=='waiting':self.status.setText(f'Invitation envoyée à {self.nickname}. Aucune capture pour le moment.')
        elif phase=='active':
            self.status.setText('Choisis ton écran dans la fenêtre du bureau…' if self.outgoing and self.portal else 'Connexion établie · en attente des premières images…')
            if self.source and not self.source.running:self.source.start(self.sources.currentData())

    def frame_ready(self,data,width,height):
        if self.phase!='active':return
        self.canvas.clear(f'Écran partagé avec {self.nickname}.\nRéduis cette fenêtre pour travailler.\nTu peux aussi arrêter depuis l’icône FT.')
        self.status.setText(f'Ton écran est visible par {self.nickname}.')
        self.status.setStyleSheet('color:#ff9da8; font-weight:600;')
        self.encoded.emit(data,width,height)

    def receive_frame(self,data,width,height):
        if not self.canvas.set_frame(data,width,height):return False
        self.status.setText(f'Écran de {self.nickname} · en direct');return True

    def finish(self,reason):
        if self.source:self.source.stop()
        self.share='';self.phase='ready';self.canvas.clear(reason);self.status.setText(reason);self.status.setStyleSheet('')
        self.sources.setEnabled(True);self.stop_button.setText('Fermer');self.start_button.setText('Partager à nouveau')
        self.stop_button.setStyleSheet('');self.start_button.setVisible(self.outgoing)
        self.start_button.setEnabled(bool(self.source and self.source.availability()[0]))
        if self.isFullScreen():self.showNormal()

    def stop_or_close(self):
        if self.phase in ('active','waiting'):self.stopped.emit()
        else:self.close()

    def closeEvent(self,event):
        if self.source:self.source.stop()
        self.canvas.clear();self.closed.emit();event.accept()


class ScreenPrompt(QDialog):
    def __init__(self,nickname,callback,parent=None):
        super().__init__(parent)
        self.callback=callback;self.answered=False
        self.setWindowTitle('Invitation à voir un écran · FortyTchat');self.resize(420,240)
        box=QVBoxLayout(self);box.setContentsMargins(24,24,24,24);box.setSpacing(16)
        box.addWidget(label(f'{nickname} te propose de voir son écran.','title',True))
        box.addWidget(label('Tu pourras arrêter à tout moment. Aucun contrôle de souris ou de clavier.','caption',True))
        row=QHBoxLayout();self.decline=button('Refuser','soft');self.accept_button=button('Voir l’écran','primary')
        self.decline.clicked.connect(self.reject);self.accept_button.clicked.connect(lambda:self.answer(True))
        row.addWidget(self.decline);row.addWidget(self.accept_button);box.addLayout(row)

    def answer(self,accepted):
        if self.answered:return
        self.answered=True;self.callback(accepted);QDialog.done(self,QDialog.DialogCode.Accepted if accepted else QDialog.DialogCode.Rejected)

    def reject(self):self.answer(False)

    def finish(self):
        self.answered=True;QDialog.done(self,QDialog.DialogCode.Rejected)

    def showEvent(self,event):
        super().showEvent(event)
        screen=QGuiApplication.screenAt(self.cursor().pos()) or self.screen()
        self.move(screen.availableGeometry().center()-self.rect().center())
