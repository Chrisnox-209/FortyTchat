from PySide6.QtCore import QTimer, QPropertyAnimation
from PySide6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QGraphicsOpacityEffect
from .widgets import label, button


class BreakPopup(QDialog):
    def __init__(self, nickname, callback, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Une pause ? · FortyTchat')
        self.setModal(False)
        self.setMinimumWidth(370)
        self.callback, self.answered = callback, False
        box = QVBoxLayout(self)
        box.setContentsMargins(28, 26, 28, 26)
        box.setSpacing(16)
        box.addWidget(label('☕  LE CODE PEUT ATTENDRE.', 'eyebrow'))
        box.addWidget(label(f'{nickname} te propose\nune pause !', 'heroTitle', True))
        box.addWidget(label('On va en pause ? Une petite respiration\net on revient avec les idées claires.', 'muted', True))
        controls = QHBoxLayout()
        later = button('Pas maintenant')
        yes = button('J’arrive !', 'primary')
        later.clicked.connect(lambda: self.answer('declined'))
        yes.clicked.connect(lambda: self.answer('accepted'))
        controls.addWidget(later)
        controls.addWidget(yes)
        box.addLayout(controls)
        box.addWidget(label('Invitation valable 2 minutes · sans son', 'caption'))
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.expire)
        self.timer.start(120000)
        effect = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(effect)
        self.fade = QPropertyAnimation(effect, b'opacity', self)
        self.fade.setDuration(200)
        self.fade.setStartValue(0.2)
        self.fade.setEndValue(1.0)
        self.fade.start()

    def answer(self, value):
        if not self.answered:
            self.answered = True
            self.callback(value)
        self.close()

    def expire(self):
        self.answered = True
        self.close()

    def closeEvent(self, event):
        if not self.answered:
            self.answered = True
            self.callback('dismissed')
        self.timer.stop()
        super().closeEvent(event)
