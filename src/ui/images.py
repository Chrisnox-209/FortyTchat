"""Bounded image previews. Clipboard captures remain in RAM until explicitly saved by a recipient."""
import base64
import threading
from datetime import datetime
from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QRectF, QSize, Qt, QObject, Signal
from PySide6.QtGui import QImage, QImageReader, QPainter, QColor, QPixmap
from PySide6.QtWidgets import QDialog, QVBoxLayout
from .widgets import label, button
from .avatar_image import read_photo
from ..avatar_data import avatar_bytes, AvatarError


def clean_image(image):
    if image.isNull() or image.width() * image.height() > 16_000_000:
        raise ValueError('Capture trop grande (16 mégapixels maximum).')
    clean = QImage(image.size(), QImage.Format.Format_RGB32)
    clean.fill(QColor('white'))
    painter = QPainter(clean)
    painter.drawImage(0, 0, image)
    painter.end()
    return clean


def encoded_image(image, fmt, quality=-1):
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    if not image.save(buffer, fmt, quality):
        raise ValueError('Impossible de préparer cette image.')
    return bytes(data)


def encode_preview(image):
    for size in (320, 240, 160):
        scaled = image.scaled(QSize(size, size), Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        clean = clean_image(scaled)
        for quality in (75, 50, 30):
            raw = encoded_image(clean, 'JPEG', quality)
            if len(raw) <= 8192:
                return base64.b64encode(raw).decode('ascii')
    return ''


def decode_preview(value):
    try:
        data = QByteArray(avatar_bytes(value))
    except AvatarError:
        return QImage()
    buffer = QBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.ReadOnly)
    reader = QImageReader(buffer, b'jpeg')
    reader.setAllocationLimit(64)
    size = reader.size()
    if not 0 < size.width() <= 320 or not 0 < size.height() <= 320:
        return QImage()
    return reader.read()


class PreviewLoader(QObject):
    ready = Signal(str, str)

    def start(self, path):
        threading.Thread(target=self.run, args=(path,), daemon=True, name='FortyTchat image preview').start()

    def run(self, path):
        preview = ''
        try:
            preview = encode_preview(read_photo(path))
        except (AvatarError, ValueError, MemoryError):
            pass  # Non-images are still offered through the normal file transfer.
        try:
            self.ready.emit(path, preview)
        except RuntimeError:
            pass


class ImageSendDialog(QDialog):
    submitted = Signal(object)

    def __init__(self, image, parent=None):
        super().__init__(parent)
        self.image = clean_image(image)
        self.setWindowTitle('Envoyer cette capture ? · FortyTchat')
        self.resize(540, 480)
        box = QVBoxLayout(self)
        box.setContentsMargins(24, 24, 24, 24)
        box.addWidget(label('Ta capture, prête à partir.', 'title', True))
        picture = label('')
        picture.setAlignment(Qt.AlignmentFlag.AlignCenter)
        picture.setPixmap(QPixmap.fromImage(self.image.scaled(QSize(480, 300), Qt.AspectRatioMode.KeepAspectRatio,
                                                             Qt.TransformationMode.SmoothTransformation)))
        box.addWidget(picture, 1)
        self.feedback = label('La capture sera envoyée après validation. Son original reste en mémoire côté expéditeur.', 'caption', True)
        box.addWidget(self.feedback)
        send = button('Envoyer la capture', 'primary')
        send.clicked.connect(self.submit)
        box.addWidget(send)
        cancel = button('Annuler')
        cancel.clicked.connect(self.reject)
        box.addWidget(cancel)

    def submit(self):
        raw = encoded_image(self.image, 'PNG')
        if len(raw) > 20 * 1024 * 1024:
            self.feedback.setText('Capture trop volumineuse (20 Mio maximum).')
            return
        self.submitted.emit(dict(memory=raw, preview=encode_preview(self.image),
                                 name=datetime.now().strftime('capture-%Y%m%d-%H%M%S.png')))
        self.accept()


class ImageViewer(QDialog):
    def __init__(self, metadata, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Aperçu · ' + metadata['name'])
        box = QVBoxLayout(self)
        image = decode_preview(metadata.get('preview', ''))
        if metadata.get('path'):
            try:
                image = read_photo(metadata['path'])
            except (AvatarError, MemoryError):
                pass
        picture = label('Aperçu indisponible' if image.isNull() else '')
        if not image.isNull():
            picture.setPixmap(QPixmap.fromImage(image.scaled(QSize(800, 560), Qt.AspectRatioMode.KeepAspectRatio,
                                                            Qt.TransformationMode.SmoothTransformation)))
        picture.setAlignment(Qt.AlignmentFlag.AlignCenter)
        box.addWidget(picture)
        close = button('Fermer')
        close.clicked.connect(self.accept)
        box.addWidget(close)
