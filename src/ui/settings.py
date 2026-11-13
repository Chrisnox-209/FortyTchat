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
    security_requested = Signal()
    blocked_requested = Signal()

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
        self.friend_notifications = QCheckBox('Notifier la connexion de mes amis')
        self.friend_notifications.setChecked(preferences.friend_notifications)
        form.addWidget(self.friend_notifications)
        form.addWidget(label('Une notification Ubuntu silencieuse quand un ami se connecte. Ne pas déranger la masque. Les amis déjà présents au démarrage et les petites coupures réseau ne déclenchent pas d’alerte.', 'caption', True))
        self.read_receipts = QCheckBox('Partager les confirmations de lecture')
        self.read_receipts.setChecked(preferences.read_receipts)
        form.addWidget(self.read_receipts)
        form.addWidget(label('Deux coches grises : livré. Deux bleues : vu dans la conversation active. Désactive cette option pour ne pas envoyer « Vu » ; la livraison reste confirmée. Les confirmations déjà partagées restent visibles.', 'caption', True))
        self.tray_menu_double_click = QCheckBox('Compatibilité doubleclic de l’icône (GNOME)')
        self.tray_menu_double_click.setChecked(preferences.tray_menu_double_click)
        form.addWidget(self.tray_menu_double_click)
        form.addWidget(label('Aide à ouvrir le tchat au doubleclic quand la barre ouvre seulement un menu. Désactive cette option si une fermeture rapide du menu ouvre le tchat involontairement.', 'caption', True))
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
        self.security_button = button('Mon identité de chiffrement', 'soft', 'Afficher mon empreinte de sécurité')
        self.security_button.clicked.connect(self.security_requested)
        form.addWidget(self.security_button)
        self.blocked_button = button(f'Contacts bloqués ({len(preferences.blocked_contacts)})', 'soft')
        self.blocked_button.clicked.connect(self.blocked_requested)
        form.addWidget(self.blocked_button)
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
        brand.addLayout(identity, 1)
        privacy_box.addLayout(brand)
        repository = button('Voir le dépôt GitHub ↗', 'soft', 'Code source, installation et mises à jour')
        repository.clicked.connect(lambda: QDesktopServices.openUrl(QUrl('https://github.com/Chrisnox-209/FortyTchat')))
        privacy_box.addWidget(repository)
        for title, text in [
            ('Le cluster, en direct', 'Une messagerie locale pour discuter dans le salon général, en privé ou dans un groupe sur invitation. Aucun compte à créer : les personnes présentes sur le réseau sont découvertes automatiquement.'),
            ('Des groupes entre potes', 'Créer un groupe invite jusqu’à 31 personnes connectées. Chacun accepte ou refuse ; seuls les membres ayant rejoint reçoivent les nouveaux messages. Fermer une fenêtre garde le groupe actif ; Quitter le groupe arrête les échanges. Les noms, photos, membres et rôles sont conservés après redémarrage ; les messages restent éphémères. Au retour d’un membre, le créateur doit être connecté pour confirmer son accès.'),
            ('Un groupe qui vous ressemble', 'Le menu ··· → Paramètres du groupe sépare les Réglages et les Membres. Chaque membre affiche son avatar et un badge Créateur, Admin ou Membre. Le créateur nomme et retire les administrateurs. Ceux-ci gèrent le nom, la photo, le délai des popups et les membres ordinaires ; seul le créateur gère les rôles. Les changements sont validés par le créateur connecté.'),
            ('Une popup pour le groupe', 'Le bouton ✦ envoie une notification personnalisée aux membres connectés ayant rejoint. Le créateur et les administrateurs règlent le délai dans Paramètres du groupe : de 1 minute à 24 heures, 42 minutes par défaut. Ce délai est partagé par tout le groupe et conservé après redémarrage. Le changer recalcule l’attente depuis la dernière popup. Le créateur connecté réserve cet envoi. Les réponses reviennent en privé à l’expéditeur. Aucune popup collective dans le général.'),
            ('Mentions et blocs de code', 'Écris @ pour choisir une personne ; sa mention est mise en évidence et l’alerte respecte Ne pas déranger. Le bouton </> ou Ctrl+Maj+C ouvre un éditeur où coller ton code. Il prépare ==== sur une ligne avant et après le code, facile à saisir sur QWERTY. Les blocs se copient avec un bouton ; aucun code n’est exécuté.'),
            ('Modifier et supprimer', 'Le menu ··· de tes messages permet de corriger le texte ou de supprimer un message, un sondage ou une pièce jointe de la conversation chez les participants compatibles. La mention Modifié ou Message supprimé indique le changement. Un fichier déjà enregistré chez un destinataire reste sur son disque.'),
            ('Sondages du cluster', 'Le bouton de sondage propose une question et deux à huit choix dans le général ou un groupe. Choisis une réponse, vérifie sa surbrillance, puis clique sur Valider le vote. Une fois validé, le vote est définitif. Les résultats remplissent les lignes des options et affichent les pourcentages. Les nouveaux sondages sont à choix unique ; les anciens sondages restent lisibles. Les résultats restent en mémoire, avec un maximum de 256 participants par sondage.'),
            ('Images et captures', 'Les images compatibles affichent un aperçu dans le tchat. Ctrl+V prépare une capture à confirmer avant envoi. La capture reste en mémoire chez l’expéditeur ; le destinataire choisit s’il enregistre le fichier original. Les miniatures et les fichiers sont chiffrés.'),
            ('Bloquer un utilisateur', 'Le menu d’une personne permet de bloquer son profil. Le choix reste actif après un changement de pseudo ou une reconnexion ; ses messages, invitations et transferts sont refusés. Paramètres → Contacts bloqués permet de le débloquer, même hors ligne.'),
            ('Envoyé, livré, vu', 'Une coche grise : envoyé, réception en attente. Deux coches grises : livré à l’application du destinataire. Deux bleues : affiché dans sa conversation active. Les compteurs du général concernent les destinataires présents à l’envoi. Désactive le partage des confirmations de lecture dans Général si tu ne veux pas envoyer « Vu ». Les états restent en mémoire et leurs confirmations sont chiffrées.'),
            ('Répondre, réagir, discuter', 'Le bouton ↩ cite un message ; un clic sur la citation y ramène. Le bouton ☺ ajoute une réaction emoji, modifiable ou retirable. Trois points animés indiquent quand quelqu’un écrit, sans partager son brouillon.'),
            ('Tes notifications, tes réponses', 'Le bouton ✦ prépare ta popup. Paramétrer la réponse ouvre une fenêtre dédiée : choisis Oui, Non ou Personnalisée, puis saisis un seul texte par réponse. Le bouton + permet d’en ajouter, jusqu’à trois ; le petit emoji change l’animation. Un seul bouton, sans réponse permet de choisir un libellé comme Bien reçu ! qui ferme simplement la popup. Valider applique les réglages, Annuler les conserve. Les anciens modèles restent utilisables. Les versions précédentes affichent Fermer à la place du libellé de fermeture personnalisé, sans envoyer de réponse.'),
            ('Tes amis, ton image, tes emojis', 'Les amis restent reconnus après un changement de pseudo. Choisis un avatar ou importe ta photo, puis exprime-toi parmi 3 781 emojis avec recherche et catégories.'),
            ('Tes amis depuis l’icône', 'Le clic droit sur l’icône FT affiche tes amis en ligne et hors ligne. Ouvre le sous-menu d’un ami pour discuter ou envoyer une popup, sans ouvrir le tchat. Le clic droit dans la liste des amis propose aussi l’éditeur de popup. Un ami hors ligne ne peut pas recevoir de popup.'),
            ('Un ami arrive dans le cluster', 'Une notification Ubuntu silencieuse t’avertit quand un ami se connecte. Elle utilise le service natif du bureau via gdbus, avec notify-send en secours. Désactive-la dans Général ou avec Ne pas déranger. Les connexions simultanées sont regroupées, sans alerte pour les changements de pseudo, les petites coupures ou les amis déjà présents au démarrage. Le bureau Ubuntu contrôle la position et l’apparence des bannières.'),
            ('Des fichiers à partager', 'Sélectionne plusieurs fichiers ou glisse-les dans le champ de message, dans le général, en privé ou dans un groupe. Jusqu’à 64 fichiers en attente, 20 Mio par fichier ; huit offres et quatre transferts actifs à la fois. Les sources restent sur leur disque. Le destinataire choisit de l’enregistrer ou de le refuser. Les fichiers enregistrés restent à l’emplacement choisi.'),
            ('Se montrer son écran', 'Le bouton écran d’une conversation privée ouvre le partage. Choisis ton écran puis Partager ; ton ami accepte avant le début de la capture. Sous Wayland, le bureau demande aussi l’autorisation et choisit l’écran. Une fenêtre indépendante affiche les images, jusqu’à 1280 × 720 et cinq par seconde. Chacun peut Arrêter ; le menu de l’icône FT permet aussi de terminer. Lecture seule, sans audio ni contrôle à distance, images chiffrées et non enregistrées. Les deux clients doivent être en 2.4.0 ou plus récent ; les anciennes versions gardent leurs conversations et fichiers. Aucun partage ne reprend automatiquement au démarrage.'),
            ('Expliquer avec un pointeur et des dessins', 'Pendant un partage entre deux clients 2.5.0 ou plus récents, le spectateur choisit Pointeur, Crayon, Cercle ou Rectangle, puis une couleur. Il déplace la souris ou clique et glisse dans l’image ; les bandes noires sont exclues. Les annotations sont chiffrées et visibles des deux côtés. Sous X11, elles se superposent à l’écran choisi, même si les commandes du partage sont réduites. La couche laisse passer les clics du streamer, qui garde sa souris et son clavier. Sous Wayland, les annotations restent visibles dans l’aperçu de sa fenêtre de partage. Chacun peut Effacer les dessins. Ils restent en mémoire, limités à 64 formes, et disparaissent à l’arrêt. Les versions 2.4 gardent le partage sans les nouveaux outils.'),
            ('Un Wizz, une pause', 'Le Wizz fait vibrer la conversation pendant 2,8 secondes et montre les derniers messages, sans son. Un envoi toutes les 42 minutes. Les pauses restent actives en Ne pas déranger : deux envois privés autorisés, puis cinq minutes de blocage ; une nouvelle série de deux envois après ce blocage entraîne quinze minutes. Les tentatives bloquées ne prolongent pas le délai. La réponse apparaît en bleu pour oui et en rouge pour non.'),
            ('Ton profil reste, les messages s’effacent', 'Pseudo, amis, blocages, photo, préférences et composition des groupes restent sur ton espace utilisateur. Messages, votes et captures préparées restent en mémoire : Quitter complètement les efface de ce processus. Fermer la fenêtre garde l’application active.'),
            ('Chiffré de bout en bout', 'Messages, fichiers, réactions, saisie, Wizz et notifications sont chiffrés entre les appareils, en direct et via le relais. Chaque démarrage renouvelle les clés de session. Le cadenas affiche les empreintes publiques : leur comparaison confirme l’identité d’un contact. Les messages restent chiffrés même sans cette comparaison. Un changement de clé bloque les échanges. Les anciennes versions doivent être mises à jour.'),
            ('Une identité à vérifier', 'La clé de ton profil et les clés publiques de tes contacts restent sur ton espace utilisateur, sans historique de messages. Le pseudo ne prouve pas l’identité d’un étudiant. Le général reste lisible par ses participants ; pseudos, présence, avatars et métadonnées de trafic restent publics. Un appareil compromis peut lire ses conversations.'),
            ('Encore dans le cluster, loin du clavier', 'Quand le bureau signale le verrouillage, un cadenas remplace la pastille verte sur l’avatar : ton ami reste dans le cluster, loin du clavier. GNOME Shell est lu directement via ScreenShield, même si le proxy ScreenSaver manque et que logind reste faux. Les autres sources et la recherche de session sans XDG_SESSION_ID restent actives. Au déverrouillage, le statut manuel précédent revient ; Ne pas déranger reste respecté. Pour voir le cadenas, l’autre poste doit utiliser 2.3.0 ou plus récent ; les versions plus anciennes gardent Absent/DND. Le script diagnostic-session.sh observe les sources pendant 60 secondes et crée un rapport texte privé dans ton dossier personnel, sans sudo, connexion au tchat ou collecte de messages/clés. Verrouille puis déverrouille pendant ce test. fortytchat --diagnose-lock propose aussi un affichage terminal sans fichier. Le correctif ScreenShield reste à confirmer sur le poste de l’école ; aucun état n’est déduit de l’inactivité ou des frappes.'),
            ('Une mise à jour dans le cluster', 'Un bandeau rouge indique qu’un poste compatible annonce une version plus récente. Le bouton Mettre à jour ouvre une fenêtre avec un bloc de commandes et un bouton Copier. Ouvre un terminal avec Ctrl+Alt+T, colle avec Ctrl+Maj+V, puis appuie sur Entrée. Si le projet est dans un autre dossier que ~/.FortyTchat, choisis-le dans cette fenêtre avant de copier. Le bloc ferme l’application puis installe la mise à jour ; il s’arrête en cas d’échec. Aucune commande reçue d’un autre poste n’est exécutée. Le protocole et les profils ne changent pas : les fonctions déjà compatibles des anciennes versions restent disponibles.'),
            ('Ubuntu et la barre supérieure', 'L’icône FT devient rouge quand des messages sont non lus. Doubleclic, activation principale ou clic milieu ouvrent le tchat. La compatibilité GNOME reconnaît aussi l’ouverture puis la fermeture rapide du menu, sans sélectionner une action ; elle peut être désactivée dans Général. Le menu propose toujours Ouvrir FortyTchat. Le bureau garde le contrôle du placement et de la mise au premier plan ; le comportement doit être vérifié sur les postes de l’école.'),
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
        self.friend_notifications.toggled.connect(lambda value: self.preference_changed.emit('friend_notifications', value))
        self.read_receipts.toggled.connect(lambda value: self.preference_changed.emit('read_receipts', value))
        self.tray_menu_double_click.toggled.connect(lambda value: self.preference_changed.emit('tray_menu_double_click', value))
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
