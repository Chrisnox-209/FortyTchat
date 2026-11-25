"""Update progress and optional copyable terminal instructions."""
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
        self.setMinimumWidth(460)
        self.busy = False
        box = QVBoxLayout(self)
        box.setContentsMargins(22, 22, 22, 18)
        box.setSpacing(14)
        box.addWidget(label('Mettre à jour FortyTchat', 'title', True))
        self.status = label('La commande manuelle est disponible ci-dessous.', '', True)
        box.addWidget(self.status)
        self.disclosure = button('▸  Commande manuelle', 'soft')
        self.disclosure.setCheckable(True)
        self.disclosure.toggled.connect(self.toggle_manual)
        box.addWidget(self.disclosure)
        self.manual = QWidget()
        manual_box = QVBoxLayout(self.manual)
        manual_box.setContentsMargins(0, 0, 0, 0)
        manual_box.setSpacing(10)
        box.addWidget(self.manual, 1)
        content = QWidget()
        details = QVBoxLayout(content)
        details.setContentsMargins(0, 0, 0, 0)
        details.setSpacing(12)
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
        manual_box.addWidget(scroll, 1)
        self.code = QPlainTextEdit()
        self.code.setMinimumHeight(88)
        self.code.setReadOnly(True)
        self.code.setAccessibleName('Bloc de mise à jour à copier dans le terminal')
        self.code.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        fixed = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
        self.code.setFont(fixed)
        self.code.setStyleSheet('QPlainTextEdit {font-family: "' + fixed.family() + '";}')
        manual_box.addWidget(self.code, 1)
        self.feedback = label('', 'caption', True)
        manual_box.addWidget(self.feedback)
        actions = QHBoxLayout()
        actions.addStretch()
        self.copy_button = button('Copier', 'primary')
        self.copy_button.clicked.connect(self.copy_command)
        actions.addWidget(self.copy_button)
        manual_box.addLayout(actions)
        self.close_button = button('Fermer', 'soft')
        self.close_button.clicked.connect(self.reject)
        box.addWidget(self.close_button, alignment=Qt.AlignmentFlag.AlignRight)
        self.repository.textChanged.connect(self.refresh_command)
        self.refresh_command()
        self.toggle_manual(False)

    def toggle_manual(self, expanded):
        self.manual.setVisible(expanded)
        self.disclosure.setText(('▾' if expanded else '▸') + '  Commande manuelle')
        if expanded:
            area = self.screen().availableGeometry()
            self.resize(min(700, max(460, area.width() - 40)), min(640, max(360, area.height() - 60)))
        else:
            self.adjustSize()

    def set_busy(self, busy):
        self.busy = busy
        self.disclosure.setEnabled(not busy)
        self.close_button.setEnabled(not busy)

    def reject(self):
        if not self.busy:
            super().reject()

    def closeEvent(self, event):
        if self.busy:
            event.ignore()
        else:
            super().closeEvent(event)

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
