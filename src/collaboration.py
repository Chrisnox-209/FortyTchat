"""Qt actions for groups and ephemeral chat interactions."""
import time
from collections import deque
from pathlib import Path
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import QDialog, QVBoxLayout, QListWidget, QListWidgetItem, QMessageBox, QInputDialog
from .features.versions import room_capable,group_capable,version_tuple
from . import __version__
from .features.collaboration import CAPABILITY, group_id, group_key
from .identity import valid_contact_id
from .memory import Message
from .ui.groups import GroupEditor, GroupWindow, GroupManager
from .ui.polls import PollEditor
from .ui.images import PreviewLoader, ImageSendDialog, ImageViewer
from .ui.widgets import button, label


class CollaborationController:
    def init_collaboration_ui(self):
        self.groups, self.group_windows, self.group_names = {}, {}, {}
        self.collaboration_dialogs, self.preview_loaders = set(), set()
        self.file_queue, self.file_queue_busy, self.file_queue_next = deque(), False, 0.0
        self.file_queue_timer = QTimer(self)
        self.file_queue_timer.setInterval(200)
        self.file_queue_timer.timeout.connect(self.drain_file_queue)
        self.file_queue_timer.start()
        self.window.chat.session_id = self.worker.client.sid
        self.window.sidebar.group_requested.connect(self.show_group_editor)
        self.window.sidebar.block_requested.connect(self.block_profile)

    def refresh_mentions(self):
        views = [self.window, *self.private_windows.values(), *self.group_windows.values()]
        for view in views:
            target = view.current
            members = self.groups.get(target, {}).get('members', ())
            names = [p['nick'] for p in self.latest_peers if p.get('encrypted') and p['sid'] not in self.window.sidebar.blocked
                     and (p['contact_id'] in members if group_id(target) else target == 'general' or target == p['sid'])]
            view.input.set_mentions(names)

    def keep_dialog(self, dialog):
        self.collaboration_dialogs.add(dialog)
        def done(*args):
            self.collaboration_dialogs.discard(dialog)
            dialog.deleteLater()
        dialog.finished.connect(done)
        dialog.show()
        return dialog

    def show_group_editor(self):
        peers = [p for p in self.latest_peers if p.get('encrypted') and group_capable(p.get('caps', ()))
                 and p['contact_id'] not in self.preferences.blocked_contacts and p['sid'] not in self.window.sidebar.blocked]
        if not peers:
            self.window.show_notice('Une personne connectée qui prend en charge les groupes est nécessaire.')
            return None
        editor = GroupEditor(peers, self.window)
        editor.submitted.connect(lambda name, contacts:self.worker.submit(self.worker.client.create_group(name, contacts)))
        return self.keep_dialog(editor)

    def open_group(self, reference):
        key = group_key(reference)
        room = self.groups.get(key)
        if not room:
            return None
        if room['state'] == 'pending':
            answer = QMessageBox.question(self.window, 'Invitation à un groupe',
                'Rejoindre ce groupe privé ? Les nouveaux membres ne reçoivent aucun ancien message.',
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No | QMessageBox.StandardButton.Cancel)
            if answer != QMessageBox.StandardButton.Cancel:
                self.worker.submit(self.worker.client.answer_group(reference, answer == QMessageBox.StandardButton.Yes))
                if answer == QMessageBox.StandardButton.Yes:
                    self.wanted_group = reference
            return None
        if key in self.group_windows:
            self.group_windows[key].open_window()
            return self.group_windows[key]
        view = GroupWindow(room, self.history.get(key), self.icon_path)
        self.group_windows[key] = view
        view.set_group(room, self.group_names)
        self.bind_interactions(view)
        for poll in self.history.get(key):
            if poll.poll:
                view.chat.update_poll(poll.poll)
        view.send_requested.connect(self.send_message)
        view.file_requested.connect(self.choose_file)
        view.chat.file_action.connect(self.file_action)
        view.viewed.connect(self.mark_viewed)
        view.closed.connect(self.close_group_window)
        view.manage_requested.connect(self.manage_group)
        view.leave_requested.connect(lambda gid:self.worker.submit(self.worker.client.answer_group(gid, False)))
        view.open_window()
        return view

    def manage_group(self, reference):
        room = self.groups.get(group_key(reference))
        if not room: return None
        peers = [p for p in self.latest_peers if p.get('encrypted') and group_capable(p.get('caps', ())) and p['sid'] not in self.window.sidebar.blocked]
        dialog = GroupManager(room, self.preferences.contact_id, self.group_names, peers, self.view_for(group_key(reference)) or self.window)
        dialog.changed.connect(lambda changes:self.update_group_from_ui(reference, changes))
        return self.keep_dialog(dialog)

    def update_group_from_ui(self, reference, changes):
        future = self.worker.submit(self.worker.client.update_group(reference, changes))
        def finished(result):
            ok = not result.cancelled() and result.exception() is None and bool(result.result())
            self.worker.received.emit('group_update_result', {'reference':reference,'changes':changes,'ok':ok})
        future.add_done_callback(finished)

    def close_group_window(self, key):
        view = self.group_windows.pop(key, None)
        if view:
            view.deleteLater()

    def show_poll_editor(self, target):
        view = self.view_for(target) or self.window
        if target != 'general' and not group_id(target):
            return None
        editor = PollEditor(view)
        editor.submitted.connect(lambda question, options:self.worker.send('poll_create', target, question=question, options=options))
        return self.keep_dialog(editor)

    def vote_poll(self, target, reference, option):
        future = self.worker.send('poll_vote', target, poll=reference, option=option)
        def finished(result):
            ok = not result.cancelled() and result.exception() is None and bool(result.result())
            self.worker.received.emit('poll_vote_result', {'target':target, 'poll':reference, 'ok':ok})
        future.add_done_callback(finished)

    def edit_message(self, target, reference):
        message = self.history.find(target, reference)
        if not message or not message.outgoing or message.deleted or message.poll or message.attachment:
            return
        view = self.view_for(target) or self.window
        text, ok = QInputDialog.getMultiLineText(view, 'Modifier le message', 'Ton message', message.text)
        if ok and text.strip():
            self.worker.send('message_edit', target, message=reference, text=text.strip())

    def delete_message(self, target, reference):
        message = self.history.find(target, reference)
        if not message or not message.outgoing or message.deleted:
            return
        view = self.view_for(target) or self.window
        text = 'Supprimer ce message pour les participants connectés et compatibles ?'
        if message.attachment:
            text += '\nLes fichiers déjà enregistrés chez eux resteront sur leur disque.'
        if QMessageBox.question(view, 'Supprimer le message', text) == QMessageBox.StandardButton.Yes:
            self.worker.send('message_delete', target, message=reference)

    def queue_files(self, target, paths):
        view = self.view_for(target) or self.window
        if not view.composer.available or not isinstance(paths, (list, tuple)): return
        if not paths or len(paths) + len(self.file_queue) + int(self.file_queue_busy) > 64:
            view.show_notice('La file peut contenir 64 fichiers au maximum. Attends avant d’en ajouter.'); return
        regular = [str(path) for path in paths if Path(path).is_file()]
        if not regular:
            view.show_notice('Glisse des fichiers ; les dossiers ne sont pas envoyés.'); return
        self.file_queue.extend((target, path) for path in regular)
        view.show_notice(f'{len(regular)} fichier(s) en file d’envoi · 20 Mio chacun · validation par les destinataires.')
        self.drain_file_queue()

    def drain_file_queue(self):
        if self.stopping or self.file_queue_busy or not self.file_queue or time.monotonic() < self.file_queue_next: return
        target, path = self.file_queue.popleft()
        self.file_queue_busy = True
        loader = PreviewLoader(self)
        self.preview_loaders.add(loader)
        def ready(source, preview):
            self.preview_loaders.discard(loader)
            if not self.stopping:
                future = self.worker.submit(self.worker.client.offer_queued_file(target, source, preview=preview))
                future.add_done_callback(lambda done:self.worker.received.emit('file_queue_ready', {}))
            loader.deleteLater()
        loader.ready.connect(ready)
        loader.start(path)

    def offer_image_file(self, target, path):
        self.queue_files(target, [path])

    def paste_image(self, target, image):
        view = self.view_for(target) or self.window
        if not view.composer.available:
            return None
        try:
            editor = ImageSendDialog(image.toImage() if isinstance(image, QPixmap) else QImage(image), view)
        except (TypeError, ValueError, MemoryError):
            view.show_notice('Capture trop grande ou invalide (16 mégapixels maximum).')
            return None
        editor.submitted.connect(lambda payload:self.worker.offer_file(target, **payload))
        return self.keep_dialog(editor)

    def refresh_blocked_state(self):
        sidebar = self.window.sidebar
        sidebar.blocked = self.session_blocks | {p['sid'] for p in self.latest_peers
                                                if p['contact_id'] in self.preferences.blocked_contacts}
        sidebar.refresh()
        for sid, view in self.private_windows.items():
            view.set_peer(self.window.peers.get(sid), sid in sidebar.blocked or view.contact_id in self.preferences.blocked_contacts)

    def block_profile(self, contact_id, blocked=True):
        if not valid_contact_id(contact_id) or contact_id == self.preferences.contact_id:
            return
        if blocked and contact_id not in self.preferences.blocked_contacts and len(self.preferences.blocked_contacts) >= 256:
            self.window.show_notice('256 contacts bloqués maximum.')
            return
        peer = next((p for p in self.latest_peers if p['contact_id'] == contact_id), None)
        if blocked:
            self.preferences.blocked_contacts[contact_id] = peer['nick'] if peer else self.preferences.friends.get(contact_id, 'Contact')
        else:
            self.preferences.blocked_contacts.pop(contact_id, None)
        saved = self.save_preferences()
        self.worker.call('block_contact', contact_id, blocked)
        self.refresh_blocked_state()
        if blocked:
            targets = {p['sid'] for p in self.latest_peers if p['contact_id'] == contact_id}
            for sender, popup in list(self.popups.values()):
                if sender in targets:
                    popup.expire()
            for target in targets:
                self.typing_state.remove(target)
                self.close_card_editors(target=target)
            self.refresh_typing()
        self.refresh_friend_surfaces(notify=False)
        if self.settings:
            self.settings.blocked_button.setText(f'Contacts bloqués ({len(self.preferences.blocked_contacts)})')
        self.window.show_notice(('Contact bloqué : ce choix reste actif après reconnexion.' if blocked else 'Contact débloqué.')
                               if saved else 'Choix appliqué pour cette session : sauvegarde du blocage impossible.')

    def show_blocked_contacts(self):
        dialog = QDialog(self.settings or self.window)
        dialog.setWindowTitle('Contacts bloqués · FortyTchat')
        dialog.resize(420, 440)
        box = QVBoxLayout(dialog)
        box.addWidget(label('Les contacts bloqués restent bloqués après un changement de pseudo ou une reconnexion.', 'muted', True))
        contacts = QListWidget()
        for cid, name in self.preferences.blocked_contacts.items():
            item = QListWidgetItem(name)
            item.setData(Qt.ItemDataRole.UserRole, cid)
            contacts.addItem(item)
        box.addWidget(contacts, 1)
        unblock = button('Débloquer la sélection', 'primary')
        def selected():
            item = contacts.currentItem()
            if item:
                self.block_profile(item.data(Qt.ItemDataRole.UserRole), False)
                contacts.takeItem(contacts.row(item))
        unblock.clicked.connect(selected)
        box.addWidget(unblock)
        close = button('Terminé')
        close.clicked.connect(dialog.accept)
        box.addWidget(close)
        return self.keep_dialog(dialog)

    def collaboration_event(self, kind, data):
        if kind == 'file_queue_ready':
            self.file_queue_busy = False
            self.file_queue_next = time.monotonic() + 1.5
        elif kind=='peer_version':
            version=version_tuple(data['version'])
            current=version_tuple(__version__)
            if version and version>current and version>version_tuple(getattr(self,'newer_version',__version__)):
                self.newer_version=data['version'];self.window.show_update_banner(data['version'])
        elif kind == 'group_update_result':
            for dialog in self.collaboration_dialogs:
                if isinstance(dialog, GroupManager) and dialog.room['id'] == data['reference']:
                    dialog.change_result(data['changes'], data['ok'])
        elif kind == 'groups':
            self.groups = {group_key(room['id']):room for room in data['groups']}
            self.window.sidebar.groups = self.groups
            self.group_names.update({self.preferences.contact_id:self.preferences.nickname,
                                     **{p['contact_id']:p['nick'] for p in self.latest_peers}})
            for key, view in self.group_windows.items():
                if key in self.groups:
                    view.set_group(self.groups[key], self.group_names)
            for dialog in self.collaboration_dialogs:
                if isinstance(dialog, GroupManager) and group_key(dialog.room['id']) in self.groups:
                    dialog.set_group(self.groups[group_key(dialog.room['id'])])
            self.refresh_mentions()
            self.window.sidebar.refresh()
            wanted = getattr(self, 'wanted_group', '')
            if wanted and self.groups.get(group_key(wanted), {}).get('state') == 'joined':
                self.wanted_group = ''
                self.open_group(wanted)
        elif kind == 'group_popup_sent':
            self.system_message(data['target'], f'✦ Popup de groupe : {data["count"]}/{data["total"]} remises. Délai partagé : {data["interval"] // 60} minutes.')
        elif kind == 'group_created':
            self.open_group(data['group']['id'])
        elif kind == 'group_invitation':
            if not self.preferences.dnd:
                self.notifications.show(data['nickname'] + ' t’invite dans un groupe',
                    'Clique sur le groupe dans FortyTchat pour accepter ou refuser.', urgency='normal', kind='group')
        elif kind == 'message_changed':
            frame, target = data['frame'], data['target']
            message = self.history.change(target, frame['message'], frame.get('text'), frame['type'] == 'message_delete')
            if message:
                message.mentions = frame.get('mentions', []) if not message.deleted else []
                if frame['type'] == 'message_delete':
                    self.file_transfers.pop(frame['message'], None)
                view = self.view_for(target)
                if view:
                    view.chat.refresh_messages(self.history.get(target), target)
                last = self.history.get(target)
                if last:
                    self.window.sidebar.previews[target] = last[-1].text[:80]
                self.window.sidebar.refresh()
        elif kind == 'poll_created':
            poll, target = data['poll'], data['target']
            self.add_message(target, Message(poll['author'], data['nickname'], poll['question'], id=poll['id'],
                outgoing=data['outgoing'], poll=poll, avatar=data.get('avatar', -1), avatar_image=data.get('avatar_image', '')))
            if not data['outgoing']:
                self.notify_chat(target, data['nickname'], poll=True)
        elif kind == 'poll_vote_result':
            view = self.view_for(data['target'])
            if view and data['poll'] in view.chat.poll_cards:
                view.chat.poll_cards[data['poll']].finish_submit(data['ok'])
        elif kind == 'poll_updated':
            message = self.history.find(data['target'], data['poll']['id'])
            if message and not message.deleted:
                message.poll = data['poll']
                view = self.view_for(data['target'])
                if view:
                    view.chat.update_poll(data['poll'])
        else:
            return False
        return True
