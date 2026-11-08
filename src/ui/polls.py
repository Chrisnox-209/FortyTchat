"""Single-choice polls: draft selection, explicit final vote and inline result bars."""
import re
from PySide6.QtCore import Signal, Qt, QSize, QRectF, QRect
from PySide6.QtGui import QPainter, QColor, QPen
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLineEdit, QScrollArea,
                               QWidget, QFrame, QLayout, QAbstractButton, QSizePolicy)
from .widgets import label, button
from .theme import colors


class PollEditor(QDialog):
    submitted = Signal(str, object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Créer un sondage · FortyTchat')
        self.resize(480, 530); self.setMinimumSize(380, 380)
        box = QVBoxLayout(self); box.setContentsMargins(24, 24, 24, 24)
        box.addWidget(label('On choisit ensemble ?', 'title', True))
        box.addWidget(label('Une seule réponse par personne. Le vote devient définitif après validation.', 'muted', True))
        scroll = QScrollArea(); scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        page = QWidget(); form = QVBoxLayout(page)
        form.setSizeConstraint(QLayout.SizeConstraint.SetMinAndMaxSize); form.setSpacing(12)
        self.question = QLineEdit(); self.question.setMaxLength(200)
        self.question.setPlaceholderText('Ta question'); self.question.setAccessibleName('Question du sondage')
        form.addWidget(self.question)
        self.options = []
        for i in range(8):
            field = QLineEdit(); field.setMaxLength(80)
            field.setPlaceholderText(f'Choix {i + 1}'); field.setAccessibleName(f'Choix {i + 1}')
            self.options.append(field); form.addWidget(field)
        scroll.setWidget(page); box.addWidget(scroll, 1)
        self.feedback = label('Remplis au moins deux choix différents.', 'caption', True); box.addWidget(self.feedback)
        submit = button('Publier le sondage', 'primary'); submit.clicked.connect(self.submit); box.addWidget(submit)
        cancel = button('Annuler'); cancel.clicked.connect(self.reject); box.addWidget(cancel)

    def submit(self):
        options = [field.text().strip() for field in self.options if field.text().strip()]
        if not self.question.text().strip() or len(options) < 2 or len({o.casefold() for o in options}) != len(options):
            self.feedback.setText('Écris une question et au moins deux choix différents.'); return
        self.submitted.emit(self.question.text().strip(), options); self.accept()


class PollOption(QAbstractButton):
    """The bar fills the option itself, underneath readable wrapped text."""
    def __init__(self, text, parent=None):
        super().__init__(parent)
        self.full_text, self.percentage, self.results, self.selected = text, 0, False, False
        self.setCheckable(True); self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAccessibleName(text); self.setToolTip(text)
        policy = QSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        policy.setHeightForWidth(True); self.setSizePolicy(policy)
        row = QHBoxLayout(self); row.setContentsMargins(13, 12, 13, 12); row.setSpacing(9)
        self.marker = label('○'); self.marker.setFixedWidth(18)
        wrapped = re.sub(r'\S{19,}', lambda m:'\u200b'.join(m[0][i:i+12] for i in range(0,len(m[0]),12)), text)
        self.title = label(wrapped, wrap=True)
        policy = self.title.sizePolicy(); policy.setHorizontalPolicy(QSizePolicy.Policy.Ignored); self.title.setSizePolicy(policy)
        self.percent = label(''); self.percent.setFixedWidth(47)
        self.percent.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(self.marker); row.addWidget(self.title, 1); row.addWidget(self.percent)
        for child in (self.marker, self.title, self.percent): child.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        from PySide6.QtCore import QTimer
        QTimer.singleShot(0, self.adjust_metrics)

    def heightForWidth(self, width):
        available=max(45,width-26-18-18-self.percent.width())
        return max(52,self.title.heightForWidth(available)+24)

    def adjust_metrics(self):
        self.percent.setFixedWidth(max(48,self.percent.fontMetrics().horizontalAdvance('100 %')+5))
        self.setMinimumHeight(self.heightForWidth(self.width()))
        self.updateGeometry()

    def resizeEvent(self,event):
        super().resizeEvent(event)
        self.adjust_metrics()

    def changeEvent(self,event):
        super().changeEvent(event)
        if hasattr(self,'percent'):
            from PySide6.QtCore import QTimer
            QTimer.singleShot(0,self.adjust_metrics)

    def sizeHint(self): return QSize(320, self.heightForWidth(320))
    def minimumSizeHint(self): return QSize(110,52)

    def set_state(self, selected, results, percentage):
        self.selected, self.results, self.percentage = selected, results, percentage
        self.setChecked(selected)
        self.marker.setText('✓' if selected and results else '●' if selected else '' if results else '○')
        self.percent.setText(f'{percentage} %' if results else '')
        palette = colors()
        self.title.setStyleSheet(f'background:transparent; color:{palette["text"]};')
        self.marker.setStyleSheet(f'background:transparent; color:{palette["accentText"]}; font-weight:700;')
        self.percent.setStyleSheet(f'background:transparent; color:{palette["text"]}; font-weight:700;')
        self.setAccessibleDescription(('Ton vote · ' if selected and results else 'Sélectionné · ' if selected else '') + (f'{percentage} %' if results else 'Non validé'))
        self.update()

    def paintEvent(self, event):
        palette = colors(); painter = QPainter(self); painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        painter.setPen(Qt.PenStyle.NoPen); painter.setBrush(QColor(palette['surface'])); painter.drawRoundedRect(rect, 12, 12)
        from PySide6.QtGui import QPainterPath
        clip = QPainterPath(); clip.addRoundedRect(rect, 12, 12); painter.setClipPath(clip)
        fill = QColor(palette['accent']); fill.setAlpha(150 if self.selected else 72)
        if self.results and self.percentage:
            painter.fillRect(QRectF(rect.x(), rect.y(), rect.width() * self.percentage / 100, rect.height()), fill)
        elif self.selected:
            painter.fillRect(rect, QColor(palette['accentSoft']))
        painter.setClipping(False)
        painter.setPen(QPen(QColor(palette['accent'] if self.selected else palette['border']), 2 if self.selected else 1))
        painter.setBrush(Qt.BrushStyle.NoBrush); painter.drawRoundedRect(rect, 12, 12)


class PollCard(QFrame):
    voted = Signal(object)

    def __init__(self, poll, actor, parent=None):
        super().__init__(parent)
        self.actor, self.poll, self.selection, self.submitting = actor, poll, None, False
        box = QVBoxLayout(self); box.setContentsMargins(0, 0, 0, 0); box.setSpacing(9)
        box.addWidget(label('SONDAGE', 'eyebrow'))
        box.addWidget(label(poll['question'], 'messageText', True))
        self.options = []
        for i, option in enumerate(poll['options']):
            control = PollOption(option, self)
            control.clicked.connect(lambda checked=False, index=i:self.choose(index))
            self.options.append(control); box.addWidget(control)
        self.apply = button('Valider le vote', 'primary'); self.apply.clicked.connect(self.submit); box.addWidget(self.apply)
        self.total = label('', 'caption', True); box.addWidget(self.total)
        self.feedback = label('', 'caption', True); box.addWidget(self.feedback)
        self.update_poll(poll)

    def choose(self, index):
        if self.actor in self.poll['votes'] or self.submitting or self.poll.get('deleted'): return
        self.selection = index
        self.update_poll(self.poll)

    def submit(self):
        if self.selection is None or not self.apply.isEnabled(): return
        self.submitting = True; self.update_poll(self.poll)
        self.voted.emit(self.selection)

    def finish_submit(self, ok):
        if self.actor not in self.poll['votes']:
            self.submitting = False
            self.update_poll(self.poll)
            if not ok: self.feedback.setText('Vote non validé. Réessaie dans un instant.')

    def update_poll(self, poll):
        self.poll = poll
        locked = self.actor in poll['votes']
        if locked:
            own=poll['votes'][self.actor]
            self.selection, self.submitting=(own[0] if own else None) if isinstance(own,list) else own,False
        eligible = self.actor in poll['audience'] and not poll.get('deleted')
        for i, control in enumerate(self.options):
            count = sum(i in value if isinstance(value,list) else value==i for value in poll['votes'].values())
            percentage = round(100 * count / len(poll['votes'])) if poll['votes'] else 0
            control.set_state(self.selection == i, locked or not eligible, percentage)
            control.setEnabled(eligible and not locked and not self.submitting)
        self.apply.setText('Vote validé ✓' if locked else 'Validation en cours…' if self.submitting else 'Valider le vote')
        self.apply.setEnabled(eligible and not locked and not self.submitting and self.selection is not None)
        self.total.setText(f'{len(poll["votes"])}/{len(poll["audience"])} participant(s) · une réponse par personne')
        self.feedback.setText('Ton vote est définitif.' if locked else 'Validation en cours…' if self.submitting else 'Choisis une réponse, puis valide ton vote.')
