"""Selectable web links, rendered as plain text with explicit anchor formats."""
from math import ceil
from PySide6.QtCore import QEvent, QSize, Qt, QUrl
from PySide6.QtGui import QColor, QDesktopServices, QFont, QTextCharFormat, QTextCursor, QTextOption
from PySide6.QtWidgets import QApplication, QFrame, QMenu, QSizePolicy, QTextBrowser
from ..features.emojis import emoji_fallback
from ..features.links import normalize_web_url, web_links
from ..features.text import MENTION
from .theme import colors


class LinkText(QTextBrowser):
    def __init__(self, text, parent=None):
        super().__init__(parent)
        self.raw_text = text
        self._viewport_width = -1
        self.setObjectName('messageText')
        self.setAccessibleName('Message avec liens')
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setReadOnly(True)
        self.setOpenLinks(False);self.setOpenExternalLinks(False)
        self.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction | Qt.TextInteractionFlag.TextSelectableByKeyboard)
        self.setWordWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setStyleSheet('QTextBrowser#messageText {background:transparent; border:0; padding:0;}')
        policy = QSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        policy.setHeightForWidth(True);self.setSizePolicy(policy)
        self.anchorClicked.connect(self.open_link)
        self.highlighted.connect(lambda url:self.setToolTip(url.toString()))
        self.rebuild()

    def rebuild(self):
        palette = colors();document = self.document()
        previous = self.textCursor()
        anchor, position = previous.anchor(), previous.position()
        document.clear();document.setDocumentMargin(0);document.setDefaultFont(self.font())
        option = document.defaultTextOption();option.setWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        document.setDefaultTextOption(option)
        cursor = QTextCursor(document)
        plain = QTextCharFormat();plain.setForeground(QColor(palette['text']))
        mention = QTextCharFormat(plain);mention.setForeground(QColor(palette['accentText']));mention.setFontWeight(QFont.Weight.DemiBold)
        link = QTextCharFormat(plain);link.setForeground(QColor(palette['accentText']));link.setFontUnderline(True);link.setAnchor(True)
        missing = False

        def insert_plain(text):
            nonlocal missing
            rendered, fallback = emoji_fallback(text, self.font());missing |= fallback
            offset = 0
            for match in MENTION.finditer(rendered):
                cursor.insertText(rendered[offset:match.start()], plain)
                cursor.insertText(match[0], mention);offset = match.end()
            cursor.insertText(rendered[offset:], plain)

        offset = 0
        for start, end, url in web_links(self.raw_text):
            insert_plain(self.raw_text[offset:start])
            link.setAnchorHref(url);cursor.insertText(self.raw_text[start:end], link)
            offset = end
        insert_plain(self.raw_text[offset:])
        previous.setPosition(min(anchor, document.characterCount()-1))
        previous.setPosition(min(position, document.characterCount()-1), QTextCursor.MoveMode.KeepAnchor)
        self.setTextCursor(previous)
        self.setToolTip('Certains emojis sont remplacés par leur nom.' if missing else '')
        self.updateGeometry()

    def heightForWidth(self, width):
        document = self.document().clone()
        document.setTextWidth(max(1, width))
        return max(self.fontMetrics().height(), ceil(document.size().height())) + 2

    def sizeHint(self):
        return QSize(240, self.heightForWidth(240))

    def minimumSizeHint(self):
        return QSize(0, self.fontMetrics().height()+2)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        width = self.viewport().width()
        if width != self._viewport_width:
            self._viewport_width = width;self.updateGeometry()

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() in (QEvent.Type.StyleChange, QEvent.Type.FontChange) and hasattr(self, 'raw_text'):
            self.rebuild()

    def wheelEvent(self, event):
        event.ignore()  # The surrounding conversation owns scrolling.

    def loadResource(self, kind, name):
        return None

    def open_link(self, value):
        value = value.toString(QUrl.ComponentFormattingOption.FullyEncoded) if isinstance(value, QUrl) else value
        url = normalize_web_url(value)
        return bool(url and QDesktopServices.openUrl(QUrl(url)))

    def copy_link(self, value):
        url = normalize_web_url(value)
        if not url:return False
        QApplication.clipboard().setText(url);return True

    def link_menu(self, position, *, keyboard=False):
        menu = QMenu(self)
        value = self.textCursor().charFormat().anchorHref() if keyboard else self.anchorAt(position)
        url = normalize_web_url(value)
        if url:
            menu.addAction('Ouvrir le lien', lambda:self.open_link(url))
            menu.addAction('Copier le lien', lambda:self.copy_link(url))
            menu.addSeparator()
        copy = menu.addAction('Copier la sélection', self.copy)
        copy.setEnabled(self.textCursor().hasSelection())
        menu.addAction('Tout sélectionner', self.selectAll)
        return menu

    def contextMenuEvent(self, event):
        menu = self.link_menu(event.pos(), keyboard=event.reason() == event.Reason.Keyboard)
        menu.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        menu.popup(event.globalPos());event.accept()
