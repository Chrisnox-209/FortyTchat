from PySide6.QtCore import Qt, QRectF
from PySide6.QtGui import QPainter, QPainterPath, QColor, QPen
from PySide6.QtWidgets import QDialog, QWidget, QVBoxLayout, QHBoxLayout, QSlider

from .avatar_image import encode_photo
from .theme import colors
from .widgets import label, button


class PhotoCrop(QWidget):
    def __init__(self, image, parent=None):
        super().__init__(parent)
        self.image, self.zoom = image, 1.0
        self.center_x, self.center_y = image.width() / 2, image.height() / 2
        self.drag_start = None
        self.setFixedSize(280, 280)
        self.setCursor(Qt.CursorShape.OpenHandCursor)

    def crop_rect(self):
        side = min(self.image.width(), self.image.height()) / self.zoom
        x = min(max(0, self.center_x - side / 2), self.image.width() - side)
        y = min(max(0, self.center_y - side / 2), self.image.height() - side)
        return QRectF(x, y, side, side)

    def set_zoom(self, percent):
        self.zoom = percent / 100
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()).adjusted(1, 1, -1, -1), 32, 32)
        painter.setClipPath(path)
        painter.fillRect(self.rect(), QColor('#eceef1'))
        painter.drawImage(QRectF(self.rect()), self.image, self.crop_rect())
        painter.setClipping(False)
        painter.setPen(QPen(QColor(colors()['border']), 2))
        painter.drawPath(path)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.drag_start = event.position()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)

    def mouseMoveEvent(self, event):
        if self.drag_start is not None:
            delta = event.position() - self.drag_start
            rect = self.crop_rect()
            self.center_x = rect.center().x() - delta.x() * rect.width() / self.width()
            self.center_y = rect.center().y() - delta.y() * rect.height() / self.height()
            # Clamp immediately, so dragging away from an edge responds naturally.
            rect = self.crop_rect()
            self.center_x, self.center_y = rect.center().x(), rect.center().y()
            self.drag_start = event.position()
            self.update()

    def mouseReleaseEvent(self, event):
        self.drag_start = None
        self.setCursor(Qt.CursorShape.OpenHandCursor)


class PhotoCropDialog(QDialog):
    def __init__(self, image, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Mon image · FortyTchat')
        self.encoded_image = ''
        box = QVBoxLayout(self)
        box.setContentsMargins(26, 24, 26, 24)
        box.setSpacing(15)
        box.addWidget(label('Juste le bon cadrage.', 'heroTitle'))
        box.addWidget(label('Déplace l’image et ajuste le zoom.', 'muted'))
        self.crop = PhotoCrop(image)
        box.addWidget(self.crop, 0, Qt.AlignmentFlag.AlignHCenter)
        self.zoom = QSlider(Qt.Orientation.Horizontal)
        self.zoom.setRange(100, 300)
        self.zoom.setValue(100)
        self.zoom.setAccessibleName('Zoom de mon avatar')
        self.zoom.valueChanged.connect(self.crop.set_zoom)
        box.addWidget(self.zoom)
        box.addWidget(label('Cette miniature sera visible par les autres utilisateurs.\nTon fichier original et ses métadonnées ne sont pas partagés.', 'caption', True))
        self.feedback = label('', 'caption', True)
        box.addWidget(self.feedback)
        row = QHBoxLayout()
        cancel = button('Annuler')
        cancel.clicked.connect(self.reject)
        self.use_button = button('Utiliser cette image', 'primary')
        self.use_button.clicked.connect(self.confirm)
        row.addWidget(cancel)
        row.addWidget(self.use_button)
        box.addLayout(row)

    def confirm(self):
        from ..avatar_data import AvatarError
        try:
            self.encoded_image = encode_photo(self.crop.image, self.crop.crop_rect())
        except AvatarError as exc:
            self.feedback.setText(str(exc))
            return
        self.accept()
