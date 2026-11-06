from pathlib import Path

from PySide6.QtCore import Qt, Signal, QTimer, QSize, QEvent
from PySide6.QtGui import QIcon, QKeySequence, QShortcut, QCursor
from PySide6.QtWidgets import QMainWindow, QWidget, QFrame, QHBoxLayout, QVBoxLayout, QMenu
from .sidebar import Sidebar
from .chat_view import ChatView
from .composer import Composer, ComposeEdit
from .widgets import button, label


class MainWindow(QMainWindow):
    send_requested = Signal(str, str)
    file_requested = Signal(str)
    private_requested = Signal(str)
    avatar_requested = Signal()
    friend_requested = Signal(str, bool)
    wizz_requested = Signal(str)
    break_requested = Signal(str)
    settings_requested = Signal(int)
    block_requested = Signal(str, bool)
    status_requested = Signal(str)
    quitting = Signal()

    def __init__(self, preferences, history, icon_path):
        super().__init__()
        self.setWindowTitle('FortyTchat — le cluster, en direct')
        self.setWindowIcon(QIcon(icon_path))
        self.resize(1120, 780)
        self.setMinimumSize(900, 580)
        self.preferences, self.history = preferences, history
        self.current, self.peers, self.known_names = 'general', {}, {}
        self.quitting_now, self.tray_available = False, False
        root = QWidget()
        self.setCentralWidget(root)
        body = QHBoxLayout(root)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        self.sidebar = Sidebar(preferences.nickname, preferences.avatar, preferences.avatar_image, preferences.friends)
        self.sidebar.avatar_requested.connect(self.avatar_requested)
        self.sidebar.friend_requested.connect(self.friend_requested)
        self.sidebar.selected.connect(self.select_chat)
        self.sidebar.settings_requested.connect(lambda: self.settings_requested.emit(0))
        self.sidebar.status_requested.connect(self.status_menu)
        rail = QFrame()
        rail.setObjectName('rail')
        rail.setFixedWidth(72)
        rail_box = QVBoxLayout(rail)
        rail_box.setContentsMargins(11, 23, 11, 20)
        rail_box.setSpacing(20)
        mark = button('FT', 'railButton', 'FortyTchat')
        mark.setStyleSheet('font-size: 19px; font-weight: 750; padding: 0;')
        mark.setFixedSize(49, 48)
        mark.clicked.connect(lambda: self.select_chat('general'))
        rail_box.addWidget(mark)
        rail_box.addSpacing(22)
        icon_dir = Path(icon_path).parent
        def rail_button(filename, tooltip, callback):
            control = button('', 'railButton', tooltip)
            control.setIcon(QIcon(str(icon_dir / filename)))
            control.setIconSize(QSize(24, 24))
            control.setFixedSize(49, 48)
            control.clicked.connect(callback)
            return control
        chats = rail_button('chats.svg', 'Chat général', lambda: self.select_chat('general'))
        chats.setCheckable(True)
        chats.setChecked(True)
        rail_box.addWidget(chats)
        rail_box.addWidget(rail_button('online.svg', 'Utilisateurs en ligne', self.sidebar.focus_online))
        rail_box.addStretch()
        rail_box.addWidget(rail_button('settings.svg', 'Paramètres', lambda: self.settings_requested.emit(0)))
        body.addWidget(rail)
        body.addWidget(self.sidebar)
        pane = QFrame()
        pane.setObjectName('mainPane')
        pane_box = QVBoxLayout(pane)
        pane_box.setContentsMargins(0, 0, 0, 0)
        pane_box.setSpacing(0)
        header = QFrame()
        header.setObjectName('header')
        header_box = QHBoxLayout(header)
        header_box.setContentsMargins(27, 23, 26, 22)
        symbol = label('#', 'roomSymbol')
        symbol.setAlignment(Qt.AlignmentFlag.AlignCenter)
        symbol.setFixedSize(48, 48)
        header_box.addWidget(symbol)
        title = QVBoxLayout()
        title.setSpacing(5)
        self.room_title = label('général', 'title')
        self.room_subtitle = label('Un seul salon. Tout le cluster.', 'caption')
        title.addWidget(self.room_title)
        title.addWidget(self.room_subtitle)
        header_box.addLayout(title, 1)
        self.members = label('Vous seul·e', 'chip')
        header_box.addWidget(self.members)
        more = button('···', 'flat', 'Options')
        more.clicked.connect(self.chat_menu)
        header_box.addWidget(more)
        pane_box.addWidget(header)
        self.notice = label('', 'notice', True)
        self.notice.hide()
        pane_box.addWidget(self.notice)
        self.notice_timer = QTimer(self)
        self.notice_timer.setSingleShot(True)
        self.notice_timer.timeout.connect(self.notice.hide)
        self.chat = ChatView()
        pane_box.addWidget(self.chat, 1)
        footer = QWidget()
        footer_box = QVBoxLayout(footer)
        footer_box.setContentsMargins(26, 16, 26, 18)
        footer_box.setSpacing(10)
        self.composer = Composer()
        self.input, self.send_button = self.composer.input, self.composer.send_button
        self.wizz_button, self.break_button = self.composer.wizz_button, self.composer.break_button
        self.composer.submitted.connect(lambda text: self.send_requested.emit('general', text))
        self.composer.file_requested.connect(lambda: self.file_requested.emit('general'))
        footer_box.addWidget(self.composer)
        details = QHBoxLayout()
        # Background status remains available to the controller, outside the UI.
        self.network_label = label('', 'caption')
        self.network_label.setParent(self)
        self.network_label.hide()
        details.addStretch()
        details.addWidget(label('↵ Envoyer  ·  ⇧↵ Nouvelle ligne', 'caption'))
        footer_box.addLayout(details)
        pane_box.addWidget(footer)
        body.addWidget(pane, 1)
        QShortcut(QKeySequence('Ctrl+,'), self, activated=lambda: self.settings_requested.emit(0))
        QShortcut(QKeySequence('Ctrl+Q'), self, activated=self.quitting.emit)
        QShortcut(QKeySequence('Ctrl+K'), self, activated=self.sidebar.focus_online)

    def select_chat(self, sid):
        if sid != 'general':
            self.private_requested.emit(sid)
            return
        self.sidebar.current = 'general'
        self.sidebar.unread.pop('general', None)
        self.sidebar.refresh()
        self.chat.reset(self.history.get('general'), 'general')
        self.input.setFocus()
        if hasattr(self, 'chat_selected_callback'):
            self.chat_selected_callback()

    def set_peers(self, peers):
        self.peers = {p['sid']: p for p in peers}
        self.sidebar.peers = self.peers
        for peer in peers:
            self.known_names[peer['sid']] = peer['nick']
            if peer['sid'] in self.sidebar.conversations:
                self.sidebar.conversations[peer['sid']] = peer['nick']
        keep = set(self.peers) | set(self.sidebar.conversations)
        self.known_names = {sid: nick for sid, nick in self.known_names.items() if sid in keep}
        self.sidebar.refresh()
        self.members.setText(f'{len(peers) + 1} participants' if peers else 'Vous seul·e')

    def update_actions(self):
        self.composer.update_actions()

    def show_notice(self, text):
        self.notice.setText(text)
        self.notice.show()
        self.notice_timer.start(10000)
        self.send_button.setEnabled(True)

    def chat_menu(self):
        menu = QMenu(self)
        menu.addAction('Paramètres', lambda: self.settings_requested.emit(0))
        menu.addAction('Quitter complètement', self.quitting.emit)
        menu.exec(QCursor.pos())

    def status_menu(self):
        menu = QMenu(self)
        for text, status in [('En ligne', 'online'), ('Absent·e', 'away'), ('Ne pas déranger', 'dnd')]:
            menu.addAction(text, lambda checked=False, s=status: self.status_requested.emit(s))
        menu.exec(QCursor.pos())

    def open_window(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()
        self.input.setFocus()
        self.sidebar.unread.pop('general', None)
        self.sidebar.refresh()
        if hasattr(self, 'chat_selected_callback'):
            self.chat_selected_callback()

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.Type.ActivationChange and self.isActiveWindow() and hasattr(self, 'sidebar'):
            self.sidebar.unread.pop('general', None)
            self.sidebar.refresh()
            if hasattr(self, 'chat_selected_callback'):
                self.chat_selected_callback()

    def closeEvent(self, event):
        if self.quitting_now:
            event.accept()
        else:
            event.ignore()
            self.hide() if self.tray_available else self.showMinimized()
