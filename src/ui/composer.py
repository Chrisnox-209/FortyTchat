import math
import time

from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtWidgets import QFrame, QVBoxLayout, QHBoxLayout, QTextEdit
from ..features.emojis import EmojiPicker
from ..features.wizz import wizz_label
from .widgets import button, label
from .social import ReplyPreview


class ComposeEdit(QTextEdit):
    submitted = Signal()
    focus_lost = Signal()
    image_pasted = Signal(object)

    def focusOutEvent(self, event):
        self.focus_lost.emit()
        super().focusOutEvent(event)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and not event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            self.submitted.emit()
            event.accept()
        else:
            super().keyPressEvent(event)

    def insertFromMimeData(self, source):
        if source.hasImage():
            self.image_pasted.emit(source.imageData())
        elif source.hasText():
            self.insertPlainText(source.text()[:2001])

    def canInsertFromMimeData(self, source):
        return source.hasImage() or source.hasText()


class Composer(QFrame):
    submitted = Signal(str)
    wizz_requested = Signal()
    break_requested = Signal()
    file_requested = Signal()
    card_requested = Signal()
    typing_changed = Signal(bool)
    image_pasted = Signal(object)
    poll_requested = Signal()

    def __init__(self, private=False, parent=None):
        super().__init__(parent)
        self.setObjectName('composer')
        self.private, self.available, self.deadline = private, True, 0.0
        box = QVBoxLayout(self)
        box.setContentsMargins(12, 5, 12, 12)
        box.setSpacing(4)
        self.reply_to, self.reply_preview = '', None
        self.reply_banner = QFrame()
        self.reply_box = QHBoxLayout(self.reply_banner)
        self.reply_box.setContentsMargins(4, 4, 4, 0)
        cancel_reply = button('×', 'flat', 'Annuler la réponse', 28)
        cancel_reply.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        cancel_reply.clicked.connect(self.clear_reply)
        self.reply_box.addWidget(cancel_reply)
        self.reply_banner.hide()
        box.addWidget(self.reply_banner)
        self.input = ComposeEdit()
        self.input.setObjectName('input')
        self.input.setAcceptRichText(False)
        self.input.setFixedHeight(69)
        self.input.setPlaceholderText('Ton message…' if private else 'Écrire au cluster…')
        self.input.submitted.connect(self.submit)
        self.input.textChanged.connect(self.text_changed)
        self.input.focus_lost.connect(self.stop_typing)
        self.input.image_pasted.connect(self.image_pasted)
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
        self.break_button = button('On fait une pause ?', 'break', 'Envoyer une invitation de pause')
        self.break_button.clicked.connect(self.break_requested)
        actions.addWidget(self.break_button)
        self.card_button = button('✦', 'flat', 'Créer une notification animée', 34)
        self.card_button.setStyleSheet('font-size: 22px; padding: 0;')
        self.card_button.clicked.connect(self.card_requested)
        actions.addWidget(self.card_button)
        self.poll_button = button('▥', 'flat', 'Créer un sondage', 34)
        self.poll_button.clicked.connect(self.poll_requested)
        self.poll_button.setVisible(not private)
        actions.addWidget(self.poll_button)
        actions.addStretch()
        self.counter = label('', 'caption')
        actions.addWidget(self.counter)
        self.send_button = button('Envoyer  ↗', 'primary', 'Entrée : envoyer · Maj + Entrée : nouvelle ligne')
        self.send_button.clicked.connect(self.submit)
        actions.addWidget(self.send_button)
        box.addLayout(actions)
        self.wizz_button.setVisible(private)
        self.break_button.setVisible(private)
        self.card_button.setVisible(private)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.update_actions)
        self.timer.start(1000)
        self.typing_active, self.last_typing = False, 0.0
        self.typing_quiet = QTimer(self)
        self.typing_quiet.setSingleShot(True)
        self.typing_quiet.setInterval(2500)
        self.typing_quiet.timeout.connect(self.stop_typing)

    def set_reply(self, reference, nickname, text):
        self.clear_reply()
        self.reply_to = reference
        self.reply_preview = ReplyPreview(reference, nickname, text)
        self.reply_box.insertWidget(0, self.reply_preview, 1)
        self.reply_banner.show()
        self.input.setFocus()

    def clear_reply(self):
        self.reply_to = ''
        if self.reply_preview:
            self.reply_box.removeWidget(self.reply_preview)
            self.reply_preview.deleteLater()
            self.reply_preview = None
        self.reply_banner.hide()

    def stop_typing(self):
        if self.typing_active:
            self.typing_active = False
            self.typing_changed.emit(False)
        self.typing_quiet.stop()

    def hideEvent(self, event):
        if hasattr(self, 'typing_quiet'):
            self.stop_typing()
        super().hideEvent(event)

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
        if self.input.toPlainText().strip() and self.available and self.input.hasFocus():
            now = time.monotonic()
            if not self.typing_active or now - self.last_typing >= 2:
                self.typing_active, self.last_typing = True, now
                self.typing_changed.emit(True)
            self.typing_quiet.start()
        else:
            self.stop_typing()

    def update_actions(self):
        remaining = max(0, self.deadline - time.monotonic())
        full = wizz_label(remaining)
        self.wizz_button.setText((f'ϟ {math.ceil(remaining / 60)} min' if remaining else 'ϟ Wizz') if self.width() < 620 else full)
        self.wizz_button.setToolTip(full + ' · sans son')
        self.wizz_button.setEnabled(self.available and remaining <= 0)
        self.break_button.setEnabled(self.available)
        self.break_button.setText('Pause' if self.width() < 520 or self.counter.text() else 'On fait une pause ?')
        self.file_button.setEnabled(self.available)
        self.card_button.setEnabled(self.available)
        self.poll_button.setEnabled(self.available)
        if not self.available:
            self.stop_typing()

    def emojis(self):
        picker = EmojiPicker(self)
        picker.chosen.connect(self.input.insertPlainText)
        picker.exec()
        self.input.setFocus()
