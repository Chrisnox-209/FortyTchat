"""Out-of-band fingerprint comparison; no one-click trust reset."""
from PySide6.QtCore import Signal, Qt
from PySide6.QtWidgets import QDialog, QVBoxLayout, QScrollArea, QWidget, QLineEdit
from .widgets import label, button
from ..security import fingerprint


class SecurityDialog(QDialog):
    verify_requested = Signal(str, str)

    def __init__(self, own_public, peer=None, parent=None):
        super().__init__(parent)
        self.peer = peer
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setWindowTitle('Chiffrement de bout en bout · FortyTchat')
        self.setMinimumSize(420, 360)
        available = self.screen().availableGeometry()
        self.resize(min(600, available.width() - 40), min(700, available.height() - 60))
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 20)
        layout.setSpacing(16)
        layout.addWidget(label('Une conversation entre vous.', 'title', True))
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        page = QWidget()
        content = QVBoxLayout(page)
        content.setContentsMargins(0, 0, 10, 0)
        content.setSpacing(14)
        content.addWidget(label('Messages, fichiers et interactions sont chiffrés avant de quitter ton appareil. Le relais ne reçoit que leur contenu chiffré.', 'muted', True))
        self.add_fingerprint(content, 'TON EMPREINTE', own_public)
        if peer:
            state = peer.get('security', 'unverified')
            text = {'verified': 'Identité vérifiée', 'unverified': 'Premier contact : identité à vérifier',
                    'changed': 'Clé modifiée : échanges bloqués'}.get(state, 'Identité à vérifier')
            content.addWidget(label(text, 'notice', True))
            self.add_fingerprint(content, f'EMPREINTE DE {peer["nick"]}', peer['identity_key'])
            content.addWidget(label('Comparez vos empreintes en personne ou par un autre canal de confiance. Demande à ton contact de te donner son empreinte, puis colle-la ci-dessous. Ne te fie pas à une empreinte reçue uniquement dans ce tchat.', 'muted', True))
            self.expected = QLineEdit()
            self.expected.setMaxLength(160)
            self.expected.setPlaceholderText('Empreinte donnée par ton contact')
            self.expected.setAccessibleName('Empreinte à comparer')
            content.addWidget(self.expected)
            self.verify = button('Vérifier cette empreinte', 'primary')
            self.verify.clicked.connect(self.compare)
            self.expected.returnPressed.connect(self.compare)
            content.addWidget(self.verify)
        content.addWidget(label('Le premier contact est mémorisé automatiquement. Un changement de clé bloque les échanges jusqu’à une nouvelle vérification. Le salon général reste lisible par ses participants. Pseudos, présence, avatars, adresses et horaires de trafic restent visibles sur le réseau.', 'caption', True))
        self.feedback = label('', 'caption', True)
        content.addWidget(self.feedback)
        content.addStretch()
        scroll.setWidget(page)
        layout.addWidget(scroll, 1)
        close = button('Terminé')
        close.clicked.connect(self.close)
        layout.addWidget(close)

    @staticmethod
    def add_fingerprint(layout, title, public):
        layout.addWidget(label(title, 'eyebrow', True))
        groups = fingerprint(public).split()
        text = '\n'.join(' '.join(groups[i:i + 4]) for i in range(0, len(groups), 4))
        value = label(text, 'chip', True)
        value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        value.setStyleSheet('font-family: monospace; padding: 12px;')
        layout.addWidget(value)

    def compare(self):
        expected = ''.join(self.expected.text().split()).upper()
        if expected != ''.join(fingerprint(self.peer['identity_key']).split()):
            self.feedback.setText('Les empreintes ne correspondent pas. Les autorisations restent inchangées.')
            return
        self.feedback.setText('Vérification du contact en cours…')
        self.verify_requested.emit(self.peer['sid'], expected)

    def verified(self):
        self.feedback.setText('Empreinte vérifiée et mémorisée sur ton appareil.')
        self.verify.setEnabled(False)
