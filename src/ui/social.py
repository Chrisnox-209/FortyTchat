"""Native reply previews, reaction pills and a quiet animated typing bubble."""
import math
from html import escape

from PySide6.QtCore import Qt, Signal, QTimer, QRectF
from PySide6.QtGui import QColor, QCursor, QPainter
from PySide6.QtWidgets import QFrame, QHBoxLayout, QVBoxLayout, QLabel, QMenu, QSizePolicy, QWidget

from .widgets import label, button
from .theme import colors
from ..features.emojis import EmojiPicker
from ..features.social import preview

QUICK_REACTIONS = [('👍', 'J’aime'), ('❤️', 'J’adore'), ('😂', 'Haha'),
                   ('😮', 'Oh !'), ('😢', 'Triste'), ('☕', 'Pause café')]


class ElidedLabel(QLabel):
    def __init__(self, text='', name='', parent=None):
        super().__init__(parent)
        self.setObjectName(name)
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.setMinimumWidth(0)
        self.set_full_text(text)

    def set_full_text(self, text):
        self.full_text = text
        self.setToolTip('<qt>' + escape(text).replace('\n', '<br>') + '</qt>')
        self.setAccessibleName(text)
        self.elide()

    def elide(self):
        self.setText(self.fontMetrics().elidedText(self.full_text, Qt.TextElideMode.ElideRight, max(20, self.width())))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.elide()


class ReplyPreview(QFrame):
    activated = Signal(str)

    def __init__(self, reference, nickname, text, parent=None):
        super().__init__(parent)
        self.reference = reference
        self.setObjectName('replyPreview')
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAccessibleName('Revenir au message cité')
        box = QVBoxLayout(self)
        box.setContentsMargins(10, 7, 9, 7)
        box.setSpacing(3)
        self.name = ElidedLabel('Réponse à ' + (nickname or 'un message'), 'messageName')
        self.text = ElidedLabel(preview(text) or 'Message plus disponible', 'caption')
        box.addWidget(self.name)
        box.addWidget(self.text)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.activated.emit(self.reference)
        super().mousePressEvent(event)


def reaction_menu(parent, callback):
    menu = QMenu(parent)
    for emoji, name in QUICK_REACTIONS:
        menu.addAction(f'{emoji}  {name}', lambda checked=False, value=emoji: callback(value))
    menu.addSeparator()
    def all_emojis():
        picker = EmojiPicker(parent.window())
        picker.chosen.connect(callback)
        picker.exec()
        picker.deleteLater()
    menu.addAction('Tous les emojis…', all_emojis)
    menu.exec(QCursor.pos())


class ReactionBar(QFrame):
    chosen = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.box = QHBoxLayout(self)
        self.box.setContentsMargins(0, 2, 0, 0)
        self.box.setSpacing(5)
        self.pills = {}

    def update_reactions(self, reactions, actor):
        while self.box.count():
            item = self.box.takeAt(0)
            if item.widget():
                item.widget().hide()
                item.widget().deleteLater()
        self.pills = {}
        grouped = {}
        for who, value in reactions.items():
            entry = grouped.setdefault(value['emoji'], {'names': [], 'mine': False})
            entry['names'].append(value['nickname'])
            entry['mine'] |= who == actor
        entries = sorted(grouped.items(), key=lambda item: not item[1]['mine'])
        for emoji, entry in entries[:3]:
            pill = button(f'{emoji} {len(entry["names"])}', 'reactionPill')
            pill.setProperty('mine', entry['mine'])
            pill.setStyleSheet('padding: 3px 8px; border-radius: 11px; font-size: 13px;')
            pill.setToolTip(', '.join(entry['names'][:24]) + ('…' if len(entry['names']) > 24 else ''))
            pill.setAccessibleName(f'{emoji} : {len(entry["names"])} réaction(s). {pill.toolTip()}')
            pill.clicked.connect(lambda checked=False, value=emoji: self.chosen.emit(value))
            self.box.addWidget(pill)
            self.pills[emoji] = pill
        if len(entries) > 3:
            more = button(f'+{len(entries) - 3}', 'reactionPill')
            more.setStyleSheet('padding: 3px 7px;')
            def show_all():
                menu = QMenu(self)
                for emoji, entry in entries:
                    action = menu.addAction(f'{emoji}  {len(entry["names"])}',
                                            lambda checked=False, value=emoji: self.chosen.emit(value))
                    action.setToolTip(', '.join(entry['names'][:24]))
                menu.setToolTipsVisible(True)
                menu.exec(QCursor.pos())
            more.clicked.connect(show_all)
            self.box.addWidget(more)
        self.box.addStretch()
        self.setVisible(bool(entries))


class TypingDots(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(56, 28)
        self.phase = 0
        self.timer = QTimer(self)
        self.timer.setInterval(70)
        self.timer.timeout.connect(self.tick)

    def tick(self):
        self.phase = (self.phase + .35) % (2 * math.pi)
        self.update()

    def showEvent(self, event):
        super().showEvent(event)
        self.timer.start()

    def hideEvent(self, event):
        self.timer.stop()
        super().hideEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(colors()['incoming']))
        painter.drawRoundedRect(QRectF(0, 0, 56, 27), 13, 13)
        for index in range(3):
            wave = (math.sin(self.phase - index * .8) + 1) / 2
            color = QColor(colors()['muted'])
            color.setAlphaF(.4 + wave * .6)
            painter.setBrush(color)
            painter.drawEllipse(QRectF(13 + index * 12, 11 - wave * 3, 6, 6))


class TypingIndicator(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        box = QHBoxLayout(self)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(9)
        self.dots = TypingDots()
        self.caption = ElidedLabel('', 'caption')
        box.addWidget(self.dots)
        box.addWidget(self.caption, 1)
        self.names = []
        self.hide()

    def set_typers(self, names):
        if names == self.names:
            return
        self.names = list(names)
        text = (f'{names[0]} écrit…' if len(names) == 1 else
                f'{", ".join(names[:2])} écrivent…' if len(names) == 2 else
                f'{names[0]} et {len(names) - 1} autres écrivent…') if names else ''
        self.caption.set_full_text(text)
        self.setAccessibleName(text)
        self.setVisible(bool(names))
