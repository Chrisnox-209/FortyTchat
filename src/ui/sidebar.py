from PySide6.QtCore import Qt, Signal, QSize
from PySide6.QtGui import QColor, QPainter, QFont
from PySide6.QtWidgets import (QFrame, QVBoxLayout, QHBoxLayout, QListWidget,
                               QListWidgetItem, QStyledItemDelegate, QStyle, QMenu)
from .widgets import Avatar, button, color_for, label, paint_avatar
from .theme import colors
from ..features.friends import friend_key, friend_rows, online_contact


class PeerDelegate(QStyledItemDelegate):
    def paint(self, painter, option, index):
        data = index.data(Qt.ItemDataRole.UserRole)
        if not data:
            return
        c = colors()
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = option.rect.adjusted(7, 4, -7, -4)
        if data.get('section'):
            painter.setPen(QColor(c['subtle']))
            painter.setFont(QFont('DejaVu Sans', 8, QFont.Weight.DemiBold))
            painter.drawText(r.adjusted(12, 0, 0, 0), Qt.AlignmentFlag.AlignVCenter, data['section'])
            painter.restore()
            return
        selected = option.state & QStyle.StateFlag.State_Selected
        if selected or option.state & QStyle.StateFlag.State_MouseOver:
            painter.setBrush(QColor(c['selected'] if selected else c['hover']))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRoundedRect(r, 21, 21)
        from PySide6.QtCore import QRectF
        avatar_rect = QRectF(r.x()+12, r.y()+12, 49, 49)
        if data['sid'] == 'general':
            painter.setBrush(QColor('#b1d4fa'))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRoundedRect(avatar_rect, 17, 17)
            painter.setPen(QColor('#3f6698'))
            painter.setFont(QFont('DejaVu Sans', 22))
            painter.drawText(avatar_rect, Qt.AlignmentFlag.AlignCenter, '#')
        else:
            paint_avatar(painter, avatar_rect, data['nick'], data.get('online', False), data.get('avatar', -1), data.get('avatar_image', ''), data.get('locked', False))
        unread = data.get('unread', 0)
        available = r.width() - 111
        painter.setFont(QFont('DejaVu Sans', 11, QFont.Weight.Medium))
        name = painter.fontMetrics().elidedText(data['nick'], Qt.TextElideMode.ElideRight, available)
        painter.setPen(QColor(c['text']))
        painter.drawText(r.x()+75, r.y()+15, available, 21, Qt.AlignmentFlag.AlignVCenter, name)
        painter.setFont(QFont('DejaVu Sans', 9))
        painter.setPen(QColor(c['muted']))
        detail = painter.fontMetrics().elidedText(data.get('detail', ''), Qt.TextElideMode.ElideRight, available)
        painter.drawText(r.x()+75, r.y()+42, available, 18, Qt.AlignmentFlag.AlignVCenter, detail)
        if unread:
            painter.setBrush(QColor(c['accent']))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawEllipse(r.right()-30, r.y()+26, 24, 24)
            painter.setPen(QColor('white'))
            painter.drawText(r.right()-30, r.y()+26, 24, 24, Qt.AlignmentFlag.AlignCenter, str(min(unread, 99)))
        elif data['sid'] != 'general':
            painter.setPen(QColor(c['subtle']))
            painter.setFont(QFont('DejaVu Sans', 12))
            painter.drawText(r.right()-28, r.y()+23, 22, 27, Qt.AlignmentFlag.AlignCenter, '★' if data.get('friend') else '↗')
        painter.restore()


class Sidebar(QFrame):
    selected = Signal(str)
    settings_requested = Signal()
    status_requested = Signal()
    avatar_requested = Signal()
    friend_requested = Signal(str, bool)
    popup_requested = Signal(str)
    group_requested = Signal()
    block_requested = Signal(str, bool)

    def __init__(self, nickname, avatar=-1, image_data='', friends=None, blocked_contacts=None):
        super().__init__()
        self.setObjectName('sidebar')
        self.setFixedWidth(312)
        self.peers, self.conversations, self.unread = {}, {}, {}
        self.blocked, self.current = set(), 'general'
        self.popup_available = None
        self.groups = {}
        self.blocked_contacts = blocked_contacts if blocked_contacts is not None else {}
        self.previews = {}
        self.friends, self.known_contacts = friends if friends is not None else {}, {}
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        heading = QVBoxLayout()
        heading.setContentsMargins(25, 29, 23, 20)
        heading.setSpacing(12)
        brand = QHBoxLayout()
        brand.addWidget(label('FortyTchat', 'brand'))
        brand.addStretch()
        brand.addWidget(label('42', 'chip'))
        heading.addLayout(brand)
        heading.addWidget(label('Vos messages, simplement.', 'muted'))
        heading.addSpacing(14)
        heading.addWidget(label('MESSAGES', 'eyebrow'))
        create_group = button('＋  Créer un groupe', 'soft', 'Inviter des personnes dans un groupe privé')
        create_group.clicked.connect(self.group_requested)
        heading.addWidget(create_group)
        layout.addLayout(heading)
        self.list = QListWidget()
        self.list.setMouseTracking(True)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.setItemDelegate(PeerDelegate(self.list))
        self.list.itemClicked.connect(self._clicked)
        self.list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self.friend_menu)
        layout.addWidget(self.list, 1)
        self.hint = label('◌  À la recherche du cluster…\nLes personnes en ligne apparaîtront ici.', 'caption', True)
        self.hint.setContentsMargins(25, 14, 22, 24)
        layout.addWidget(self.hint)
        profile = QFrame()
        profile.setObjectName('profile')
        profile_box = QHBoxLayout(profile)
        profile_box.setContentsMargins(22, 19, 17, 19)
        self.avatar = Avatar(nickname, 42, True, avatar, image_data)
        self.avatar.setCursor(Qt.CursorShape.PointingHandCursor)
        self.avatar.setToolTip('Changer mon avatar')
        self.avatar.clicked.connect(self.avatar_requested)
        profile_box.addWidget(self.avatar)
        names = QVBoxLayout()
        names.setSpacing(4)
        self.name_label = label(nickname)
        self.name_label.setMaximumWidth(154)
        self.name_label.setToolTip(nickname)
        names.addWidget(self.name_label)
        self.status_button = button('●  En ligne', 'flat', 'Changer de statut')
        self.status_button.setStyleSheet('text-align: left; padding: 0; font-size: 11px;')
        self.status_button.clicked.connect(self.status_requested)
        names.addWidget(self.status_button)
        profile_box.addLayout(names, 1)
        settings = button('⚙', 'flat', 'Paramètres')
        settings.clicked.connect(self.settings_requested)
        profile_box.addWidget(settings)
        layout.addWidget(profile)
        self.refresh()

    def _clicked(self, item):
        data = item.data(Qt.ItemDataRole.UserRole)
        if data and 'sid' in data:
            self.selected.emit(data['sid'])

    def friend_menu(self, position):
        item = self.list.itemAt(position)
        data = item.data(Qt.ItemDataRole.UserRole) if item else None
        menu = self.build_friend_menu(data)
        if menu:
            menu.exec(self.list.viewport().mapToGlobal(position))
            menu.deleteLater()

    def build_friend_menu(self, data):
        if not data or not data.get('contact_id'):
            return
        contact_id = data['contact_id']
        added = contact_id in self.friends
        menu = QMenu(self)
        if added:
            menu.addAction('Discuter…', lambda: self.selected.emit(friend_key(contact_id)))
            popup = menu.addAction('Envoyer une popup…', lambda: self.popup_requested.emit(contact_id))
            row = friend_rows({contact_id:self.friends[contact_id]}, self.peers.values(), self.blocked,
                              popup_available=self.popup_available)[0]
            popup.setEnabled(row['popup'])
            menu.addSeparator()
        menu.addAction('Retirer des amis' if added else 'Ajouter aux amis',
                       lambda: self.friend_requested.emit(contact_id, not added))
        blocked = contact_id in self.blocked_contacts
        menu.addSeparator()
        menu.addAction('Débloquer cet utilisateur' if blocked else 'Bloquer cet utilisateur',
                       lambda:self.block_requested.emit(contact_id, not blocked))
        return menu

    def _section(self, name):
        item = QListWidgetItem()
        item.setData(Qt.ItemDataRole.UserRole, {'section': name})
        item.setSizeHint(QSize(260, 43))
        item.setFlags(Qt.ItemFlag.NoItemFlags)
        self.list.addItem(item)

    def _row(self, sid, nick, detail, online=False, avatar=-1, avatar_image='', contact_id='', friend=False):
        locked = bool(online and self.peers.get(sid,{}).get('locked') and not detail.startswith(('Bloqué', 'Clé modifiée')))
        if locked:
            detail='Session verrouillée'
        item = QListWidgetItem()
        unread = self.unread.get(sid, 0)
        if friend and contact_id:
            unread = sum(value for key, value in self.unread.items()
                         if key == friend_key(contact_id) or self.known_contacts.get(key) == contact_id)
        item.setData(Qt.ItemDataRole.UserRole, {'sid': sid, 'nick': nick, 'detail': detail,
                                             'online': online, 'locked': locked, 'avatar': avatar, 'avatar_image': avatar_image,
                                             'contact_id': contact_id, 'friend': friend, 'unread': unread})
        item.setSizeHint(QSize(280, 82))
        item.setToolTip(nick + ' · ' + detail + (f' · session {sid[:8]}' if sid != 'general' else ''))
        self.list.addItem(item)
        if sid == self.current:
            self.list.setCurrentItem(item)

    def refresh(self):
        scroll = self.list.verticalScrollBar().value()
        self.list.clear()
        self.known_contacts.update({sid: p.get('contact_id', '') for sid, p in self.peers.items()})
        keep = set(self.peers) | set(self.conversations) | set(self.unread)
        self.known_contacts = {sid: cid for sid, cid in self.known_contacts.items() if sid in keep}
        self._row('general', 'général', self.previews.get('general', 'Tout le cluster, au même endroit'))
        if self.groups:
            self._section(f'GROUPES  ·  {len(self.groups)}')
            for key, room in self.groups.items():
                detail = 'Invitation · cliquer pour rejoindre' if room['state'] == 'pending' else (
                    self.previews.get(key, f'{len(set(room["joined"]) - set(room["left"]))} membres · groupe privé')
                    if room['state'] == 'joined' else 'Restauration · créateur hors ligne' if room['state'] == 'restoring' else 'Retiré du groupe' if room['state'] == 'removed' else 'Groupe quitté ou invitation refusée')
                self._row(key, room['name'], detail, room['state'] == 'joined', room.get('avatar', -1), room.get('photo', ''))
        if self.friends:
            self._section(f'AMIS  ·  {len(self.friends)}')
            live_friends = {cid: online_contact(self.peers.values(), cid) for cid in self.friends}
            for contact_id, saved_name in sorted(self.friends.items(), key=lambda x: (live_friends[x[0]] is None, x[1].casefold())):
                peer = live_friends[contact_id]
                if peer:
                    detail = {'online': 'En ligne · ami', 'away': 'Absent·e · ami', 'dnd': 'Ne pas déranger'}.get(peer['status'], '')
                    if contact_id in self.blocked_contacts or peer['sid'] in self.blocked:
                        detail = 'Bloqué'
                    self._row(peer['sid'], peer['nick'], detail, True, peer.get('avatar', -1),
                              peer.get('avatar_image', ''), contact_id, True)
                else:
                    changed = any(p.get('contact_id') == contact_id and p.get('security') == 'changed'
                                  for p in self.peers.values())
                    self._row(friend_key(contact_id), saved_name, 'Bloqué · hors ligne' if contact_id in self.blocked_contacts else 'Clé modifiée · vérifier' if changed else 'Hors ligne',
                              contact_id=contact_id, friend=True)
        others = [p for p in self.peers.values() if p.get('contact_id') not in self.friends]
        self._section(f'{"AUTRES EN LIGNE" if self.friends else "EN LIGNE"}  ·  {len(others)}')
        for peer in sorted(self.peers.values(), key=lambda p: (p['nick'].casefold(), p['sid'])):
            if peer.get('contact_id') in self.friends:
                continue
            detail = {'online': self.previews.get(peer['sid'], 'Cliquer pour discuter'), 'away': 'Absent·e', 'dnd': 'Ne pas déranger'}.get(peer['status'], '')
            if peer['sid'] in self.blocked:
                detail = 'Bloqué · cliquer pour gérer'
            elif peer.get('security') == 'changed':
                detail = 'Clé modifiée · vérifier le cadenas'
            self._row(peer['sid'], peer['nick'], detail, True, peer.get('avatar', -1), peer.get('avatar_image', ''), peer.get('contact_id', ''))
        offline = [(sid, nick) for sid, nick in self.conversations.items() if sid not in self.peers
                   and self.known_contacts.get(sid) not in self.friends and not sid.startswith(('friend:', 'group:'))]
        if offline:
            self._section('CONVERSATIONS DE CETTE SESSION')
            for sid, nick in offline[-64:]:
                self._row(sid, nick, 'Hors ligne · ouvrir la conversation')
        self.hint.setVisible(not self.peers)
        self.list.verticalScrollBar().setValue(scroll)

    def focus_online(self):
        for index in range(self.list.count()):
            item = self.list.item(index)
            data = item.data(Qt.ItemDataRole.UserRole) or {}
            if data.get('sid') in self.peers:
                self.list.setCurrentItem(item)
                self.list.scrollToItem(item)
                break
        self.list.setFocus()

    def set_nickname(self, nickname):
        self.name_label.setText(self.name_label.fontMetrics().elidedText(nickname, Qt.TextElideMode.ElideRight, 150))
        self.name_label.setToolTip(nickname)
        self.avatar.nickname = nickname
        self.avatar.update()
