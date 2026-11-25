"""Private conversation page with messages and drafts kept in memory."""
import time
from PySide6.QtCore import Qt, Signal, QTimer, QEvent
from PySide6.QtGui import QIcon, QCursor, QKeySequence, QShortcut
from PySide6.QtWidgets import QMainWindow, QWidget, QFrame, QVBoxLayout, QHBoxLayout, QMenu
from .chat_view import ChatView
from .composer import Composer
from .widgets import Avatar, button, label
from .social import ElidedLabel
from ..features.screen_share import FEATURE as SCREEN_FEATURE


class PrivateWindow(QMainWindow):
    send_requested = Signal(str, str)
    file_requested = Signal(str)
    wizz_requested = Signal(str)
    break_requested = Signal(str)
    block_requested = Signal(str, bool)
    friend_requested = Signal(str, bool)
    security_requested = Signal(str)
    screen_requested = Signal(str)
    viewed = Signal(str)
    closed = Signal(str)

    def __init__(self, sid, nickname, messages, icon_path, deadline=0):
        super().__init__()
        self.current, self.nickname, self.blocked = sid, nickname, False
        self.contact_id, self.is_friend = '', False
        self.recovered_drafts = []
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
        self.name_label = ElidedLabel(nickname, 'title')
        self.status_label = label('● En ligne', 'caption')
        title.addWidget(self.name_label)
        title.addWidget(self.status_label)
        head.addLayout(title, 1)
        self.friend_button = button('☆', 'flat', 'Ajouter aux amis', 38)
        self.friend_button.setStyleSheet('font-size: 22px; padding: 3px;')
        self.friend_button.setMinimumHeight(40)
        self.friend_button.clicked.connect(lambda: self.friend_requested.emit(self.contact_id, not self.is_friend))
        head.addWidget(self.friend_button)
        self.security_button = button('🔒', 'flat', 'Vérifier le chiffrement et les empreintes', 38)
        self.security_button.setStyleSheet('padding:2px;')
        self.security_button.setMinimumHeight(40)
        self.security_button.clicked.connect(lambda: self.security_requested.emit(self.current))
        head.addWidget(self.security_button)
        from pathlib import Path
        self.screen_button=button('', 'flat', 'Partager mon écran',38)
        self.screen_button.setMinimumHeight(40)
        self.screen_button.setIcon(QIcon(str(Path(icon_path).parent/'screen.svg')))
        self.screen_button.setAccessibleName('Partager mon écran')
        self.screen_button.setEnabled(False)
        self.screen_button.clicked.connect(lambda:self.screen_requested.emit(self.current))
        head.addWidget(self.screen_button)
        more = button('···', 'flat', 'Options de la conversation')
        more.setMinimumHeight(40)
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
        self.composer.file_requested.connect(lambda: self.file_requested.emit(self.current))
        self.composer.wizz_requested.connect(lambda: self.wizz_requested.emit(self.current))
        self.composer.break_requested.connect(lambda: self.break_requested.emit(self.current))
        self.input, self.send_button = self.composer.input, self.composer.send_button
        self.wizz_button, self.break_button = self.composer.wizz_button, self.composer.break_button
        footer_box.addWidget(self.composer)
        self.security_note = label('Chiffré de bout en bout · identité à vérifier via le cadenas', 'caption', True)
        note = self.security_note
        note.setAlignment(Qt.AlignmentFlag.AlignCenter)
        footer_box.addWidget(note)
        box.addWidget(footer)
        close_shortcut = QShortcut(QKeySequence('Ctrl+W'), self, activated=self.close)
        close_shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self.composer.update_actions()

    def set_peer(self, peer, blocked=False):
        self.blocked = blocked
        if peer:
            self.contact_id = peer.get('contact_id', '')
            self.nickname = peer['nick']
            self.name_label.set_full_text(self.nickname)
            self.avatar.nickname = self.nickname
            self.avatar.avatar = peer.get('avatar', -1)
            self.avatar.image_data = peer.get('avatar_image', '')
            self.avatar.online = not blocked
            self.avatar.locked = bool(peer.get('locked') and not blocked)
            self.setWindowTitle(f'{self.nickname} · FortyTchat')
        else:
            self.avatar.online = False
            self.avatar.locked = False
        self.avatar.update()
        text = 'Utilisateur bloqué' if blocked else ({'online': '● En ligne', 'away': '● Absent·e', 'dnd': '● Ne pas déranger'}.get(peer['status'], '') if peer else '○ Hors ligne')
        if peer and peer.get('locked') and not blocked:text='🔒 Session verrouillée · dans le cluster'
        self.status_label.setText(text)
        state = peer.get('security', 'unverified') if peer else ''
        self.security_button.setEnabled(bool(peer and peer.get('identity_key')))
        self.security_button.setText('⚠' if state == 'changed' else '🔒')
        self.security_note.setText({'verified': 'Chiffré de bout en bout · identité vérifiée',
                                   'changed': 'Clé modifiée · échanges bloqués · vérifie le cadenas',
                                   'unverified': 'Chiffré de bout en bout · identité à vérifier via le cadenas'}.get(state, 'Contact hors ligne · aucun envoi en attente'))
        available = bool(peer and not blocked and peer.get('encrypted', True))
        share_available=bool(available and SCREEN_FEATURE in peer.get('features',()))
        self.screen_button.setEnabled(share_available)
        self.screen_button.setToolTip('Partager mon écran' if share_available else 'Le partage d’écran nécessite deux clients 2.4.0 ou plus récents')
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
        menu = self.build_menu()
        menu.exec(QCursor.pos())
        menu.deleteLater()

    def build_menu(self):
        menu = QMenu(self)
        if self.contact_id:
            menu.addAction('Retirer des amis' if self.is_friend else 'Ajouter aux amis',
                           lambda: self.friend_requested.emit(self.contact_id, not self.is_friend))
        menu.addAction('Débloquer cet utilisateur' if self.blocked else 'Bloquer cet utilisateur',
                       lambda: self.block_requested.emit(self.current, not self.blocked))
        if self.recovered_drafts:
            drafts = QMenu('Brouillons récupérés', menu)
            menu.addMenu(drafts)
            for index, (text, _) in enumerate(self.recovered_drafts):
                preview = ' '.join(text.split())
                title = (preview[:60] + '…' if len(preview) > 60 else preview) or 'Réponse en cours'
                drafts.addAction(title, lambda checked=False, index=index: self.select_recovered_draft(index))
        menu.addAction('Fermer la conversation', self.close)
        return menu

    def recover_drafts(self, previous):
        drafts = [previous.composer.draft(), *previous.recovered_drafts]
        for draft in drafts:
            if not any(draft):
                continue
            if not any(self.composer.draft()):
                self.composer.restore_draft(draft)
            else:
                self.recovered_drafts.append(draft)

    def select_recovered_draft(self, index):
        current = self.composer.draft()
        draft = self.recovered_drafts.pop(index)
        if any(current):
            self.recovered_drafts.append(current)
        self.composer.restore_draft(draft)
        self.input.setFocus()

    def open_window(self):
        host = getattr(self, 'chat_host', None)
        if host:
            host.show_conversation(self.current, self)
            host.open_window()
            return
        self.showNormal()
        self.raise_()
        self.activateWindow()
        self.input.setFocus()
        self.viewed.emit(self.current)

    def changeEvent(self, event):
        super().changeEvent(event)
        if getattr(self, 'chat_host', None):return
        if event.type() == QEvent.Type.ActivationChange and self.isActiveWindow():
            self.viewed.emit(self.current)

    def closeEvent(self, event):
        self.closed.emit(self.current)
        event.accept()
