from src.config import ConfigStore
from src.features.friends import FriendPresence, friend_rows

CID, OTHER = 'a' * 32, 'b' * 32


def peer(cid=CID, sid='1' * 32, nick='Alice42', **values):
    return dict(sid=sid, nick=nick, contact_id=cid, status='online', encrypted=True,
                caps=['cards', 'choices'], direct=True, relay=False, **values)


def test_presence_startup_rename_status_and_multiple_sessions_are_quiet():
    clock = [100.0]
    monitor = FriendPresence([CID, OTHER], clock=lambda: clock[0])
    alice = peer()
    assert monitor.update([CID, OTHER], [alice]) == []
    clock[0] += 10
    alice.update(nick='Renamed42', status='dnd')
    assert monitor.update([CID, OTHER], [alice, peer(sid='2' * 32)]) == []
    assert monitor.update([CID, OTHER], [alice]) == []
    bob = peer(cid=OTHER, sid='3' * 32, nick='Bob42')
    assert monitor.update([CID, OTHER], [alice, bob]) == [bob]
    assert monitor.update([CID, OTHER], [bob]) == []
    # Switching to another live device without an offline gap is not a login.
    assert monitor.update([CID, OTHER], [dict(bob, sid='4' * 32)]) == []


def test_presence_reconnect_debounce_cooldown_and_current_name():
    clock = [0.0]
    monitor = FriendPresence([CID], clock=lambda: clock[0])
    assert monitor.update([CID], []) == []
    clock[0] = 10
    assert monitor.update([CID], [peer()])
    clock[0] = 20
    assert monitor.update([CID], []) == []
    clock[0] = 30
    assert monitor.update([CID], [peer()]) == []  # Within the 60 s cooldown.
    clock[0] = 80
    monitor.update([CID], [])
    clock[0] = 82
    assert monitor.update([CID], [peer()]) == []  # Short network flap.
    clock[0] = 90
    monitor.update([CID], [])
    clock[0] = 100
    renamed = peer(sid='5' * 32, nick='Renamed42')
    assert monitor.update([CID], [renamed]) == [renamed]
    assert monitor.update([CID], [renamed]) == []


def test_presence_preferences_masking_changed_keys_and_friend_edits():
    clock = [0.0]
    monitor = FriendPresence([CID], clock=lambda: clock[0])
    clock[0] = 10
    # DND or disabled preference still consumes the transition.
    assert monitor.update([CID], [peer()], enabled=False) == []
    assert monitor.update([CID], [peer()], enabled=True) == []
    clock[0] = 20
    monitor.update([CID], [])
    clock[0] = 30
    assert monitor.update([CID], [peer()], blocked=['1' * 32]) == []
    assert monitor.update([CID], [peer()]) == []
    # Neither adding an already-online friend nor removing it triggers a login.
    assert monitor.update([CID, OTHER], [peer(), peer(cid=OTHER)]) == []
    assert monitor.update([OTHER], [peer(cid=OTHER)]) == []
    assert CID not in monitor.notified and CID not in monitor.offline_since
    clock[0] = 40
    monitor.update([OTHER], [])
    clock[0] = 50
    assert monitor.update([OTHER], [dict(peer(cid=OTHER), security='changed')]) == []
    assert monitor.update([OTHER], [dict(peer(cid=OTHER), encrypted=False)]) == []


def test_presence_simultaneous_connections_are_sorted_and_nonfriends_ignored():
    clock = [0.0]
    monitor = FriendPresence([CID, OTHER], clock=lambda: clock[0])
    clock[0] = 10
    a, b = peer(), peer(cid=OTHER, nick='Bob42')
    stranger = peer(cid='c' * 32)
    assert monitor.update([CID, OTHER], [b, stranger, a]) == [a, b]


def test_friend_menu_data_tracks_identity_security_status_and_popup_route():
    saved = {CID:'OldName42', OTHER:'Offline42', 'c' * 32:'Changed42'}
    a = peer(nick='NewName42')
    changed = dict(peer(cid='c' * 32), security='changed', encrypted=False)
    rows = friend_rows(saved, [a, changed])
    assert [r['contact_id'] for r in rows] == [CID, 'c' * 32, OTHER]
    assert rows[0]['nick'] == 'NewName42' and rows[0]['popup']
    assert rows[1]['status'] == 'Clé modifiée · vérifier' and not rows[1]['popup']
    assert rows[2]['status'] == 'Hors ligne' and not rows[2]['popup']
    assert friend_rows(saved, [a], blocked=[a['sid']])[0]['status'] == 'Masqué'
    assert not friend_rows(saved, [a], blocked=[a['sid']])[0]['popup']
    assert not friend_rows(saved, [a], popup_available=lambda p:False)[0]['popup']
    assert not friend_rows(saved, [dict(a, caps=[])])[0]['popup']
    other_device = dict(a, sid='9' * 32, nick='OtherDevice42', status='away')
    assert friend_rows(saved, [a, other_device], preferred={CID:other_device['sid']})[0]['nick'] == 'OtherDevice42'


def test_friend_notifications_preference_default_validation_and_persistence(tmp_path):
    store = ConfigStore(tmp_path)
    assert store.preferences.friend_notifications
    store.preferences.friend_notifications = False
    store.save()
    assert not ConfigStore(tmp_path).preferences.friend_notifications
    store.path.write_text('{"friend_notifications":"false"}')
    assert ConfigStore(tmp_path).preferences.friend_notifications
