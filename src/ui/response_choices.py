"""Author-selected buttons; closing a configured card sends no answer."""
from copy import deepcopy
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QCheckBox
from .widgets import button, label
from .social import ElidedLabel
from ..features.cards import default_responses


class ResponseChoices(QWidget):
    def __init__(self, templates, parent=None):
        super().__init__(parent)
        self.templates, self.buttons, self.editor = templates, default_responses()['buttons'], None
        box = QVBoxLayout(self)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(10)
        self.close_only = QCheckBox('Fermer uniquement · aucune réponse attendue')
        self.close_only.toggled.connect(self.refresh)
        box.addWidget(self.close_only)
        self.rows = QVBoxLayout()
        box.addLayout(self.rows)
        self.add_button = button('Ajouter une réponse', 'soft')
        self.add_button.clicked.connect(self.add_choice)
        box.addWidget(self.add_button)
        self.free = QCheckBox('Autoriser aussi une réponse libre avec texte et emoji')
        self.free.setChecked(True)
        box.addWidget(self.free)
        box.addWidget(label('Trois boutons maximum. Modifie leur libellé, leur couleur et la réponse renvoyée. « Fermer » reste toujours disponible et ne renvoie rien.', 'caption', True))
        self.refresh()

    def policy(self):
        return {'buttons': [], 'free': False} if self.close_only.isChecked() else {
            'buttons': deepcopy(self.buttons), 'free': self.free.isChecked()}

    def set_policy(self, value):
        self.buttons = deepcopy(value['buttons'])
        self.free.setChecked(value['free'])
        self.close_only.setChecked(not self.buttons and not value['free'])
        self.refresh()

    def refresh(self):
        while self.rows.count():
            widget = self.rows.takeAt(0).widget()
            if widget:
                widget.hide(); widget.deleteLater()
        closed = self.close_only.isChecked()
        for index, item in enumerate(self.buttons):
            row = QWidget()
            layout = QHBoxLayout(row)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.addWidget(ElidedLabel(item['label'], 'messageName'), 1)
            modify = button('Modifier', 'soft')
            modify.clicked.connect(lambda checked=False, i=index: self.edit_choice(i))
            remove = button('×', 'flat', 'Retirer cette réponse', 32)
            remove.clicked.connect(lambda checked=False, i=index: self.remove_choice(i))
            layout.addWidget(modify); layout.addWidget(remove)
            row.setEnabled(not closed)
            self.rows.addWidget(row)
        self.add_button.setEnabled(not closed and len(self.buttons) < 3)
        self.free.setEnabled(not closed)

    def add_choice(self):
        if len(self.buttons) >= 3:
            return
        self.edit_choice(None)

    def remove_choice(self, index):
        self.buttons.pop(index)
        self.refresh()

    def edit_choice(self, index):
        if self.editor:
            self.editor.raise_()
            return
        from .card_editor import CardEditor
        choice = self.buttons[index] if index is not None else {'label': 'Autre', 'answer': 'custom',
                 'title': 'Dans cinq minutes !', 'text': '', 'emoji': '1f44d'}
        editor = CardEditor('', self.templates, reply=True, parent=self.window(), choice=choice)
        self.editor = editor
        editor.setWindowModality(Qt.WindowModality.WindowModal)
        def save(payload):
            if index is None:
                self.buttons.append(payload)
            else:
                self.buttons[index] = payload
            self.refresh()
        def done(result):
            self.editor = None
            editor.deleteLater()
        editor.submitted.connect(save)
        editor.finished.connect(done)
        editor.show()

    def close_editor(self):
        if self.editor:
            self.editor.close()
