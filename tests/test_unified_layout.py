"""Keep the shared conversation window usable at its supported compact size."""
import os
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
ICON = ROOT / 'assets/icons/fortytchat.svg'
NORA, IDRISS, SELF = 'a' * 32, 'b' * 32, 'c' * 32
PRIVATE, OTHER, GROUP = '1' * 32, '2' * 32, 'group:' + 'd' * 32


def demo_scene(app):
    from src.config import Preferences
    from src.memory import Conversations, Message
    from src.ui.groups import GroupWindow
    from src.ui.main_window import MainWindow
    from src.ui.private_window import PrivateWindow

    history = Conversations()
    history.append('general', Message(PRIVATE, 'Nora42', 'Bienvenue dans le cluster !', id='general-demo'))
    messages = [
        Message(PRIVATE, 'Nora42', 'Salut ! Tu peux relire le parsing de minishell avec moi ?', id='demo-1', avatar=5),
        Message('self', 'Camille42', 'Oui, mes tests passent. Je regarde ton dépôt.', outgoing=True, id='demo-2',
                delivery_recipients={PRIVATE: {'state': 'read', 'nick': 'Nora42', 'reads': True}}),
        Message(PRIVATE, 'Nora42', 'Le sujet est ici : https://projects.intra.42.fr\nOn se retrouve au cluster après ?',
                id='demo-3', avatar=5),
    ]
    preferences = Preferences(nickname='Camille42', friends={NORA: 'Nora42', IDRISS: 'Idriss42'})
    window = MainWindow(preferences, history, str(ICON))
    peers = [dict(sid=PRIVATE, contact_id=NORA, nick='Nora42', status='online', avatar=5,
                  security='verified', encrypted=True, identity_key='demo-public-key', features=['screen_share_v1']),
             dict(sid=OTHER, contact_id=IDRISS, nick='Idriss42', status='away', avatar=9, encrypted=True)]
    window.set_peers(peers)
    private = PrivateWindow(PRIVATE, peers[0]['nick'], messages, str(ICON))
    private.set_peer(peers[0])
    private.set_friend(True)
    room = dict(id=GROUP[6:], name='Minishell · entraide', state='joined',
                creator=SELF, members=[SELF, NORA, IDRISS], joined=[SELF, NORA, IDRISS],
                left=[], admins=[], avatar=3, popup_at=0, popup_interval=2520)
    group = GroupWindow(room, [Message(PRIVATE, 'Nora42', 'On compare nos tests demain ?', id='group-demo')], str(ICON))
    window.sidebar.groups = {GROUP: room}
    window.sidebar.unread = {'general': 3, OTHER: 104, GROUP: 12}
    window.add_conversation(PRIVATE, private)
    window.add_conversation(GROUP, group)
    window.show_conversation(PRIVATE)
    private.input.setPlainText('Je prends mon ordinateur et j’arrive…')
    return window, private, group


def settle(app):
    for _ in range(8):
        app.processEvents()


def check_layout():
    from PySide6.QtCore import QPoint, QRect, QSize, Qt
    from PySide6.QtGui import QColor, QFontMetrics
    from PySide6.QtWidgets import QApplication, QLabel, QPushButton, QStyle, QStyleOptionButton
    from src.ui.main_window import MainWindow
    from src.ui.private_window import PrivateWindow
    from src.ui.theme import apply_theme, colors, stylesheet

    app = QApplication([])
    app.setQuitOnLastWindowClosed(False)
    failures = []

    def require(condition, scenario, detail):
        if not condition:
            failures.append(f'{scenario}: {detail}')

    def label_fits(widget, scenario):
        if not widget.isVisible() or not widget.text():
            return
        text = widget.text()
        if widget.wordWrap():
            needed = widget.heightForWidth(widget.width())
            require(widget.height() >= needed, scenario,
                    f'label height {widget.height()} < {needed}: {text!r}')
        else:
            metrics = QFontMetrics(widget.font())
            require(metrics.horizontalAdvance(text) <= widget.contentsRect().width(), scenario,
                    f'label width {widget.contentsRect().width()} < {metrics.horizontalAdvance(text)}: {text!r}')
            require(metrics.height() <= widget.contentsRect().height(), scenario,
                    f'label height {widget.contentsRect().height()} < {metrics.height()}: {text!r}')

    for theme in ('dark', 'light'):
        for font_size in (14, 20):
            apply_theme(app, theme)
            app.setStyleSheet(stylesheet(theme).replace('font-size: 14px;', f'font-size: {font_size}px;', 1))
            window, private, group = demo_scene(app)
            # Names may be elided, while the complete name remains accessible.
            private.set_peer(dict(sid=PRIVATE, contact_id=NORA, nick='AlexandreDuCluster4200000000',
                                  status='dnd', security='verified', encrypted=True, identity_key='public-demo'))
            group.set_group(dict(group.room, name='Un très long nom de groupe pour vérifier la place dans le titre'))
            window.show()
            for width, height in ((1120, 780), (900, 580)):
                window.resize(width, height)
                settle(app)
                for target in ('general', PRIVATE, GROUP):
                    window.show_conversation(target)
                    active = window.active_view()
                    active.composer.update_actions()
                    settle(app)
                    scenario = f'{theme}, font {font_size}, {width}x{height}, {target}'
                    require(window.size() == QSize(width, height), scenario,
                            f'window grew to {window.width()}x{window.height()}')
                    require(window.pages.currentWidget() is (window.general_page if target == 'general' else active),
                            scenario, 'wrong central page')
                    page = window.pages.currentWidget()
                    require(page.size() == window.pages.contentsRect().size(), scenario,
                            f'page {page.size()} differs from available pane {window.pages.contentsRect().size()}')
                    require(window.centralWidget().rect().contains(QRect(window.pages.mapTo(window.centralWidget(), QPoint()),
                                                                        window.pages.size())),
                            scenario, 'conversation pane extends outside main window')
                    require(active.chat.scroll.viewport().height() >= active.input.fontMetrics().lineSpacing(),
                            scenario, 'message viewport cannot show one text line')
                    require(active.chat.scroll.widget().width() <= active.chat.scroll.viewport().width(),
                            scenario, 'messages require hidden horizontal scrolling')
                    selected = window.sidebar.list.currentItem()
                    require(selected is not None and selected.data(Qt.ItemDataRole.UserRole)['sid'] == target,
                            scenario, 'sidebar selection differs from central conversation')
                    rows = {data['sid']: data for index in range(window.sidebar.list.count())
                            if (data := window.sidebar.list.item(index).data(Qt.ItemDataRole.UserRole)) and 'sid' in data}
                    require(rows[OTHER]['unread'] == 104, scenario, 'other friend lost its unread badge')
                    other_item = next(window.sidebar.list.item(index) for index in range(window.sidebar.list.count())
                                      if (window.sidebar.list.item(index).data(Qt.ItemDataRole.UserRole) or {}).get('sid') == OTHER)
                    window.sidebar.list.scrollToItem(other_item)
                    settle(app)
                    bounds = window.sidebar.list.visualItemRect(other_item)
                    rendered = window.sidebar.list.viewport().grab().toImage().scaled(window.sidebar.list.viewport().size())
                    red_pixels = sum(rendered.pixelColor(x, y) == QColor('#c63150')
                                     for x in range(max(0, bounds.right()-64), bounds.right()+1)
                                     for y in range(max(0, bounds.top()), min(rendered.height(), bounds.bottom()+1)))
                    require(red_pixels > 20, scenario, 'unread badge is not visibly red')
                    window.sidebar.list.scrollToItem(selected)
                    settle(app)
                    bounds = window.sidebar.list.visualItemRect(selected)
                    rendered = window.sidebar.list.viewport().grab().toImage().scaled(window.sidebar.list.viewport().size())
                    marker_pixels = sum(rendered.pixelColor(x, y) == QColor(colors()['accent'])
                                        for x in range(max(0, bounds.left()), min(rendered.width(), bounds.left()+15))
                                        for y in range(max(0, bounds.top()), min(rendered.height(), bounds.bottom()+1)))
                    require(marker_pixels > 40, scenario, 'selected conversation has no visible marker')
                    header = (window.general_page if target == 'general' else active).findChild(QLabel, 'title').parentWidget()
                    for text_label in header.findChildren(QLabel):
                        label_fits(text_label, scenario)
                    if target != 'general':
                        label_fits(active.security_note, scenario)
                        require(active.name_label.accessibleName() == active.name_label.full_text,
                                scenario, 'full conversation name is inaccessible')
                    for control in active.composer.findChildren(QPushButton) + header.findChildren(QPushButton):
                        if not control.isVisible():
                            continue
                        owner = active.composer if active.composer.isAncestorOf(control) else header
                        rect = QRect(control.mapTo(owner, QPoint()), control.size())
                        require(owner.rect().contains(rect), scenario,
                                f'button {control.text()!r} extends outside {owner.objectName()}: {rect}')
                        option = QStyleOptionButton()
                        control.initStyleOption(option)
                        contents = control.style().subElementRect(QStyle.SubElement.SE_PushButtonContents, option, control)
                        metrics = control.fontMetrics()
                        needed_width = metrics.horizontalAdvance(control.text().replace('&', ''))
                        require(contents.width() >= needed_width, scenario,
                                f'button width {contents.width()} < {needed_width}: {control.text()!r}')
                        if control.text():
                            require(contents.height() >= metrics.height(), scenario,
                                    f'button height {contents.height()} < {metrics.height()}: {control.text()!r}')
                    input_rect = QRect(active.input.mapTo(active.composer, QPoint()), active.input.size())
                    require(active.composer.rect().contains(input_rect), scenario, 'message field extends outside composer')
                    require(active.input.viewport().height() >= active.input.fontMetrics().lineSpacing(),
                            scenario, 'message field clips its first text line')
                    original_draft = active.input.toPlainText()
                    active.input.setPlainText('a' * 1700)
                    active.composer.update_actions()
                    settle(app)
                    label_fits(active.composer.counter, scenario + ', long draft')
                    for control in active.composer.findChildren(QPushButton):
                        if not control.isVisible() or not control.text():
                            continue
                        option = QStyleOptionButton()
                        control.initStyleOption(option)
                        contents = control.style().subElementRect(QStyle.SubElement.SE_PushButtonContents, option, control)
                        needed = control.fontMetrics().horizontalAdvance(control.text().replace('&', ''))
                        require(contents.width() >= needed, scenario + ', long draft',
                                f'button width {contents.width()} < {needed}: {control.text()!r}')
                    active.input.setPlainText(original_draft)
                    active.composer.update_actions()
                    settle(app)
                    visible_chat_windows = [widget for widget in app.topLevelWidgets()
                                            if isinstance(widget, (MainWindow, PrivateWindow)) and widget.isVisible()]
                    require(visible_chat_windows == [window], scenario, 'more than one chat window is visible')
            window.quitting_now = True
            window.close()
            window.deleteLater()
            settle(app)
    assert not failures, '\n'.join(failures[:100])
    print('Unified layout verified')


@pytest.mark.parametrize('scale', ['1', '1.5', '2'])
def test_unified_window_layout_at_supported_sizes_themes_and_fonts(scale):
    env = {**os.environ, 'QT_QPA_PLATFORM': 'offscreen', 'QT_SCALE_FACTOR': scale,
           'QT_SCREEN_SCALE_FACTORS': '', 'QT_FONT_DPI': '96'}
    result = subprocess.run([sys.executable, str(Path(__file__).resolve()), '--check'],
                            cwd=ROOT, env=env, capture_output=True, text=True, timeout=40)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'Unified layout verified' in result.stdout


if __name__ == '__main__':
    sys.path.insert(0, str(ROOT))
    if sys.argv[1:] == ['--check']:
        check_layout()
