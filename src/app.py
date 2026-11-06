"""Application controller: preferences may reach disk, conversations never do."""
import time
import ssl
from pathlib import Path

from PySide6.QtCore import QObject, Signal, QTimer, QSignalBlocker, QStandardPaths
from PySide6.QtWidgets import QApplication, QFileDialog

from .autostart import Autostart
from .config import ConfigStore, config_home
from .identity import valid_nickname, valid_contact_id
from .features.friends import MAX_FRIENDS, contact_from_key, friend_key, online_contact
from .features.social import TypingState, PendingReactions, preview
from .features.cards import CardTemplates, answer_card
from .avatar_data import avatar_hash, AvatarError
from .instance import SingleInstance
from .memory import Conversations, Message
from .networking.settings import NetworkConfig
from .networking.worker import NetworkWorker
from .security import CryptoIdentity
from .ui.security import SecurityDialog
from .ui.break_popup import BreakPopup, BreakReplyPopup, CardPopup
from .ui.card_editor import CardEditor
from .ui.avatar_picker import AvatarPicker
from .ui.main_window import MainWindow
from .ui.private_window import PrivateWindow
from .ui.notifications import Notifications
from .ui.settings import SettingsDialog
from .ui.theme import apply_theme
from .ui.tray import Tray


class AlreadyRunning(Exception):
    pass


class Application(QObject):
    activation_requested = Signal()

    def __init__(self, project_root, args):
        super().__init__()
        self.root, self.args = Path(project_root), args
        self.instance = SingleInstance(self.activation_requested.emit, args.dev or '')
        if not self.instance.acquire(activate=not args.background):
            self.instance.close()
            raise AlreadyRunning()
        self.activation_requested.connect(self.activate)
        self.history, self.popups = Conversations(), {}
        self.reply_popups = {}
        self.file_transfers = {}
        self.card_editors, self.card_deliveries = {}, set()
        self.typing_state, self.pending_reactions = TypingState(), PendingReactions()
        self.private_windows = {}
        self.security_dialogs = {}
        self.peer_avatars = {}
        self.contact_sessions = {}
        self.settings, self.stopping, self.status = None, False, 'online'
        self.network, self.latest_peers = {}, []
        root = config_home() / 'fortytchat-dev' / args.dev if args.dev else None
        self.store = ConfigStore(root)
        self.preferences = self.store.preferences
        if not any((self.store.root / name).exists() for name in ('identity.json', 'trusted_contacts.json')):
            # Persist the UUID before creating a key bound to that UUID.
            self.store.save()
        self.crypto_identity = CryptoIdentity(self.preferences.contact_id, self.store.root)
        self.card_templates = CardTemplates(self.store.root)
        remaining = min(2520, max(0, 2520 - (time.time() - self.preferences.last_wizz_at))) if self.preferences.last_wizz_at else 0
        self.wizz_deadline = time.monotonic() + remaining
        self.autostart = Autostart(self.root)
        startup_errors = [self.store.error] if self.store.error else []
        try:
            if not args.dev:
                self.autostart.initialize(self.store)
            self.store.save()
        except (OSError, ValueError):
            startup_errors.append('Préférences ou démarrage XDG non accessibles. Vérifiez votre dossier de configuration.')
        try:
            network = NetworkConfig.load(self.root / 'network.json')
        except (OSError, ValueError, TypeError):
            network = NetworkConfig()
            startup_errors.append('network.json invalide : utilisation des paramètres LAN par défaut.')
        icon = str(self.root / 'assets' / 'icons' / 'fortytchat.svg')
        self.icon_path = icon
        self.qt = QApplication.instance()
        apply_theme(self.qt, self.preferences.theme)
        self.window = MainWindow(self.preferences, self.history, icon)
        self.window.chat_selected_callback = self.update_badge
        self.window.sidebar.list.setProperty('light', self.preferences.theme == 'light')
        self.window.private_requested.connect(self.open_private)
        self.window.avatar_requested.connect(self.show_avatar_picker)
        self.window.friend_requested.connect(self.change_friend)
        self.tray = Tray(icon, self)
        self.notifications = Notifications(icon, self)
        self.tray.open_requested.connect(self.activate)
        self.tray.settings_requested.connect(lambda: self.show_settings(0))
        self.tray.quit_requested.connect(self.quit)
        self.tray.status_requested.connect(self.set_status)
        self.tray.timer.timeout.connect(self.refresh_integration)
        self.window.send_requested.connect(self.send_message)
        self.bind_interactions(self.window)
        self.window.file_requested.connect(self.choose_file)
        self.window.chat.file_action.connect(self.file_action)
        self.window.wizz_requested.connect(lambda target: self.worker.send('wizz', target))
        self.window.break_requested.connect(lambda target: self.worker.send('break_invite', target))
        self.window.settings_requested.connect(self.show_settings)
        self.window.block_requested.connect(self.block)
        self.window.status_requested.connect(self.set_status)
        self.window.quitting.connect(self.quit)
        try:
            context = ssl.create_default_context(cafile=network.relay_ca or None)
        except (OSError, ssl.SSLError):
            context = ssl.create_default_context()
            network = NetworkConfig(mode='lan', udp_port=network.udp_port, tcp_port=network.tcp_port)
            startup_errors.append('Certificat de relais illisible : relais désactivé, découverte LAN disponible.')
        options = dict(discovery=network.mode != 'relay', discovery_port=network.udp_port,
                       tcp_port=0 if args.dev else network.tcp_port,
                       heartbeat=network.heartbeat, peer_timeout=network.peer_timeout,
                       multicast_group=network.multicast_group,
                       relay_host=network.relay_host if network.mode != 'lan' else '',
                       relay_port=network.relay_port, tls=context,
                       last_wizz_at=self.preferences.last_wizz_at, persist_wizz=self.store.reserve_wizz,
                       avatar=self.preferences.avatar, avatar_image=self.preferences.avatar_image,
                       contact_id=self.preferences.contact_id, crypto_identity=self.crypto_identity)
        if args.insecure_local_relay:
            options.update(relay_host='127.0.0.1', relay_port=args.insecure_local_relay, tls=False)
        self.worker = NetworkWorker(self.preferences.nickname, **options)
        self.worker.received.connect(self.on_network)
        self.peer_timer = QTimer(self)
        self.peer_timer.setSingleShot(True)
        self.peer_timer.timeout.connect(self.flush_peers)
        self.typing_timer = QTimer(self)
        self.typing_timer.setInterval(350)
        self.typing_timer.timeout.connect(self.refresh_typing)
        self.typing_timer.start()
        self.refresh_integration()
        self.worker.start()
        self.apply_network_preferences()
        self.qt.aboutToQuit.connect(self.cleanup)
        if self.preferences.dnd:
            self.window.sidebar.status_button.setText('●  Ne pas déranger')
        background = args.background or (self.store.first_run and not args.dev)
        if not background:
            self.window.show()
        elif not self.tray.available:
            self.window.showMinimized()
        if not self.tray.available:
            startup_errors.append('Icône de barre indisponible : la fenêtre reste accessible dans le dock. Fermer réduit l’application.')
        if args.dev:
            startup_errors.append(f'Profil de développement « {args.dev} » : port TCP dynamique, démarrage automatique non modifié.')
        if startup_errors:
            QTimer.singleShot(150, lambda: self.window.show_notice('\n'.join(startup_errors)))

    def activate(self):
        if hasattr(self, 'window'):
            self.window.open_window()

    def refresh_integration(self):
        self.window.tray_available = self.tray.available
        # Do not leave a previously hidden app unreachable if the tray host disappears.
        if not self.tray.available and not self.window.isVisible():
            self.window.showMinimized()
        if self.settings and not self.args.dev:
            blocker = QSignalBlocker(self.settings.autostart)
            self.settings.autostart.setChecked(self.autostart.enabled())
            del blocker

    def view_for(self, target):
        return self.window if target == 'general' else self.private_windows.get(target)

    def is_viewed(self, target):
        view = self.view_for(target)
        return bool(view and view.isVisible() and view.isActiveWindow())

    def open_private(self, sid):
        contact_id = contact_from_key(sid)
        if contact_id:
            live = online_contact(self.latest_peers, contact_id, self.contact_sessions.get(contact_id, ''))
            changed = next((peer for peer in self.latest_peers
                            if peer.get('contact_id') == contact_id and peer.get('security') == 'changed'), None)
            if not live and changed:
                self.show_security(changed['sid'])
                return None
            for existing in self.private_windows.values():
                if existing.contact_id == contact_id:
                    existing.open_window()
                    return existing
            sid = live['sid'] if live else self.contact_sessions.get(contact_id, sid)
        if sid in self.private_windows:
            self.private_windows[sid].open_window()
            return self.private_windows[sid]
        peer = self.window.peers.get(sid) or next((p for p in self.latest_peers if p['sid'] == sid), None)
        contact_id = peer.get('contact_id', '') if peer else contact_id
        nickname = peer['nick'] if peer else self.preferences.friends.get(contact_id, self.window.known_names.get(sid, 'Étudiant'))
        self.window.known_names[sid] = nickname
        self.window.sidebar.conversations[sid] = nickname
        view = PrivateWindow(sid, nickname, self.history.get(sid), self.icon_path, self.wizz_deadline)
        view.contact_id = contact_id
        self.private_windows[sid] = view
        view.set_peer(peer, sid in self.window.sidebar.blocked)
        view.set_friend(contact_id in self.preferences.friends)
        view.send_requested.connect(self.send_message)
        self.bind_interactions(view)
        view.file_requested.connect(self.choose_file)
        view.chat.file_action.connect(self.file_action)
        view.wizz_requested.connect(lambda target: self.worker.send('wizz', target))
        view.break_requested.connect(lambda target: self.worker.send('break_invite', target))
        view.block_requested.connect(self.block)
        view.friend_requested.connect(self.change_friend)
        view.security_requested.connect(self.show_security)
        view.viewed.connect(self.mark_viewed)
        view.closed.connect(self.close_private)
        self.window.sidebar.refresh()
        view.open_window()
        self.refresh_typing()
        return view

    def show_security(self, sid=''):
        if sid in self.security_dialogs:
            self.security_dialogs[sid].raise_()
            self.security_dialogs[sid].activateWindow()
            return
        peer = next((item for item in self.latest_peers if item['sid'] == sid), None)
        if sid and not peer:
            self.window.show_notice('Reconnecte ce contact pour comparer les empreintes.')
            return
        dialog = SecurityDialog(self.crypto_identity.public, peer, self.view_for(sid) or self.window)
        self.security_dialogs[sid] = dialog
        dialog.verify_requested.connect(lambda target, expected: self.worker.call('verify_contact', target, expected))
        dialog.finished.connect(lambda *_: self.security_dialogs.pop(sid, None))
        dialog.show()

    def bind_interactions(self, view):
        view.chat.actor_id = self.preferences.contact_id
        for _, _, message in view.chat.message_rows.values():
            view.chat.update_reactions(message)
        view.chat.reply_requested.connect(lambda reference: self.begin_reply(view.current, reference))
        view.chat.reaction_requested.connect(lambda reference, emoji: self.react(view.current, reference, emoji))
        view.chat.quote_missing.connect(lambda: view.show_notice('Le message cité n’est plus disponible dans cette session.'))
        view.composer.typing_changed.connect(lambda active: self.send_typing(view.current, active))
        view.composer.card_requested.connect(lambda: self.show_card_editor(view.current))

    def show_card_editor(self, target, request='', popup=None):
        view = self.view_for(target) or self.window
        peer = self.window.peers.get(target) or next((item for item in self.latest_peers if item['sid'] == target), None)
        if not self.cards_available(peer):
            view.show_notice('Les notifications animées nécessitent des clients et un relais compatibles 1.5.0, ou une connexion directe.')
            return None
        key = (target, request)
        if key in self.card_editors:
            self.card_editors[key].raise_()
            self.card_editors[key].activateWindow()
            return self.card_editors[key]
        editor = CardEditor(peer['nick'], self.card_templates, reply=bool(request), parent=self.window)
        self.card_editors[key] = editor
        if popup:
            editor.submitted.connect(lambda payload: popup.answer(payload) if not popup.answered else None)
            popup.finished.connect(editor.close)
        else:
            editor.submitted.connect(lambda payload: self.worker.send('card_invite', target, **payload))
        def done(result):
            self.card_editors.pop(key, None)
            editor.deleteLater()
        editor.finished.connect(done)
        editor.show()
        return editor

    def cards_available(self, peer):
        return bool(peer and 'cards' in peer.get('caps', ())
                    and (peer.get('direct') or (peer.get('relay') and self.worker.client.relay_cards)))

    def send_card_answer(self, target, request, original_kind, value):
        if isinstance(value, dict):
            return self.worker.send('card_reply', target, request=request, answer='custom', **value)
        if original_kind == 'card_invite':
            return self.worker.send('card_reply', target, request=request, answer=value, **answer_card(value))
        return self.worker.send('break_reply', target, request=request, answer=value)

    def show_card_templates(self):
        key = ('templates', '')
        if key in self.card_editors:
            self.card_editors[key].raise_()
            self.card_editors[key].activateWindow()
            return self.card_editors[key]
        editor = CardEditor('', self.card_templates, parent=self.window, manage=True)
        self.card_editors[key] = editor
        def done(result):
            self.card_editors.pop(key, None)
            editor.deleteLater()
        editor.finished.connect(done)
        editor.show()
        return editor

    def close_card_editors(self, target=None, request=None):
        for (peer, reference), editor in list(self.card_editors.items()):
            if (target is None or peer == target) and (request is None or reference == request):
                editor.close()

    def send_message(self, target, text):
        view = self.view_for(target)
        reply_to = view.composer.reply_to if view else ''
        self.worker.send('message', target, text=text, **({'reply_to': reply_to} if reply_to else {}))

    def begin_reply(self, target, reference):
        message = self.history.find(target, reference)
        view = self.view_for(target)
        if view and message:
            view.composer.set_reply(reference, 'Vous' if message.outgoing else message.nickname, message.text)

    def react(self, target, reference, emoji):
        message = self.history.find(target, reference)
        if not message:
            return
        current = message.reactions.get(self.preferences.contact_id, {}).get('emoji', '')
        self.worker.send('reaction', target, message=reference, emoji='' if current == emoji else emoji)

    def apply_reaction(self, target, reference, actor, emoji, nickname):
        message = self.history.react(target, reference, actor, emoji, nickname)
        if message is None:
            self.pending_reactions.add(target, reference, actor, emoji, nickname)
        else:
            view = self.view_for(target)
            if view:
                view.chat.update_reactions(message)

    def send_typing(self, target, active):
        if not self.stopping:
            self.worker.send('typing', target, active=active)

    def refresh_typing(self):
        self.pending_reactions.prune()
        self.window.chat.typing.set_typers(self.typing_state.names('general'))
        for sid, view in self.private_windows.items():
            view.chat.typing.set_typers(self.typing_state.names(sid))

    def close_private(self, sid):
        dialog = self.security_dialogs.get(sid)
        if dialog:
            dialog.close()
        self.close_card_editors(target=sid)
        view = self.private_windows.pop(sid, None)
        if view:
            view.deleteLater()

    def mark_viewed(self, sid):
        self.window.sidebar.unread.pop(sid, None)
        self.window.sidebar.refresh()
        self.update_badge()

    def flush_peers(self):
        changed_names = False
        for contact_id, saved_name in list(self.preferences.friends.items()):
            peer = online_contact(self.latest_peers, contact_id, self.contact_sessions.get(contact_id, ''))
            if peer:
                old_sid = self.contact_sessions.get(contact_id, friend_key(contact_id))
                if old_sid != peer['sid']:
                    self.move_private(old_sid, peer['sid'])
                self.contact_sessions[contact_id] = peer['sid']
                if saved_name != peer['nick']:
                    self.preferences.friends[contact_id] = peer['nick']
                    changed_names = True
        if changed_names:
            self.save_preferences()
        changed = {p['sid']: p for p in self.latest_peers
                   if self.peer_avatars.get(p['sid']) != (p.get('avatar', -1), p.get('avatar_image', ''))}
        self.peer_avatars = {p['sid']: (p.get('avatar', -1), p.get('avatar_image', '')) for p in self.latest_peers}
        if changed:
            for messages in self.history.chats.values():
                for message in messages:
                    if message.sender in changed:
                        message.avatar = changed[message.sender].get('avatar', -1)
                        message.avatar_image = changed[message.sender].get('avatar_image', '')
            self.window.chat.reset(self.history.get('general'), 'general')
            for sid, view in self.private_windows.items():
                view.chat.reset(self.history.get(sid), sid)
        self.window.set_peers(self.latest_peers)
        for sid, view in self.private_windows.items():
            view.set_peer(self.window.peers.get(sid), sid in self.window.sidebar.blocked)
            view.set_friend(view.contact_id in self.preferences.friends)

    def move_private(self, old_sid, new_sid):
        dialog = self.security_dialogs.get(old_sid)
        if dialog:
            dialog.close()
        self.history.move(old_sid, new_sid)
        self.typing_state.remove(old_sid)
        for metadata in self.file_transfers.values():
            if metadata['target'] == old_sid:
                metadata['target'] = new_sid
        sidebar = self.window.sidebar
        count = sidebar.unread.pop(old_sid, 0)
        if count:
            sidebar.unread[new_sid] = min(999, sidebar.unread.get(new_sid, 0) + count)
        if old_sid in sidebar.conversations:
            sidebar.conversations[new_sid] = sidebar.conversations.pop(old_sid)
        if old_sid in sidebar.previews:
            sidebar.previews[new_sid] = sidebar.previews.pop(old_sid)
        if old_sid in self.window.known_names:
            self.window.known_names[new_sid] = self.window.known_names.pop(old_sid)
        view = self.private_windows.get(old_sid)
        if view:
            existing = self.private_windows.get(new_sid)
            if existing and existing is not view:
                view.close()
                view = existing
            else:
                self.private_windows.pop(old_sid, None)
                view.current = new_sid
                self.private_windows[new_sid] = view
            view.chat.reset(self.history.get(new_sid), new_sid)
            if view.isActiveWindow():
                sidebar.unread.pop(new_sid, None)
        self.update_badge()

    def change_friend(self, contact_id, added):
        if not valid_contact_id(contact_id) or contact_id == self.preferences.contact_id:
            return
        if added:
            peer = online_contact(self.latest_peers, contact_id)
            if not peer:
                return
            if contact_id not in self.preferences.friends and len(self.preferences.friends) >= MAX_FRIENDS:
                self.window.show_notice('La liste peut contenir 256 amis au maximum.')
                return
            self.preferences.friends[contact_id] = peer['nick']
            self.contact_sessions[contact_id] = peer['sid']
        else:
            self.preferences.friends.pop(contact_id, None)
            self.contact_sessions.pop(contact_id, None)
            self.window.sidebar.conversations.pop(friend_key(contact_id), None)
        self.save_preferences()
        self.window.sidebar.refresh()
        for view in self.private_windows.values():
            view.set_friend(view.contact_id in self.preferences.friends)
        self.window.show_notice('Ami ajouté : il restera reconnu même si son pseudo change.' if added else 'Contact retiré des amis.')

    def add_message(self, target, message):
        if message.reply_to:
            original = self.history.find(target, message.reply_to)
            message.reply_name = ('Vous' if original.outgoing else original.nickname) if original else ''
            message.reply_text = preview(original.text) if original else 'Message plus disponible'
        self.history.append(target, message)
        for actor, emoji, nickname in self.pending_reactions.take(target, message.id):
            self.history.react(target, message.id, actor, emoji, nickname)
        if not message.system:
            self.window.sidebar.previews[target] = message.text.replace('\n', ' ')[:80]
            while len(self.window.sidebar.previews) > 65:
                self.window.sidebar.previews.pop(next(iter(self.window.sidebar.previews)))
        view = self.view_for(target)
        if view:
            view.chat.append(message)
        if message.id:
            for existing in self.history.get(target):
                if existing.reply_to == message.id and not existing.reply_name:
                    existing.reply_name = 'Vous' if message.outgoing else message.nickname
                    existing.reply_text = preview(message.text)
                    if view:
                        view.chat.update_quote(existing)
        if target != 'general':
            nick = (message.nickname if not message.outgoing and message.nickname
                    else self.window.known_names.get(target, 'Étudiant'))
            self.window.sidebar.conversations[target] = nick
            while len(self.window.sidebar.conversations) > 64:
                self.window.sidebar.conversations.pop(next(iter(self.window.sidebar.conversations)))
        if not message.outgoing and not message.system and not self.is_viewed(target):
            self.window.sidebar.unread[target] = min(999, self.window.sidebar.unread.get(target, 0) + 1)
        self.window.sidebar.refresh()
        self.update_badge()

    def system_message(self, target, text):
        self.add_message(target, Message('', '', text, system=True))

    def on_network(self, kind, data):
        if self.stopping:
            return
        if kind.startswith('file_'):
            self.file_event(kind, data)
            return
        if kind == 'peers':
            self.latest_peers = data['peers']
            self.network['peers'] = len(data['peers'])
            self.update_network_label()
            if not self.peer_timer.isActive():
                self.peer_timer.start(90)
        elif kind in ('joined', 'left'):
            nick = data['peer']['nick']
            self.system_message('general', f'{nick} a rejoint le cluster.' if kind == 'joined' else f'{nick} s’est déconnecté·e.')
            if kind == 'left':
                self.typing_state.remove(data['peer']['sid'])
                self.refresh_typing()
        elif kind == 'identity':
            self.preferences.nickname = data['nickname']
            self.window.sidebar.set_nickname(data['nickname'])
            self.save_preferences()
            if self.settings:
                self.settings.nickname.setText(data['nickname'])
                self.settings.avatar_preview.nickname = data['nickname']
                self.settings.avatar_preview.update()
                self.settings.feedback.setText('Pseudo mis à jour et annoncé aux autres utilisateurs.')
        elif kind == 'network':
            self.network = data
            self.update_network_label()
            if self.settings:
                self.settings.update_network(data)
        elif kind == 'notice':
            active = self.qt.activeWindow()
            (active if isinstance(active, PrivateWindow) else self.window).show_notice(data['text'])
        elif kind == 'security_verified':
            dialog = self.security_dialogs.get(data['sid'])
            if dialog:
                dialog.verified()
        elif kind == 'wizz_used':
            self.wizz_deadline = time.monotonic() + 42 * 60
            for view in self.private_windows.values():
                view.composer.deadline = self.wizz_deadline
                view.composer.update_actions()
        elif kind == 'diagnostic':
            if self.settings:
                self.settings.update_network(data)
                self.settings.diagnostic_result(data['rows'])
        elif kind == 'delivery':
            view = self.view_for(data['target']) or self.window
            if data['id'] in self.card_deliveries:
                self.card_deliveries.discard(data['id'])
                if not data['ok']:
                    view.show_notice('Notification non confirmée : le contact peut être en Ne pas déranger ou utiliser une ancienne version.')
            elif data.get('local_only'):
                self.window.show_notice('Aucun autre utilisateur détecté : ce message est visible uniquement ici.')
            elif not data['ok']:
                view.show_notice('Livraison non confirmée pour au moins un destinataire. Le message ne sera pas renvoyé à une reconnexion.')
        elif kind == 'sent':
            frame = data['frame']
            target = frame['to']
            view = self.view_for(target)
            if view and frame['type'] == 'message':
                view.send_button.setEnabled(True)
            if frame['type'] == 'message':
                if view and view.input.toPlainText().strip() == frame['text']:
                    view.input.clear()
                    if view.composer.reply_to == frame.get('reply_to', ''):
                        view.composer.clear_reply()
                self.add_message(target, Message(self.worker.client.sid, self.preferences.nickname, frame['text'], outgoing=True,
                                                 id=frame['id'], avatar=self.preferences.avatar,
                                                 avatar_image=self.preferences.avatar_image, reply_to=frame.get('reply_to', '')))
            elif frame['type'] == 'reaction':
                self.apply_reaction(target, frame['message'], self.preferences.contact_id, frame['emoji'], self.preferences.nickname)
            elif frame['type'] == 'wizz':
                self.system_message(target, 'ϟ Tu as envoyé un Wizz. Prochain Wizz dans 42 minutes.')
            elif frame['type'] == 'break_invite':
                self.system_message(target, '☕ Tu as proposé une pause. Invitation valable 2 minutes.')
            elif frame['type'] == 'break_reply':
                self.system_message(target, '☕ Tu as accepté la pause.' if frame['answer'] == 'accepted' else '☕ Tu as décliné la pause.')
            elif frame['type'] in ('card_invite', 'card_reply'):
                self.card_deliveries.add(frame['id'])
                prefix = '✦ Notification envoyée : ' if frame['type'] == 'card_invite' else '✦ Ta réponse : '
                self.system_message(target, prefix + frame['title'] + ('\n' + frame['text'] if frame['text'] else ''))
        elif kind == 'event':
            self.receive_event(data['frame'], data['nickname'], data.get('avatar', -1), data.get('avatar_image', ''), data.get('contact_id', ''))

    def receive_event(self, frame, nickname, avatar=-1, avatar_image='', contact_id=''):
        target = 'general' if frame['to'] == 'general' else frame['from']
        self.window.known_names[frame['from']] = nickname
        kind = frame['type']
        if kind in ('reaction', 'typing') and frame['from'] in self.window.sidebar.blocked:
            return
        if kind == 'message':
            self.add_message(target, Message(frame['from'], nickname, frame['text'], id=frame['id'],
                                             avatar=avatar, avatar_image=avatar_image, reply_to=frame.get('reply_to', '')))
            self.typing_state.update(target, frame['from'], nickname, False)
            self.refresh_typing()
            if not self.preferences.dnd and not self.is_viewed(target):
                self.notifications.show(f'{nickname} · nouveau message')
        elif kind == 'reaction':
            self.apply_reaction(target, frame['message'], contact_id or frame['from'], frame['emoji'], nickname)
        elif kind == 'typing':
            self.typing_state.update(target, frame['from'], nickname, frame['active'])
            self.refresh_typing()
        elif kind == 'wizz':
            self.system_message(target, f'ϟ {nickname} t’a envoyé un Wizz !')
            view = self.open_private(target)
            view.chat.wizz()
            view.show_notice(f'ϟ Wizz de {nickname} !')
            self.notifications.show(f'{nickname} t’a envoyé un Wizz !')
        elif kind in ('break_invite', 'card_invite'):
            if len(self.popups) >= 3:
                self.send_card_answer(frame['from'], frame['id'], kind, 'dismissed')
                return
            self.system_message(target, ('✦ ' + nickname + ' : ' + frame['title'] + ('\n' + frame['text'] if frame['text'] else ''))
                                if kind == 'card_invite' else f'☕ {nickname} te propose une pause.')
            def answer(value):
                self.send_card_answer(frame['from'], frame['id'], kind, value)
            peer = self.window.peers.get(target) or next((item for item in self.latest_peers if item['sid'] == target), {})
            popup = (CardPopup(nickname, frame, answer, self.window) if kind == 'card_invite'
                     else BreakPopup(nickname, answer, self.window, allow_custom=self.cards_available(peer)))
            popup.custom_requested.connect(lambda: self.show_card_editor(target, frame['id'], popup))
            self.popups[frame['id']] = (target, popup)
            popup.finished.connect(lambda result, key=frame['id']: self.remove_popup(key))
            popup.show()
            self.notifications.show(f'{nickname} · notification personnalisée' if kind == 'card_invite' else f'{nickname} : on fait une pause ?')
        elif kind in ('break_reply', 'card_reply'):
            text = (f'{nickname} : {frame["title"]}' + ('\n' + frame['text'] if frame['text'] else '')) if kind == 'card_reply' else (
                f'{nickname} a accepté ta proposition de pause.' if frame['answer'] == 'accepted' else f'{nickname} n’est pas disponible pour le moment.')
            self.system_message(target, '☕ ' + text)
            popup = BreakReplyPopup(nickname, frame['answer'], self.window, card=frame if kind == 'card_reply' else None)
            self.reply_popups[frame['id']] = popup
            popup.finished.connect(lambda result, key=frame['id']: self.remove_reply_popup(key))
            if len(self.reply_popups) > 8:
                self.reply_popups[next(iter(self.reply_popups))].close()
            popup.show()

    def remove_reply_popup(self, key):
        popup = self.reply_popups.pop(key, None)
        if popup:
            popup.deleteLater()

    def choose_file(self, target):
        view = self.view_for(target) or self.window
        path, _ = QFileDialog.getOpenFileName(view, 'Joindre un fichier · 20 Mio maximum')
        if path:
            self.worker.offer_file(target, path)

    def refresh_file_card(self, metadata):
        view = self.view_for(metadata['target'])
        if view:
            view.chat.update_transfer(metadata)

    def file_action(self, action, data):
        metadata = self.file_transfers.get(data['id'])
        if not metadata:
            return
        view = self.view_for(metadata['target']) or self.window
        sender = metadata['from']
        if action == 'accept' and metadata['status'] == 'offered' and not metadata['outgoing']:
            folder = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DownloadLocation) or str(Path.home()))
            suggestion = folder / metadata['name']
            index = 1
            while suggestion.exists() or suggestion.is_symlink():
                suggestion = folder / f'{Path(metadata["name"]).stem} ({index}){Path(metadata["name"]).suffix}'
                index += 1
            path, _ = QFileDialog.getSaveFileName(view, 'Enregistrer le fichier reçu', str(suggestion))
            if not path or metadata['status'] != 'offered':
                return
            if Path(path).exists() or Path(path).is_symlink():
                view.show_notice('Ce nom existe déjà. Choisis un autre nom pour enregistrer le fichier.')
                return
            metadata.update(status='transferring', detail='Préparation du téléchargement…')
            self.refresh_file_card(metadata)
            self.worker.accept_file(metadata['id'], sender, path)
        elif action == 'decline' and metadata['status'] == 'offered' and not metadata['outgoing']:
            metadata.update(status='declined', detail='Fichier refusé.')
            self.refresh_file_card(metadata)
            self.worker.decline_file(metadata['id'], sender)
        elif action == 'cancel' and metadata['status'] in ('offered', 'transferring'):
            peer = metadata['to'] if metadata['outgoing'] else sender
            self.worker.cancel_file(metadata['id'], peer)

    def file_event(self, kind, data):
        key = data['id']
        if kind == 'file_offer':
            if key in self.file_transfers:
                return
            while len(self.file_transfers) >= 128:
                victim = next((k for k, m in self.file_transfers.items() if m['status'] != 'transferring'), None)
                if victim is None:
                    return
                del self.file_transfers[victim]
            outgoing = data.get('outgoing', False)
            target = data['to'] if outgoing or data['to'] == 'general' else data['from']
            metadata = {**data, 'target': target, 'status': 'offered', 'received': 0}
            if outgoing and target == 'general':
                metadata['recipients_state'] = {sid: {'status': 'offered', 'received': 0} for sid in data.get('recipients', [])}
            self.file_transfers[key] = metadata
            self.add_message(target, Message(data['from'], data['nickname'], '↧ ' + data['name'],
                                             outgoing=outgoing, id=key, attachment=metadata))
            if not outgoing and not self.preferences.dnd and not self.is_viewed(target):
                self.notifications.show(f'{data["nickname"]} · fichier proposé', 'Ouvre la conversation pour choisir où l’enregistrer.')
            return
        metadata = self.file_transfers.get(key)
        if not metadata:
            return
        if kind == 'file_deferred':
            metadata.update(status='offered', detail=data.get('reason', 'Réessaie le téléchargement.'))
            self.refresh_file_card(metadata)
            (self.view_for(metadata['target']) or self.window).show_notice(metadata['detail'])
            return
        status = {'file_progress': 'transferring', 'file_complete': 'complete',
                  'file_declined': 'declined', 'file_failed': 'failed'}.get(kind)
        if not status:
            return
        if metadata['outgoing'] and metadata['target'] == 'general' and metadata.get('recipients_state'):
            recipients = metadata['recipients_state']
            peer = data.get('peer')
            if peer in recipients and recipients[peer]['status'] not in ('complete', 'declined', 'failed'):
                recipients[peer].update(status=status, received=data.get('received', metadata['size'] if status == 'complete' else 0))
            elif peer == 'general':
                for state in recipients.values():
                    if state['status'] not in ('complete', 'declined', 'failed'):
                        state.update(status=status)
            active = sum(p['status'] == 'transferring' for p in recipients.values())
            pending = sum(p['status'] == 'offered' for p in recipients.values())
            complete = sum(p['status'] == 'complete' for p in recipients.values())
            failed = len(recipients) - active - pending - complete
            metadata['status'] = 'transferring' if active else ('offered' if pending else ('complete' if complete else status))
            metadata['detail'] = f'{complete} reçu(s) · {pending} en attente · {active} en cours' + (f' · {failed} refusé(s) ou interrompu(s)' if failed else '')
            metadata['received'] = round(sum(p['received'] for p in recipients.values()) / len(recipients))
        else:
            if metadata['status'] in ('complete', 'declined', 'failed'):
                return
            metadata.update(status=status, detail=data.get('reason', ''),
                            received=data.get('received', metadata['size'] if status == 'complete' else metadata['received']))
        self.refresh_file_card(metadata)

    def remove_popup(self, key):
        self.close_card_editors(request=key)
        value = self.popups.pop(key, None)
        if value:
            value[1].deleteLater()

    def update_badge(self):
        count = sum(self.window.sidebar.unread.values())
        self.tray.update_unread(count)
        self.window.setWindowTitle(f'FortyTchat{f" ({count})" if count else ""} — le cluster, en direct')

    def update_network_label(self):
        data = self.network
        if data.get('relay'):
            text = f'●  Relais connecté · {data.get("peers", 0)} pair(s)'
        elif data.get('direct'):
            text = f'●  LAN · {data.get("peers", 0)} pair(s) détecté(s)' if data.get('peers') else '◌  Recherche LAN · aucun pair détecté'
        else:
            text = '○  Communication indisponible · diagnostic'
        self.window.network_label.setText(text)

    def show_settings(self, tab=0):
        if self.settings:
            self.settings.tabs.setCurrentIndex(tab)
            self.settings.raise_()
            self.settings.activateWindow()
            return
        dialog = SettingsDialog(self.preferences, False if self.args.dev else self.autostart.enabled(), self.network, bool(self.args.dev), self.window)
        self.settings = dialog
        dialog.tabs.setCurrentIndex(tab)
        dialog.nickname_requested.connect(self.rename)
        dialog.avatar_requested.connect(self.show_avatar_picker)
        dialog.cards_requested.connect(self.show_card_templates)
        dialog.security_requested.connect(lambda: self.show_security())
        dialog.preference_changed.connect(self.change_preference)
        dialog.finished.connect(self.close_settings)
        dialog.show()

    def show_avatar_picker(self):
        picker = AvatarPicker(self.preferences.nickname, self.preferences.avatar, self.settings or self.window,
                              image_data=self.preferences.avatar_image)
        picker.selected.connect(lambda avatar: self.change_preference('avatar', avatar))
        picker.photo_selected.connect(lambda data: self.change_preference('avatar_image', data))
        picker.exec()
        picker.deleteLater()

    def close_settings(self, result):
        old = self.settings
        self.settings = None
        if old:
            old.deleteLater()

    def rename(self, nickname):
        if not valid_nickname(nickname):
            self.settings.feedback.setText('Pseudo invalide : 3 à 28 lettres ASCII, chiffres, tirets ou underscores.')
            return
        self.worker.call('rename', nickname)

    def save_preferences(self):
        try:
            self.store.save()
            return True
        except (OSError, ValueError):
            self.window.show_notice('Impossible de sauvegarder les préférences dans votre espace utilisateur.')
            return False

    def change_preference(self, key, value):
        if key == 'avatar_image':
            try:
                avatar_hash(value)
            except AvatarError:
                self.window.show_notice('Impossible d’utiliser cette image comme avatar.')
                return
        if key == 'avatar':
            self.preferences.avatar_image = ''
        if key == 'autostart':
            if self.args.dev:
                return
            try:
                self.autostart.set_enabled(value)
                self.preferences.autostart = self.autostart.enabled()
            except (OSError, ValueError):
                self.window.show_notice('Impossible de modifier l’entrée XDG. Aucune configuration système n’a été changée.')
            if self.settings:
                blocker = QSignalBlocker(self.settings.autostart)
                self.settings.autostart.setChecked(self.autostart.enabled())
                del blocker
        else:
            setattr(self.preferences, key, value)
        self.window.sidebar.avatar.avatar = self.preferences.avatar
        self.window.sidebar.avatar.image_data = self.preferences.avatar_image
        self.window.sidebar.avatar.update()
        if self.settings:
            self.settings.avatar_preview.avatar = self.preferences.avatar
            self.settings.avatar_preview.image_data = self.preferences.avatar_image
            self.settings.avatar_preview.update()
        self.save_preferences()
        apply_theme(self.qt, self.preferences.theme)
        self.window.sidebar.list.setProperty('light', self.preferences.theme == 'light')
        self.window.sidebar.list.viewport().update()
        self.apply_network_preferences()
        if self.settings:
            self.settings.feedback.setText('Préférences appliquées immédiatement.' )

    def apply_network_preferences(self):
        self.worker.call('preferences', wizz_enabled=self.preferences.wizz_enabled, dnd=self.preferences.dnd,
                         status=self.status, avatar=self.preferences.avatar, avatar_image=self.preferences.avatar_image)
        shown = 'Ne pas déranger' if self.preferences.dnd else ('Absent·e' if self.status == 'away' else 'En ligne')
        self.window.sidebar.status_button.setText('●  ' + shown)

    def set_status(self, status):
        self.preferences.dnd = status == 'dnd'
        if status != 'dnd':
            self.status = status
        self.save_preferences()
        self.apply_network_preferences()
        if self.settings:
            blocker = QSignalBlocker(self.settings.dnd)
            self.settings.dnd.setChecked(self.preferences.dnd)
            del blocker

    def block(self, sid, value):
        self.worker.call('block', sid, value)
        if value:
            self.window.sidebar.blocked.add(sid)
            self.typing_state.remove(sid)
            self.refresh_typing()
            for peer, popup in list(self.popups.values()):
                if peer == sid:
                    popup.expire()
        else:
            self.window.sidebar.blocked.discard(sid)
        self.window.sidebar.refresh()
        self.window.update_actions()
        if sid in self.private_windows:
            self.private_windows[sid].set_peer(self.window.peers.get(sid), value)
        self.window.show_notice('Utilisateur masqué pour cette session.' if value else 'Utilisateur démasqué.')

    def quit(self):
        self.qt.quit()

    def cleanup(self):
        if self.stopping:
            return
        self.stopping = True
        self.typing_timer.stop()
        self.window.composer.stop_typing()
        self.window.chat.typing.set_typers([])
        self.window.quitting_now = True
        self.notifications.stop()
        self.tray.icon.hide()
        for _, popup in list(self.popups.values()):
            popup.expire()
        for popup in list(self.reply_popups.values()):
            popup.close()
        self.close_card_editors()
        for view in list(self.private_windows.values()):
            view.close()
        self.worker.stop()
        self.history.clear()
        self.file_transfers.clear()
        self.card_deliveries.clear()
        self.typing_state.peers.clear()
        self.pending_reactions.items.clear()
        self.instance.close()
