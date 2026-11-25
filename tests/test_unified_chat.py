"""Conversation navigation and actual read receipts in the shared chat window."""
import asyncio
import time
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtWidgets import QApplication, QDialog, QMainWindow, QStackedWidget

from src.app import Application
from src.features.collaboration import group_key
from src.networking.client import ChatClient
from src.networking.settings import NetworkConfig
from src.ui.main_window import MainWindow
from src.ui.private_window import PrivateWindow


ROOT = Path(__file__).resolve().parents[1]


def until(qt, predicate, timeout=7):
    deadline = time.monotonic() + timeout
    while not predicate():
        qt.processEvents()
        assert time.monotonic() < deadline, 'Shared conversation UI did not settle'
        time.sleep(.005)
    qt.processEvents()


def settle(qt, seconds=.8):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        qt.processEvents()
        time.sleep(.005)


def select_sidebar(app, target):
    sidebar = app.window.sidebar
    item = next(sidebar.list.item(index) for index in range(sidebar.list.count())
                if (sidebar.list.item(index).data(Qt.ItemDataRole.UserRole) or {}).get('sid') == target)
    sidebar.list.itemClicked.emit(item)


def read_ids(events):
    return {data['id'] for kind, data in events if kind == 'message_read'}


def restore_host(app, qt):
    app.window.open_window()
    qt.processEvents()
    # Headless Qt has no window manager to grant a reactivation after hide().
    if qt.platformName() in ('offscreen', 'minimal'):
        qt.setActiveWindow(app.window)
    until(qt, lambda: app.is_viewed(app.window.active_target))


def assert_embedded(app, view):
    assert view.chat_host is app.window
    assert not view.isWindow()
    assert view.window() is app.window
    chat_windows = [widget for widget in QApplication.topLevelWidgets()
                    if isinstance(widget, (MainWindow, PrivateWindow)) and widget.isVisible()]
    assert chat_windows == [app.window]
    assert isinstance(app.window, QMainWindow)


@pytest.fixture(scope='module')
def qt():
    instance = QApplication.instance() or QApplication([])
    instance.setQuitOnLastWindowClosed(False)
    return instance


@pytest.fixture
def session(qt, tmp_path, monkeypatch):
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path / 'config'))
    monkeypatch.setattr(NetworkConfig, 'load', classmethod(
        lambda cls, path: NetworkConfig(mode='relay', relay_host='')))
    app = Application(ROOT, SimpleNamespace(dev='unified-' + uuid.uuid4().hex[:10],
                                            background=False, insecure_local_relay=None))
    peers = []
    notices = []
    monkeypatch.setattr(app.notifications, 'show', lambda *args, **kwargs: notices.append((args, kwargs)) or True)
    until(qt, lambda: app.worker.client.port > 0)

    def connect_peer(nickname='Alice42', **options):
        events = []
        peer = ChatClient(nickname, lambda kind, data: events.append((kind, data)),
                          discovery=False, tcp_port=0, **options)
        peers.append(peer)
        app.worker.submit(peer.start()).result(4)
        client = app.worker.client

        def announce():
            peer.observe(client.hello(), '127.0.0.1')
            client.observe(peer.hello(), '127.0.0.1')

        app.worker.loop.call_soon_threadsafe(announce)
        until(qt, lambda: peer.sid in app.window.peers and app.window.peers[peer.sid].get('encrypted'))
        return peer, events

    def send(peer, target, text):
        reference = app.worker.submit(peer.send('message', target, text=text)).result(4)
        assert reference
        local_target = peer.sid if target == app.worker.client.sid else target
        until(qt, lambda: app.history.find(local_target, reference) is not None)
        return reference

    def drain():
        async def finish_local_exchanges():
            clients = [app.worker.client, *peers]
            deadline = time.monotonic() + 3
            quiet = 0
            while quiet < 3:
                busy = any(client.operations or client.handlers or client.connections or client._queued
                           for client in clients)
                quiet = 0 if busy else quiet + 1
                assert time.monotonic() < deadline, 'Local socket exchanges did not close'
                await asyncio.sleep(.02)
        app.worker.submit(finish_local_exchanges()).result(4)

    yield SimpleNamespace(app=app, connect_peer=connect_peer, send=send, drain=drain, notices=notices)

    app.typing_timer.stop()
    app.file_queue_timer.stop()
    app.tray.timer.stop()
    app.peer_timer.stop()
    for view in [app.window, *app.private_windows.values(), *app.group_windows.values()]:
        view.composer.stop_typing()
    try:
        drain()
    finally:
        try:
            for peer in peers:
                if not peer._closing:
                    app.worker.submit(peer.stop()).result(4)
            drain()
        finally:
            app.cleanup()
            app.window.close()
            app.deleteLater()
            settle(qt, .05)


def test_sidebar_navigation_preserves_each_draft_reply_and_scroll(session, qt):
    app = session.app
    first, _ = session.connect_peer()
    second, _ = session.connect_peer('Bob42')
    window = app.window
    general_widgets = window.chat, window.composer, window.input
    window.input.setPlainText('UNIFIED_GENERAL_DRAFT')
    select_sidebar(app, first.sid)
    first_view = app.private_windows[first.sid]
    assert_embedded(app, first_view)
    assert isinstance(window.pages, QStackedWidget)
    assert window.active_view() is first_view
    assert window.current == 'general' and window.active_target == first.sid
    original = session.send(first, app.worker.client.sid, 'UNIFIED_QUOTED_MESSAGE')
    for index in range(20):
        app.system_message(first.sid, f'Older content {index}\n' + 'Several visible lines\n' * 5)
    settle(qt, .15)
    bar = first_view.chat.scroll.verticalScrollBar()
    assert bar.maximum() > 1000
    bar.setValue(bar.maximum() // 3)
    first_view.input.setPlainText('UNIFIED_FIRST_DRAFT')
    app.begin_reply(first.sid, original)
    settle(qt, .05)
    scroll_position = bar.value()
    # A presence/avatar refresh must not jump a reader back to the latest message.
    app.latest_peers = [dict(peer, avatar=9) if peer['sid'] == first.sid else peer for peer in app.latest_peers]
    app.flush_peers()
    settle(qt, .1)
    assert abs(bar.value() - scroll_position) <= 2
    select_sidebar(app, second.sid)
    second_view = app.private_windows[second.sid]
    assert_embedded(app, second_view)
    second_view.input.setPlainText('UNIFIED_SECOND_DRAFT')
    select_sidebar(app, first.sid)
    settle(qt, .05)
    assert window.active_view() is first_view
    assert first_view.input.toPlainText() == 'UNIFIED_FIRST_DRAFT'
    assert first_view.composer.reply_to == original
    assert abs(bar.value() - scroll_position) <= 2
    assert second_view.input.toPlainText() == 'UNIFIED_SECOND_DRAFT'
    select_sidebar(app, 'general')
    settle(qt, .05)
    assert window.active_target == 'general'
    assert window.pages.currentWidget() is window.general_page
    assert window.active_view() is window
    assert (window.chat, window.composer, window.input) == general_widgets
    assert window.input.toPlainText() == 'UNIFIED_GENERAL_DRAFT'
    assert window.sidebar.current == 'general'


def test_receiving_other_chats_keeps_selection_and_only_reads_selected_page(session, qt):
    app = session.app
    first, first_events = session.connect_peer()
    second, second_events = session.connect_peer('Bob42')
    app.open_private(first.sid)
    selected = app.open_private(second.sid)
    until(qt, lambda: app.is_viewed(second.sid))
    general = session.send(first, 'general', 'UNIFIED_HIDDEN_GENERAL')
    private = session.send(first, app.worker.client.sid, 'UNIFIED_HIDDEN_PRIVATE')
    visible = session.send(second, app.worker.client.sid, 'UNIFIED_VISIBLE_PRIVATE')
    until(qt, lambda: visible in read_ids(second_events))
    settle(qt)
    assert app.window.active_target == second.sid
    assert app.window.active_view() is selected
    assert app.window.sidebar.current == second.sid
    assert not app.is_viewed('general') and not app.is_viewed(first.sid)
    assert general not in read_ids(first_events) and private not in read_ids(first_events)
    assert app.window.sidebar.unread.get('general') == 1
    assert app.window.sidebar.unread.get(first.sid) == 1
    assert not app.window.sidebar.unread.get(second.sid)
    app.activate()
    settle(qt)
    assert app.window.active_target == second.sid
    assert app.window.sidebar.unread.get('general') == 1
    assert general not in read_ids(first_events)
    select_sidebar(app, first.sid)
    until(qt, lambda: private in read_ids(first_events))
    assert not app.window.sidebar.unread.get(first.sid)
    assert app.window.sidebar.unread.get('general') == 1
    assert general not in read_ids(first_events)
    select_sidebar(app, 'general')
    until(qt, lambda: general in read_ids(first_events))
    assert not app.window.sidebar.unread.get('general')
    assert app.history.find(first.sid, private).text == 'UNIFIED_HIDDEN_PRIVATE'
    assert private in app.private_windows[first.sid].chat.message_rows
    assert private not in app.window.chat.message_rows
    assert general in app.window.chat.message_rows


def test_send_confirmation_clears_its_composer_after_navigation(session, qt):
    app = session.app
    first, first_events = session.connect_peer()
    second, second_events = session.connect_peer('Bob42')
    view = app.open_private(first.sid)
    original = session.send(first, app.worker.client.sid, 'UNIFIED_REPLY_ORIGINAL')
    app.begin_reply(first.sid, original)
    view.input.setPlainText('UNIFIED_PRIVATE_REPLY')
    view.send_button.click()
    other = app.open_private(second.sid)
    other.input.setPlainText('UNIFIED_PRIVATE_REPLY')
    until(qt, lambda: any(kind == 'event' and data['frame'].get('text') == 'UNIFIED_PRIVATE_REPLY'
                          for kind, data in first_events))
    until(qt, lambda: not view.input.toPlainText())
    frame = next(data['frame'] for kind, data in first_events
                 if kind == 'event' and data['frame'].get('text') == 'UNIFIED_PRIVATE_REPLY')
    assert frame['to'] == first.sid and frame['reply_to'] == original
    assert not any(kind == 'event' and data['frame'].get('text') == 'UNIFIED_PRIVATE_REPLY'
                   for kind, data in second_events)
    assert other.input.toPlainText() == 'UNIFIED_PRIVATE_REPLY'
    assert not view.composer.reply_to
    assert app.window.active_target == second.sid
    select_sidebar(app, 'general')
    app.window.input.setPlainText('UNIFIED_GENERAL_SEND')
    app.window.send_button.click()
    app.open_private(first.sid)
    view.input.setPlainText('UNIFIED_GENERAL_SEND')
    until(qt, lambda: all(any(kind == 'event' and data['frame'].get('text') == 'UNIFIED_GENERAL_SEND'
                              and data['frame']['to'] == 'general' for kind, data in events)
                          for events in (first_events, second_events)))
    until(qt, lambda: not app.window.input.toPlainText())
    assert view.input.toPlainText() == 'UNIFIED_GENERAL_SEND'
    assert app.window.active_target == first.sid
    assert app.window.current == 'general'


def test_receipts_require_visible_active_host_and_visible_message(session, qt):
    app = session.app
    peer, events = session.connect_peer()
    view = app.open_private(peer.sid)
    until(qt, lambda: app.is_viewed(peer.sid))
    for state in ('hidden', 'minimized', 'dialog'):
        dialog = None
        if state == 'hidden':
            app.window.hide()
        elif state == 'minimized':
            app.window.showMinimized()
        else:
            dialog = QDialog(app.window)
            dialog.setWindowTitle('A dialog obscures the conversation')
            dialog.show()
            dialog.raise_()
            dialog.activateWindow()
            until(qt, lambda: qt.activeWindow() is dialog)
        settle(qt, .05)
        reference = session.send(peer, app.worker.client.sid, 'UNIFIED_UNSEEN_' + state)
        settle(qt)
        assert reference not in read_ids(events), state
        assert not app.is_viewed(peer.sid), state
        assert app.window.sidebar.unread.get(peer.sid) == 1, state
        if dialog:
            dialog.close()
            dialog.deleteLater()
        restore_host(app, qt)
        try:
            until(qt, lambda: reference in read_ids(events))
        except AssertionError:
            raise AssertionError((state, qt.activeWindow() is app.window,
                                  app.window.isVisible(), app.window.isMinimized(),
                                  app.window.isActiveWindow(), view.isVisible(),
                                  reference in view.chat.visible_incoming(),
                                  app.worker.client.receipts.incoming.get(reference))) from None
        assert not app.window.sidebar.unread.get(peer.sid), state
    for index in range(18):
        app.system_message(peer.sid, f'Previous message {index}\n' + 'Content\n' * 6)
    settle(qt, .1)
    bar = view.chat.scroll.verticalScrollBar()
    assert bar.maximum() > 1000
    bar.setValue(0)
    reference = session.send(peer, app.worker.client.sid, 'UNIFIED_OUTSIDE_VIEWPORT')
    settle(qt)
    assert reference not in view.chat.visible_incoming()
    assert reference not in read_ids(events)
    view.chat.bottom()
    until(qt, lambda: reference in read_ids(events))


def test_closing_active_and_inactive_pages_preserves_history_and_host(session, qt):
    app = session.app
    first, _ = session.connect_peer()
    second, _ = session.connect_peer('Bob42')
    first_view = app.open_private(first.sid)
    reference = session.send(first, app.worker.client.sid, 'UNIFIED_REOPEN_HISTORY')
    second_view = app.open_private(second.sid)
    first_view.close()
    qt.processEvents()
    assert first.sid not in app.private_windows
    assert first.sid not in app.window.conversation_pages
    assert app.window.active_target == second.sid
    assert app.window.active_view() is second_view
    assert app.window.isVisible()
    app.close_private(second.sid)
    qt.processEvents()
    assert second.sid not in app.private_windows
    assert second.sid not in app.window.conversation_pages
    assert app.window.active_target == 'general'
    assert app.window.active_view() is app.window
    assert app.window.pages.currentWidget() is app.window.general_page
    reopened = app.open_private(first.sid)
    assert_embedded(app, reopened)
    assert reference in reopened.chat.message_rows
    assert app.history.find(first.sid, reference).text == 'UNIFIED_REOPEN_HISTORY'


@pytest.mark.parametrize('active', [True, False], ids=['selected', 'background'])
def test_friend_new_session_keeps_page_state_and_correct_selection(session, qt, active):
    app = session.app
    old, old_events = session.connect_peer('OldFriend42')
    spectator, _ = session.connect_peer('Bob42')
    app.change_friend(old.contact_id, True)
    view = app.open_private(old.sid)
    reference = session.send(old, app.worker.client.sid, 'UNIFIED_FRIEND_HISTORY')
    until(qt, lambda: reference in read_ids(old_events))
    view.input.setPlainText('UNIFIED_AFTER_RECONNECT')
    app.begin_reply(old.sid, reference)
    if not active:
        app.open_private(spectator.sid)
        session.send(old, app.worker.client.sid, 'UNIFIED_FRIEND_UNREAD')
        assert app.window.sidebar.unread.get(old.sid) == 1
    view.composer.stop_typing()
    session.drain()
    app.worker.submit(old.stop()).result(4)
    app.worker.loop.call_soon_threadsafe(app.worker.client.observe,
        {'v': 1, 'type': 'bye', 'sid': old.sid}, '127.0.0.1')
    until(qt, lambda: old.sid not in app.window.peers)
    new, events = session.connect_peer('NewFriend42', contact_id=old.contact_id, crypto_identity=old.identity)
    until(qt, lambda: app.private_windows.get(new.sid) is view and view.input.isEnabled())
    assert old.sid not in app.private_windows
    assert old.sid not in app.window.conversation_pages
    assert app.window.conversation_pages[new.sid] is view
    assert view.current == new.sid
    assert view.input.toPlainText() == 'UNIFIED_AFTER_RECONNECT'
    assert view.composer.reply_to == reference
    assert app.history.find(new.sid, reference).text == 'UNIFIED_FRIEND_HISTORY'
    if active:
        assert app.window.active_target == new.sid
        assert app.window.active_view() is view
    else:
        assert app.window.active_target == spectator.sid
        assert app.window.sidebar.unread.get(new.sid) == 1
        app.open_private(new.sid)
    assert_embedded(app, view)
    view.send_button.click()
    until(qt, lambda: any(kind == 'event' and data['frame'].get('text') == 'UNIFIED_AFTER_RECONNECT'
                          for kind, data in events))
    frame = next(data['frame'] for kind, data in events
                 if kind == 'event' and data['frame'].get('text') == 'UNIFIED_AFTER_RECONNECT')
    assert frame['to'] == new.sid and frame['reply_to'] == reference
    until(qt, lambda: not view.input.toPlainText())


def test_friend_session_merge_keeps_both_drafts(session, qt):
    app = session.app
    old, _ = session.connect_peer('OldFriend42')
    app.change_friend(old.contact_id, True)
    old_view = app.open_private(old.sid)
    reference = session.send(old, app.worker.client.sid, 'Message cité')
    old_view.input.setPlainText('Réponse du premier brouillon')
    app.begin_reply(old.sid, reference)

    new, _ = session.connect_peer('NewFriend42', contact_id=old.contact_id, crypto_identity=old.identity)
    new_view = app.open_private(new.sid)
    new_view.input.setPlainText('Deuxième brouillon')
    old_view.composer.stop_typing()
    new_view.composer.stop_typing()
    session.drain()
    app.worker.submit(old.stop()).result(4)
    app.worker.loop.call_soon_threadsafe(app.worker.client.observe,
        {'v': 1, 'type': 'bye', 'sid': old.sid}, '127.0.0.1')
    until(qt, lambda: old.sid not in app.private_windows)
    assert app.window.active_view() is new_view
    assert new_view.input.toPlainText() == 'Deuxième brouillon'

    def choose_draft():
        menu = new_view.build_menu()
        recovered = next(action.menu() for action in menu.actions()
                         if action.text() == 'Brouillons récupérés')
        recovered.actions()[0].trigger()
        menu.deleteLater()

    choose_draft()
    assert new_view.input.toPlainText() == 'Réponse du premier brouillon'
    assert new_view.composer.reply_to == reference
    choose_draft()
    assert new_view.input.toPlainText() == 'Deuxième brouillon'
    assert not new_view.composer.reply_to


def test_incoming_wizz_selects_its_page_and_animates_the_shared_host(session, qt, monkeypatch):
    app = session.app
    peer, _ = session.connect_peer()
    other, _ = session.connect_peer('Bob42')
    app.open_private(other.sid)
    effects = []
    monkeypatch.setattr('src.ui.attention.shake_window', lambda window, content=None: effects.append((window, content)))
    app.window.hide()
    assert app.worker.submit(peer.send('wizz', app.worker.client.sid)).result(4)
    until(qt, lambda: bool(effects))
    view = app.private_windows[peer.sid]
    assert_embedded(app, view)
    assert app.window.active_target == peer.sid
    assert app.window.active_view() is view
    assert app.window.isVisible()
    assert effects == [(app.window, app.window.centralWidget())]
    assert 'Wizz' in view.notice.text()


def test_group_navigation_rename_leave_and_global_banners(session, qt):
    app = session.app
    peer, events = session.connect_peer()
    gid = app.worker.submit(app.worker.client.create_group('Unified group', [peer.contact_id])).result(4)
    assert gid
    key = group_key(gid)
    until(qt, lambda: key in app.group_windows and gid in peer.rooms)
    assert app.worker.submit(peer.answer_group(gid, True)).result(4)
    until(qt, lambda: peer.contact_id in app.groups[key]['joined'])
    view = app.group_windows[key]
    assert_embedded(app, view)
    assert app.window.active_target == key
    view.input.setPlainText('UNIFIED_GROUP_DRAFT')
    select_sidebar(app, 'general')
    reference = session.send(peer, key, 'UNIFIED_HIDDEN_GROUP')
    settle(qt)
    assert app.window.active_target == 'general'
    assert app.window.sidebar.unread.get(key) == 1
    assert reference not in read_ids(events)
    app.open_group(gid)
    until(qt, lambda: reference in read_ids(events))
    assert app.window.active_view() is view
    assert view.input.toPlainText() == 'UNIFIED_GROUP_DRAFT'
    assert app.worker.submit(app.worker.client.update_group(gid, {'name': 'Renamed shared group'})).result(4)
    until(qt, lambda: view.name_label.text() == 'Renamed shared group')
    assert app.window.active_target == key and app.window.active_view() is view
    view.send_button.click()
    select_sidebar(app, 'general')
    until(qt, lambda: any(kind == 'event' and data['frame'].get('text') == 'UNIFIED_GROUP_DRAFT'
                          for kind, data in events))
    sent = next(data['frame'] for kind, data in events
                if kind == 'event' and data['frame'].get('text') == 'UNIFIED_GROUP_DRAFT')
    assert sent.get('group') == gid
    assert app.window.active_target == 'general'
    app.open_group(gid)
    app.window.show_update_banner('999.0.0')
    app.window.show_notice('UNIFIED_GLOBAL_NOTICE')
    qt.processEvents()
    assert app.window.update_banner.isVisible()
    assert app.window.notice.isVisible()
    private = app.open_private(peer.sid)
    assert_embedded(app, private)
    assert app.window.update_banner.isVisible()
    assert app.window.notice.isVisible()
    app.open_group(gid)
    assert app.worker.submit(app.worker.client.answer_group(gid, False)).result(4)
    until(qt, lambda: app.groups[key]['state'] == 'left')
    assert not view.composer.available and not view.input.isEnabled()
    assert app.history.find(key, reference).text == 'UNIFIED_HIDDEN_GROUP'
    manager = app.manage_group(gid)
    assert manager in app.collaboration_dialogs
    app.collaboration_event('groups', {'groups': []})
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert manager not in app.collaboration_dialogs
    assert key not in app.window.conversation_pages
    assert app.window.active_target == 'general'
    for profile_file in app.store.root.glob('*.json'):
        assert 'UNIFIED_' not in profile_file.read_text()
