"""Safe text bubbles with highlighted mentions and selectable/copyable code."""
import html
import re
from PySide6.QtCore import Qt
from PySide6.QtGui import QFontDatabase, QSyntaxHighlighter, QTextCharFormat, QColor
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QPlainTextEdit, QApplication, QDialog, QLineEdit
from .widgets import label, button
from ..features.text import text_parts, MENTION
from ..features.emojis import emoji_fallback


class CodeHighlighter(QSyntaxHighlighter):
    def highlightBlock(self, text):
        for pattern, color in ((r'\b(?:def|class|return|if|else|elif|for|while|import|from|as|int|char|void|struct|const|include|printf|echo|then|fi|do|done|True|False|None|break|continue)\b', '#c8acff'),
                               (r'\b\d+(?:\.\d+)?\b', '#facb8e'),
                               (r'"[^"\n]*"|\x27[^\x27\n]*\x27', '#a6d6ad'),
                               (r'//.*$|#(?!include\b).*$', '#8899ad')):
            fmt = QTextCharFormat(); fmt.setForeground(QColor(color))
            for match in re.finditer(pattern, text):
                # Qt offsets are UTF-16, unlike Python indexes.
                start = len(text[:match.start()].encode('utf-16-le')) // 2
                length = len(match[0].encode('utf-16-le')) // 2
                self.setFormat(start, length, fmt)


class MessageText(QWidget):
    def __init__(self, text, mentioned=False, parent=None):
        super().__init__(parent)
        box = QVBoxLayout(self); box.setContentsMargins(0, 0, 0, 0); box.setSpacing(8)
        if mentioned: box.addWidget(label('@ Tu es mentionné·e', 'caption'))
        for kind, language, body in text_parts(text):
            if kind == 'code':
                row = QHBoxLayout(); row.addWidget(label(language or 'Code', 'caption')); row.addStretch()
                copy = button('Copier', 'soft'); copy.clicked.connect(lambda checked=False, value=body:QApplication.clipboard().setText(value))
                row.addWidget(copy); box.addLayout(row)
                code = QPlainTextEdit(); code.setReadOnly(True); code.setPlainText(body)
                code.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
                code.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
                code.setFixedHeight(min(250, max(70, (body.count('\n') + 1) * code.fontMetrics().lineSpacing() + 30)))
                code.setStyleSheet('QPlainTextEdit {background:#171c25; color:#e4e9f3; border:1px solid #3b4352; border-radius:9px; padding:8px;}')
                code.highlighter = CodeHighlighter(code.document())
                box.addWidget(code)
            else:
                rendered, missing = emoji_fallback(body.strip('\n'), self.font())
                chunks, offset = [], 0
                for match in MENTION.finditer(rendered):
                    chunks.extend((html.escape(rendered[offset:match.start()]), '<span style="color:#aabaff;font-weight:600;">' + html.escape(match[0]) + '</span>'))
                    offset = match.end()
                chunks.append(html.escape(rendered[offset:]))
                text_label = label('', 'messageText', True)
                if MENTION.search(rendered):
                    text_label.setTextFormat(Qt.TextFormat.RichText)
                    text_label.setText(''.join(chunks).replace('\n', '<br>'))
                else:
                    text_label.setText(rendered)
                text_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
                if missing: text_label.setToolTip('Certains emojis sont remplacés par leur nom.')
                box.addWidget(text_label)


class CodeEditor(QDialog):
    def __init__(self, input_widget):
        super().__init__(input_widget)
        self.setWindowTitle('Insérer un bloc de code · FortyTchat'); self.resize(500, 410)
        box = QVBoxLayout(self)
        box.addWidget(label('Un bout de code à partager.', 'title', True))
        self.language = QLineEdit(); self.language.setPlaceholderText('Langage (facultatif) : c, python, sh…'); self.language.setMaxLength(24)
        box.addWidget(self.language)
        self.code = QPlainTextEdit(); self.code.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)); box.addWidget(self.code, 1)
        self.feedback = label('', 'caption', True); box.addWidget(self.feedback)
        insert = button('Insérer dans le message', 'primary'); box.addWidget(insert)
        def submit():
            language = re.sub(r'[^A-Za-z0-9_+#.-]', '', self.language.text())
            value = '\n```' + language + '\n' + self.code.toPlainText() + '\n```\n'
            if len(value) + len(input_widget.toPlainText()) > 2000:
                self.feedback.setText('Le message complet doit rester sous 2 000 caractères.'); return
            input_widget.insertPlainText(value); self.accept()
        insert.clicked.connect(submit)
