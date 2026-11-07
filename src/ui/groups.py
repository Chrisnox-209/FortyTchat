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

    def __init__(self, room, messages, icon_path):
        super().__init__('group:' + room['id'], room['name'], messages, icon_path)
        self.room, self.member_names = room, {}
        self.name_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        for control in (self.friend_button, self.security_button, self.composer.wizz_button,
                        self.composer.break_button, self.composer.card_button):
            control.hide()
        self.composer.poll_button.show()
        self.input.setPlaceholderText('Écrire au groupe…')
        self.set_group(room)

    def set_group(self, room, names=None):
        self.room = room
        if names is not None:
            self.member_names = names
        self.name_label.setText(self.name_label.fontMetrics().elidedText(room['name'], Qt.TextElideMode.ElideRight, max(100, self.width() - 150)))
        self.name_label.setToolTip(room['name'])
        self.setWindowTitle(room['name'] + ' · FortyTchat')
        joined = len(set(room['joined']) - set(room['left']))
        self.status_label.setText(f'{joined}/{len(room["members"])} membres ont rejoint' if room['state'] == 'joined'
                                  else {'pending':'Invitation à rejoindre', 'joining':'Connexion au groupe…',
                                        'left':'Tu as quitté le groupe', 'declined':'Invitation refusée'}.get(room['state'], ''))
        available = room['state'] == 'joined'
        self.composer.available = available
        self.input.setEnabled(available)
        self.send_button.setEnabled(available)
        self.composer.update_actions()
        self.security_note.setText('Chiffré de bout en bout · membres invités uniquement · messages éphémères')

    def menu(self):
        menu = QMenu(self)
        menu.addAction('Voir les membres', self.show_members)
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
