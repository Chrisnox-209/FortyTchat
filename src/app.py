"""Application controller: preferences may reach disk, conversations never do."""
import time
import ssl
from pathlib import Path

from PySide6.QtCore import QObject, Signal, QTimer, QSignalBlocker
from PySide6.QtWidgets import QApplication

from .autostart import Autostart
from .config import ConfigStore, config_home
from .identity import valid_nickname, valid_contact_id
from .features.friends import MAX_FRIENDS, contact_from_key, friend_key, online_contact
from .avatar_data import avatar_hash, AvatarError
from .instance import SingleInstance
from .memory import Conversations, Message
from .networking.settings import NetworkConfig
from .networking.worker import NetworkWorker
from .ui.break_popup import BreakPopup
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
        self.private_windows = {}
        self.peer_avatars = {}
        self.contact_sessions = {}
        self.settings, self.stopping, self.status = None, False, 'online'
        self.network, self.latest_peers = {}, []
        root = config_home() / 'fortytchat-dev' / args.dev if args.dev else None
        self.store = ConfigStore(root)
        self.preferences = self.store.preferences
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
        self.window.send_requested.connect(lambda target, text: self.worker.send('message', target, text=text))
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
                       contact_id=self.preferences.contact_id)
        if args.insecure_local_relay:
            options.update(relay_host='127.0.0.1', relay_port=args.insecure_local_relay, tls=False)
        self.worker = NetworkWorker(self.preferences.nickname, **options)
        self.worker.received.connect(self.on_network)
        self.peer_timer = QTimer(self)
        self.peer_timer.setSingleShot(True)
        self.peer_timer.timeout.connect(self.flush_peers)
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
        view.send_requested.connect(lambda target, text: self.worker.send('message', target, text=text))
        view.wizz_requested.connect(lambda target: self.worker.send('wizz', target))
        view.break_requested.connect(lambda target: self.worker.send('break_invite', target))
        view.block_requested.connect(self.block)
        view.friend_requested.connect(self.change_friend)
        view.viewed.connect(self.mark_viewed)
        view.closed.connect(self.close_private)
        self.window.sidebar.refresh()
        view.open_window()
        return view

    def close_private(self, sid):
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
        self.history.move(old_sid, new_sid)
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
        self.history.append(target, message)
        if not message.system:
            self.window.sidebar.previews[target] = message.text.replace('\n', ' ')[:80]
            while len(self.window.sidebar.previews) > 65:
                self.window.sidebar.previews.pop(next(iter(self.window.sidebar.previews)))
        view = self.view_for(target)
        if view:
            view.chat.append(message)
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
        if kind == 'peers':
            self.latest_peers = data['peers']
            self.network['peers'] = len(data['peers'])
            self.update_network_label()
            if not self.peer_timer.isActive():
                self.peer_timer.start(90)
        elif kind in ('joined', 'left'):
            nick = data['peer']['nick']
            self.system_message('general', f'{nick} a rejoint le cluster.' if kind == 'joined' else f'{nick} s’est déconnecté·e.')
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
            if data.get('local_only'):
                self.window.show_notice('Aucun autre utilisateur détecté : ce message est visible uniquement ici.')
            elif not data['ok']:
                view.show_notice('Livraison non confirmée pour au moins un destinataire. Le message ne sera pas renvoyé à une reconnexion.')
        elif kind == 'sent':
            frame = data['frame']
            target = frame['to']
            view = self.view_for(target)
            if view:
                view.send_button.setEnabled(True)
            if frame['type'] == 'message':
                if view and view.input.toPlainText().strip() == frame['text']:
                    view.input.clear()
                self.add_message(target, Message(self.worker.client.sid, self.preferences.nickname, frame['text'], outgoing=True,
                                                 id=frame['id'], avatar=self.preferences.avatar,
                                                 avatar_image=self.preferences.avatar_image))
            elif frame['type'] == 'wizz':
                self.system_message(target, 'ϟ Tu as envoyé un Wizz. Prochain Wizz dans 42 minutes.')
            elif frame['type'] == 'break_invite':
                self.system_message(target, '☕ Tu as proposé une pause. Invitation valable 2 minutes.')
            elif frame['type'] == 'break_reply':
                self.system_message(target, '☕ Tu as accepté la pause.' if frame['answer'] == 'accepted' else '☕ Tu as décliné la pause.')
        elif kind == 'event':
            self.receive_event(data['frame'], data['nickname'], data.get('avatar', -1), data.get('avatar_image', ''))

    def receive_event(self, frame, nickname, avatar=-1, avatar_image=''):
        target = 'general' if frame['to'] == 'general' else frame['from']
        self.window.known_names[frame['from']] = nickname
        kind = frame['type']
        if kind == 'message':
            self.add_message(target, Message(frame['from'], nickname, frame['text'], id=frame['id'],
                                             avatar=avatar, avatar_image=avatar_image))
            if not self.preferences.dnd and not self.is_viewed(target):
                self.notifications.show(f'{nickname} · nouveau message')
        elif kind == 'wizz':
            self.system_message(target, f'ϟ {nickname} t’a envoyé un Wizz !')
            view = self.open_private(target)
            view.chat.wizz()
            view.show_notice(f'ϟ Wizz de {nickname} !')
            self.notifications.show(f'{nickname} t’a envoyé un Wizz !')
        elif kind == 'break_invite':
            if len(self.popups) >= 3:
                self.worker.send('break_reply', frame['from'], request=frame['id'], answer='dismissed')
                return
            self.system_message(target, f'☕ {nickname} te propose une pause.')
            def answer(value):
                self.worker.send('break_reply', frame['from'], request=frame['id'], answer=value)
            popup = BreakPopup(nickname, answer, self.window)
            self.popups[frame['id']] = (target, popup)
            popup.finished.connect(lambda result, key=frame['id']: self.remove_popup(key))
            popup.show()
            self.notifications.show(f'{nickname} te propose une pause !')
        elif kind == 'break_reply':
            text = f'{nickname} a accepté ta proposition de pause.' if frame['answer'] == 'accepted' else f'{nickname} n’est pas disponible pour le moment.'
            self.system_message(target, '☕ ' + text)
            if not self.preferences.dnd:
                self.notifications.show(text)

    def remove_popup(self, key):
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
        dialog.preference_changed.connect(self.change_preference)
        dialog.diagnostic_requested.connect(lambda: self.worker.submit(self.worker.client.diagnose()))
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
        if self.preferences.dnd:
            for _, popup in list(self.popups.values()):
                popup.expire()

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
        self.window.quitting_now = True
        self.notifications.stop()
        self.tray.icon.hide()
        for _, popup in list(self.popups.values()):
            popup.expire()
        for view in list(self.private_windows.values()):
            view.close()
        self.worker.stop()
        self.history.clear()
        self.instance.close()
