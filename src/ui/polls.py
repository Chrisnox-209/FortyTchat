from PySide6.QtCore import Signal, Qt, QSize
from PySide6.QtWidgets import QDialog, QVBoxLayout, QLineEdit, QScrollArea, QWidget, QFrame, QLayout, QPushButton, QCheckBox, QProgressBar
from .widgets import label, button


class PollEditor(QDialog):
    submitted = Signal(str, object, bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Créer un sondage · FortyTchat')
        self.resize(480, 530)
        self.setMinimumSize(380, 380)
        box = QVBoxLayout(self)
        box.setContentsMargins(24, 24, 24, 24)
        box.addWidget(label('On choisit ensemble ?', 'title', True))
        box.addWidget(label('Un choix ou plusieurs réponses par personne, modifiables et retirables. Le sondage reste dans la session.', 'muted', True))
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        page = QWidget()
        form = QVBoxLayout(page)
        form.setSizeConstraint(QLayout.SizeConstraint.SetMinAndMaxSize)
        form.setSpacing(12)
        self.question = QLineEdit()
        self.question.setMaxLength(200)
        self.question.setPlaceholderText('Ta question')
        self.question.setAccessibleName('Question du sondage')
        form.addWidget(self.question)
        self.multiple = QCheckBox('Autoriser plusieurs réponses')
        form.addWidget(self.multiple)
        self.options = []
        for i in range(8):
            field = QLineEdit()
            field.setMaxLength(80)
            field.setPlaceholderText(f'Choix {i + 1}')
            field.setAccessibleName(f'Choix {i + 1}')
            self.options.append(field)
            form.addWidget(field)
        scroll.setWidget(page)
        box.addWidget(scroll, 1)
        self.feedback = label('Remplis au moins deux choix ; les autres peuvent rester vides.', 'caption', True)
        box.addWidget(self.feedback)
        submit = button('Publier le sondage', 'primary')
        submit.clicked.connect(self.submit)
        box.addWidget(submit)
        cancel = button('Annuler')
        cancel.clicked.connect(self.reject)
        box.addWidget(cancel)

    def submit(self):
        options = [field.text().strip() for field in self.options if field.text().strip()]
        if not self.question.text().strip() or len(options) < 2 or len({o.casefold() for o in options}) != len(options):
            self.feedback.setText('Écris une question et au moins deux choix différents.')
            return
        self.submitted.emit(self.question.text().strip(), options, self.multiple.isChecked())
        self.accept()


class VoteButton(QPushButton):
    def sizeHint(self):
        hint = super().sizeHint()
        return QSize(min(240, hint.width()), hint.height())

    def minimumSizeHint(self):
        return QSize(80, super().sizeHint().height())

    def set_full_text(self, text):
        self.full_text = text
        self.setToolTip(text)
        self.fit_text()

    def fit_text(self):
        if hasattr(self, 'full_text'):
            self.setText(self.fontMetrics().elidedText(self.full_text, Qt.TextElideMode.ElideRight, max(40, self.width() - 30)))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.fit_text()


class PollCard(QFrame):
    voted = Signal(object)

    def __init__(self, poll, actor, parent=None):
        super().__init__(parent)
        self.actor, self.poll = actor, poll
        box = QVBoxLayout(self)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(8)
        box.addWidget(label('SONDAGE', 'eyebrow'))
        box.addWidget(label(poll['question'], 'messageText', True))
        self.options, self.bars = [], []
        self.selection = set()
        for i, option in enumerate(poll['options']):
            control = VoteButton()
            control.setCheckable(True)
            control.clicked.connect(lambda checked=False, index=i:self.choose(index))
            self.options.append(control)
            box.addWidget(control)
            bar = QProgressBar()
            bar.setRange(0, 100); bar.setTextVisible(False); bar.setFixedHeight(6)
            bar.setStyleSheet('QProgressBar {border:0; border-radius:3px; background:#343a46;} QProgressBar::chunk {border-radius:3px; background:#7e96ff;}')
            self.bars.append(bar); box.addWidget(bar)
        self.apply = button('Valider mes choix', 'primary')
        self.apply.setVisible(poll.get('multiple', False))
        self.apply.clicked.connect(lambda:self.voted.emit(sorted(i for i, b in enumerate(self.options) if b.isChecked())))
        box.addWidget(self.apply)
        self.total = label('', 'caption', True)
        box.addWidget(self.total)
        self.update_poll(poll)

    def choose(self, index):
        if not self.poll.get('multiple'):
            self.voted.emit(-1 if self.poll['votes'].get(self.actor) == index else index)

    def update_poll(self, poll):
        self.poll = poll
        for i, control in enumerate(self.options):
            count = sum(i in value if isinstance(value, list) else value == i for value in poll['votes'].values())
            percentage = round(100 * count / len(poll['votes'])) if poll['votes'] else 0
            self.bars[i].setValue(percentage)
            self.bars[i].setAccessibleName(f'{poll["options"][i]} : {count} voix, {percentage} % des votants')
            control.set_full_text(f'{poll["options"][i]} · {count} · {percentage} %')
            own = poll['votes'].get(self.actor, [] if poll.get('multiple') else -1)
            selected = i in own if isinstance(own, list) else own == i
            control.setChecked(selected)
            control.setObjectName('primary' if selected else 'soft')
            control.setEnabled(not poll.get('deleted') and self.actor in poll['audience'])
            control.style().unpolish(control); control.style().polish(control)
        self.apply.setEnabled(not poll.get('deleted') and self.actor in poll['audience'])
        detail = 'plusieurs choix · % des votants' if poll.get('multiple') else 'un choix · reclique pour retirer ta voix'
        self.total.setText(f'{len(poll["votes"])}/{len(poll["audience"])} participant(s) · {detail}')
