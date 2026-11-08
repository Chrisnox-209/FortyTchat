import json
from pathlib import Path
from types import SimpleNamespace
import time
import uuid

import pytest
from PySide6.QtCore import Qt, QEvent
from PySide6.QtGui import QFont, QKeyEvent
from PySide6.QtWidgets import QApplication, QWidget

from src.config import ConfigStore, Preferences
from src.identity import contact_from_caps, session_id
from src.features.friends import friend_key
from src.features.emojis import EmojiPicker, catalogue, search_emojis, emoji_fallback
from src.memory import Conversations, Message
from src.networking.client import ChatClient
from src.networking.protocol import encode
from src.networking.settings import NetworkConfig
from src.app import Application
from src.ui.sidebar import Sidebar

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope='module')
def qt():
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    return app


def pump(qt, predicate, timeout=6):
    deadline = time.monotonic() + timeout
    while not predicate():
        qt.processEvents()
        if time.monotonic() >= deadline:
            raise AssertionError('Le contact attendu n’est pas apparu')
        time.sleep(0.01)
    qt.processEvents()


def test_profile_contact_survives_rename_and_process_restart_without_messages(tmp_path):
    store = ConfigStore(tmp_path)
    own_id = store.preferences.contact_id
    buddy_id = session_id()
    store.preferences.friends[buddy_id] = 'FriendBefore42'
    store.preferences.nickname = 'MyNewNick42'
    store.preferences.autostart = False
    store.save()
    loaded = ConfigStore(tmp_path)
    assert loaded.preferences.contact_id == own_id
    assert loaded.preferences.friends == {buddy_id: 'FriendBefore42'}
    a = ChatClient(loaded.preferences.nickname, contact_id=own_id)
    b = ChatClient('AnotherNick42', contact_id=own_id)
    a.port = b.port = 39742
    assert a.sid != b.sid and a.contact_id == b.contact_id
    assert a.hello()['contact_id']==own_id and a.crypto.check_presence(a.hello())[0]==own_id
    assert len(encode(a.hello())) < 1024
    assert set(a.hello()) == {'v','type','sid','nick','status','port','caps','avatar','avatar_hash','crypto','contact_id'}
    assert not Conversations().get('general')
    assert 'text' not in json.loads(store.path.read_text())


def test_legacy_profile_migration_keeps_existing_choices_and_bounds_friends(tmp_path):
    (tmp_path / 'config.json').write_text(json.dumps({'nickname':'KeptNick42','autostart':False,'theme':'light',
        'friends': {'bad-id':'Rejected42', 'a'*32:'ValidNick42', 'b'*32:'<invalid>'}}))
    store = ConfigStore(tmp_path)
    assert len(store.preferences.contact_id) == 32 and store.preferences.nickname == 'KeptNick42'
    assert store.preferences.theme == 'light' and not store.preferences.autostart
    assert store.preferences.friends == {'a'*32:'ValidNick42'}
    store.preferences.friends = {f'{i:032x}':'N'*28 for i in range(1,257)}
    store.save()
    assert store.path.stat().st_size < 32768
    assert len(ConfigStore(tmp_path).preferences.friends) == 256


def test_friend_row_stays_offline_and_uses_profile_not_nickname(qt):
    cid, sid = 'a'*32, 'b'*32
    friends = {cid:'RenamedFriend42', 'c'*32:'AnotherOffline42'}
    sidebar = Sidebar('SelfNick42', friends=friends)
    sidebar.peers = {sid:{'sid':sid,'nick':'RenamedFriend42','status':'online','contact_id':cid}}
    sidebar.conversations[sid] = 'RenamedFriend42'
    sidebar.unread[sid] = 2
    sidebar.refresh()
    data = [sidebar.list.item(i).data(Qt.ItemDataRole.UserRole) for i in range(sidebar.list.count())]
    rows = [x for x in data if x.get('friend')]
    assert len(rows) == 2 and rows[0]['sid'] == sid and rows[0]['unread'] == 2
    sidebar.peers = {}
    sidebar.conversations.clear()
    sidebar.refresh()
    data = [sidebar.list.item(i).data(Qt.ItemDataRole.UserRole) for i in range(sidebar.list.count())]
    rows = [x for x in data if x.get('friend') and x.get('contact_id') == cid]
    assert rows[0]['sid'] == friend_key(cid) and not rows[0]['online'] and rows[0]['unread'] == 2
    assert rows[0]['nick'] == 'RenamedFriend42'
    sidebar.deleteLater()


def test_wide_emoji_catalogue_search_categories_and_virtual_grid(qt):
    assert len(catalogue()) == 3781
    assert len({entry[1] for entry in catalogue()}) == 9
    assert any(e[0] == '☕' for e in search_emojis('cafe'))
    assert any(e[0] == '❤️' for e in search_emojis('coeur'))
    assert any(e[0] == '🐧' for e in search_emojis('penguin'))
    picker = EmojiPicker()
    picker.show()
    qt.processEvents()
    assert picker.model.rowCount() == 3781
    assert len(picker.findChildren(QWidget)) < 200  # No button object for every emoji.
    picker.search.setText('café')
    picker.populate()
    assert picker.model.rowCount() == 1
    chosen = []
    picker.chosen.connect(chosen.append)
    picker.grid.clicked.emit(picker.model.index(0))
    assert chosen == ['☕']
    picker.deleteLater()


def test_missing_family_emoji_falls_back_as_one_sequence(qt, monkeypatch):
    class MissingFont:
        def __init__(self, *args): pass
        def inFontUcs4(self, *args): return False
    monkeypatch.setattr('src.features.emojis.QFontMetrics', MissingFont)
    monkeypatch.setattr('src.features.emojis.QFontDatabase.families', lambda: [])
    rendered, missing = emoji_fallback('Famille 👨‍👩‍👧‍👦 !', QFont())
    assert missing and rendered.startswith('Famille [') and rendered.endswith(' !')
    assert '\u200d' not in rendered and '👨' not in rendered


def test_friend_reconnect_renamed_new_session_routes_same_open_window(qt, tmp_path, monkeypatch):
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path / 'config'))
    monkeypatch.setattr(NetworkConfig, 'load', classmethod(lambda cls, path: NetworkConfig(mode='relay', relay_host='')))
    profile = 'friends-' + uuid.uuid4().hex[:8]
    controller = Application(ROOT, SimpleNamespace(dev=profile, background=False, insecure_local_relay=None))
    cid = session_id()
    old = ChatClient('OldFriend42', contact_id=cid, tcp_port=0, discovery=False)
    new = None
    try:
        controller.worker.submit(old.start()).result(4)
        client = controller.worker.client
        pump(qt, lambda: client.port > 0)
        def announce(peer):
            peer.observe(client.hello(), '127.0.0.1')
            client.observe(peer.hello(), '127.0.0.1')
        controller.worker.loop.call_soon_threadsafe(announce, old)
        pump(qt, lambda: old.sid in controller.window.peers)
        view = controller.open_private(old.sid)
        from src.security import fingerprint
        view.security_button.click()
        security = controller.security_dialogs[old.sid]
        security.expected.setText(fingerprint(old.identity.public))
        security.verify.click()
        pump(qt, lambda: 'identité vérifiée' in view.security_note.text())
        security.close()
        assert view.friend_button.isEnabled()
        view.friend_button.click()
        assert controller.preferences.friends[cid] == 'OldFriend42'
        def rename():
            old.rename('RenamedFriend42')
            client.observe(old.hello(), '127.0.0.1')
        controller.worker.loop.call_soon_threadsafe(rename)
        pump(qt, lambda: controller.preferences.friends[cid] == 'RenamedFriend42')
        controller.worker.submit(old.send('message', client.sid, text='RAM_ONLY_FRIEND_MESSAGE')).result(4)
        pump(qt, lambda: bool(controller.history.get(old.sid)))
        controller.worker.submit(old.stop()).result(4)
        controller.worker.loop.call_soon_threadsafe(client.observe, {'v':1,'type':'bye','sid':old.sid}, '127.0.0.1')
        pump(qt, lambda: old.sid not in controller.window.peers)
        assert not view.input.isEnabled()
        received = []
        new = ChatClient('NewestFriend42', lambda k,d: received.append((k,d)), contact_id=cid, crypto_identity=old.identity, tcp_port=0, discovery=False)
        controller.worker.submit(new.start()).result(4)
        controller.worker.loop.call_soon_threadsafe(announce, new)
        pump(qt, lambda: new.sid in controller.private_windows and view.current == new.sid)
        assert controller.private_windows[new.sid] is view and new.sid != old.sid
        assert view.is_friend and view.input.isEnabled()
        assert 'identité vérifiée' in view.security_note.text()
        assert controller.preferences.friends[cid] == 'NewestFriend42'
        assert controller.history.get(new.sid)[0].text == 'RAM_ONLY_FRIEND_MESSAGE'
        view.input.setPlainText('To the current session')
        QApplication.sendEvent(view.input, QKeyEvent(QEvent.Type.KeyPress,Qt.Key.Key_Return,Qt.KeyboardModifier.NoModifier))
        pump(qt, lambda: any(k == 'event' and d['frame'].get('text') == 'To the current session' for k,d in received))
        content = controller.store.path.read_text()
        assert 'RAM_ONLY_FRIEND_MESSAGE' not in content and 'To the current session' not in content
        assert json.loads(content)['friends'][cid] == 'NewestFriend42'
        # A changed key must remain reachable from an offline friend row without
        # attaching the old conversation before the new fingerprint is verified.
        controller.worker.submit(new.stop()).result(4)
        controller.worker.loop.call_soon_threadsafe(client.observe, {'v':1, 'type':'bye', 'sid':new.sid}, '127.0.0.1')
        pump(qt, lambda: new.sid not in controller.window.peers)
        changed = ChatClient('ChangedKey42', contact_id=cid, discovery=False)
        changed.port = 39742
        controller.worker.loop.call_soon_threadsafe(client.observe, changed.hello(), '127.0.0.1')
        pump(qt, lambda: changed.sid in controller.window.peers)
        assert controller.open_private(friend_key(cid)) is None
        security = controller.security_dialogs[changed.sid]
        assert view.current == new.sid and not view.input.isEnabled()
        security.expected.setText(fingerprint(changed.identity.public))
        security.verify.click()
        pump(qt, lambda: view.current == changed.sid and view.input.isEnabled())
        assert 'identité vérifiée' in view.security_note.text()
        security.close()
        controller.change_friend(cid, False)
        assert cid not in controller.preferences.friends and not view.is_friend
    finally:
        for peer in (old,new):
            if peer and not peer._closing:
                controller.worker.submit(peer.stop()).result(4)
        controller.cleanup()
        controller.window.close()
        controller.deleteLater()
