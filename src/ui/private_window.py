"""One compact native window per private conversation; all messages stay in RAM."""
import time
from PySide6.QtCore import Qt, Signal, QTimer, QEvent
from PySide6.QtGui import QIcon, QCursor, QKeySequence, QShortcut
from PySide6.QtWidgets import QMainWindow, QWidget, QFrame, QVBoxLayout, QHBoxLayout, QMenu
from .chat_view import ChatView
from .composer import Composer
from .widgets import Avatar, button, label


class PrivateWindow(QMainWindow):
    send_requested = Signal(str, str)
    wizz_requested = Signal(str)
    break_requested = Signal(str)
    block_requested = Signal(str, bool)
    friend_requested = Signal(str, bool)
    viewed = Signal(str)
    closed = Signal(str)

    def __init__(self, sid, nickname, messages, icon_path, deadline=0):
        super().__init__()
        self.current, self.nickname, self.blocked = sid, nickname, False
        self.contact_id, self.is_friend = '', False
        self.setWindowTitle(f'{nickname} · FortyTchat')
        self.setWindowIcon(QIcon(icon_path))
        self.resize(600, 740)
        self.setMinimumSize(480, 540)
        root = QWidget()
        self.setCentralWidget(root)
        box = QVBoxLayout(root)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(0)
        header = QFrame()
        header.setObjectName('header')
        head = QHBoxLayout(header)
        head.setContentsMargins(24, 23, 22, 22)
        head.setSpacing(13)
        self.avatar = Avatar(nickname, 48, True)
        head.addWidget(self.avatar)
        title = QVBoxLayout()
        title.setSpacing(5)
        self.name_label = label(nickname, 'title')
        self.status_label = label('● En ligne', 'caption')
        title.addWidget(self.name_label)
        title.addWidget(self.status_label)
        head.addLayout(title, 1)
        self.friend_button = button('☆', 'flat', 'Ajouter aux amis', 38)
        self.friend_button.setStyleSheet('font-size: 22px; padding: 3px;')
        self.friend_button.clicked.connect(lambda: self.friend_requested.emit(self.contact_id, not self.is_friend))
        head.addWidget(self.friend_button)
        more = button('···', 'flat', 'Options de la conversation')
        more.clicked.connect(self.menu)
        head.addWidget(more)
        box.addWidget(header)
        self.notice = label('', 'notice', True)
        self.notice.hide()
        box.addWidget(self.notice)
        self.notice_timer = QTimer(self)
        self.notice_timer.setSingleShot(True)
        self.notice_timer.timeout.connect(self.notice.hide)
        self.chat = ChatView(private=True)
        self.chat.reset(messages, sid)
        box.addWidget(self.chat, 1)
        footer = QWidget()
        footer_box = QVBoxLayout(footer)
        footer_box.setContentsMargins(22, 12, 22, 17)
        footer_box.setSpacing(10)
        self.composer = Composer(private=True)
        self.composer.deadline = deadline
        self.composer.submitted.connect(lambda text: self.send_requested.emit(self.current, text))
        self.composer.wizz_requested.connect(lambda: self.wizz_requested.emit(self.current))
        self.composer.break_requested.connect(lambda: self.break_requested.emit(self.current))
        self.input, self.send_button = self.composer.input, self.composer.send_button
        self.wizz_button, self.break_button = self.composer.wizz_button, self.composer.break_button
        footer_box.addWidget(self.composer)
        note = label('Une fenêtre à vous. Une conversation éphémère.', 'caption')
        note.setAlignment(Qt.AlignmentFlag.AlignCenter)
        footer_box.addWidget(note)
        box.addWidget(footer)
        QShortcut(QKeySequence('Ctrl+W'), self, activated=self.close)
        self.composer.update_actions()

    def set_peer(self, peer, blocked=False):
        self.blocked = blocked
        if peer:
            self.contact_id = peer.get('contact_id', '')
            self.nickname = peer['nick']
            self.name_label.setText(self.nickname)
            self.avatar.nickname = self.nickname
            self.avatar.avatar = peer.get('avatar', -1)
            self.avatar.image_data = peer.get('avatar_image', '')
            self.avatar.online = not blocked
            self.setWindowTitle(f'{self.nickname} · FortyTchat')
        else:
            self.avatar.online = False
        self.avatar.update()
        text = 'Masqué pour cette session' if blocked else ({'online': '● En ligne', 'away': '● Absent·e', 'dnd': '● Ne pas déranger'}.get(peer['status'], '') if peer else '○ Hors ligne')
        self.status_label.setText(text)
        available = bool(peer and not blocked)
        self.composer.available = available
        self.input.setEnabled(available)
        self.send_button.setEnabled(available)
        self.composer.update_actions()
        self.set_friend(self.is_friend)

    def set_friend(self, added):
        self.is_friend = added
        self.friend_button.setText('★' if added else '☆')
        self.friend_button.setEnabled(bool(self.contact_id))
        self.friend_button.setToolTip(('Retirer des amis' if added else 'Ajouter aux amis') if self.contact_id
                                      else 'Ce client doit être mis à jour pour gérer les amis')

    def show_notice(self, text):
        self.notice.setText(text)
        self.notice.show()
        self.notice_timer.start(10000)
        if self.composer.available:
            self.send_button.setEnabled(True)

    def menu(self):
        menu = QMenu(self)
        if self.contact_id:
            menu.addAction('Retirer des amis' if self.is_friend else 'Ajouter aux amis',
                           lambda: self.friend_requested.emit(self.contact_id, not self.is_friend))
        menu.addAction('Démasquer cet utilisateur' if self.blocked else 'Masquer cet utilisateur',
                       lambda: self.block_requested.emit(self.current, not self.blocked))
        menu.addAction('Fermer la conversation', self.close)
        menu.exec(QCursor.pos())

    def open_window(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()
        self.input.setFocus()
        self.viewed.emit(self.current)

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.Type.ActivationChange and self.isActiveWindow():
            self.viewed.emit(self.current)

    def closeEvent(self, event):
        self.closed.emit(self.current)
        event.accept()
