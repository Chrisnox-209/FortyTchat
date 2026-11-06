"""Bundled GIFs only: one scaled frame cached, no remote URLs in widgets."""
from pathlib import Path

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QIcon, QImageReader, QMovie, QPixmap
from PySide6.QtWidgets import QLabel, QWidget, QGridLayout, QToolButton, QButtonGroup

from ..features.cards import EMOJIS, EMOJI_IDS

ASSETS = Path(__file__).resolve().parents[2] / 'assets/animated'


class AnimatedEmoji(QLabel):
    def __init__(self, code='1f44d', size=112, parent=None):
        super().__init__(parent)
        self.setFixedSize(size, size)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.animation = QMovie(self)
        self.animation.setCacheMode(QMovie.CacheMode.CacheNone)
        self.animation.setScaledSize(QSize(size, size))
        self.setMovie(self.animation)
        self.set_code(code)

    def set_code(self, code):
        if code not in EMOJI_IDS:
            raise ValueError('Animation inconnue')
        self.code = code
        self.animation.stop()
        self.animation.setFileName(str(ASSETS / f'{code}.gif'))
        self.animation.jumpToFrame(0)
        self.setAccessibleName(next(name for key, glyph, name in EMOJIS if key == code))
        if self.isVisible(): self.animation.start()

    def showEvent(self, event):
        super().showEvent(event)
        self.animation.start()

    def hideEvent(self, event):
        self.animation.stop()
        super().hideEvent(event)


class AnimatedEmojiChoice(QWidget):
    chosen = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        grid = QGridLayout(self)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(7)
        self.buttons = {}
        for index, (code, glyph, name) in enumerate(EMOJIS):
            control = QToolButton()
            control.setObjectName('animatedChoice')
            control.setCheckable(True)
            control.setFixedSize(52, 52)
            control.setCursor(Qt.CursorShape.PointingHandCursor)
            control.setToolTip(name)
            control.setAccessibleName(name)
            reader = QImageReader(str(ASSETS / f'{code}.gif'))
            reader.setScaledSize(QSize(42, 42))
            image = reader.read()
            if not image.isNull():
                control.setIcon(QIcon(QPixmap.fromImage(image)))
                control.setIconSize(QSize(42, 42))
            else:
                control.setText(glyph)
            control.clicked.connect(lambda checked=False, value=code: self.chosen.emit(value))
            self.group.addButton(control)
            self.buttons[code] = control
            grid.addWidget(control, index // 6, index % 6)
        self.set_code(EMOJIS[0][0])

    def set_code(self, code):
        self.buttons[code].setChecked(True)
