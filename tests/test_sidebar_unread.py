import pytest
from PySide6.QtCore import QRect, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QImage, QPainter
from PySide6.QtWidgets import QApplication, QStyle, QStyleOptionViewItem

from src.features.friends import friend_key
from src.ui.sidebar import PeerDelegate, Sidebar
from src.ui.theme import apply_theme, colors


CID = 'a' * 32
OTHER_CID = 'b' * 32


@pytest.fixture(scope='module')
def qt():
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    return app


@pytest.fixture
def sidebar(qt):
    widget = Sidebar('Self42')
    yield widget
    widget.close()
    widget.deleteLater()
    qt.processEvents()


def rows(sidebar):
    return {data['sid']: item
            for item in (sidebar.list.item(index) for index in range(sidebar.list.count()))
            if (data := item.data(Qt.ItemDataRole.UserRole)) and 'sid' in data}


def peer(sid, contact_id=CID, **values):
    return dict(sid=sid, nick='Alice42', status='online', contact_id=contact_id,
                **values)


def test_unread_general_group_and_session_rows_keep_counts_and_selection(sidebar):
    group = 'group:room'
    sid = '1' * 32
    sidebar.groups = {group: dict(name='Projet42', state='joined', joined=['me', sid], left=[])}
    sidebar.peers = {sid: peer(sid)}
    sidebar.conversations = {'offline-session': 'Ancien42'}
    sidebar.unread = {'general': 1, group: 123, sid: 99, 'offline-session': 7}
    sidebar.current = group
    sidebar.refresh()

    items = rows(sidebar)
    assert set(items) == {'general', group, sid, 'offline-session'}
    for key, count in sidebar.unread.items():
        item = items[key]
        data = item.data(Qt.ItemDataRole.UserRole)
        assert data['unread'] == count
        expected = f'{count} message non lu' if count == 1 else f'{count} messages non lus'
        assert expected in item.toolTip()
        assert expected in item.data(Qt.ItemDataRole.AccessibleTextRole)
        assert expected in item.data(Qt.ItemDataRole.AccessibleDescriptionRole)
        assert data['nick'] in item.data(Qt.ItemDataRole.AccessibleTextRole)
    assert sidebar.list.currentItem() is items[group]
    selected = []
    sidebar.selected.connect(selected.append)
    sidebar._clicked(items[sid])
    assert selected == [sid]


def test_friend_unread_aggregates_all_sessions_and_survives_offline(sidebar):
    first, second, stranger = '1' * 32, '2' * 32, '3' * 32
    sidebar.friends = {CID: 'Alice42', OTHER_CID: 'Bob42'}
    sidebar.peers = {first: peer(first), second: peer(second),
                     stranger: peer(stranger, contact_id='c' * 32)}
    sidebar.unread = {first: 39, second: 60, friend_key(CID): 2,
                      friend_key(OTHER_CID): 4, stranger: 3, 'general': 1}
    sidebar.current = first
    sidebar.current_contact = CID
    sidebar.refresh()
    items = rows(sidebar)
    assert second not in items and friend_key(CID) not in items
    assert items[first].data(Qt.ItemDataRole.UserRole)['unread'] == 101
    assert sidebar.list.currentItem() is items[first]
    assert '101 messages non lus' in items[first].toolTip()
    assert items[friend_key(OTHER_CID)].data(Qt.ItemDataRole.UserRole)['unread'] == 4
    assert items[stranger].data(Qt.ItemDataRole.UserRole)['unread'] == 3

    sidebar.peers.clear()
    sidebar.refresh()
    item = rows(sidebar)[friend_key(CID)]
    assert sidebar.list.currentItem() is item
    assert item.data(Qt.ItemDataRole.UserRole)['unread'] == 101
    assert 'Hors ligne' in item.toolTip()
    assert '101 messages non lus' in item.data(Qt.ItemDataRole.AccessibleTextRole)

    current = '4' * 32
    sidebar.peers = {current: peer(current)}
    sidebar.unread[current] = 5
    sidebar.refresh()
    assert rows(sidebar)[current].data(Qt.ItemDataRole.UserRole)['unread'] == 106
    assert sidebar.list.currentItem() is rows(sidebar)[current]
    assert sidebar.current == first  # Selection follows identity without rewriting the route.
    sidebar.unread.clear()
    sidebar.refresh()
    item = rows(sidebar)[current]
    assert item.data(Qt.ItemDataRole.UserRole)['unread'] == 0
    assert 'Aucun message non lu' in item.data(Qt.ItemDataRole.AccessibleTextRole)


def test_unread_peer_preserves_lock_avatar_friend_and_blocking(sidebar):
    sid = '1' * 32
    sidebar.friends = {CID: 'Alice42'}
    sidebar.peers = {sid: peer(sid, locked=True, avatar=8, avatar_image='profile-photo')}
    sidebar.unread[sid] = 2
    sidebar.current = sid
    sidebar.refresh()
    item = rows(sidebar)[sid]
    data = item.data(Qt.ItemDataRole.UserRole)
    assert data['locked'] and data['friend'] and data['unread'] == 2
    assert data['avatar'] == 8 and data['avatar_image'] == 'profile-photo'
    assert data['detail'] == 'Session verrouillée'
    assert sidebar.list.currentItem() is item
    menu = sidebar.build_friend_menu(data)
    assert {'Discuter…', 'Retirer des amis', 'Bloquer cet utilisateur'} <= {
        action.text() for action in menu.actions()}
    menu.deleteLater()
    sidebar.blocked_contacts[CID] = 'Alice42'
    sidebar.refresh()
    data = rows(sidebar)[sid].data(Qt.ItemDataRole.UserRole)
    assert data['detail'] == 'Bloqué' and not data['locked'] and data['unread'] == 2


def test_contact_selection_only_matches_friend_rows(sidebar):
    sid = '1' * 32
    assert sidebar.current_contact == ''
    sidebar.peers = {sid: peer(sid)}
    sidebar.current_contact = CID
    sidebar.refresh()
    assert sidebar.list.currentItem() is rows(sidebar)['general']


class RecordingPainter:
    def __init__(self, painter):
        self.painter = painter
        self.text = []

    def __getattr__(self, name):
        return getattr(self.painter, name)

    def drawText(self, *args):
        rect = QRectF(*args[:4]) if len(args) == 6 else QRectF(args[0])
        self.text.append((args[-1], rect, QFont(self.painter.font()),
                          QColor(self.painter.pen().color())))
        return self.painter.drawText(*args)


def render_row(sidebar, qt, theme, count, *, friend=False, selected=False):
    apply_theme(qt, theme)
    sidebar.list.clear()
    sidebar.unread = {'session': count}
    # Keep the full nickname in accessibility while the painted name is elided.
    sidebar._row('session', 'TrèsLongNomDeConversation42' * 4,
                 'Un aperçu de message suffisamment long pour être tronqué' * 2,
                 friend=friend)
    image = QImage(292, 82, QImage.Format.Format_ARGB32)
    image.fill(QColor(colors()['sidebar']))
    option = QStyleOptionViewItem()
    option.rect = QRect(0, 0, image.width(), image.height())
    option.state = QStyle.StateFlag.State_Enabled
    if selected:
        option.state |= QStyle.StateFlag.State_Selected
    painter = QPainter(image)
    recording = RecordingPainter(painter)
    try:
        PeerDelegate(sidebar.list).paint(recording, option, sidebar.list.model().index(0, 0))
    finally:
        painter.end()
    return image, recording.text


@pytest.mark.parametrize('theme', ['dark', 'light'])
@pytest.mark.parametrize('count, expected', [(0, None), (1, '1'), (9, '9'), (99, '99'), (100, '99+'), (12345, '99+')])
def test_badges_fit_without_covering_elided_names_in_both_themes(sidebar, qt, theme, count, expected):
    previous_theme = qt.property('fortytchatTheme') or 'dark'
    try:
        image, text = render_row(sidebar, qt, theme, count)
        name, detail = text[:2]
        assert name[0].endswith('…') and detail[0].endswith('…')
        assert QFontMetrics(name[2]).horizontalAdvance(name[0]) <= name[1].width()
        assert QFontMetrics(detail[2]).horizontalAdvance(detail[0]) <= detail[1].width()
        assert not any(entry[0] == '↗' for entry in text)
        assert name[2].bold() == bool(count)
        assert name[3] == QColor(colors()['text' if count else 'muted'])
        if expected is None:
            assert len(text) == 2
            return
        assert len(text) == 3
        badge = text[2]
        assert badge[0] == expected and badge[2].bold() and badge[3] == QColor('white')
        assert name[1].right()+9 <= badge[1].left()
        assert detail[1].right()+9 <= badge[1].left()
        assert badge[1].right() < image.width()-7
        assert QFontMetrics(badge[2]).horizontalAdvance(expected)+10 <= badge[1].width()
        assert image.pixelColor(int(badge[1].center().x()), int(badge[1].top())+2) == QColor('#c63150')
        # White badge text remains legible on the same red in either theme.
        white_pixels = sum(image.pixelColor(x, y) == QColor('white')
                           for x in range(int(badge[1].left()), int(badge[1].right()))
                           for y in range(int(badge[1].top()), int(badge[1].bottom())))
        assert white_pixels > 0
    finally:
        apply_theme(qt, previous_theme)


@pytest.mark.parametrize('theme', ['dark', 'light'])
def test_selection_has_clear_marker_and_friend_star_does_not_overlap(sidebar, qt, theme):
    previous_theme = qt.property('fortytchatTheme') or 'dark'
    try:
        image, text = render_row(sidebar, qt, theme, 0, friend=True, selected=True)
        assert image.pixelColor(10, 40) == QColor(colors()['accent'])
        assert image.pixelColor(76, 40) == QColor(colors()['selected'])
        assert text[0][3] == QColor(colors()['text'])
        assert text[-1][0] == '★'
        assert text[0][1].right()+9 <= text[-1][1].left()
        _, unread_text = render_row(sidebar, qt, theme, 8, friend=True)
        assert unread_text[-1][0] == '8'
        assert '★' not in [entry[0] for entry in unread_text]
    finally:
        apply_theme(qt, previous_theme)
