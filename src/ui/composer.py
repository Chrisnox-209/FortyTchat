import math
import time

from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtWidgets import QFrame, QVBoxLayout, QHBoxLayout, QTextEdit
from ..features.emojis import EmojiPicker
from ..features.wizz import wizz_label
from .widgets import button, label


class ComposeEdit(QTextEdit):
    submitted = Signal()

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and not event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            self.submitted.emit()
            event.accept()
        else:
            super().keyPressEvent(event)

    def insertFromMimeData(self, source):
        if source.hasText():
            self.insertPlainText(source.text()[:2001])


class Composer(QFrame):
    submitted = Signal(str)
    wizz_requested = Signal()
    break_requested = Signal()
    file_requested = Signal()

    def __init__(self, private=False, parent=None):
        super().__init__(parent)
        self.setObjectName('composer')
        self.private, self.available, self.deadline = private, True, 0.0
        box = QVBoxLayout(self)
        box.setContentsMargins(12, 5, 12, 12)
        box.setSpacing(4)
        self.input = ComposeEdit()
        self.input.setObjectName('input')
        self.input.setAcceptRichText(False)
        self.input.setFixedHeight(69)
        self.input.setPlaceholderText('Ton message…' if private else 'Écrire au cluster…')
        self.input.submitted.connect(self.submit)
        self.input.textChanged.connect(self.text_changed)
        box.addWidget(self.input)
        actions = QHBoxLayout()
        actions.setSpacing(8)
        emoji = button('☺', 'flat', 'Choisir un emoji', 34)
        emoji.setStyleSheet('font-size: 24px;')
        emoji.clicked.connect(self.emojis)
        actions.addWidget(emoji)
        self.file_button = button('＋', 'flat', 'Joindre un fichier (20 Mio maximum)', 34)
        self.file_button.setStyleSheet('font-size: 23px; padding: 0;')
        self.file_button.clicked.connect(self.file_requested)
        actions.addWidget(self.file_button)
        self.wizz_button = button('ϟ Wizz', 'soft', 'Un Wizz toutes les 42 minutes, sans son')
        self.wizz_button.clicked.connect(self.wizz_requested)
        actions.addWidget(self.wizz_button)
        self.break_button = button('On va en pause ?', 'break', 'Envoyer une invitation de pause')
        self.break_button.clicked.connect(self.break_requested)
        actions.addWidget(self.break_button)
        actions.addStretch()
        self.counter = label('', 'caption')
        actions.addWidget(self.counter)
        self.send_button = button('Envoyer  ↗', 'primary', 'Entrée : envoyer · Maj + Entrée : nouvelle ligne')
        self.send_button.clicked.connect(self.submit)
        actions.addWidget(self.send_button)
        box.addLayout(actions)
        self.wizz_button.setVisible(private)
        self.break_button.setVisible(private)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.update_actions)
        self.timer.start(1000)

    def submit(self):
        text = self.input.toPlainText().strip()
        if text and len(text) <= 2000 and self.send_button.isEnabled():
            self.send_button.setEnabled(False)
            self.submitted.emit(text)

    def text_changed(self):
        text = self.input.toPlainText()
        if len(text) > 2001:
            from PySide6.QtGui import QTextCursor
            self.input.blockSignals(True)
            self.input.setPlainText(text[:2001])
            cursor = self.input.textCursor()
            cursor.movePosition(QTextCursor.MoveOperation.End)
            self.input.setTextCursor(cursor)
            self.input.blockSignals(False)
        count = len(self.input.toPlainText())
        self.counter.setText(f'{count}/2000' if count > 1600 else '')
        self.counter.setStyleSheet('color: #ff9cb4;' if count > 2000 else '')
        self.send_button.setToolTip('Message limité à 2 000 caractères' if count > 2000 else 'Entrée pour envoyer')

    def update_actions(self):
        remaining = max(0, self.deadline - time.monotonic())
        full = wizz_label(remaining)
        self.wizz_button.setText((f'ϟ {math.ceil(remaining / 60)} min' if remaining else 'ϟ Wizz') if self.width() < 620 else full)
        self.wizz_button.setToolTip(full + ' · sans son')
        self.wizz_button.setEnabled(self.available and remaining <= 0)
        self.break_button.setEnabled(self.available)
        self.break_button.setText('Pause' if self.width() < 520 else 'On va en pause ?')
        self.file_button.setEnabled(self.available)

    def emojis(self):
        picker = EmojiPicker(self)
        picker.chosen.connect(self.input.insertPlainText)
        picker.exec()
        self.input.setFocus()
