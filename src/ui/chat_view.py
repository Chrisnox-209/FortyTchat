from PySide6.QtCore import Qt, QTimer, QPropertyAnimation, Signal, QRect, QPoint
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QVBoxLayout, QScrollArea,
                               QWidget, QGraphicsOpacityEffect, QApplication)
from PySide6.QtWidgets import QMenu
from PySide6.QtGui import QCursor
from .widgets import Avatar, label
from .file_card import FileCard
from .social import ElidedLabel, ReplyPreview, ReactionBar, TypingIndicator, reaction_menu
from .widgets import button
from ..features.emojis import emoji_fallback
from ..features.receipts import delivery_label
from .theme import colors
from .polls import PollCard


class ChatView(QFrame):
    file_action = Signal(str, object)
    reply_requested = Signal(str)
    reaction_requested = Signal(str, str)
    quote_missing = Signal()
    edit_requested = Signal(str)
    delete_requested = Signal(str)
    vote_requested = Signal(str, object)

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
        self.follow_bottom = True
        self.scroll.verticalScrollBar().valueChanged.connect(self.scroll_position_changed)
        self.scroll.verticalScrollBar().rangeChanged.connect(self.scroll_range_changed)
        layout.addWidget(self.scroll, 1)
        self.typing = TypingIndicator()
        layout.addWidget(self.typing)
        self.rows = []
        self.bubbles = []
        self.empty = None
        self.animation = None
        self.file_cards = {}
        self.actor_id = ''
        self.session_id = ''
        self.poll_cards = {}
        self.message_rows, self.reaction_bars, self.quote_previews = {}, {}, {}
        self.delivery_labels = {}
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
        self.poll_cards = {}
        self.message_rows, self.reaction_bars, self.quote_previews = {}, {}, {}
        self.delivery_labels = {}
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
        self.follow_bottom = True
        QTimer.singleShot(0, lambda: self.scroll.verticalScrollBar().setValue(self.scroll.verticalScrollBar().maximum()))

    def scroll_position_changed(self, value):
        self.follow_bottom = value >= self.scroll.verticalScrollBar().maximum() - 70

    def scroll_range_changed(self, minimum, maximum):
        # Qt can finish laying out a new bubble after bottom()'s single-shot.
        # Follow late range changes only while the reader has stayed at the bottom.
        if self.follow_bottom:
            self.scroll.verticalScrollBar().setValue(maximum)

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
            heading.addWidget(ElidedLabel('Vous' if message.outgoing else message.nickname, 'messageName'), 1)
            heading.addWidget(label(message.time.strftime('%H:%M'), 'caption'))
            if message.id and not message.deleted:
                for text, tooltip, callback in (
                    ('↩', 'Répondre à ce message', lambda checked=False, mid=message.id: self.reply_requested.emit(mid)),
                    ('☺', 'Réagir avec un emoji', lambda checked=False, mid=message.id: self.pick_reaction(mid)),
                ):
                    control = button(text, 'flat', tooltip)
                    control.setFixedSize(25, 24)
                    control.setStyleSheet('padding: 0; font-size: 17px; border-radius: 7px;')
                    control.clicked.connect(callback)
                    heading.addWidget(control)
                if message.outgoing:
                    more = button('···', 'flat', 'Modifier ou supprimer ce message')
                    more.setFixedSize(25, 24)
                    more.setStyleSheet('padding: 0;')
                    more.clicked.connect(lambda checked=False, mid=message.id:self.message_menu(mid))
                    heading.addWidget(more)
            content.addLayout(heading)
            if message.edited:
                content.addWidget(label('Modifié', 'caption'))
            if message.reply_to:
                quote = ReplyPreview(message.reply_to, message.reply_name, message.reply_text, bubble)
                quote.activated.connect(self.focus_message)
                content.addWidget(quote)
                self.quote_previews[message.id] = quote
            if message.poll:
                card = PollCard(message.poll, self.session_id, bubble)
                card.voted.connect(lambda option, mid=message.id:self.vote_requested.emit(mid, option))
                content.addWidget(card)
                self.poll_cards[message.id] = card
                bubble.desired_width = 360
            elif message.attachment:
                card = FileCard(message.attachment, bubble)
                card.action_requested.connect(self.file_action)
                content.addWidget(card)
                self.file_cards[message.id] = (row, card)
                bubble.desired_width = 360
            else:
                from .message_text import MessageText
                content.addWidget(MessageText(message.text, self.actor_id in message.mentions, bubble))
                bubble.desired_width = 480 if '```' in message.text else max(240, min(580, max((self.fontMetrics().horizontalAdvance(line) for line in message.text.split('\n')), default=0) + 34))
            if message.reply_to:
                bubble.desired_width = max(300, bubble.desired_width)
            if message.id:
                reactions = ReactionBar(bubble)
                reactions.chosen.connect(lambda emoji, mid=message.id: self.reaction_requested.emit(mid, emoji))
                reactions.update_reactions(message.reactions, self.actor_id)
                reactions.setVisible(not message.deleted)
                content.addWidget(reactions)
                self.message_rows[message.id] = (row, bubble, message)
                self.reaction_bars[message.id] = reactions
                if message.outgoing and not message.attachment:
                    status = label('', 'deliveryStatus', True)
                    status.setAlignment(Qt.AlignmentFlag.AlignRight)
                    content.addWidget(status)
                    self.delivery_labels[message.id] = status
                    self.update_delivery(message)
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
            self.poll_cards = {key: value for key, value in self.poll_cards.items() if key in self.message_rows and self.message_rows[key][0] is not oldest}
            self.message_rows = {key: value for key, value in self.message_rows.items() if value[0] is not oldest}
            self.reaction_bars = {key: value for key, value in self.reaction_bars.items() if key in self.message_rows}
            self.quote_previews = {key: value for key, value in self.quote_previews.items() if key in self.message_rows}
            self.delivery_labels = {key: value for key, value in self.delivery_labels.items() if key in self.message_rows}
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

    def refresh_messages(self, messages, target):
        previous, follow = self.scroll.verticalScrollBar().value(), self.follow_bottom
        self.reset(messages, target)
        if not follow:
            self.follow_bottom = False
            QTimer.singleShot(0, lambda:self.scroll.verticalScrollBar().setValue(previous))

    def message_menu(self, reference):
        entry = self.message_rows.get(reference)
        if not entry or not entry[2].outgoing or entry[2].deleted:
            return
        menu = QMenu(self)
        if not entry[2].poll and not entry[2].attachment:
            menu.addAction('Modifier le message', lambda:self.edit_requested.emit(reference))
        menu.addAction('Supprimer pour les participants', lambda:self.delete_requested.emit(reference))
        menu.exec(QCursor.pos())
        menu.deleteLater()

    def update_poll(self, poll):
        card = self.poll_cards.get(poll['id'])
        if card:
            card.update_poll(poll)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, 'bubbles'):
            self.adjust_bubbles()
            QTimer.singleShot(0, self.adjust_bubbles)

    def update_transfer(self, metadata):
        entry = self.file_cards.get(metadata['id'])
        if entry:
            entry[1].update_transfer(metadata)

    def update_delivery(self, message):
        status = self.delivery_labels.get(message.id)
        if status:
            text, blue, tooltip = delivery_label(message)
            status.setText(text)
            status.setToolTip(tooltip)
            status.setAccessibleName(text + '. ' + tooltip)
            blue_color = '#2868c7' if QApplication.instance().property('fortytchatTheme') == 'light' else '#69b6ff'
            color = blue_color if blue else colors()['muted']
            status.setStyleSheet(f'color: {color}; font-size: 11px; background: transparent;')
            status.setVisible(bool(text))

    def visible_incoming(self):
        if not self.isVisible():
            return []
        viewport = self.scroll.viewport()
        visible = []
        for reference, (row, _, message) in self.message_rows.items():
            if message.outgoing or message.system or message.attachment or message.poll or message.deleted or not row.isVisible():
                continue
            bounds = QRect(row.mapTo(viewport, QPoint(0, 0)), row.size())
            if bounds.intersected(viewport.rect()).height() >= min(24, row.height()):
                visible.append(reference)
        return visible

    def update_reactions(self, message):
        bar = self.reaction_bars.get(message.id)
        if bar:
            at_bottom = self.scroll.verticalScrollBar().value() >= self.scroll.verticalScrollBar().maximum() - 60
            bar.update_reactions(message.reactions, self.actor_id)
            if at_bottom:
                self.bottom()

    def update_quote(self, message):
        quote = self.quote_previews.get(message.id)
        if quote:
            quote.name.set_full_text('Réponse à ' + message.reply_name)
            quote.text.set_full_text(message.reply_text)

    def pick_reaction(self, reference):
        reaction_menu(self, lambda emoji: self.reaction_requested.emit(reference, emoji))

    def focus_message(self, reference):
        entry = self.message_rows.get(reference)
        if not entry:
            self.quote_missing.emit()
            return
        row, bubble, _ = entry
        self.scroll.ensureWidgetVisible(row, 0, 40)
        from .theme import colors
        bubble.setStyleSheet(f'QFrame#{bubble.objectName()} {{ border: 1px solid {colors()["accent"]}; }}')
        timer = getattr(bubble, 'quote_timer', None)
        if timer is None:
            timer = QTimer(bubble)
            timer.setSingleShot(True)
            timer.timeout.connect(lambda: bubble.setStyleSheet(''))
            bubble.quote_timer = timer
        timer.start(1200)

    def wizz(self):
        from .attention import shake_window
        self.bottom()
        QTimer.singleShot(80, self.bottom)
        self.animation = shake_window(self.window(), self.window().centralWidget())
