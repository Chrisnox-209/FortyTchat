from PySide6.QtCore import Qt, Signal, QRectF, QSize
from PySide6.QtGui import QPixmap, QPainter, QIcon
from PySide6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QGridLayout, QToolButton, QFileDialog, QWidget, QScrollArea, QFrame
from .theme import colors
from .widgets import paint_avatar, label, button, Avatar
from .avatar_image import PhotoLoader
from .photo_crop import PhotoCropDialog


class AvatarPicker(QDialog):
    selected = Signal(int)
    photo_selected = Signal(str)

    def __init__(self, nickname, current=-1, parent=None, *, image_data=''):
        super().__init__(parent)
        self.setWindowTitle('Mon avatar · FortyTchat')
        self.photo_loader = None
        self.closed = False
        self.setMinimumWidth(440)
        box = QVBoxLayout(self)
        box.setContentsMargins(23, 23, 23, 22)
        box.setSpacing(14)
        box.addWidget(label('Un avatar à ton image.', 'heroTitle', True))
        box.addWidget(label('Ton image ou un personnage, à toi de choisir.', 'muted', True))
        photo_row = QHBoxLayout()
        self.photo_preview = Avatar(nickname, 58, avatar=current, image_data=image_data)
        photo_row.addWidget(self.photo_preview)
        self.import_button = button('Importer mon image…', 'primary')
        self.import_button.clicked.connect(self.import_photo)
        photo_row.addWidget(self.import_button, 1)
        box.addLayout(photo_row)
        box.addWidget(label('PNG, JPEG ou WebP · jusqu’à 10 Mo', 'caption'))
        if image_data:
            remove = button('Retirer mon image', 'flat')
            remove.clicked.connect(lambda: self.choose(current))
            box.addWidget(remove)
        self.feedback = label('', 'caption', True)
        box.addWidget(self.feedback)
        box.addWidget(label('OU CHOISIS UN PERSONNAGE  ·  24', 'eyebrow'))
        grid = QGridLayout()
        grid.setSpacing(9)
        c = colors()
        for position, avatar in enumerate(range(-1, 24)):
            pixmap = QPixmap(72, 72)
            pixmap.fill(Qt.GlobalColor.transparent)
            painter = QPainter(pixmap)
            paint_avatar(painter, QRectF(0, 0, 72, 72), nickname, avatar=avatar)
            painter.end()
            tile = QToolButton()
            tile.setIcon(QIcon(pixmap))
            tile.setIconSize(QSize(54, 54))
            tile.setFixedSize(73, 78)
            tile.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
            tile.setText('Auto' if avatar == -1 else str(avatar + 1))
            tile.setToolTip('Avatar automatique lié à ton pseudo' if avatar == -1 else f'Choisir l’avatar {avatar + 1}')
            tile.setCursor(Qt.CursorShape.PointingHandCursor)
            chosen = avatar == current and not image_data
            tile.setStyleSheet(f'QToolButton {{ background: {c["surface"]}; border: {2 if chosen else 1}px solid {c["accent"] if chosen else c["border"]}; border-radius: 14px; color: {c["muted"]}; font-size: 10px; padding: 3px; }} QToolButton:hover {{ border: 2px solid {c["accent"]}; }}')
            tile.clicked.connect(lambda checked=False, value=avatar: self.choose(value))
            grid.addWidget(tile, position // 5, position % 5)
        presets = QWidget()
        presets.setLayout(grid)
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setFrameShape(QFrame.Shape.NoFrame)
        area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        area.setFixedHeight(280)
        area.setWidget(presets)
        box.addWidget(area)
        box.addWidget(label('Ta miniature sera visible par les autres utilisateurs.\nAucune conversation n’est enregistrée.', 'caption', True))

    def import_photo(self):
        path, _ = QFileDialog.getOpenFileName(self, 'Choisir mon image', '',
                                             'Images (*.png *.jpg *.jpeg *.webp)')
        if not path:
            return
        if self.photo_loader is not None:
            return
        self.feedback.setText('')
        self.import_button.setEnabled(False)
        self.import_button.setText('Chargement…')
        self.photo_loader = PhotoLoader(self)
        self.photo_loader.ready.connect(self.preview_photo)
        self.photo_loader.failed.connect(self.photo_failed)
        self.photo_loader.start(path)

    def finish_loading(self):
        if self.photo_loader:
            self.photo_loader.deleteLater()
            self.photo_loader = None
        self.import_button.setEnabled(True)
        self.import_button.setText('Importer mon image…')

    def photo_failed(self, reason):
        self.finish_loading()
        self.feedback.setText(reason)

    def preview_photo(self, image):
        self.finish_loading()
        if self.closed:
            return
        crop = PhotoCropDialog(image, self)
        if crop.exec() == QDialog.DialogCode.Accepted:
            self.photo_selected.emit(crop.encoded_image)
            self.accept()
        crop.deleteLater()

    def choose(self, avatar):
        self.closed = True
        self.selected.emit(avatar)
        self.accept()

    def reject(self):
        self.closed = True
        super().reject()

    def closeEvent(self, event):
        self.closed = True
        super().closeEvent(event)
