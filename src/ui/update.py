"""Copyable update instructions, without executing a command from the UI."""
from PySide6.QtCore import Qt
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import (
    QApplication, QDialog, QFileDialog, QHBoxLayout, QLineEdit,
    QPlainTextEdit, QScrollArea, QVBoxLayout, QWidget,
)
from ..features.updates import DEFAULT_REPOSITORY, update_command
from .widgets import button, label


class UpdateDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Mettre à jour · FortyTchat')
        self.setMinimumSize(460, 420)
        area = self.screen().availableGeometry()
        self.resize(min(740, max(460, area.width() - 40)), min(700, max(420, area.height() - 60)))
        box = QVBoxLayout(self)
        box.setContentsMargins(22, 22, 22, 18)
        box.setSpacing(14)
        content = QWidget()
        details = QVBoxLayout(content)
        details.setContentsMargins(0, 0, 0, 0)
        details.setSpacing(12)
        details.addWidget(label('Une mise à jour, en quelques clics.', 'title', True))
        details.addWidget(label(
            '1. Clique sur Copier pour prendre tout le bloc.\n'
            '2. Ouvre un terminal avec Ctrl+Alt+T.\n'
            '3. Colle avec Ctrl+Maj+V, puis appuie sur Entrée.', '', True))
        details.addWidget(label('Dossier du projet cloné depuis GitHub', 'muted', True))
        path = QHBoxLayout()
        self.repository = QLineEdit(DEFAULT_REPOSITORY)
        self.repository.setAccessibleName('Dossier du projet cloné depuis GitHub')
        self.repository.setToolTip('Garde ~/.FortyTchat pour une installation standard, ou choisis ton dossier Git.')
        path.addWidget(self.repository, 1)
        browse = button('Parcourir…', 'soft')
        browse.clicked.connect(self.choose_repository)
        path.addWidget(browse)
        details.addLayout(path)
        details.addWidget(label(
            'Le bloc ferme FortyTchat, récupère la mise à jour et réinstalle l’application. '
            'En cas d’échec, il s’arrête et explique le problème. Tes amis et tes préférences sont conservés ; '
            'les conversations en mémoire s’effacent à la fermeture.', 'muted', True))
        details.addStretch()
        scroll = QScrollArea()
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(content)
        box.addWidget(scroll, 1)
        self.code = QPlainTextEdit()
        self.code.setReadOnly(True)
        self.code.setAccessibleName('Bloc de mise à jour à copier dans le terminal')
        self.code.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        fixed = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
        self.code.setFont(fixed)
        self.code.setStyleSheet('QPlainTextEdit {font-family: "' + fixed.family() + '";}')
        box.addWidget(self.code, 1)
        self.feedback = label('', 'caption', True)
        box.addWidget(self.feedback)
        actions = QHBoxLayout()
        close = button('Fermer', 'soft')
        close.clicked.connect(self.reject)
        actions.addWidget(close)
        actions.addStretch()
        self.copy_button = button('Copier', 'primary')
        self.copy_button.clicked.connect(self.copy_command)
        actions.addWidget(self.copy_button)
        box.addLayout(actions)
        self.repository.textChanged.connect(self.refresh_command)
        self.refresh_command()

    def choose_repository(self):
        path = QFileDialog.getExistingDirectory(self, 'Choisir le dossier du projet')
        if path:
            self.repository.setText(path)

    def refresh_command(self):
        try:
            self.code.setPlainText(update_command(self.repository.text()))
            self.feedback.setText('Copie le bloc entier, puis colle-le dans ton terminal.')
            self.copy_button.setEnabled(True)
        except ValueError as error:
            self.code.clear()
            self.feedback.setText(str(error))
            self.copy_button.setEnabled(False)
        self.copy_button.setText('Copier')

    def copy_command(self):
        if not self.copy_button.isEnabled():
            return
        QApplication.clipboard().setText(self.code.toPlainText())
        self.copy_button.setText('Copié !')
        self.feedback.setText('Copié ! Ouvre le terminal, colle avec Ctrl+Maj+V et appuie sur Entrée.')
