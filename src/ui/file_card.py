"""A transfer lives in RAM; only an explicitly accepted download reaches disk."""
from PySide6.QtCore import Signal, Qt
from PySide6.QtWidgets import QFrame, QVBoxLayout, QHBoxLayout, QProgressBar

from .widgets import label, button
from .social import ElidedLabel


def file_size(size):
    if size >= 1024 * 1024:
        return f'{size / (1024 * 1024):.1f} Mio'
    if size >= 1024:
        return f'{size / 1024:.1f} Kio'
    return f'{size} octets'


class FileCard(QFrame):
    action_requested = Signal(str, object)

    def __init__(self, metadata, parent=None):
        super().__init__(parent)
        self.setObjectName('fileCard')
        box = QVBoxLayout(self)
        box.setContentsMargins(0, 3, 0, 2)
        box.setSpacing(9)
        self.name = ElidedLabel('', 'messageText', mode=Qt.TextElideMode.ElideMiddle)
        self.name.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        box.addWidget(self.name)
        self.details = label('', 'caption', True)
        box.addWidget(self.details)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setFixedHeight(9)
        self.progress.setTextVisible(False)
        self.progress.setStyleSheet('QProgressBar {background: #465060; border: none; border-radius: 4px;} '
                                    'QProgressBar::chunk {background: #7891ff; border-radius: 4px;}')
        box.addWidget(self.progress)
        self.controls = QHBoxLayout()
        self.accept = button('Enregistrer…', 'primary')
        self.decline = button('Refuser')
        self.cancel = button('Annuler')
        for control, action in ((self.accept, 'accept'), (self.decline, 'decline'), (self.cancel, 'cancel')):
            control.clicked.connect(lambda checked=False, a=action: self.action_requested.emit(a, dict(self.metadata)))
            self.controls.addWidget(control)
        self.controls.addStretch()
        box.addLayout(self.controls)
        self.update_transfer(metadata)

    def update_transfer(self, metadata):
        self.metadata = metadata
        status = metadata.get('status', 'offered')
        outgoing = metadata.get('outgoing', False)
        self.name.set_full_text('↧  ' + metadata['name'])
        self.name.setAccessibleName(metadata['name'])
        received = metadata.get('received', 0)
        total = metadata.get('size', 0)
        descriptions = {
            'offered': 'Fichier proposé · en attente d’acceptation' if outgoing else 'Choisis où enregistrer ce fichier.',
            'transferring': 'Envoi en cours…' if outgoing else 'Réception en cours…',
            'complete': 'Fichier envoyé.' if outgoing else 'Enregistré · intégrité vérifiée.',
            'declined': 'Fichier refusé.',
            'failed': 'Transfert interrompu.',
        }
        detail = metadata.get('detail') or descriptions.get(status, status)
        self.details.setText(file_size(total) + ' · ' + detail)
        self.progress.setVisible(status == 'transferring')
        self.progress.setValue(min(100, round(100 * received / total)) if total else 100)
        self.accept.setVisible(not outgoing and status == 'offered')
        self.decline.setVisible(not outgoing and status == 'offered')
        self.cancel.setVisible(status == 'transferring' or (outgoing and status == 'offered'))
