from PySide6.QtCore import Qt, QTimer, QPropertyAnimation, Signal
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QVBoxLayout, QScrollArea,
                               QWidget, QGraphicsOpacityEffect)
from .widgets import Avatar, label
from .file_card import FileCard
from ..features.emojis import emoji_fallback


class ChatView(QFrame):
    file_action = Signal(str, object)

    def __init__(self, private=False):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(26, 19, 26, 0)
        layout.setSpacing(15)
        privacy = label('◈  Juste entre vous. Aucun historique conservé.' if private else '◈  Ici, les conversations sont éphémères. Profite du moment.', 'privacy', True)
        layout.addWidget(privacy)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.body = QWidget()
        self.messages = QVBoxLayout(self.body)
        self.messages.setSpacing(14)
        self.messages.setContentsMargins(0, 8, 0, 12)
        self.scroll.setWidget(self.body)
        layout.addWidget(self.scroll, 1)
        self.rows = []
        self.bubbles = []
        self.empty = None
        self.animation = None
        self.file_cards = {}
        self.reset([], 'private' if private else 'general')

    def reset(self, messages, chat):
        while self.messages.count():
            item = self.messages.takeAt(0)
            if item.widget():
                item.widget().hide()
                item.widget().deleteLater()
        self.rows = []
        self.bubbles = []
        self.file_cards = {}
        self.empty = None
        if not messages:
            self.empty = QWidget()
            box = QVBoxLayout(self.empty)
            box.setAlignment(Qt.AlignmentFlag.AlignCenter)
            box.setSpacing(16)
            symbol = label('✦' if chat == 'general' else '↗', 'emptySymbol')
            symbol.setAlignment(Qt.AlignmentFlag.AlignCenter)
            symbol.setFixedSize(88, 88)
            box.addWidget(symbol, 0, Qt.AlignmentFlag.AlignHCenter)
            box.addSpacing(5)
            title = label('Tout commence\npar un salut.' if chat == 'general' else 'Un petit salut ?', 'heroTitle', True)
            title.setAlignment(Qt.AlignmentFlag.AlignCenter)
            box.addWidget(title)
            subtitle = label('Une idée, un coup de main ou une pause café.\nLe cluster est à portée de message.' if chat == 'general' else 'Un message pour lancer la conversation.\nLe reste vient tout seul.', 'muted', True)
            subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
            box.addWidget(subtitle)
            box.addSpacing(8)
            badge = label('SIMPLE. SPONTANÉ. ÉPHÉMÈRE.', 'eyebrow')
            badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
            box.addWidget(badge)
            self.messages.addWidget(self.empty, 1)
        else:
            self.messages.addStretch(1)
            for message in messages[-180:]:
                self.append(message, animate=False)
        self.bottom()

    def bottom(self):
        QTimer.singleShot(0, lambda: self.scroll.verticalScrollBar().setValue(self.scroll.verticalScrollBar().maximum()))

    def append(self, message, animate=True):
        bar = self.scroll.verticalScrollBar()
        at_bottom = bar.value() >= bar.maximum() - 70
        if self.empty:
            self.messages.removeWidget(self.empty)
            self.empty.hide()
            self.empty.deleteLater()
            self.empty = None
            self.messages.addStretch(1)
        row = QWidget()
        row_box = QHBoxLayout(row)
        row_box.setContentsMargins(0, 0, 0, 0)
        row_box.setSpacing(9)
        if message.system:
            text = label(message.text, 'system', True)
            text.setAlignment(Qt.AlignmentFlag.AlignCenter)
            row_box.addWidget(text)
        else:
            if message.outgoing:
                row_box.addStretch(1)
            else:
                row_box.addWidget(Avatar(message.nickname, 36, avatar=message.avatar, image_data=message.avatar_image), 0, Qt.AlignmentFlag.AlignTop)
            bubble = QFrame()
            bubble.setObjectName('outgoing' if message.outgoing else 'incoming')
            bubble.setMaximumWidth(650)
            bubble.setMinimumWidth(130)
            content = QVBoxLayout(bubble)
            content.setContentsMargins(14, 10, 14, 11)
            content.setSpacing(6)
            heading = QHBoxLayout()
            heading.addWidget(label('Vous' if message.outgoing else message.nickname, 'messageName'))
            heading.addStretch()
            heading.addWidget(label(message.time.strftime('%H:%M'), 'caption'))
            content.addLayout(heading)
            if message.attachment:
                card = FileCard(message.attachment, bubble)
                card.action_requested.connect(self.file_action)
                content.addWidget(card)
                self.file_cards[message.id] = (row, card)
                bubble.desired_width = 360
            else:
                rendered, missing = emoji_fallback(message.text, self.font())
                text = label(rendered, 'messageText', True)
                if missing:
                    text.setToolTip('Certains emojis sont remplacés par leur nom : la police ne les prend pas en charge.')
                text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
                content.addWidget(text)
                bubble.desired_width = max(170, min(580, max((text.fontMetrics().horizontalAdvance(line) for line in rendered.split('\n')), default=0) + 34))
            self.bubbles.append((row, bubble))
            row_box.addWidget(bubble)
            if not message.outgoing:
                row_box.addStretch(1)
        self.messages.addWidget(row)
        self.rows.append(row)
        if len(self.rows) > 180:
            oldest = self.rows.pop(0)
            self.messages.removeWidget(oldest)
            oldest.hide()
            oldest.deleteLater()
            self.bubbles = [(owner, bubble) for owner, bubble in self.bubbles if owner is not oldest]
            self.file_cards = {key: value for key, value in self.file_cards.items() if value[0] is not oldest}
        self.adjust_bubbles()
        if animate:
            effect = QGraphicsOpacityEffect(row)
            row.setGraphicsEffect(effect)
            animation = QPropertyAnimation(effect, b'opacity', row)
            animation.setDuration(140)
            animation.setStartValue(0.0)
            animation.setEndValue(1.0)
            animation.start()
            row.fade = animation
        if at_bottom or message.outgoing:
            self.bottom()

    def adjust_bubbles(self):
        available = max(170, self.scroll.viewport().width() - 65)
        for _, bubble in self.bubbles:
            bubble.setFixedWidth(min(available, bubble.desired_width))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, 'bubbles'):
            self.adjust_bubbles()
            QTimer.singleShot(0, self.adjust_bubbles)

    def update_transfer(self, metadata):
        entry = self.file_cards.get(metadata['id'])
        if entry:
            entry[1].update_transfer(metadata)

    def wizz(self):
        from .attention import shake_window
        self.bottom()
        QTimer.singleShot(80, self.bottom)
        self.animation = shake_window(self.window(), self.window().centralWidget())
