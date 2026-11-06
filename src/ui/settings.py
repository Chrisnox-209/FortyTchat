from PySide6.QtCore import Signal, Qt
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QTabWidget, QWidget,
                               QCheckBox, QLineEdit, QComboBox, QPlainTextEdit, QScrollArea,
                               QFrame, QLayout, QSizePolicy)
from .widgets import button, label, Avatar
from .. import __version__


class SettingsDialog(QDialog):
    nickname_requested = Signal(str)
    preference_changed = Signal(str, object)
    diagnostic_requested = Signal()
    avatar_requested = Signal()

    def __init__(self, preferences, autostart_enabled, network, dev=False, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f'Paramètres · FortyTchat {__version__}')
        self.setMinimumSize(460, 420)
        available = self.screen().availableGeometry()
        self.resize(min(640, max(460, available.width() - 48)),
                    min(700, max(420, available.height() - 80)))
        box = QVBoxLayout(self)
        box.setContentsMargins(24, 23, 24, 20)
        box.setSpacing(16)
        box.addWidget(label('À ta façon.', 'heroTitle'))
        box.addWidget(label('Les préférences restent sur ton espace utilisateur.', 'muted', True))
        self.tabs = QTabWidget()
        box.addWidget(self.tabs, 1)
        general = QWidget()
        form = QVBoxLayout(general)
        form.setContentsMargins(18, 20, 18, 16)
        form.setSpacing(12)
        form.setSizeConstraint(QLayout.SizeConstraint.SetMinAndMaxSize)
        avatar_row = QHBoxLayout()
        self.avatar_preview = Avatar(preferences.nickname, 56, avatar=preferences.avatar, image_data=preferences.avatar_image)
        avatar_row.addWidget(self.avatar_preview)
        change_avatar = button('Changer d’avatar')
        change_avatar.clicked.connect(self.avatar_requested)
        avatar_row.addWidget(change_avatar)
        avatar_row.addStretch()
        form.addLayout(avatar_row)
        form.addWidget(label('TON PSEUDO', 'eyebrow'))
        nick_row = QHBoxLayout()
        self.nickname = QLineEdit(preferences.nickname)
        self.nickname.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.nickname.setMaxLength(28)
        apply_nick = button('Enregistrer', 'primary')
        apply_nick.clicked.connect(lambda: self.nickname_requested.emit(self.nickname.text().strip()))
        self.nickname.returnPressed.connect(apply_nick.click)
        nick_row.addWidget(self.nickname, 1)
        nick_row.addWidget(apply_nick)
        form.addLayout(nick_row)
        form.addWidget(label('3 à 28 lettres ASCII, chiffres, tirets ou underscores.\nLes collisions sont résolues automatiquement.', 'caption', True))
        self.autostart = QCheckBox('Lancer FortyTchat au démarrage')
        self.autostart.setChecked(autostart_enabled)
        self.autostart.setEnabled(not dev)
        self.autostart.setToolTip('Désactivé pour les profils de développement' if dev else 'Entrée XDG personnelle ; aucun droit administrateur')
        form.addWidget(self.autostart)
        self.wizz = QCheckBox('Recevoir les Wizz (toujours sans son)')
        self.wizz.setChecked(preferences.wizz_enabled)
        form.addWidget(self.wizz)
        self.dnd = QCheckBox('Ne pas déranger')
        self.dnd.setChecked(preferences.dnd)
        form.addWidget(self.dnd)
        form.addWidget(label('En mode Ne pas déranger, les messages arrivent sans\nnotification ; les Wizz et invitations sont refusés.', 'caption', True))
        theme_row = QHBoxLayout()
        theme_row.addWidget(label('Apparence'))
        self.theme = QComboBox()
        self.theme.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.theme.addItem('Anthracite & bleu', 'dark')
        self.theme.addItem('Gris clair & bleu', 'light')
        self.theme.setCurrentIndex(0 if preferences.theme == 'dark' else 1)
        theme_row.addWidget(self.theme, 1)
        form.addLayout(theme_row)
        form.addStretch()
        self.tabs.addTab(self.scroll_page(general), 'Général')
        network_page = QWidget()
        network_box = QVBoxLayout(network_page)
        network_box.setContentsMargins(18, 18, 18, 15)
        network_box.setSpacing(12)
        network_box.setSizeConstraint(QLayout.SizeConstraint.SetMinAndMaxSize)
        network_box.addWidget(label('RÉSEAU → DIAGNOSTIC', 'eyebrow'))
        self.network_info = QPlainTextEdit()
        self.network_info.setReadOnly(True)
        self.network_info.setMinimumHeight(180)
        network_box.addWidget(self.network_info, 1)
        self.test_button = button('Tester le réseau', 'primary')
        self.test_button.clicked.connect(self.run_diagnostic)
        network_box.addWidget(self.test_button)
        self.diagnostic = label('Le test contacte uniquement un pair découvert ou le relais\nconfiguré. Il ne scanne aucune adresse.', 'caption', True)
        network_box.addWidget(self.diagnostic)
        self.tabs.addTab(self.scroll_page(network_page), 'Réseau')
        privacy = QWidget()
        privacy_box = QVBoxLayout(privacy)
        privacy_box.setContentsMargins(20, 23, 20, 20)
        privacy_box.setSpacing(18)
        privacy_box.setSizeConstraint(QLayout.SizeConstraint.SetMinAndMaxSize)
        privacy_box.addWidget(label(f'FortyTchat {__version__}', 'eyebrow'))
        for title, text in [
            ('Une conversation éphémère', 'Les messages restent uniquement dans la mémoire du processus, en quantité limitée. Quitter complètement FortyTchat les efface de l’application. Fermer la fenêtre garde l’application ouverte.'),
            ('Une identité d’affichage', 'Le pseudo et l’identifiant d’ami reconnaissent un profil sans authentifier un étudiant. Le LAN direct n’est pas chiffré. Le relais utilise TLS en production, sans chiffrement de bout en bout.'),
            ('Ubuntu et la barre supérieure', 'L’icône dépend du support StatusNotifier/AppIndicator de la session GNOME. Sans support, la fermeture réduit la fenêtre et le démarrage reste visible. Aucune extension ni règle système n’est installée.'),
            ('Le réseau de l’école reste à tester', 'Une écoute locale ou un test sur un seul PC ne garantit pas la communication entre les postes. Le mode relais nécessite une machine autorisée et une configuration DNS préétablie.'),
        ]:
            privacy_box.addWidget(label(title, 'title', True))
            privacy_box.addWidget(label(text, 'muted', True))
        privacy_box.addStretch()
        self.tabs.addTab(self.scroll_page(privacy), 'À propos')
        self.feedback = label('', 'caption', True)
        box.addWidget(self.feedback)
        close = button('Terminé')
        close.clicked.connect(self.accept)
        box.addWidget(close)
        self.autostart.toggled.connect(lambda value: self.preference_changed.emit('autostart', value))
        self.wizz.toggled.connect(lambda value: self.preference_changed.emit('wizz_enabled', value))
        self.dnd.toggled.connect(lambda value: self.preference_changed.emit('dnd', value))
        self.theme.currentIndexChanged.connect(lambda index: self.preference_changed.emit('theme', self.theme.itemData(index)))
        self.update_network(network)

    @staticmethod
    def scroll_page(content):
        page = QScrollArea()
        page.setWidgetResizable(True)
        page.setFrameShape(QFrame.Shape.NoFrame)
        page.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        page.setWidget(content)
        return page

    def run_diagnostic(self):
        self.test_button.setEnabled(False)
        self.test_button.setText('Test en cours…')
        self.diagnostic.setText('Vérification des services et recherche de pairs…')
        self.diagnostic_requested.emit()

    def update_network(self, data):
        interface = data.get('interface') or {}
        mode = 'Relais connecté' if data.get('relay') else ('LAN / P2P — découverte active' if data.get('direct') else 'Services indisponibles ou en démarrage')
        rows = [f'Mode : {mode}',
                f'Interface : {interface.get("name", "aucune sélectionnée")}' + (' (secours non physique)' if interface and not interface.get('physical') else ''),
                f'IPv4 : {interface.get("address", "—")}', f'Sous-réseau : {interface.get("subnet", "—")}',
                f'Broadcast : {interface.get("broadcast", "—")}',
                f'UDP : {data.get("udp_port", 39741)}', f'TCP : {data.get("port", "—")} · {"écoute active" if data.get("tcp_listening") else "indisponible"}',
                f'Erreurs d’envoi UDP : {data.get("udp_errors", 0)}',
                f'Autres utilisateurs : {data.get("peers", 0)}',
                f'Relais : {data.get("relay_host") or "non configuré"}',
                'Dernières erreurs :'] + (data.get('errors') or ['Aucune erreur enregistrée.'])
        self.network_info.setPlainText('\n'.join(rows))

    def diagnostic_result(self, rows):
        self.test_button.setEnabled(True)
        self.test_button.setText('Tester le réseau')
        self.diagnostic.setText('\n'.join(rows))
        # Results can be long; the main text area scrolls without growing the dialog.
        self.network_info.appendPlainText('\nRésultat du test :\n' + '\n'.join(rows))
        self.diagnostic.setText('Test terminé. Les résultats figurent ci-dessus.\nUne validation sur deux postes reste nécessaire.')
