from pathlib import Path
from types import SimpleNamespace
import time
import uuid

import pytest
from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QApplication

from src.app import Application
from src.config import ConfigStore, Preferences
from src.features.friends import FriendPresence, friend_key, friend_rows
from src.networking.client import ChatClient
from src.networking.settings import NetworkConfig
from src.ui.notifications import Notifications
from src.ui.settings import SettingsDialog
from src.ui.sidebar import Sidebar
from src.ui.tray import Tray

ROOT = Path(__file__).resolve().parents[1]
ICON = str(ROOT / 'assets/icons/fortytchat.svg')
CID, OFFLINE = 'a' * 32, 'b' * 32


@pytest.fixture(scope='module')
def qt():
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    return app


def rows(online=True):
    peers = [dict(sid='1' * 32, nick='Alice42', status='online', contact_id=CID,
                  encrypted=True, direct=True, caps=['cards'])] if online else []
    return friend_rows({CID:'Alice42', OFFLINE:'Bob42'}, peers)


def test_tray_friend_groups_stable_actions_and_disabled_offline_popup(qt):
    tray = Tray(ICON)
    chosen, chats, opened = [], [], []
    tray.popup_requested.connect(chosen.append)
    tray.friend_requested.connect(chats.append)
    tray.open_requested.connect(lambda:opened.append(True))
    try:
        tray.update_friends(rows())
        assert tray.online_friends.title() == 'Amis en ligne (1)'
        assert tray.offline_friends.title() == 'Amis hors ligne (1)'
        assert tray.popup_actions[CID].isEnabled()
        assert not tray.popup_actions[OFFLINE].isEnabled()
        tray.popup_actions[CID].trigger()
        assert chosen == [CID] and not chats and not opened
        next(a for a in tray.friend_menus[OFFLINE].actions() if a.text() == 'Discuter…').trigger()
        assert chats == [OFFLINE] and not opened
        tray.update_friends([])
        assert not tray.friend_menus
        assert not tray.online_friends.actions()[0].isEnabled()
    finally:
        tray.timer.stop(); tray.icon.hide(); tray.deleteLater(); qt.processEvents()


def test_open_menu_defers_rebuild_but_disables_stale_popup_and_reverses_pending(qt):
    tray = Tray(ICON)
    try:
        tray.update_friends(rows())
        action = tray.popup_actions[CID]
        tray.menu_opened()
        tray.update_friends(rows(False))
        assert tray.popup_actions[CID] is action and not action.isEnabled()
        assert tray._pending_friends == rows(False)
        tray.update_friends(rows())
        assert action.isEnabled() and tray._pending_friends is None
        tray.update_friends(rows(False))
        tray.menu_used(None); tray.menu_closed(); qt.processEvents()
        assert tray.online_friends.title() == 'Amis en ligne (0)'
        assert not tray.popup_actions[CID].isEnabled()
    finally:
        tray.timer.stop(); tray.icon.hide(); tray.deleteLater(); qt.processEvents()


def test_friend_submenu_navigation_does_not_trigger_gnome_double_click_fallback(qt):
    clock = [10.0]
    tray = Tray(ICON, clock=lambda:clock[0]); tray.menu_double_click = True
    opened = []
    tray.open_requested.connect(lambda:opened.append(True))
    try:
        tray.update_friends(rows())
        tray.menu_opened(); tray.friend_menus[CID].aboutToShow.emit()
        clock[0] += .1
        tray.menu_closed(); qt.processEvents()
        assert not opened
        clock[0] = 20
        tray.menu_opened()
        QApplication.sendEvent(tray.friend_menus[CID], QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier))
        clock[0] += .1
        tray.menu_closed(); qt.processEvents()
        assert not opened
    finally:
        tray.timer.stop(); tray.icon.hide(); tray.deleteLater(); qt.processEvents()


def test_sidebar_friend_context_opens_editor_action_and_offline_disables_it(qt):
    sidebar = Sidebar('Self42', friends={CID:'Alice42'})
    chosen = []
    sidebar.popup_requested.connect(chosen.append)
    sidebar.peers = {'1' * 32:dict(sid='1' * 32, nick='Alice42', status='online', contact_id=CID,
                                caps=['cards'], direct=True)}
    menu = sidebar.build_friend_menu({'contact_id':CID})
    popup = next(a for a in menu.actions() if a.text() == 'Envoyer une popup…')
    assert popup.isEnabled()
    popup.trigger()
    assert chosen == [CID]
    sidebar.peers.clear()
    offline = sidebar.build_friend_menu({'contact_id':CID})
    assert not next(a for a in offline.actions() if a.text() == 'Envoyer une popup…').isEnabled()
    assert sidebar.build_friend_menu({'section':'AMIS'}) is None
    menu.deleteLater(); offline.deleteLater(); sidebar.deleteLater(); qt.processEvents()


def test_settings_friend_notification_toggle_and_about(qt):
    prefs = Preferences(friend_notifications=False)
    settings = SettingsDialog(prefs, False, {}, dev=True)
    changes = []
    settings.preference_changed.connect(lambda k,v:changes.append((k,v)))
    assert not settings.friend_notifications.isChecked()
    settings.friend_notifications.setChecked(True)
    assert changes == [('friend_notifications', True)]
    settings.deleteLater(); qt.processEvents()


def test_native_friend_banner_is_normal_silent_and_headless_is_suppressed(qt, monkeypatch):
    calls = []
    monkeypatch.setattr('src.ui.notifications.QProcess.start', lambda self:calls.append((self.program(), self.arguments())))
    notifications = Notifications(ICON)
    notifications.executable = '/usr/bin/notify-send'
    assert notifications.show('Alice42 est en ligne', urgency='normal') is False
    assert not calls
    notifications.headless = False
    assert notifications.show('Alice42 est en ligne', 'Ton ami vient de se connecter.', urgency='normal')
    program, args = calls[0]
    assert program == '/usr/bin/notify-send'
    assert '--urgency=normal' in args and '--hint=boolean:suppress-sound:true' in args
    assert args[-3:] == ['--','Alice42 est en ligne','Ton ami vient de se connecter.']
    with pytest.raises(ValueError): notifications.show('invalid', urgency='critical')
    notifications.stop(); notifications.deleteLater(); qt.processEvents()


def test_simultaneous_friends_create_one_bounded_native_banner():
    cids = [f'{i:032x}' for i in range(1, 6)]
    peers = [dict(sid=cid, contact_id=cid, nick=f'Ami{i}42', status='online', direct=True, caps=['cards'])
             for i,cid in enumerate(cids, 1)]
    banners, menus = [], []
    clock = [0.0]
    state = SimpleNamespace(
        preferences=SimpleNamespace(friends={p['contact_id']:p['nick'] for p in peers}, friend_notifications=True, dnd=False),
        window=SimpleNamespace(sidebar=SimpleNamespace(blocked=set())), latest_peers=peers, contact_sessions={},
        cards_available=lambda p:True, tray=SimpleNamespace(update_friends=menus.append),
        notifications=SimpleNamespace(show=lambda *a,**kw:banners.append((a,kw))),
        friend_presence=FriendPresence(cids, clock=lambda:clock[0]))
    clock[0] = 10
    Application.refresh_friend_surfaces(state)
    assert banners == [(('5 amis sont en ligne', 'Ami142, Ami242, Ami342 et 2 autre(s) viennent de se connecter au cluster.'), {'urgency':'normal'})]
    assert len(menus[0]) == 5
    Application.refresh_friend_surfaces(state)
    assert len(banners) == 1


def until(qt, predicate, timeout=6):
    deadline = time.monotonic() + timeout
    while not predicate():
        qt.processEvents()
        if time.monotonic() >= deadline: raise AssertionError('État ami non atteint')
        time.sleep(.01)
    qt.processEvents()


def test_real_controller_friend_reconnect_banner_editor_current_session_and_dnd(qt, tmp_path, monkeypatch):
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path / 'config'))
    monkeypatch.setattr(NetworkConfig, 'load', classmethod(lambda cls,path:NetworkConfig(mode='relay', relay_host='')))
    controller = Application(ROOT, SimpleNamespace(dev='friend-menu-' + uuid.uuid4().hex[:8], background=True, insecure_local_relay=None))
    old = ChatClient('Alice42', contact_id=CID, tcp_port=0, discovery=False)
    new = None
    banners = []
    monkeypatch.setattr(controller.notifications, 'show', lambda *a,**kw:banners.append((a,kw)))
    clock = [0.0]
    controller.friend_presence = FriendPresence(clock=lambda:clock[0])
    try:
        controller.worker.submit(old.start()).result(4)
        client = controller.worker.client
        until(qt, lambda:client.port > 0)
        def announce(peer):
            peer.observe(client.hello(), '127.0.0.1')
            client.observe(peer.hello(), '127.0.0.1')
        controller.worker.loop.call_soon_threadsafe(announce, old)
        until(qt, lambda:old.sid in controller.window.peers)
        controller.change_friend(CID, True)
        assert not banners
        assert controller.tray.popup_actions[CID].isEnabled()
        controller.tray.popup_actions[CID].trigger()
        assert (old.sid, '') in controller.card_editors
        assert not controller.private_windows  # An editor alone is sufficient.
        controller.card_editors[(old.sid, '')].close(); qt.processEvents()
        controller.worker.submit(old.stop()).result(4)
        controller.worker.loop.call_soon_threadsafe(client.observe, {'v':1,'type':'bye','sid':old.sid}, '127.0.0.1')
        until(qt, lambda:old.sid not in controller.window.peers)
        assert controller.show_friend_card(CID) is None
        assert not controller.tray.popup_actions[CID].isEnabled()
        clock[0] = 10
        events = []
        new = ChatClient('Renamed42', lambda k,d:events.append((k,d)), contact_id=CID, crypto_identity=old.identity,
                         tcp_port=0, discovery=False)
        controller.worker.submit(new.start()).result(4)
        controller.worker.loop.call_soon_threadsafe(announce, new)
        until(qt, lambda:new.sid in controller.window.peers)
        assert banners == [(('Renamed42 est en ligne', 'Ton ami vient de se connecter au cluster. Retrouve-le depuis l’icône FortyTchat.'), {'urgency':'normal'})]
        assert controller.preferences.friends[CID] == 'Renamed42'
        controller.tray.popup_actions[CID].trigger()
        editor = controller.card_editors[(new.sid, '')]
        assert (old.sid, '') not in controller.card_editors and not controller.private_windows
        editor.submitted.emit(dict(title='Salut !', text='On se retrouve ?', emoji='1f44d'))
        until(qt, lambda:any(k == 'event' and d['frame']['type'] == 'card_invite' for k,d in events))
        editor.close(); qt.processEvents()
        # The local preference and DND consume transitions without deferred alerts.
        connected_peers = list(controller.latest_peers)
        controller.change_preference('friend_notifications', False)
        assert not ConfigStore(controller.store.root).preferences.friend_notifications
        controller.latest_peers = []; controller.flush_peers(); clock[0] = 100
        controller.latest_peers = connected_peers; controller.flush_peers()
        assert len(banners) == 1
        controller.change_preference('friend_notifications', True)
        controller.set_status('dnd')
        controller.latest_peers = []; controller.flush_peers(); clock[0] = 200
        controller.latest_peers = connected_peers; controller.flush_peers()
        assert len(banners) == 1
        controller.set_status('online'); controller.flush_peers()
        assert len(banners) == 1
        controller.change_friend(CID, False)
        assert not controller.tray.friend_menus and controller.show_friend_card(CID) is None
    finally:
        for candidate in (old, new):
            if candidate and not candidate._closing: controller.worker.submit(candidate.stop()).result(4)
        controller.cleanup(); controller.window.close(); controller.deleteLater(); qt.processEvents()
