import time
import math
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import QDialog, QVBoxLayout, QLineEdit, QListWidget, QListWidgetItem, QMenu, QMessageBox, QSizePolicy
from .widgets import label, button
from .private_window import PrivateWindow
from ..features.collaboration import MAX_MEMBERS


class GroupEditor(QDialog):
    submitted = Signal(str, object)

    def __init__(self, peers, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Créer un groupe · FortyTchat')
        self.resize(460, 600)
        box = QVBoxLayout(self)
        box.setContentsMargins(24, 24, 24, 24)
        box.setSpacing(14)
        box.addWidget(label('À plusieurs, c’est mieux.', 'title', True))
        box.addWidget(label('Invite jusqu’à 31 personnes. Chacun choisit de rejoindre ; les messages du groupe restent en mémoire.', 'muted', True))
        self.name = QLineEdit()
        self.name.setMaxLength(64)
        self.name.setPlaceholderText('Nom du groupe')
        self.name.setAccessibleName('Nom du groupe')
        box.addWidget(self.name)
        self.members = QListWidget()
        seen = set()
        for peer in sorted(peers, key=lambda p: p['nick'].casefold()):
            cid = peer['contact_id']
            if cid in seen:
                continue
            seen.add(cid)
            item = QListWidgetItem(peer['nick'])
            item.setData(Qt.ItemDataRole.UserRole, cid)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Unchecked)
            self.members.addItem(item)
        box.addWidget(self.members, 1)
        self.feedback = label('', 'caption', True)
        box.addWidget(self.feedback)
        create = button('Créer et inviter', 'primary')
        create.clicked.connect(self.submit)
        box.addWidget(create)
        cancel = button('Annuler')
        cancel.clicked.connect(self.reject)
        box.addWidget(cancel)

    def submit(self):
        contacts = [self.members.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.members.count())
                    if self.members.item(i).checkState() == Qt.CheckState.Checked]
        if not self.name.text().strip() or not 1 <= len(contacts) < MAX_MEMBERS:
            self.feedback.setText('Choisis un nom et entre 1 et 31 personnes connectées.')
            return
        self.submitted.emit(self.name.text().strip(), contacts)
        self.accept()


class GroupWindow(PrivateWindow):
    leave_requested = Signal(str)
    manage_requested = Signal(str)

    def __init__(self, room, messages, icon_path):
        super().__init__('group:' + room['id'], room['name'], messages, icon_path)
        self.room, self.member_names = room, {}
        self.name_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        for control in (self.friend_button, self.security_button, self.composer.wizz_button,
                        self.composer.break_button):
            control.hide()
        self.composer.poll_button.show()
        self.composer.card_button.show()
        self.input.setPlaceholderText('Écrire au groupe…')
        self.set_group(room)

    def set_group(self, room, names=None):
        self.room = room
        self.avatar.nickname = room['name']
        self.avatar.avatar = room.get('avatar', -1)
        self.avatar.image_data = room.get('photo', '')
        self.avatar.online = False
        self.avatar.update()
        self.composer.group_popup_at = room.get('popup_at', 0)
        if names is not None:
            self.member_names = names
        self.name_label.setText(self.name_label.fontMetrics().elidedText(room['name'], Qt.TextElideMode.ElideRight, max(100, self.width() - 150)))
        self.name_label.setToolTip(room['name'])
        self.setWindowTitle(room['name'] + ' · FortyTchat')
        joined = len(set(room['joined']) - set(room['left']))
        self.status_label.setText(f'{joined}/{len(room["members"])} membres ont rejoint' if room['state'] == 'joined'
                                  else {'pending':'Invitation à rejoindre', 'joining':'Connexion au groupe…',
                                        'left':'Tu as quitté le groupe', 'declined':'Invitation refusée', 'removed':'Tu as été retiré du groupe', 'restoring':'En attente du créateur pour restaurer le groupe…'}.get(room['state'], ''))
        available = room['state'] == 'joined'
        self.composer.available = available
        self.input.setEnabled(available)
        self.send_button.setEnabled(available)
        self.composer.update_actions()
        self.security_note.setText('Chiffré de bout en bout · membres invités uniquement · messages éphémères')

    def menu(self):
        menu = QMenu(self)
        menu.addAction('Membres, rôles, nom et photo', lambda:self.manage_requested.emit(self.room['id']))
        if self.room['state'] == 'joined':
            menu.addAction('Quitter le groupe', self.leave)
        menu.addAction('Fermer la fenêtre', self.close)
        menu.exec(QCursor.pos())
        menu.deleteLater()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, 'room'):
            self.name_label.setText(self.name_label.fontMetrics().elidedText(self.room['name'], Qt.TextElideMode.ElideRight, max(100, self.width() - 150)))

    def show_members(self):
        lines = []
        for cid in self.room['members']:
            state = 'a quitté' if cid in self.room['left'] else 'a rejoint' if cid in self.room['joined'] else 'invité·e'
            lines.append(self.member_names.get(cid, cid[:10]) + ' · ' + state)
        QMessageBox.information(self, 'Membres du groupe', '\n'.join(lines))

    def leave(self):
        if QMessageBox.question(self, 'Quitter le groupe', 'Quitter ce groupe ? Tes messages reçus resteront en mémoire jusqu’à l’arrêt de FortyTchat.') == QMessageBox.StandardButton.Yes:
            self.leave_requested.emit(self.room['id'])


class GroupManager(QDialog):
    changed = Signal(object)

    def __init__(self, room, actor, names, peers, parent=None):
        super().__init__(parent)
        from PySide6.QtWidgets import QHBoxLayout, QComboBox, QScrollArea, QWidget, QFrame
        from .widgets import Avatar
        self.actor, self.names, self.peers = actor, names, peers
        self.setWindowTitle('Gérer le groupe · FortyTchat')
        self.resize(500, 660)
        self.setMinimumSize(390, 440)
        box = QVBoxLayout(self)
        box.setContentsMargins(22, 22, 22, 22)
        box.addWidget(label('Notre petit groupe.', 'title', True))
        box.addWidget(label('Créateur : attribue les rôles. Administrateurs : nom, photo et membres ordinaires. Le créateur doit être connecté pour valider les changements.', 'muted', True))
        self.picture = Avatar(room['name'], 64)
        photo = button('Changer la photo', 'soft')
        photo.clicked.connect(self.choose_photo)
        head = QHBoxLayout(); head.addWidget(self.picture); head.addWidget(photo); head.addStretch()
        self.photo_button = photo
        box.addLayout(head)
        self.name = QLineEdit(); self.name.setMaxLength(64)
        rename = button('Renommer', 'primary')
        rename.clicked.connect(lambda:self.changed.emit({'name': self.name.text().strip()}) if self.name.text().strip() else None)
        row = QHBoxLayout(); row.addWidget(self.name, 1); row.addWidget(rename)
        self.rename_button = rename
        box.addLayout(row)
        self.members = QListWidget()
        self.members.currentItemChanged.connect(self.selection_changed)
        box.addWidget(self.members, 1)
        row = QHBoxLayout()
        self.remove = button('Retirer', 'soft')
        self.promote = button('Nommer admin', 'soft')
        self.remove.clicked.connect(lambda:self.member_action('remove'))
        self.promote.clicked.connect(self.change_role)
        row.addWidget(self.remove); row.addWidget(self.promote)
        box.addLayout(row)
        self.add_choice = QComboBox(); self.add_choice.setMinimumWidth(100)
        self.add_choice.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.add_choice.setMinimumContentsLength(10)
        self.add_button = button('Ajouter', 'primary')
        self.add_button.clicked.connect(lambda:self.changed.emit({'add': self.add_choice.currentData()}) if self.add_choice.currentData() else None)
        row = QHBoxLayout(); row.addWidget(self.add_choice, 1); row.addWidget(self.add_button)
        box.addLayout(row)
        box.addWidget(label('Les nouveaux membres acceptent l’invitation. Un membre retiré ne reçoit plus les nouveaux échanges dès que son retrait est reçu. Aucun ancien message n’est envoyé.', 'caption', True))
        close = button('Terminé'); close.clicked.connect(self.accept); box.addWidget(close)
        self.set_group(room)

    def set_group(self, room):
        self.room = room
        self.name.setText(room['name'])
        self.picture.nickname, self.picture.avatar = room['name'], room.get('avatar', -1)
        self.picture.image_data = room.get('photo', ''); self.picture.update()
        admin = room['state'] == 'joined' and (self.actor == room['owner'] or self.actor in room.get('admins', ()))
        for control in (self.name, self.rename_button, self.photo_button, self.add_choice): control.setEnabled(admin)
        old = self.members.currentItem().data(Qt.ItemDataRole.UserRole) if self.members.currentItem() else ''
        self.members.clear()
        for cid in room['members']:
            role = 'Créateur' if cid == room['owner'] else 'Admin' if cid in room.get('admins', ()) else 'Membre'
            name = self.names.get(cid, cid[:10]) + (' (toi)' if cid == self.actor else '')
            item = QListWidgetItem(name + ' · ' + role)
            item.setData(Qt.ItemDataRole.UserRole, cid)
            self.members.addItem(item)
            if cid == old: self.members.setCurrentItem(item)
        self.add_choice.clear()
        for peer in self.peers:
            cid = peer['contact_id']
            if cid not in room['members'] and self.add_choice.findData(cid) < 0:
                self.add_choice.addItem(peer['nick'], cid)
        self.add_button.setEnabled(admin and self.add_choice.count() > 0)
        self.selection_changed()

    def selection_changed(self, *args):
        if not hasattr(self, 'room'): return
        cid = self.members.currentItem().data(Qt.ItemDataRole.UserRole) if self.members.currentItem() else ''
        owner = self.actor == self.room['owner']
        admin = owner or self.actor in self.room.get('admins', ())
        self.remove.setEnabled(bool(cid and cid != self.room['owner'] and admin and (owner or cid not in self.room.get('admins', ())) and self.room['state'] == 'joined'))
        self.promote.setEnabled(bool(cid and cid != self.room['owner'] and owner and self.room['state'] == 'joined'))
        self.promote.setText('Retirer le rôle admin' if cid in self.room.get('admins', ()) else 'Nommer admin')

    def member_action(self, action):
        item = self.members.currentItem()
        if item:
            cid = item.data(Qt.ItemDataRole.UserRole)
            if action == 'remove' and QMessageBox.question(self, 'Retirer ce membre', 'Retirer cette personne du groupe ?') != QMessageBox.StandardButton.Yes: return
            self.changed.emit({action: cid})

    def change_role(self):
        item = self.members.currentItem()
        if item: self.member_action('demote' if item.data(Qt.ItemDataRole.UserRole) in self.room.get('admins', ()) else 'promote')

    def choose_photo(self):
        from .avatar_picker import AvatarPicker
        picker = AvatarPicker(self.room['name'], self.room.get('avatar', -1), self, image_data=self.room.get('photo', ''))
        picker.setWindowTitle('Photo du groupe · FortyTchat')
        picker.selected.connect(lambda value:self.changed.emit({'avatar': value}))
        picker.photo_selected.connect(lambda value:self.changed.emit({'photo': value}))
        picker.exec()
        picker.deleteLater()
