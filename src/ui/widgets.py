import hashlib

from PySide6.QtCore import Qt, QRectF, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QLabel, QPushButton, QSizePolicy
from .theme import colors

PASTELS = ['#b3d9fa', '#f2c49d', '#a7d5b1', '#e9d592', '#c8bce5', '#ecb8c2']


def color_for(text):
    return QColor(PASTELS[hashlib.sha256(text.encode()).digest()[0] % len(PASTELS)])


def paint_avatar(painter, rect, nickname, online=False, avatar=-1, image_data=''):
    """Deterministic illustrated avatar, drawn locally; no downloaded profile data."""
    seed = nickname if avatar < 0 else f'fortytchat-avatar-{avatar}'
    digest = hashlib.sha256(seed.encode()).digest()
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.translate(rect.x(), rect.y())
    painter.scale(rect.width() / 100, rect.height() / 100)
    painter.setPen(Qt.PenStyle.NoPen)
    if image_data:
        from .avatar_image import decoded_photo
        image = decoded_photo(image_data)
        if not image.isNull():
            clip = QPainterPath()
            clip.addRoundedRect(QRectF(0, 0, 100, 100), 25, 25)
            painter.setClipPath(clip)
            painter.drawImage(QRectF(0, 0, 100, 100), image)
            painter.setClipping(False)
            if online:
                painter.setBrush(QColor(colors()['online']))
                painter.setPen(QPen(QColor(colors()['sidebar']), 3))
                painter.drawEllipse(QRectF(79, 79, 19, 19))
            painter.restore()
            return
    painter.setBrush(color_for(seed))
    painter.drawRoundedRect(QRectF(0, 0, 100, 100), 25, 25)
    skins = ['#f3c9a5', '#d8a47d', '#a36e50', '#efd4bf', '#875b46']
    skin = QColor(skins[digest[1] % len(skins)])
    hair = QColor(['#4b3930', '#2b2c31', '#9b6f42', '#dad5cb', '#694a38'][digest[2] % 5])
    shirt = QColor(['#558e9e', '#617ab2', '#7c6b9d', '#59967e', '#b4796d'][digest[3] % 5])
    painter.setBrush(shirt)
    painter.drawRoundedRect(QRectF(20, 68, 60, 47), 25, 25)
    painter.setBrush(skin.darker(110))
    painter.drawRoundedRect(QRectF(42, 59, 17, 22), 6, 6)
    painter.setBrush(skin)
    painter.drawEllipse(QRectF(25, 37, 9, 15))
    painter.drawEllipse(QRectF(66, 37, 9, 15))
    painter.drawRoundedRect(QRectF(30, 19, 40, 48), 18, 18)
    painter.setBrush(hair)
    path = QPainterPath()
    path.moveTo(29, 39)
    path.cubicTo(22, 4, 78, 5, 71, 40)
    path.lineTo(66, 28)
    path.cubicTo(51, 34, 46, 21, 33, 31)
    path.lineTo(33, 40)
    painter.drawPath(path)
    painter.setBrush(QColor('#343234'))
    painter.drawEllipse(QRectF(39, 41, 3.5, 4.5))
    painter.drawEllipse(QRectF(57, 41, 3.5, 4.5))
    if digest[4] % 3 == 0:
        painter.setPen(QPen(QColor('#424853'), 2.2))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(QRectF(33, 37, 15, 13), 4, 4)
        painter.drawRoundedRect(QRectF(52, 37, 15, 13), 4, 4)
        painter.drawLine(48, 41, 52, 41)
    painter.setPen(QPen(skin.darker(125), 1.5))
    painter.drawLine(50, 45, 48, 51)
    painter.setPen(QPen(QColor('#8f5a50'), 1.7, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    smile = QPainterPath()
    smile.moveTo(43, 55)
    smile.quadTo(50, 61, 57, 55)
    painter.drawPath(smile)
    if online:
        painter.setBrush(QColor(colors()['online']))
        painter.setPen(QPen(QColor(colors()['sidebar']), 3))
        painter.drawEllipse(QRectF(79, 79, 19, 19))
    painter.restore()


def label(text='', name='', wrap=False):
    item = QLabel(text)
    item.setTextFormat(Qt.TextFormat.PlainText)
    item.setObjectName(name)
    item.setWordWrap(wrap)
    # Preserve QLabel's height-for-width flag so scroll layouts measure wrapped text.
    policy = item.sizePolicy()
    policy.setVerticalPolicy(QSizePolicy.Policy.Preferred)
    policy.setHeightForWidth(wrap)
    item.setSizePolicy(policy)
    return item


def button(text, name='', tooltip='', width=None):
    item = QPushButton(text)
    item.setObjectName(name)
    item.setCursor(Qt.CursorShape.PointingHandCursor)
    policy = item.sizePolicy()
    policy.setVerticalPolicy(QSizePolicy.Policy.Fixed)
    item.setSizePolicy(policy)
    if tooltip:
        item.setToolTip(tooltip)
    if width:
        item.setFixedWidth(width)
    return item


class Avatar(QLabel):
    clicked = Signal()

    def __init__(self, nickname, size=40, online=False, avatar=-1, image_data=''):
        super().__init__()
        self.nickname, self.online, self.avatar = nickname, online, avatar
        self.image_data = image_data
        self.setFixedSize(size, size)

    def paintEvent(self, event):
        painter = QPainter(self)
        paint_avatar(painter, self.rect(), self.nickname, self.online, self.avatar, self.image_data)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)
