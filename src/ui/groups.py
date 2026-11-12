import time
import math
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import QDialog, QVBoxLayout, QLineEdit, QListWidget, QListWidgetItem, QMenu, QMessageBox, QSizePolicy, QLayout
from .widgets import label, button
from .private_window import PrivateWindow
from ..features.collaboration import MAX_MEMBERS
from ..features.group_profile import popup_interval, MIN_POPUP_MINUTES, MAX_POPUP_MINUTES


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
        for control in (self.friend_button, self.security_button, self.screen_button, self.composer.wizz_button,
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
        self.composer.group_popup_interval = popup_interval(room)
        if names is not None:
            self.member_names = names
        self.name_label.set_full_text(room['name'])
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
        menu.addAction('Paramètres du groupe', lambda:self.manage_requested.emit(self.room['id']))
        if self.room['state'] == 'joined':
            menu.addAction('Quitter le groupe', self.leave)
        menu.addAction('Fermer la fenêtre', self.close)
        menu.exec(QCursor.pos())
        menu.deleteLater()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, 'room'):
            self.name_label.set_full_text(self.room['name'])

    def show_members(self):
        lines = []
        for cid in self.room['members']:
            state = 'a quitté' if cid in self.room['left'] else 'a rejoint' if cid in self.room['joined'] else 'invité·e'
            lines.append(self.member_names.get(cid, cid[:10]) + ' · ' + state)
        QMessageBox.information(self, 'Membres du groupe', '\n'.join(lines))

    def leave(self):
        if QMessageBox.question(self, 'Quitter le groupe', 'Quitter ce groupe ? Tes messages reçus resteront en mémoire jusqu’à l’arrêt de FortyTchat.') == QMessageBox.StandardButton.Yes:
            self.leave_requested.emit(self.room['id'])


class MinuteInput(QLineEdit):
    """A regular line editor avoids native spinbox clipping with enlarged fonts."""
    def __init__(self, parent=None):
        super().__init__(parent)
        from PySide6.QtGui import QIntValidator
        self.setValidator(QIntValidator(MIN_POPUP_MINUTES, MAX_POPUP_MINUTES, self))
        self.setAccessibleName('Délai entre les popups, en minutes')
        self.setMaxLength(4)

    def value(self): return int(self.text()) if self.hasAcceptableInput() else 0
    def setValue(self, value): self.setText(str(max(self.minimum(), min(self.maximum(), value))))
    def minimum(self): return MIN_POPUP_MINUTES
    def maximum(self): return MAX_POPUP_MINUTES
    def stepDown(self): self.setValue((self.value() or 42) - 1)
    def stepUp(self): self.setValue((self.value() or 42) + 1)


class GroupManager(QDialog):
    changed = Signal(object)

    def __init__(self, room, actor, names, peers, parent=None):
        super().__init__(parent)
        from PySide6.QtWidgets import QHBoxLayout, QComboBox, QTabWidget
        from .widgets import Avatar
        self.actor, self.names, self.peers = actor, names, peers
        self.pending_delay, self.applied_minutes = None, None
        from PySide6.QtCore import QTimer
        self.delay_timer=QTimer(self);self.delay_timer.setSingleShot(True);self.delay_timer.setInterval(8000)
        self.delay_timer.timeout.connect(self.delay_timeout)
        self.setWindowTitle('Paramètres du groupe · FortyTchat')
        self.resize(550, 700); self.setMinimumSize(390, 440)
        outer = QVBoxLayout(self); outer.setContentsMargins(22, 22, 22, 22); outer.setSpacing(12)
        outer.addWidget(label('Votre groupe, à votre façon.', 'title', True))
        self.access_note = label('', 'muted', True); outer.addWidget(self.access_note)
        self.tabs = QTabWidget(); outer.addWidget(self.tabs, 1)
        page, form = self.page()
        photo_row = QHBoxLayout()
        self.picture = Avatar(room['name'], 64)
        self.photo_button = button('Changer la photo', 'soft'); self.photo_button.clicked.connect(self.choose_photo)
        photo_row.addWidget(self.picture); photo_row.addWidget(self.photo_button); photo_row.addStretch(); form.addLayout(photo_row)
        form.addWidget(label('Nom du groupe', 'eyebrow', True))
        self.name = QLineEdit(); self.name.setMaxLength(64)
        form.addWidget(self.name)
        self.rename_button = button('Renommer', 'primary')
        self.rename_button.clicked.connect(lambda:self.changed.emit({'name':self.name.text().strip()}) if self.name.text().strip() else None)
        form.addWidget(self.rename_button)
        form.addSpacing(12)
        form.addWidget(label('Notifications du groupe', 'title', True))
        form.addWidget(label('Temps à attendre entre deux popups, partagé par tous les membres.', 'muted', True))
        self.popup_current = label('', 'caption', True); form.addWidget(self.popup_current)
        self.popup_delay = MinuteInput()
        self.popup_delay.setPlaceholderText('42')
        self.popup_delay.textChanged.connect(self.update_delay_controls)
        self.popup_less = button('−', 'soft', 'Réduire d’une minute', 40)
        self.popup_more = button('+', 'soft', 'Augmenter d’une minute', 40)
        self.popup_less.clicked.connect(self.popup_delay.stepDown); self.popup_more.clicked.connect(self.popup_delay.stepUp)
        row = QHBoxLayout(); row.addWidget(self.popup_delay, 1)
        row.addWidget(self.popup_less); row.addWidget(self.popup_more); form.addLayout(row)
        self.popup_delay_button = button('Enregistrer le délai', 'primary')
        self.popup_delay_button.clicked.connect(self.submit_delay); form.addWidget(self.popup_delay_button)
        self.feedback = label('', 'notice', True); self.feedback.hide(); form.addWidget(self.feedback)
        form.addWidget(label('De 1 à 1440 minutes (24 h). Par défaut : 42 minutes. Le délai est calculé depuis la dernière popup et conservé après redémarrage.', 'caption', True))
        form.addStretch(); self.tabs.addTab(page, 'Réglages')
        page, form = self.page()
        self.member_count = label('', 'title', True); form.addWidget(self.member_count)
        form.addWidget(label('Le créateur gère les rôles. Les admins gèrent les réglages et les membres ordinaires.', 'muted', True))
        self.members = QListWidget(); self.members.setMinimumHeight(190)
        self.members.currentItemChanged.connect(self.selection_changed); form.addWidget(self.members, 1)
        self.remove = button('Retirer du groupe', 'soft'); self.remove.clicked.connect(lambda:self.member_action('remove'))
        self.promote = button('Nommer admin', 'soft'); self.promote.clicked.connect(self.change_role)
        form.addWidget(self.promote); form.addWidget(self.remove)
        form.addWidget(label('Inviter une personne connectée', 'eyebrow', True))
        self.add_choice = QComboBox(); self.add_choice.setMinimumWidth(100)
        self.add_choice.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.add_choice.setMinimumContentsLength(10); form.addWidget(self.add_choice)
        self.add_button = button('Ajouter au groupe', 'primary')
        self.add_button.clicked.connect(lambda:self.changed.emit({'add':self.add_choice.currentData()}) if self.add_choice.currentData() else None)
        form.addWidget(self.add_button)
        form.addWidget(label('La personne choisit d’accepter l’invitation. Aucun ancien message n’est envoyé.', 'caption', True))
        self.tabs.addTab(page, 'Membres')
        close = button('Terminé'); close.clicked.connect(self.accept); outer.addWidget(close)
        self.set_group(room)
        from PySide6.QtCore import QTimer
        QTimer.singleShot(0, self.fit_controls)

    @staticmethod
    def page():
        from PySide6.QtWidgets import QScrollArea, QFrame, QWidget
        scroll = QScrollArea(); scroll.setWidgetResizable(True); scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = QWidget(); box = QVBoxLayout(content); box.setContentsMargins(10, 16, 10, 16); box.setSpacing(12)
        box.setSizeConstraint(QLayout.SizeConstraint.SetMinAndMaxSize)
        scroll.setWidget(content)
        return scroll, box

    def fit_controls(self):
        from PySide6.QtWidgets import QPushButton
        for control in [self.name, self.popup_delay, self.add_choice, *self.findChildren(QPushButton)]:
            control.setMinimumHeight(max(44, control.fontMetrics().height() + 24))

    def changeEvent(self, event):
        super().changeEvent(event)
        from PySide6.QtCore import QEvent, QTimer
        if hasattr(self, 'add_choice') and event.type() in (QEvent.Type.FontChange, QEvent.Type.StyleChange):
            QTimer.singleShot(0, self.fit_controls)

    def is_admin(self):
        return self.room['state'] == 'joined' and (self.actor == self.room['owner'] or self.actor in self.room.get('admins', ()))

    def update_delay_controls(self):
        if not hasattr(self, 'room'): return
        valid = self.popup_delay.hasAcceptableInput()
        if not valid and self.popup_delay.hasFocus() and hasattr(self, 'feedback'):
            self.feedback.setText('Entre un nombre de 1 à 1440 minutes.'); self.feedback.show()
        self.popup_delay_button.setEnabled(self.is_admin() and valid and self.pending_delay is None
                                          and self.popup_delay.value() != popup_interval(self.room) // 60)

    def submit_delay(self):
        if not self.is_admin() or not self.popup_delay.hasAcceptableInput(): return
        self.pending_delay = self.popup_delay.value() * 60
        self.feedback.setText('Enregistrement en cours…'); self.feedback.show(); self.update_delay_controls()
        self.delay_timer.start()
        self.changed.emit({'popup_interval':self.pending_delay})

    def change_result(self, changes, ok):
        if 'popup_interval' not in changes: return
        if not ok:
            self.delay_timer.stop()
            self.pending_delay = None
            self.feedback.setText('Réglage refusé : créateur hors ligne, droits insuffisants ou version ancienne.')
            self.feedback.show(); self.update_delay_controls()
        elif self.pending_delay is not None:
            self.feedback.setText('Réglage envoyé. En attente de confirmation du créateur…'); self.feedback.show()

    def delay_timeout(self):
        if self.pending_delay is not None:
            self.pending_delay=None;self.feedback.setText('Confirmation non reçue. Vérifie la présence du créateur puis réessaie.');self.feedback.show();self.update_delay_controls()

    def set_group(self, room):
        self.room = room
        if not self.name.hasFocus(): self.name.setText(room['name'])
        self.picture.nickname, self.picture.avatar = room['name'], room.get('avatar', -1)
        self.picture.image_data = room.get('photo', ''); self.picture.update()
        actual = popup_interval(room) // 60
        if self.pending_delay == actual * 60:
            self.delay_timer.stop()
            self.pending_delay = None; self.popup_delay.setValue(actual)
            self.feedback.setText(f'Délai enregistré : {actual} min pour tout le groupe.'); self.feedback.show()
        elif self.applied_minutes is None or self.popup_delay.text() == str(self.applied_minutes):
            self.popup_delay.setValue(actual)
        self.applied_minutes = actual
        self.popup_current.setText(f'Délai enregistré : {actual} min. Nouvelle durée, en minutes :')
        admin = self.is_admin()
        self.access_note.setText('Le créateur doit être connecté pour valider les changements.' if admin
                                 else 'Tu peux consulter ces réglages. Le créateur et les admins peuvent les modifier.')
        for control in (self.name,self.rename_button,self.photo_button,self.add_choice,self.popup_delay,self.popup_less,self.popup_more): control.setEnabled(admin)
        self.update_delay_controls()
        scroll_position=self.members.verticalScrollBar().value()
        old = self.members.currentItem().data(Qt.ItemDataRole.UserRole) if self.members.currentItem() else ''
        self.members.clear(); self.member_count.setText(f'{len(room["members"])} membres')
        for cid in sorted(room['members'], key=lambda c:(0 if c == room['owner'] else 1 if c in room.get('admins',()) else 2,self.names.get(c,c).casefold())):
            self.add_member_row(cid)
            item = self.members.item(self.members.count()-1)
            if cid == old:self.members.setCurrentItem(item)
        self.members.verticalScrollBar().setValue(scroll_position)
        self.add_choice.clear()
        for peer in self.peers:
            cid = peer['contact_id']
            if cid not in room['members'] and self.add_choice.findData(cid) < 0:self.add_choice.addItem(peer['nick'],cid)
        self.add_button.setEnabled(admin and self.add_choice.count()>0); self.selection_changed()

    def add_member_row(self, cid):
        from PySide6.QtWidgets import QWidget, QHBoxLayout
        from PySide6.QtCore import QSize
        from .widgets import Avatar
        from .social import ElidedLabel
        from .theme import colors
        palette = colors(); peer = next((p for p in self.peers if p['contact_id']==cid),{})
        name = self.names.get(cid,cid[:10]) + (' (toi)' if cid == self.actor else '')
        row = QWidget(); row.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        row.setStyleSheet('background:transparent;'); layout = QHBoxLayout(row); layout.setContentsMargins(10,10,10,10)
        layout.addWidget(Avatar(name,38,online=bool(peer),avatar=peer.get('avatar',-1),image_data=peer.get('avatar_image',''),locked=bool(peer.get('locked'))))
        text = QVBoxLayout(); text.setSpacing(3)
        title = ElidedLabel(name,'messageName'); title.setToolTip(name); text.addWidget(title)
        state = 'A quitté le groupe' if cid in self.room['left'] else 'A rejoint' if cid in self.room['joined'] else 'Invitation en attente'
        if peer.get('locked') and cid in self.room['joined']:state='🔒 Session verrouillée'
        text.addWidget(label(state,'caption')); layout.addLayout(text,1)
        role = 'CRÉATEUR' if cid == self.room['owner'] else 'ADMIN' if cid in self.room.get('admins',()) else 'MEMBRE'
        badge = label(role); badge.setStyleSheet(f'color:{palette["accentText"] if role=="CRÉATEUR" else palette["online"] if role=="ADMIN" else palette["muted"]}; background:{palette["accentSoft"] if role=="CRÉATEUR" else palette["surface"]}; border-radius:8px; padding:6px 8px; font-size:11px; font-weight:700;')
        layout.addWidget(badge,0,Qt.AlignmentFlag.AlignVCenter)
        item = QListWidgetItem(); item.setData(Qt.ItemDataRole.UserRole,cid)
        item.setData(Qt.ItemDataRole.AccessibleTextRole,name+' · '+role)
        item.setData(Qt.ItemDataRole.AccessibleDescriptionRole,state)
        item.setSizeHint(QSize(0,max(78,title.fontMetrics().height()*2+36)))
        item.setToolTip(name+' · '+role); self.members.addItem(item); self.members.setItemWidget(item,row)

    def selection_changed(self,*args):
        if not hasattr(self,'room'):return
        item=self.members.currentItem();cid=item.data(Qt.ItemDataRole.UserRole) if item else ''
        owner=self.actor==self.room['owner'];admin=self.is_admin()
        self.remove.setEnabled(bool(cid and cid!=self.room['owner'] and admin and (owner or cid not in self.room.get('admins',()))))
        self.promote.setEnabled(bool(cid and cid!=self.room['owner'] and owner and self.room['state']=='joined'))
        self.promote.setText('Retirer le rôle admin' if cid in self.room.get('admins',()) else 'Nommer admin')

    def member_action(self,action):
        item=self.members.currentItem()
        if not item:return
        cid=item.data(Qt.ItemDataRole.UserRole)
        if action=='remove' and QMessageBox.question(self,'Retirer ce membre','Retirer cette personne du groupe ?')!=QMessageBox.StandardButton.Yes:return
        self.changed.emit({action:cid})

    def change_role(self):
        item=self.members.currentItem()
        if item:self.member_action('demote' if item.data(Qt.ItemDataRole.UserRole) in self.room.get('admins',()) else 'promote')

    def choose_photo(self):
        from .avatar_picker import AvatarPicker
        picker=AvatarPicker(self.room['name'],self.room.get('avatar',-1),self,image_data=self.room.get('photo',''))
        picker.setWindowTitle('Photo du groupe · FortyTchat')
        picker.selected.connect(lambda value:self.changed.emit({'avatar':value}))
        picker.photo_selected.connect(lambda value:self.changed.emit({'photo':value}))
        picker.exec();picker.deleteLater()
