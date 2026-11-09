"""A compact entry point and one independent window for popup answers."""
from copy import deepcopy

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QWidget, QDialog, QVBoxLayout, QHBoxLayout, QFrame,
                               QScrollArea, QLineEdit, QComboBox, QCheckBox, QSizePolicy)
from .widgets import button, label
from .animated import AnimatedEmojiChoice
from ..features.cards import valid_responses, valid_close_label, card_text, MAX_TITLE


def simple_responses():
    return {'buttons': [dict(label='Oui', answer='accepted', title='Oui', text='', emoji='1f44d'),
                        dict(label='Non', answer='declined', title='Non', text='', emoji='1f44e')], 'free': False}


class ResponseRow(QFrame):
    changed = Signal()
    removed = Signal()

    def __init__(self, item, parent=None):
        super().__init__(parent)
        self.item = deepcopy(item)
        box = QVBoxLayout(self);box.setContentsMargins(10,10,10,10);box.setSpacing(8)
        top = QHBoxLayout()
        self.kind = QComboBox()
        for name, value in [('Oui', 'accepted'), ('Non', 'declined'), ('Personnalisée', 'custom')]:
            self.kind.addItem(name, value)
        self.kind.setCurrentIndex(self.kind.findData(item['answer']))
        self.kind.setAccessibleName('Type de réponse')
        self.kind.setMinimumHeight(max(42, self.kind.fontMetrics().height()+18))
        top.addWidget(self.kind, 1)
        self.remove = button('×', 'flat', 'Retirer cette réponse', 36)
        self.remove.setAccessibleName('Retirer cette réponse')
        self.remove.clicked.connect(self.removed)
        top.addWidget(self.remove);box.addLayout(top)
        text_row = QHBoxLayout()
        self.text = QLineEdit(item['label']);self.text.setMaxLength(32)
        self.text.setMinimumHeight(max(42, self.text.fontMetrics().height()+18))
        self.text.setPlaceholderText('Écris ta réponse…')
        self.text.setAccessibleName('Texte de la réponse et du bouton')
        text_row.addWidget(self.text,1)
        self.emoji_button = button('', 'soft', 'Changer l’emoji de cette réponse', 44)
        self.emoji_button.setAccessibleName('Changer l’emoji de cette réponse')
        text_row.addWidget(self.emoji_button);box.addLayout(text_row)
        self.emojis = AnimatedEmojiChoice();self.emojis.hide()
        box.addWidget(self.emojis,0,Qt.AlignmentFlag.AlignHCenter)
        self.select_emoji(item['emoji'])
        self.emoji_button.clicked.connect(lambda:self.emojis.setVisible(not self.emojis.isVisible()))
        self.emojis.chosen.connect(self.select_emoji)
        self.kind.currentIndexChanged.connect(self.change_kind)
        self.text.textChanged.connect(self.change_text)

    def select_emoji(self, code):
        self.item['emoji'] = code
        self.emojis.set_code(code)
        self.emoji_button.setIcon(self.emojis.buttons[code].icon())
        self.emojis.hide();self.changed.emit()

    def change_text(self, text):
        # Untouched older templates retain their richer title/body. Editing the
        # simple field deliberately makes the button and returned text identical.
        self.item.update(label=text.strip(), title=text.strip(), text='')
        self.changed.emit()

    def change_kind(self, index):
        kind = self.kind.itemData(index)
        previous = self.item['answer']
        self.item['answer'] = kind
        if not self.text.text().strip() or self.text.text() in ('Oui','Non','Autre'):
            self.text.setText({'accepted':'Oui','declined':'Non','custom':''}[kind])
        if previous != kind:
            self.select_emoji('1f44e' if kind == 'declined' else '1f44d')
        self.changed.emit()


class ResponseSettings(QDialog):
    applied = Signal(object)

    def __init__(self, title, policy, close_label='Fermer', parent=None):
        super().__init__(parent)
        self.setWindowTitle('Paramétrer la réponse · FortyTchat')
        self.setMinimumSize(420,400)
        area = self.screen().availableGeometry()
        self.resize(min(490,max(420,area.width()-32)),min(720,max(400,area.height()-60)))
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.rows = []
        outer = QVBoxLayout(self);outer.setContentsMargins(20,20,20,18);outer.setSpacing(12)
        outer.addWidget(label('Quelle réponse proposer ?', 'title', True))
        scroll = QScrollArea();scroll.setWidgetResizable(True);scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        page = QWidget();box = QVBoxLayout(page);box.setContentsMargins(0,0,12,8);box.setSpacing(12)
        box.addWidget(label('Titre de la popup', 'caption'))
        self.title = QLineEdit(title);self.title.setMaxLength(MAX_TITLE)
        self.title.setMinimumHeight(max(42,self.title.fontMetrics().height()+18));box.addWidget(self.title)
        self.close_only = QCheckBox('Un seul bouton, sans réponse')
        self.close_only.setChecked(not policy['buttons'] and not policy['free'])
        box.addWidget(self.close_only)
        self.answers = QWidget();answers_box = QVBoxLayout(self.answers);answers_box.setContentsMargins(0,0,0,0);answers_box.setSpacing(10)
        self.rows_box = QVBoxLayout();self.rows_box.setSpacing(10);answers_box.addLayout(self.rows_box)
        self.add_button = button('+ Ajouter une réponse','soft')
        self.add_button.clicked.connect(self.add_choice);answers_box.addWidget(self.add_button)
        answers_box.addWidget(label('Choisis Oui, Non ou Personnalisée, puis écris le texte du bouton. Ce texte sera aussi ta réponse reçue.', 'caption', True))
        self.free = QCheckBox('Autoriser aussi le destinataire à écrire sa réponse')
        self.free.setChecked(policy['free']);self.free.setVisible(policy['free'])
        answers_box.addWidget(self.free);box.addWidget(self.answers)
        box.addWidget(label('Texte du bouton pour fermer', 'caption'))
        self.close_text = QLineEdit(close_label);self.close_text.setMaxLength(32)
        self.close_text.setPlaceholderText('Fermer');self.close_text.setMinimumHeight(max(42,self.close_text.fontMetrics().height()+18))
        box.addWidget(self.close_text)
        box.addWidget(label('Ce bouton ferme la popup sans rien renvoyer. Les anciennes versions affichent simplement « Fermer ».','caption',True))
        box.addStretch();scroll.setWidget(page);outer.addWidget(scroll,1)
        self.feedback = label('', 'caption', True);outer.addWidget(self.feedback)
        controls = QHBoxLayout();cancel=button('Annuler');cancel.clicked.connect(self.reject)
        cancel.setAutoDefault(False)
        self.apply_button=button('Valider','primary');self.apply_button.clicked.connect(self.save)
        self.apply_button.setDefault(True)
        controls.addWidget(cancel);controls.addWidget(self.apply_button,1);outer.addLayout(controls)
        for item in policy['buttons']:self.append_row(item)
        self.title.textChanged.connect(self.refresh)
        self.close_text.textChanged.connect(self.refresh)
        self.close_only.toggled.connect(self.refresh)
        self.free.toggled.connect(self.refresh)
        self.refresh()

    def append_row(self, item):
        row=ResponseRow(item,self)
        self.rows.append(row);self.rows_box.addWidget(row)
        row.changed.connect(self.refresh)
        row.removed.connect(lambda:self.remove_choice(row))
        return row

    def add_choice(self):
        if len(self.rows)>=3 or self.close_only.isChecked():return
        row=self.append_row(dict(label='',answer='custom',title='',text='',emoji='1f44d'))
        self.refresh();row.text.setFocus()
        scroll=self.findChild(QScrollArea);scroll.ensureWidgetVisible(row)

    def remove_choice(self,row):
        self.rows.remove(row);self.rows_box.removeWidget(row);row.hide();row.deleteLater();self.refresh()

    def policy(self):
        return {'buttons':[],'free':False} if self.close_only.isChecked() else {
            'buttons':[deepcopy(row.item) for row in self.rows],'free':self.free.isChecked()}

    def refresh(self,*args):
        self.answers.setVisible(not self.close_only.isChecked())
        self.add_button.setEnabled(len(self.rows)<3)
        self.add_button.setText('+ Ajouter une réponse' if len(self.rows)<3 else '3 réponses maximum')
        policy=self.policy();title=self.title.text().strip();closing=self.close_text.text().strip() or 'Fermer'
        issue=''
        if not card_text(title,MAX_TITLE) or '\n' in title:issue='Ajoute un titre à ta popup.'
        elif not valid_close_label(closing):issue='Écris le texte du bouton pour fermer.'
        elif not self.close_only.isChecked() and not policy['buttons'] and not policy['free']:
            issue='Ajoute une réponse ou choisis seulement le bouton pour fermer.'
        elif not valid_responses(policy):
            issue='Écris un texte différent pour chaque réponse.'
        self.feedback.setText(issue);self.apply_button.setEnabled(not issue)

    def save(self):
        self.refresh()
        if not self.apply_button.isEnabled():return
        self.applied.emit(dict(title=self.title.text().strip(), responses=self.policy(), close_label=self.close_text.text().strip() or 'Fermer'))
        self.accept()


class ResponseChoices(QWidget):
    """Main editor: one button and one summary, with no embedded form."""
    def __init__(self, templates, parent=None, *, enabled=True):
        super().__init__(parent)
        self.templates=templates;self.explicit=enabled;self._policy=simple_responses()
        self.close_label='Fermer';self.editor=None
        box=QVBoxLayout(self);box.setContentsMargins(0,0,0,0);box.setSpacing(8)
        self.configure_button=button('Paramétrer la réponse','soft')
        self.configure_button.setEnabled(enabled)
        self.configure_button.clicked.connect(self.open_settings);box.addWidget(self.configure_button)
        self.summary=label('', 'caption', True);box.addWidget(self.summary)
        self.refresh()

    def policy(self):return deepcopy(self._policy)

    def set_policy(self, value, *, explicit=True, close_label='Fermer'):
        self._policy=deepcopy(value);self.explicit=explicit;self.close_label=close_label;self.refresh()

    def refresh(self):
        if not self.explicit:text='Réponses classiques : Oui / Non / réponse libre.'
        elif not self._policy['buttons'] and not self._policy['free']:text=f'Un bouton « {self.close_label} » · aucune réponse envoyée.'
        else:
            text=' / '.join(item['label'] for item in self._policy['buttons'])
            if self._policy['free']:text+=(' / ' if text else '')+'réponse libre'
        self.summary.setText(text)

    def open_settings(self):
        if self.editor:
            self.editor.raise_();self.editor.activateWindow();return
        owner=self.window()
        editor=ResponseSettings(owner.title.text(),self.policy(),self.close_label,owner)
        self.editor=editor
        def apply(value):
            owner.title.setText(value['title'])
            self.set_policy(value['responses'],close_label=value['close_label'])
        def done(result):
            self.editor=None;editor.deleteLater()
        editor.applied.connect(apply);editor.finished.connect(done);editor.show()

    def close_editor(self):
        if self.editor:self.editor.reject()
