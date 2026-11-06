"""Neutral charcoal and periwinkle, inspired by the user's visual reference."""
from string import Template

DARK_COLORS = dict(
    bg='#202329', sidebar='#23262c', rail='#121315', surface='#2c3139',
    hover='#343a43', selected='#323842', border='#383e47',
    text='#f2f3f5', muted='#a0a6b1', subtle='#7f8794',
    accent='#7891ff', accentHover='#8ca0ff', accentSoft='#303b56', accentText='#b6c5ff',
    incoming='#2d323a', outgoing='#34405b', chip='#303745',
    notice='#343c4d', noticeBorder='#536383', online='#7bceab',
)
LIGHT_COLORS = dict(
    bg='#f7f8fb', sidebar='#eef0f5', rail='#e1e5ed', surface='#ffffff',
    hover='#e1e6f0', selected='#dce5fa', border='#dbe0e9',
    text='#292f3a', muted='#687383', subtle='#8893a3',
    accent='#5278e3', accentHover='#6488ef', accentSoft='#e1e9fb', accentText='#4267c5',
    incoming='#ffffff', outgoing='#e1e9fb', chip='#e4e9f3',
    notice='#e8edf8', noticeBorder='#bdcae4', online='#399d77',
)


def colors(theme=None):
    if theme is None:
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance()
        theme = app.property('fortytchatTheme') if app else 'dark'
    return LIGHT_COLORS if theme == 'light' else DARK_COLORS


STYLE = Template('''
QWidget { color: $text; font-family: "Inter", "Ubuntu", "DejaVu Sans"; font-size: 14px; }
QMainWindow, QDialog { background: $bg; }
QFrame#rail { background: $rail; }
QFrame#sidebar { background: $sidebar; border-right: 1px solid $border; }
QFrame#mainPane, QScrollArea, QScrollArea > QWidget > QWidget { background: $bg; }
QFrame#header { border-bottom: 1px solid $border; }
QLabel#brand { font-size: 25px; font-weight: 700; letter-spacing: -0.8px; }
QLabel#eyebrow { color: $subtle; font-size: 10px; font-weight: 650; letter-spacing: 1.5px; }
QLabel#muted { color: $muted; }
QLabel#caption { color: $muted; font-size: 11px; }
QLabel#title { font-size: 20px; font-weight: 650; }
QLabel#heroTitle { font-size: 29px; font-weight: 650; letter-spacing: -0.7px; }
QLabel#emptyTitle { color: $text; font-size: 18px; font-weight: 600; }
QLabel#emptySymbol { font-size: 43px; color: $accent; background: $surface; border: 1px solid $border; border-radius: 28px; }
QLabel#roomSymbol { background: $surface; color: $accentText; border-radius: 17px; font-size: 28px; }
QLabel#privacy { background: transparent; border: none; color: $subtle; padding: 8px 0; font-size: 11px; }
QLabel#chip { color: $muted; background: $chip; border-radius: 12px; padding: 7px 11px; font-size: 11px; }
QLabel#notice { background: $notice; color: $text; border: 1px solid $noticeBorder; border-radius: 12px; padding: 12px; }
QFrame#profile { background: $sidebar; border-top: 1px solid $border; }
QFrame#composer { background: $surface; border: 1px solid $border; border-radius: 23px; }
QTextEdit#input { background: transparent; border: none; padding: 10px 8px; font-size: 14px; selection-background-color: $accentSoft; }
QLineEdit, QComboBox, QPlainTextEdit { background: $surface; border: 1px solid $border; border-radius: 11px; padding: 10px; selection-background-color: $accentSoft; }
QLineEdit:focus, QComboBox:focus { border-color: $accent; }
QPushButton { background: $surface; border: 1px solid transparent; border-radius: 12px; padding: 9px 13px; font-weight: 550; }
QPushButton:hover { background: $hover; }
QPushButton:pressed { background: $selected; }
QPushButton:disabled { color: $subtle; background: $surface; }
QPushButton#primary { background: $accent; color: white; font-weight: 650; border-radius: 13px; }
QPushButton#primary:hover { background: $accentHover; }
QPushButton#primary:disabled { background: $selected; color: $subtle; }
QPushButton#flat { background: transparent; color: $muted; padding: 6px; }
QPushButton#flat:hover { background: $hover; color: $text; }
QPushButton#railButton { background: transparent; color: $muted; border-radius: 15px; padding: 8px; }
QPushButton#railButton:checked { background: $surface; color: $text; }
QPushButton#railButton:hover { background: $surface; }
QPushButton#soft { background: $selected; color: $muted; border-radius: 11px; }
QPushButton#break { background: $selected; color: $accentText; border-radius: 11px; }
QListWidget, QListView { background: transparent; border: none; outline: none; padding: 5px; }
QListWidget::item { border: none; }
QScrollBar:vertical { background: transparent; width: 10px; margin: 2px 0; }
QScrollBar::handle:vertical { background: $subtle; border-radius: 5px; min-height: 32px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
QFrame#incoming { background: $incoming; border: none; border-radius: 20px; }
QFrame#outgoing { background: $outgoing; border: none; border-radius: 20px; }
QFrame#replyPreview { background: $accentSoft; border-left: 3px solid $accent; border-radius: 7px; }
QPushButton#reactionPill { background: $surface; color: $text; border: 1px solid $border; }
QPushButton#reactionPill[mine="true"] { background: $accentSoft; color: $accentText; border-color: $accent; }
QLabel#messageText { font-size: 14px; background: transparent; }
QLabel#messageName { color: $text; font-size: 12px; font-weight: 650; background: transparent; }
QLabel#system { color: $subtle; font-size: 11px; padding: 6px; }
QCheckBox { spacing: 10px; padding: 6px 0; }
QCheckBox::indicator { width: 18px; height: 18px; border: 1px solid $subtle; background: $surface; border-radius: 6px; }
QCheckBox::indicator:checked { background: $accent; border: 1px solid $accentHover; }
QTabWidget::pane { border: 1px solid $border; border-radius: 12px; }
QTabBar::tab { background: $surface; padding: 11px 15px; color: $muted; }
QTabBar::tab:selected { background: $selected; color: $text; }
QToolTip { background: $surface; color: $text; border: 1px solid $border; padding: 8px; }
QMenu { background: $surface; border: 1px solid $border; border-radius: 10px; padding: 6px; }
QMenu::item { padding: 8px 19px; border-radius: 6px; }
QMenu::item:selected { background: $hover; }
''')


def stylesheet(theme='dark'):
    return STYLE.substitute(colors(theme))


def apply_theme(app, theme):
    app.setProperty('fortytchatTheme', theme)
    app.setStyleSheet(stylesheet(theme))
