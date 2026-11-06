"""Reusable local notification and response editor with a real GIF preview."""
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QWidget, QScrollArea,
                               QFrame, QLineEdit, QPlainTextEdit, QComboBox, QSizePolicy, QCheckBox)

from .animated import AnimatedEmoji, AnimatedEmojiChoice
from .widgets import button, label
from ..features.cards import MAX_TITLE, MAX_TEXT, valid_card, valid_invite, valid_responses, default_responses
from .response_choices import ResponseChoices


class CardEditor(QDialog):
    submitted = Signal(object)

    def __init__(self, nickname, templates, reply=False, parent=None, manage=False, allow_choices=True, choice=None):
        super().__init__(parent)
        self.setWindowTitle(('Mes modèles' if manage else 'Ma réponse' if reply else 'Ma notification') + ' · FortyTchat')
        self.setMinimumSize(430, 440)
        area = self.screen().availableGeometry()
        self.resize(min(540, max(430, area.width() - 40)), min(760, max(440, area.height() - 60)))
        self.templates, self.kind = templates, 'reply' if reply else 'invite'
        self.allow_choices, self.choice_mode = allow_choices, choice is not None
        self.template_id, self.emoji = '', '1f44d'
        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 20, 20, 18)
        outer.setSpacing(12)
        outer.addWidget(label('Ta réponse, à ta façon.' if reply else 'Fais passer le message.', 'title', True))
        outer.addWidget(label('Tes notifications et réponses réutilisables.' if manage else f'Pour {nickname} · sans son', 'caption', True))
        if manage:
            self.types = QComboBox()
            self.types.addItem('Modèles de notification', 'invite')
            self.types.addItem('Modèles de réponse', 'reply')
            self.types.currentIndexChanged.connect(self.change_kind)
            outer.addWidget(self.types)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        page = QWidget()
        box = QVBoxLayout(page)
        box.setContentsMargins(0, 4, 12, 8)
        box.setSpacing(12)
        self.models = QComboBox()
        self.models.setMinimumContentsLength(15)
        self.models.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.models.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.models.currentIndexChanged.connect(self.load_template)
        box.addWidget(label('MES MODÈLES', 'eyebrow'))
        box.addWidget(self.models)
        if self.choice_mode:
            self.setWindowTitle('Réponse proposée · FortyTchat')
            box.addWidget(label('LIBELLÉ DU BOUTON', 'eyebrow'))
            self.choice_label = QLineEdit(choice['label'])
            self.choice_label.setMaxLength(32)
            box.addWidget(self.choice_label)
            self.choice_type = QComboBox()
            for name, value in [('Oui · réponse bleue', 'accepted'), ('Non · réponse rouge', 'declined'), ('Autre · réponse bleue', 'custom')]:
                self.choice_type.addItem(name, value)
            self.choice_type.setCurrentIndex(self.choice_type.findData(choice['answer']))
            box.addWidget(self.choice_type)
        self.title = QLineEdit('Ma réponse' if reply else 'On fait une pause ?')
        self.title.setMaxLength(MAX_TITLE)
        self.title.setPlaceholderText('Titre de la notification')
        box.addWidget(label('TITRE', 'eyebrow'))
        box.addWidget(self.title)
        self.text = QPlainTextEdit()
        self.text.setPlaceholderText('Écris ton petit message…')
        self.text.setMinimumHeight(95)
        self.text.setMaximumHeight(125)
        self.text.textChanged.connect(self.update_text)
        box.addWidget(label('TON TEXTE', 'eyebrow'))
        box.addWidget(self.text)
        self.counter = label(f'0/{MAX_TEXT}', 'caption')
        box.addWidget(self.counter)
        box.addWidget(label('CHOISIS TON EMOJI ANIMÉ', 'eyebrow'))
        self.choices = AnimatedEmojiChoice()
        self.choices.chosen.connect(self.select_emoji)
        box.addWidget(self.choices, 0, Qt.AlignmentFlag.AlignHCenter)
        preview = QHBoxLayout()
        self.animation = AnimatedEmoji(self.emoji, 100)
        preview.addWidget(self.animation)
        preview.addWidget(label('L’animation choisie sera affichée\ndans la notification.', 'caption', True), 1)
        box.addLayout(preview)
        self.use_responses = QCheckBox('Choisir les réponses proposées au destinataire')
        self.use_responses.setChecked(allow_choices)
        self.use_responses.setEnabled(allow_choices)
        self.use_responses.setVisible(self.kind == 'invite')
        box.addWidget(self.use_responses)
        self.responses = ResponseChoices(templates, self)
        self.responses.setVisible(self.kind == 'invite' and self.use_responses.isChecked())
        self.use_responses.toggled.connect(lambda active: self.responses.setVisible(active and self.kind == 'invite'))
        box.addWidget(self.responses)
        if not allow_choices:
            box.addWidget(label('Les réponses au choix nécessitent FortyTchat 1.7.0 chez le destinataire.', 'caption', True))
        models_row = QHBoxLayout()
        self.save_button = button('Enregistrer ce modèle')
        self.save_button.clicked.connect(self.save_template)
        self.remove_button = button('Supprimer', 'flat')
        self.remove_button.clicked.connect(self.remove_template)
        models_row.addWidget(self.save_button)
        models_row.addWidget(self.remove_button)
        box.addLayout(models_row)
        box.addWidget(label('Les modèles restent dans ton profil.\nLes notifications envoyées restent éphémères.', 'caption', True))
        scroll.setWidget(page)
        outer.addWidget(scroll, 1)
        self.feedback = label(templates.error, 'caption', True)
        outer.addWidget(self.feedback)
        controls = QHBoxLayout()
        cancel = button('Annuler')
        cancel.clicked.connect(self.reject)
        preview_button = button('Aperçu')
        preview_button.clicked.connect(self.preview)
        self.send_button = button('Terminé' if manage else 'Répondre' if reply else 'Envoyer', 'primary')
        self.send_button.clicked.connect(self.accept if manage else self.send)
        controls.addWidget(cancel)
        controls.addWidget(preview_button)
        controls.addWidget(self.send_button, 1)
        outer.addLayout(controls)
        self.preview_window = None
        self.sending = False
        self.refresh_models()
        if choice is not None:
            self.title.setText(choice['title'])
            self.text.setPlainText(choice['text'])
            self.select_emoji(choice['emoji'])

    def change_kind(self, index):
        self.kind = self.types.itemData(index)
        self.template_id = ''
        self.title.setText('Ma réponse' if self.kind == 'reply' else 'On fait une pause ?')
        self.text.clear()
        self.select_emoji('1f44d')
        self.use_responses.setVisible(self.kind == 'invite')
        self.responses.setVisible(self.kind == 'invite' and self.use_responses.isChecked())
        self.refresh_models()

    def payload(self):
        result = {'title': self.title.text().strip(), 'text': self.text.toPlainText().strip(), 'emoji': self.emoji}
        if self.kind == 'invite' and self.use_responses.isChecked():
            result['responses'] = self.responses.policy()
        return result

    def update_text(self):
        value = self.text.toPlainText()
        if len(value) > MAX_TEXT:
            cursor = self.text.textCursor()
            position = min(cursor.position(), MAX_TEXT)
            self.text.blockSignals(True)
            self.text.setPlainText(value[:MAX_TEXT])
            cursor = self.text.textCursor()
            cursor.setPosition(position)
            self.text.setTextCursor(cursor)
            self.text.blockSignals(False)
        self.counter.setText(f'{len(self.text.toPlainText())}/{MAX_TEXT}')

    def select_emoji(self, code):
        self.emoji = code
        self.choices.set_code(code)
        self.animation.set_code(code)

    def refresh_models(self):
        self.models.blockSignals(True)
        self.models.clear()
        self.models.addItem('Nouveau modèle', '')
        for item in self.templates.items:
            if item['kind'] == self.kind:
                self.models.addItem(item['title'], item['id'])
        self.models.setCurrentIndex(max(0, self.models.findData(self.template_id)))
        self.models.blockSignals(False)
        self.remove_button.setEnabled(bool(self.template_id))
        self.save_button.setText('Enregistrer les modifications' if self.template_id else 'Enregistrer ce modèle')

    def load_template(self, index):
        self.template_id = self.models.itemData(index) or ''
        item = next((item for item in self.templates.items if item['id'] == self.template_id), None)
        if item:
            self.title.setText(item['title'])
            self.text.setPlainText(item['text'])
            self.select_emoji(item['emoji'])
            if self.kind == 'invite':
                self.use_responses.setChecked('responses' in item)
                self.responses.set_policy(item.get('responses', default_responses()))
        self.remove_button.setEnabled(bool(item))
        self.save_button.setText('Enregistrer les modifications' if item else 'Enregistrer ce modèle')

    def save_template(self):
        try:
            self.template_id = self.templates.save(self.payload(), self.kind, self.template_id)
            self.refresh_models()
            self.feedback.setText('Modèle enregistré dans ton profil.')
        except (OSError, ValueError) as exc:
            self.feedback.setText(str(exc))

    def remove_template(self):
        try:
            self.templates.remove(self.template_id)
            self.template_id = ''
            self.refresh_models()
            self.feedback.setText('Modèle supprimé.')
        except OSError:
            self.feedback.setText('Impossible de supprimer ce modèle.')

    def send(self):
        if self.sending:
            return
        payload = self.payload()
        if not (valid_invite if self.kind == 'invite' else valid_card)(payload):
            self.feedback.setText('Choisis un titre et une animation valides.')
            return
        if 'responses' in payload and not self.allow_choices:
            self.feedback.setText('Actualise le destinataire en 1.7.0 pour utiliser ces choix de réponse.')
            return
        if self.choice_mode:
            payload = {**payload, 'label': self.choice_label.text().strip(), 'answer': self.choice_type.currentData()}
            if not valid_responses({'buttons': [payload], 'free': False}):
                self.feedback.setText('Choisis un libellé de bouton et une réponse valides.')
                return
        self.sending = True
        self.send_button.setEnabled(False)
        self.submitted.emit(payload)
        if self.isVisible():
            self.accept()

    def preview(self):
        from .break_popup import CardPreviewPopup
        payload = self.payload()
        if not (valid_invite if self.kind == 'invite' else valid_card)(payload):
            self.feedback.setText('Complète le titre pour afficher l’aperçu.')
            return
        if self.preview_window:
            self.preview_window.close()
            self.preview_window.deleteLater()
        self.preview_window = CardPreviewPopup(payload, self, reply=self.kind == 'reply')
        self.preview_window.show()

    def done(self, result):
        self.responses.close_editor()
        if self.preview_window:
            self.preview_window.close()
            self.preview_window.deleteLater()
            self.preview_window = None
        super().done(result)
