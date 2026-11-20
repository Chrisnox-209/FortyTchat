"""Link interaction tests: desktop opening is always replaced with a local spy."""
import os
from pathlib import Path
import subprocess
import sys

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QPoint, Qt, QUrl
from PySide6.QtGui import QContextMenuEvent, QDesktopServices, QTextCursor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel, QMenu, QPlainTextEdit

from src.ui.link_text import LinkText
from src.ui.message_text import MessageText


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def opened_urls(monkeypatch):
    opened = []
    monkeypatch.setattr(QDesktopServices, 'openUrl', lambda url: opened.append(QUrl(url)) or True)
    return opened


@pytest.fixture
def widgets(app):
    owned = []

    def own(widget):
        owned.append(widget)
        return widget

    yield own
    for widget in reversed(owned):
        widget.close()
        widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()


def show(app, widget, width=420):
    height = widget.heightForWidth(width)
    widget.resize(width, max(100, height))
    widget.show()
    widget.activateWindow()
    for _ in range(5):
        app.processEvents()


def cursor_at(editor, python_index):
    cursor = QTextCursor(editor.document())
    cursor.setPosition(len(editor.toPlainText()[:python_index].encode('utf-16-le')) // 2)
    return cursor


def point_at(editor, python_index):
    rect = editor.cursorRect(cursor_at(editor, python_index))
    point = QPoint(rect.left() + 2, rect.center().y())
    assert editor.viewport().rect().contains(point), (point, editor.viewport().rect())
    return point


def format_runs(editor):
    block = editor.document().begin()
    while block.isValid():
        iterator = block.begin()
        while not iterator.atEnd():
            fragment = iterator.fragment()
            if fragment.isValid():
                yield fragment.text(), fragment.charFormat()
            iterator += 1
        block = block.next()


def context_menu(app, editor, point):
    QTest.mouseClick(editor.viewport(), Qt.MouseButton.RightButton, pos=point)
    app.processEvents()
    menu = QApplication.activePopupWidget()
    if not isinstance(menu, QMenu):
        # Offscreen has no window-system context-menu synthesis. Deliver the
        # same native Qt event, then exercise the actual popup with mouse input.
        event = QContextMenuEvent(QContextMenuEvent.Reason.Mouse, point,
                                 editor.viewport().mapToGlobal(point))
        QApplication.sendEvent(editor.viewport(), event)
        app.processEvents()
        menu = QApplication.activePopupWidget()
    assert isinstance(menu, QMenu) and menu.isVisible()
    return menu


def click_action(app, menu, text):
    action = next(action for action in menu.actions() if action.text() == text)
    assert action.isEnabled()
    QTest.mouseClick(menu, Qt.MouseButton.LeftButton, pos=menu.actionGeometry(action).center())
    app.processEvents()


@pytest.mark.parametrize('source, target', [
    ('https://example.org/a?x=1&y=two%20words#section',
     'https://example.org/a?x=1&y=two%20words#section'),
    ('www.example.org/a?x=1&y=2#section',
     'https://www.example.org/a?x=1&y=2#section'),
    ('http://poste-cluster:8080/document?q=1&next=2',
     'http://poste-cluster:8080/document?q=1&next=2'),
])
def test_actual_left_click_opens_complete_link_once(app, widgets, opened_urls, source, target):
    view = widgets(LinkText('Voir ' + source + '.'))
    show(app, view, 600)
    point = point_at(view, view.toPlainText().index(source) + 10)
    assert view.anchorAt(point) == target
    assert opened_urls == []
    QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, pos=point)
    app.processEvents()
    assert [url.toEncoded().data() for url in opened_urls] == [QUrl(target).toEncoded().data()]
    assert view.toPlainText() == 'Voir ' + source + '.'


def test_show_hover_focus_selection_and_rebuild_do_not_open_links(app, widgets, opened_urls):
    text = 'Simple texte https://example.org/a?x=1&y=2#section'
    view = widgets(LinkText(text))
    show(app, view)
    point = point_at(view, text.index('https://') + 10)
    QTest.mouseMove(view.viewport(), point)
    view.setFocus()
    view.selectAll()
    view.rebuild()
    menu = view.link_menu(point)
    assert any(action.text() == 'Ouvrir le lien' for action in menu.actions())
    menu.deleteLater()
    app.processEvents()
    assert opened_urls == []
    assert view.toPlainText() == text


@pytest.mark.parametrize('action', ['Ouvrir le lien', 'Copier le lien'])
def test_right_click_menu_acts_on_the_complete_exact_url(app, widgets, opened_urls, action):
    url = 'https://équipe.fr/été%20projet?x=1&y=deux%26trois#résumé'
    view = widgets(LinkText('Lien : ' + url + '.'))
    show(app, view, 560)
    QApplication.clipboard().setText('avant')
    point = point_at(view, view.toPlainText().index(url) + 10)
    menu = context_menu(app, view, point)
    assert [item.text() for item in menu.actions() if not item.isSeparator()][:2] == [
        'Ouvrir le lien', 'Copier le lien',
    ]
    assert opened_urls == []
    click_action(app, menu, action)
    if action == 'Copier le lien':
        assert QApplication.clipboard().text() == url
        assert opened_urls == []
    else:
        assert [value.toEncoded().data() for value in opened_urls] == [QUrl(url).toEncoded().data()]
        assert QApplication.clipboard().text() == 'avant'


def test_bare_www_menu_copies_normalized_full_url_and_plain_menu_has_no_link_action(app, widgets, opened_urls):
    source = 'www.example.org/a?x=1&y=2#section'
    view = widgets(LinkText('Phrase. ' + source))
    show(app, view, 560)
    point = point_at(view, view.toPlainText().index(source) + 6)
    menu = context_menu(app, view, point)
    click_action(app, menu, 'Copier le lien')
    assert QApplication.clipboard().text() == 'https://' + source
    menu = context_menu(app, view, point_at(view, 2))
    assert not any(action.text() in {'Ouvrir le lien', 'Copier le lien'} for action in menu.actions())
    menu.close()
    assert opened_urls == []


def test_keyboard_link_menu_uses_the_cursor_anchor(app, widgets, opened_urls):
    url = 'https://example.org/a?x=1&y=2#section'
    view = widgets(LinkText('Avant ' + url))
    show(app, view, 560)
    view.setTextCursor(cursor_at(view, view.toPlainText().index(url) + 9))
    menu = view.link_menu(QPoint(0, 0), keyboard=True)
    action = next(action for action in menu.actions() if action.text() == 'Copier le lien')
    action.trigger()
    assert QApplication.clipboard().text() == url
    assert opened_urls == []
    menu.deleteLater()


def test_ctrl_c_copies_only_selected_exact_plain_text(app, widgets, opened_urls):
    selected = 'sélection <b>brut</b> https://example.org/a?x=1&y=2#section'
    text = 'Avant ' + selected + ' après'
    view = widgets(LinkText(text))
    show(app, view, 600)
    cursor = cursor_at(view, text.index(selected))
    cursor.setPosition(len(text[:text.index(selected) + len(selected)].encode('utf-16-le')) // 2,
                       QTextCursor.MoveMode.KeepAnchor)
    view.setTextCursor(cursor)
    view.setFocus()
    QApplication.clipboard().setText('avant')
    QTest.keyClick(view, Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier)
    app.processEvents()
    assert QApplication.clipboard().text() == selected
    assert opened_urls == []


@pytest.mark.parametrize('value', [
    'javascript:alert(1)', 'file:///tmp/secret', 'mailto:alice@example.org',
    'data:text/html,<script>alert(1)</script>', 'https://example.org:bad',
])
def test_open_and_copy_methods_revalidate_without_side_effects(app, widgets, opened_urls, value):
    view = widgets(LinkText('https://example.org'))
    QApplication.clipboard().setText('conserver')
    assert not view.open_link(value)
    assert not view.open_link(QUrl(value))
    assert not view.copy_link(value)
    assert opened_urls == []
    assert QApplication.clipboard().text() == 'conserver'


def test_html_is_literal_and_resources_and_unsafe_urls_stay_inert(app, widgets, opened_urls):
    text = ('<b>gras</b> &lt;texte&gt; <img src="file:///tmp/private.png"> '
            '<a href="javascript:alert(1)">exécuter</a>\n'
            'https://example.org/?x=1&y=2#fin')
    view = widgets(LinkText(text))
    show(app, view, 560)
    assert view.toPlainText() == text
    assert not view.openLinks() and not view.openExternalLinks()
    runs = list(format_runs(view))
    assert all(not char_format.isImageFormat() for _, char_format in runs)
    anchors = [(text, char_format.anchorHref()) for text, char_format in runs if char_format.isAnchor()]
    assert anchors == [('https://example.org/?x=1&y=2#fin', 'https://example.org/?x=1&y=2#fin')]
    assert view.loadResource(2, QUrl('file:///tmp/private.png')) is None
    QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, pos=point_at(view, 3))
    app.processEvents()
    assert opened_urls == []


def test_message_text_keeps_fenced_urls_as_selectable_nonclickable_code(app, widgets, opened_urls):
    before = 'https://example.org/avant?x=1&y=2'
    code = 'https://example.org/code?x=1&y=2\n<a href="file:///tmp/private">brut</a>'
    after = 'www.example.org/apres#fin'
    view = widgets(MessageText(before + '\n====html\n' + code + '\n====\n' + after))
    show(app, view, 560)
    links = view.findChildren(LinkText)
    assert [item.toPlainText() for item in links] == [before, after]
    editor = view.findChild(QPlainTextEdit)
    assert editor is not None and editor.isReadOnly()
    assert editor.toPlainText() == code
    assert all(not char_format.isAnchor() and not char_format.isImageFormat()
               for _, char_format in format_runs(editor))
    QTest.mouseClick(editor.viewport(), Qt.MouseButton.LeftButton, pos=point_at(editor, 12))
    app.processEvents()
    assert opened_urls == []
    editor.selectAll()
    editor.setFocus()
    QTest.keyClick(editor, Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier)
    assert QApplication.clipboard().text() == code
    QTest.mouseClick(links[1].viewport(), Qt.MouseButton.LeftButton, pos=point_at(links[1], 8))
    app.processEvents()
    assert [url.toString() for url in opened_urls] == ['https://' + after]


def test_message_without_web_links_retains_plain_label_rendering(app, widgets, opened_urls):
    text = '<a href="javascript:alert(1)">inert</a> &lt;literal&gt;'
    view = widgets(MessageText(text))
    show(app, view)
    assert not view.findChildren(LinkText)
    labels = view.findChildren(QLabel)
    assert any(label.text() == text and label.textFormat() == Qt.TextFormat.PlainText for label in labels)
    assert opened_urls == []


@pytest.mark.parametrize('scale', ['1', '1.5', '2'])
@pytest.mark.parametrize('theme', ['dark', 'light'])
@pytest.mark.parametrize('font_size', [14, 20])
def test_long_links_wrap_without_clipping_or_losing_their_target(scale, theme, font_size):
    code = r'''
import sys
from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices, QTextCursor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from src.ui.link_text import LinkText
from src.ui.message_text import MessageText
from src.ui.theme import apply_theme, colors
app = QApplication([])
apply_theme(app, sys.argv[1])
app.setStyleSheet(app.styleSheet() + 'QWidget {font-size: ' + sys.argv[2] + 'px;}')
opened = []
QDesktopServices.openUrl = lambda url: opened.append(QUrl(url)) or True
url = 'https://example.org/' + 'abcdefghij' * 65 + '?first=one&second=two%20words#complete'
text = 'Avant\n' + url + '\nAprès'
for width in (190, 280):
    widget = MessageText(text)
    widget.resize(width, widget.heightForWidth(width))
    widget.show()
    for _ in range(8): app.processEvents()
    editor = widget.findChild(LinkText)
    assert editor is not None and editor.toPlainText() == text
    assert widget.width() == width
    assert editor.width() <= width
    assert editor.horizontalScrollBar().maximum() == 0
    assert editor.document().size().width() <= editor.viewport().width() + 1
    assert editor.document().size().height() <= editor.viewport().height() + 1, (width, editor.size(), editor.document().size())
    block = editor.document().findBlockByNumber(1)
    assert block.layout().lineCount() > 2
    cursor = QTextCursor(editor.document())
    cursor.setPosition(text.index(url) + len(url) - 5)
    rect = editor.cursorRect(cursor)
    point = rect.center()
    point.setX(point.x() + 2)
    assert editor.viewport().rect().contains(point), (width, point, editor.viewport().rect())
    assert editor.anchorAt(point) == url
    assert not opened
    QTest.mouseClick(editor.viewport(), Qt.MouseButton.LeftButton, pos=point)
    app.processEvents()
    assert [value.toEncoded().data() for value in opened] == [QUrl(url).toEncoded().data()]
    assert editor.copy_link(editor.anchorAt(point))
    assert QApplication.clipboard().text() == url
    cursor.setPosition(text.index(url) + 5)
    assert cursor.charFormat().foreground().color().name() == colors()['accentText']
    opened.clear()
    widget.close()
'''
    result = subprocess.run(
        [sys.executable, '-c', code, theme, str(font_size)], cwd=ROOT,
        env={**os.environ, 'QT_QPA_PLATFORM': 'offscreen', 'QT_SCALE_FACTOR': scale},
        capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0, result.stdout + result.stderr
