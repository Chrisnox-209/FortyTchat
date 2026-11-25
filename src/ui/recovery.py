"""Local startup recovery for an altered version or application tree."""
import subprocess
import sys

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QDialog, QHBoxLayout, QVBoxLayout

from ..config import ConfigStore
from ..features.releases import verified_tree
from ..features.updates import installed_app
from .theme import apply_theme
from .widgets import button, label


class RecoveryDialog(QDialog):
    def __init__(self, root):
        super().__init__()
        self.root = root
        self.setWindowTitle('FortyTchat · Restauration nécessaire')
        self.setWindowFlag(Qt.WindowType.WindowCloseButtonHint, False)
        self.setMinimumWidth(420)
        self.setMaximumWidth(520)
        box = QVBoxLayout(self)
        box.setContentsMargins(24, 22, 24, 20)
        box.setSpacing(12)
        box.addWidget(label('Installation non vérifiée', 'title', True))
        box.addWidget(label('Les fichiers ou le numéro de version diffèrent de la publication signée. '
                            'Le tchat est suspendu jusqu’à la restauration.', '', True))
        self.feedback = label('La restauration utilise d’abord la copie locale signée. Ton profil est conservé.',
                              'muted', True)
        box.addWidget(self.feedback)
        row = QHBoxLayout()
        quit_button = button('Quitter', 'soft')
        quit_button.clicked.connect(lambda: self.done(1))
        self.restore_button = button('Restaurer FortyTchat', 'primary')
        self.restore_button.clicked.connect(self.restore)
        row.addWidget(quit_button)
        row.addStretch()
        row.addWidget(self.restore_button)
        box.addLayout(row)
        if root.resolve() != installed_app().resolve():
            self.restore_button.setEnabled(False)
            self.feedback.setText('Ce dossier est une copie de travail. Installe une copie officielle pour retrouver le tchat.')

    def reject(self):
        # Keep the recovery action visible until the user restores or quits.
        pass

    def restore(self):
        backup = self.root.parent / 'recovery'
        source = backup if verified_tree(backup) else self.root
        updater = source / 'tools' / 'update_user.py'
        if not updater.is_file():
            self.feedback.setText('Le programme de restauration est absent. Réinstalle depuis une copie officielle.')
            return
        try:
            subprocess.Popen([sys.executable, str(updater), '--restore'], cwd=self.root.parent,
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, start_new_session=True)
        except OSError:
            self.feedback.setText('La restauration n’a pas démarré. Réessaie ou réinstalle depuis une copie officielle.')
            return
        self.done(0)


def recovery_dialog(root):
    apply_theme(QApplication.instance(), ConfigStore().preferences.theme)
    return RecoveryDialog(root).exec()
