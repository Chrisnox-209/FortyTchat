from pathlib import Path

from PySide6.QtCore import Signal, Qt, QUrl
from PySide6.QtGui import QDesktopServices, QIcon
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QTabWidget, QWidget,
                               QCheckBox, QLineEdit, QComboBox, QScrollArea,
                               QFrame, QLayout, QSizePolicy)
from .widgets import button, label, Avatar
from .. import __version__


class SettingsDialog(QDialog):
    nickname_requested = Signal(str)
    preference_changed = Signal(str, object)
    diagnostic_requested = Signal()
    avatar_requested = Signal()
    cards_requested = Signal()

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
        form.addWidget(label('En mode Ne pas déranger, les messages restent silencieux ; Wizz et notifications personnalisées sont refusés. Les demandes de pause restent actives.', 'caption', True))
        theme_row = QHBoxLayout()
        theme_row.addWidget(label('Apparence'))
        self.theme = QComboBox()
        self.theme.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.theme.addItem('Anthracite & bleu', 'dark')
        self.theme.addItem('Gris clair & bleu', 'light')
        self.theme.setCurrentIndex(0 if preferences.theme == 'dark' else 1)
        theme_row.addWidget(self.theme, 1)
        form.addLayout(theme_row)
        self.cards_button = button('Mes notifications animées', 'soft', 'Créer tes modèles de notification et de réponse')
        self.cards_button.clicked.connect(self.cards_requested)
        form.addWidget(self.cards_button)
        form.addStretch()
        self.tabs.addTab(self.scroll_page(general), 'Général')
        privacy = QWidget()
        privacy_box = QVBoxLayout(privacy)
        privacy_box.setContentsMargins(20, 23, 20, 20)
        privacy_box.setSpacing(18)
        privacy_box.setSizeConstraint(QLayout.SizeConstraint.SetMinAndMaxSize)
        brand = QHBoxLayout()
        brand.setSpacing(14)
        logo = label('')
        logo.setPixmap(QIcon(str(Path(__file__).resolve().parents[2] / 'assets/icons/fortytchat.svg')).pixmap(56, 56))
        logo.setFixedSize(56, 56)
        brand.addWidget(logo)
        identity = QVBoxLayout()
        identity.setSpacing(5)
        identity.addWidget(label(f'FortyTchat {__version__}', 'title', True))
        identity.addWidget(label('Par Chrisnox-209 · pour le cluster 42', 'caption', True))
        brand.addLayout(identity, 1)
        privacy_box.addLayout(brand)
        repository = button('Voir le dépôt GitHub ↗', 'soft', 'Code source, installation et mises à jour')
        repository.clicked.connect(lambda: QDesktopServices.openUrl(QUrl('https://github.com/Chrisnox-209/FortyTchat')))
        privacy_box.addWidget(repository)
        for title, text in [
            ('Le cluster, en direct', 'Une messagerie locale pour discuter dans le salon général ou dans une fenêtre privée. Aucun compte à créer : les personnes présentes sur le réseau sont découvertes automatiquement.'),
            ('Répondre, réagir, discuter', 'Le bouton ↩ cite un message ; un clic sur la citation y ramène. Le bouton ☺ ajoute une réaction emoji, modifiable ou retirable. Trois points animés indiquent quand quelqu’un écrit, sans partager son brouillon.'),
            ('Des notifications à ton image', 'Le bouton ✦ ouvre un éditeur de titre, texte et emoji animé. Onze animations sont disponibles hors connexion. Réponds par Oui, Non ou une réponse personnalisée. Tes modèles enregistrés restent dans ton profil et sont accessibles depuis les paramètres.'),
            ('Tes amis, ton image, tes emojis', 'Les amis restent reconnus après un changement de pseudo. Choisis un avatar ou importe ta photo, puis exprime-toi parmi 3 781 emojis avec recherche et catégories.'),
            ('Des fichiers à partager', 'Envoie un fichier de 20 Mio maximum dans le général ou en privé. Le destinataire choisit de l’enregistrer ou de le refuser. Les fichiers enregistrés restent à l’emplacement choisi.'),
            ('Un Wizz, une pause', 'Le Wizz fait vibrer la conversation pendant 2,8 secondes et montre les derniers messages, sans son. Un envoi toutes les 42 minutes. Les pauses restent actives en Ne pas déranger : trois demandes successives, puis cinq minutes de blocage à la quatrième. La réponse apparaît en bleu pour oui et en rouge pour non.'),
            ('Ton profil reste, les messages s’effacent', 'Pseudo, amis, photo et préférences restent sur ton espace utilisateur. Les messages restent en mémoire : Quitter complètement les efface de ce processus. Fermer la fenêtre garde l’application active.'),
            ('Une identité d’affichage', 'Le pseudo et l’identifiant d’ami reconnaissent un profil sans authentifier un étudiant. Le LAN direct n’est pas chiffré. Le relais utilise TLS, sans chiffrement de bout en bout.'),
            ('Ubuntu et la barre supérieure', 'L’icône FT devient rouge quand des messages sont non lus. Le doubleclic, son placement et la mise au premier plan dépendent du bureau. Sans barre compatible, FortyTchat reste accessible dans le dock.'),
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

    @staticmethod
    def scroll_page(content):
        page = QScrollArea()
        page.setWidgetResizable(True)
        page.setFrameShape(QFrame.Shape.NoFrame)
        page.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        page.setWidget(content)
        return page

    def update_network(self, data):
        # Kept for controllers receiving background network state. Configuration is
        # deployment-owned (network.json), with no network controls in preferences.
        pass

    def diagnostic_result(self, rows):
        pass
