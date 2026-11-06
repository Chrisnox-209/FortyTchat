"""Offline Unicode/CLDR catalogue with a virtualized, searchable emoji grid."""
from collections import defaultdict
from functools import lru_cache
import json
from pathlib import Path
import unicodedata

from PySide6.QtCore import Qt, Signal, QAbstractListModel, QModelIndex, QSize, QTimer
from PySide6.QtGui import QFont, QFontMetrics, QFontDatabase, QPainter, QColor
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLineEdit, QComboBox,
                               QListView, QStyledItemDelegate, QStyle, QSizePolicy)
from ..ui.widgets import label
from ..ui.theme import colors

EXTRA_WORDS = {'☕': 'cafe coffee pause', '🐧': 'linux 42 pingouin penguin',
               '💻': 'code coding ordinateur cluster', '❤️': 'coeur amour love heart',
               '😂': 'mdr lol rire laugh', '👍': 'oui yes ok super pouce', '⚡': 'wizz eclair'}


def normalize(value):
    value = value.casefold().replace('œ', 'oe').replace('’', "'")
    return ''.join(c for c in unicodedata.normalize('NFKD', value) if not unicodedata.combining(c))


@lru_cache(maxsize=1)
def catalogue():
    path = Path(__file__).resolve().parents[2] / 'assets/emoji/catalog.json'
    rows = json.loads(path.read_text(encoding='utf-8'))['entries']
    return tuple((glyph, category, french, english,
                  normalize(' '.join((french, english, keywords, EXTRA_WORDS.get(glyph, '')))))
                 for glyph, category, french, english, keywords in rows)


def search_emojis(query='', category=''):
    words = normalize(query).split()
    return [entry for entry in catalogue() if (not category or entry[1] == category)
            and (not words or all(word in entry[4] for word in words) or entry[0] == query)]


class EmojiModel(QAbstractListModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.entries = []

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.entries)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or not 0 <= index.row() < len(self.entries):
            return None
        entry = self.entries[index.row()]
        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.UserRole):
            return entry[0]
        if role == Qt.ItemDataRole.ToolTipRole:
            return f'{entry[2]}\n{entry[3]}'
        if role == Qt.ItemDataRole.AccessibleTextRole:
            return entry[2]
        return None

    def replace(self, entries):
        self.beginResetModel()
        self.entries = entries
        self.endResetModel()


class EmojiDelegate(QStyledItemDelegate):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.font = QFont('Noto Color Emoji' if 'Noto Color Emoji' in QFontDatabase.families() else 'DejaVu Sans')
        self.font.setPixelSize(27)
        self.metrics = QFontMetrics(self.font)
        self.supported = {}

    def paint(self, painter, option, index):
        glyph = index.data(Qt.ItemDataRole.UserRole)
        supported = self.supported.get(glyph)
        if supported is None:
            supported = all(self.metrics.inFontUcs4(ord(c)) for c in glyph if ord(c) not in (0xfe0f, 0x200d))
            self.supported[glyph] = supported
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        c = colors()
        rect = option.rect.adjusted(2, 2, -2, -2)
        if option.state & (QStyle.StateFlag.State_Selected | QStyle.StateFlag.State_MouseOver):
            painter.setBrush(QColor(c['hover']))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRoundedRect(rect, 12, 12)
        painter.setPen(QColor(c['text']))
        if supported:
            painter.setFont(self.font)
            text = glyph
        else:
            font = QFont('DejaVu Sans')
            font.setPixelSize(9)
            painter.setFont(font)
            text = index.data(Qt.ItemDataRole.AccessibleTextRole)
            text = painter.fontMetrics().elidedText(text, Qt.TextElideMode.ElideRight, rect.width()-4)
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)
        painter.restore()

    def sizeHint(self, option, index):
        return QSize(50, 50)


class EmojiPicker(QDialog):
    chosen = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Emojis · FortyTchat')
        self.resize(490, 510)
        self.setMinimumSize(370, 350)
        self.selected_once = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 18)
        layout.setSpacing(12)
        layout.addWidget(label('Trouve le bon emoji.', 'title'))
        self.search = QLineEdit()
        self.search.setPlaceholderText('Rechercher : café, amour, chat, football…')
        self.search.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.search.setClearButtonEnabled(True)
        layout.addWidget(self.search)
        row = QHBoxLayout()
        self.category = QComboBox()
        self.category.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.category.addItem('Toutes les catégories', '')
        for category in dict.fromkeys(entry[1] for entry in catalogue()):
            self.category.addItem(category, category)
        row.addWidget(self.category, 1)
        self.count = label('', 'caption')
        row.addWidget(self.count)
        layout.addLayout(row)
        self.model = EmojiModel(self)
        self.grid = QListView()
        self.grid.setViewMode(QListView.ViewMode.IconMode)
        self.grid.setResizeMode(QListView.ResizeMode.Adjust)
        self.grid.setMovement(QListView.Movement.Static)
        self.grid.setUniformItemSizes(True)
        self.grid.setGridSize(QSize(50, 50))
        self.grid.setSpacing(3)
        self.grid.setMouseTracking(True)
        self.grid.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.grid.setModel(self.model)
        self.grid.setItemDelegate(EmojiDelegate(self.grid))
        self.grid.clicked.connect(lambda index: self.choose(index.data(Qt.ItemDataRole.UserRole)))
        self.grid.activated.connect(lambda index: self.choose(index.data(Qt.ItemDataRole.UserRole)))
        layout.addWidget(self.grid, 1)
        self.empty = label('Aucun résultat. Essaie un autre mot.', 'muted', True)
        layout.addWidget(self.empty)
        layout.addWidget(label('Survole un emoji pour lire son nom. Clique pour l’insérer.', 'caption', True))
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(100)
        self.timer.timeout.connect(self.populate)
        self.search.textChanged.connect(lambda: self.timer.start())
        self.category.currentIndexChanged.connect(self.populate)
        self.populate()

    def populate(self, *args):
        entries = search_emojis(self.search.text(), self.category.currentData())
        self.model.replace(entries)
        self.count.setText(f'{len(entries):,} emojis'.replace(',', ' '))
        self.empty.setVisible(not entries)

    def choose(self, emoji):
        if not emoji or self.selected_once:
            return
        self.selected_once = True
        self.chosen.emit(emoji)
        self.accept()


@lru_cache(maxsize=1)
def fallback_index():
    by_start = defaultdict(list)
    for glyph, _, french, _, _ in catalogue():
        by_start[glyph[0]].append((glyph, french))
    return {key: sorted(values, key=lambda pair: len(pair[0]), reverse=True) for key, values in by_start.items()}


def emoji_fallback(text, font):
    """Replace an unsupported emoji as a whole, including its modifiers/ZWJ sequence."""
    metrics = [QFontMetrics(font)]
    if 'Noto Color Emoji' in QFontDatabase.families():
        metrics.append(QFontMetrics(QFont('Noto Color Emoji')))
    rendered, missing, position = [], False, 0
    index = fallback_index()
    while position < len(text):
        char = text[position]
        match = next(((glyph, name) for glyph, name in index.get(char, ()) if text.startswith(glyph, position)), None)
        glyph, name = match if match else (char, unicodedata.name(char, f'U+{ord(char):X}').lower())
        is_emoji = match is not None or (ord(char) >= 0x1f000 and unicodedata.category(char) == 'So')
        supported = all(any(m.inFontUcs4(ord(c)) for m in metrics) for c in glyph if ord(c) not in (0xfe0f, 0x200d))
        if is_emoji and not supported:
            rendered.append('[' + name + ']')
            missing = True
        else:
            rendered.append(glyph)
        position += len(glyph)
    return ''.join(rendered), missing
