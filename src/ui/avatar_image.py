"""Local photo import and a bounded, metadata-free avatar thumbnail."""
from functools import lru_cache
from pathlib import Path
import threading

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QRectF, QSize, Qt, QObject, Signal
from PySide6.QtGui import QImage, QImageReader, QPainter, QColor

from ..avatar_data import AVATAR_SIZE, MAX_AVATAR_BYTES, AvatarError, avatar_bytes

MAX_SOURCE_BYTES = 10 * 1024 * 1024
MAX_SOURCE_PIXELS = 50_000_000


class PhotoLoader(QObject):
    """Read/decode off the GUI thread. Closing the picker safely abandons the result."""
    ready = Signal(object)
    failed = Signal(str)

    def start(self, path):
        threading.Thread(target=self._run, args=(path,), daemon=True,
                         name='FortyTchat photo import').start()

    def _run(self, path):
        try:
            image = read_photo(path)
        except (AvatarError, MemoryError) as exc:
            try:
                self.failed.emit(str(exc) if isinstance(exc, AvatarError) else 'Image trop grande. Essaie une image plus petite.')
            except RuntimeError:
                pass  # The owning dialog may already have been destroyed.
        else:
            try:
                self.ready.emit(image)
            except RuntimeError:
                pass


def read_photo(path):
    path = Path(path)
    try:
        if not path.is_file() or not 0 < path.stat().st_size <= MAX_SOURCE_BYTES:
            raise AvatarError('Choisis une image de moins de 10 Mo.')
        with path.open('rb') as source:
            raw = source.read(MAX_SOURCE_BYTES + 1)
    except OSError as exc:
        raise AvatarError('Cette image n’est pas accessible.') from exc
    if not raw or len(raw) > MAX_SOURCE_BYTES:
        raise AvatarError('Choisis une image de moins de 10 Mo.')
    data = QByteArray(raw)
    buffer = QBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.ReadOnly)
    reader = QImageReader(buffer)
    reader.setDecideFormatFromContent(True)
    reader.setAutoTransform(True)
    if bytes(reader.format()).lower() not in (b'jpeg', b'jpg', b'png', b'webp'):
        raise AvatarError('Formats acceptés : PNG, JPEG et WebP.')
    size = reader.size()
    if size.width() <= 0 or size.height() <= 0 or size.width() * size.height() > MAX_SOURCE_PIXELS:
        raise AvatarError('Image trop grande ou endommagée (50 mégapixels maximum).')
    reader.setAllocationLimit(64)
    if size.width() > 1024 or size.height() > 1024:
        reader.setScaledSize(size.scaled(QSize(1024, 1024), Qt.AspectRatioMode.KeepAspectRatio))
    image = reader.read()
    if image.isNull():
        raise AvatarError('Impossible de lire cette image. Essaie une image plus petite.')
    return image


def encode_photo(image, crop=None):
    if image.isNull():
        raise AvatarError('Image invalide')
    if crop is None:
        side = min(image.width(), image.height())
        crop = QRectF((image.width() - side) / 2, (image.height() - side) / 2, side, side)
    # A fresh canvas copies pixels only, removing filename, EXIF, text and GPS metadata.
    thumbnail = QImage(AVATAR_SIZE, AVATAR_SIZE, QImage.Format.Format_RGB32)
    thumbnail.fill(QColor('#eceef1'))
    painter = QPainter(thumbnail)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    painter.drawImage(QRectF(0, 0, AVATAR_SIZE, AVATAR_SIZE), image, crop)
    painter.end()
    import base64
    for quality in (85, 72, 60, 45, 30):
        output = QByteArray()
        buffer = QBuffer(output)
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        if not thumbnail.save(buffer, 'JPEG', quality):
            raise AvatarError('Impossible de préparer cette image.')
        if len(output) <= MAX_AVATAR_BYTES:
            return base64.b64encode(bytes(output)).decode('ascii')
    raise AvatarError('Image trop complexe. Essaie une autre photo.')


@lru_cache(maxsize=256)
def decoded_photo(value):
    """Untrusted received images are size-checked before Qt allocates pixel storage."""
    try:
        raw = avatar_bytes(value)
    except AvatarError:
        return QImage()
    data = QByteArray(raw)
    buffer = QBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.ReadOnly)
    reader = QImageReader(buffer, b'jpeg')
    reader.setAllocationLimit(64)
    if reader.size() != QSize(AVATAR_SIZE, AVATAR_SIZE):
        return QImage()
    return reader.read()
