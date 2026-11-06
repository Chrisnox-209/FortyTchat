import asyncio
import json
import os
from pathlib import Path
import socket
import time
import threading

import pytest

from src.autostart import Autostart, ENTRY_NAME
from src.config import ConfigStore
from src.features.break_request import Invitations
from src.features.wizz import Cooldown, TokenBucket, SeenMessages, WIZZ_INTERVAL
from src.identity import random_nickname, session_id, unique_nickname, valid_nickname
from src.instance import SingleInstance
from src.memory import Conversations, Message
from src.networking.diagnostics import probe_ports
from src.networking.interfaces import interface_record
from src.networking.protocol import ProtocolError, decode, encode, event, read_frame, MAX_FRAME
from src.networking.settings import NetworkConfig


def test_identity_sessions_and_collision():
    names = [random_nickname() for _ in range(1000)]
    assert all(map(valid_nickname, names))
    assert len(set(names)) > 980
    assert session_id() != session_id()
    assert unique_nickname('NeonCat42', ['neoncat42', 'NeonCat42_2']) == 'NeonCat42_3'
    assert len(unique_nickname('A' * 28, ['A' * 28])) <= 28


@pytest.mark.parametrize('nick', ['', 'ab', 'a' * 29, 'hello world', '<script>', 'école42', 'Fox\n'])
def test_invalid_names(nick):
    assert not valid_nickname(nick)


def test_first_run_persistence_and_no_message_storage(tmp_path, monkeypatch):
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path))
    store = ConfigStore()
    assert store.first_run and store.preferences.autostart
    store.preferences.nickname = 'NeonCat42'
    store.save()
    history = Conversations()
    history.append('general', Message('abc', 'A', 'SECRET_CONVERSATION_123'))
    # Config saves only allowlisted preferences even when adjacent state contains messages.
    store.save()
    files = list(tmp_path.rglob('*'))
    assert [p.name for p in files if p.is_file()] == ['config.json']
    assert 'SECRET_CONVERSATION_123' not in store.path.read_text()
    assert os.stat(store.path).st_mode & 0o777 == 0o600
    again = ConfigStore()
    assert not again.first_run and again.preferences.nickname == 'NeonCat42'
    assert not Conversations().get('general')
    history.clear()
    assert not history.chats


def test_corrupt_config_never_reenables_autostart(tmp_path):
    root = tmp_path / 'fortytchat'
    root.mkdir()
    (root / 'config.json').write_text('{broken')
    store = ConfigStore(root)
    assert not store.first_run and not store.preferences.autostart and store.error
    store.save()
    assert json.loads(store.path.read_text())['autostart'] is False


def test_xdg_autostart_disable_stays_disabled_and_only_own_file(tmp_path, monkeypatch):
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path))
    unrelated = tmp_path / 'autostart' / 'another-app.desktop'
    unrelated.parent.mkdir()
    unrelated.write_text('do not touch')
    store = ConfigStore()
    autostart = Autostart(tmp_path / 'project with space', interpreter='/tmp/venv/bin/python')
    autostart.initialize(store)
    store.save()
    assert autostart.enabled()
    content = autostart.path.read_text()
    assert '"/tmp/venv/bin/python"' in content and '--background' in content
    assert str((tmp_path / 'project with space' / 'main.py').absolute()) in content
    autostart.set_enabled(False)
    store.preferences.autostart = False
    store.save()
    autostart.initialize(ConfigStore())
    assert not autostart.enabled()
    assert unrelated.read_text() == 'do not touch'
    assert autostart.path.name == ENTRY_NAME


def test_external_disable_or_removal_not_undone(tmp_path):
    store = ConfigStore(tmp_path / 'prefs')
    auto = Autostart(tmp_path, config_root=tmp_path)
    auto.initialize(store)
    store.save()
    auto.path.unlink()
    loaded = ConfigStore(tmp_path / 'prefs')
    auto.initialize(loaded)
    assert not auto.path.exists() and not loaded.preferences.autostart


def test_preexisting_disabled_entry_on_first_run(tmp_path):
    auto = Autostart(tmp_path, config_root=tmp_path)
    auto.set_enabled(False)
    store = ConfigStore(tmp_path / 'prefs')
    auto.initialize(store)
    assert not auto.enabled() and not store.preferences.autostart


def test_wizz_cooldown_and_bucket_with_clock():
    now = [0.0]
    clock = lambda: now[0]
    gate = Cooldown(WIZZ_INTERVAL, clock)
    assert gate.take() and not gate.take()
    now[0] = 2519
    assert not gate.take()
    now[0] = 2520
    assert gate.take()
    bucket = TokenBucket(3, 1, clock)
    assert [bucket.allow() for _ in range(4)] == [True, True, True, False]
    now[0] += 1
    assert bucket.allow() and not bucket.allow()


def test_invite_reply_bound_to_sender_and_expires():
    now = [0.0]
    invites = Invitations(lambda: now[0])
    invites.add('request', 'Alice')
    assert not invites.consume('request', 'Mallory')
    assert invites.consume('request', 'Alice')
    assert not invites.consume('request', 'Alice')
    invites.add('request2', 'Alice')
    now[0] = 121
    assert not invites.consume('request2', 'Alice')


def test_dedup_and_memory_are_bounded():
    seen = SeenMessages(3)
    assert seen.add('a') and not seen.add('a')
    for key in 'bcdef':
        seen.add(key)
    assert len(seen.ids) == 3 and len(seen.order) == 3
    history = Conversations(per_chat=3, max_chats=2, total_limit=4)
    for i in range(20):
        history.append(str(i % 4), Message('', '', str(i)))
    assert len(history.chats) <= 2 and sum(map(len, history.chats.values())) <= 4


@pytest.mark.parametrize('raw', [b'[]', b'null', b'{"v":1,"type":[]}', b'{"v":true,"type":"ping"}', b'{' + b'x' * 20000, b'{"v":1,"type":"nope"}', b'\xff', b'[' * 2000])
def test_malformed_protocol(raw):
    with pytest.raises(ProtocolError):
        decode(raw)


def test_protocol_unicode_size_control_and_partial_tcp():
    frame = event('message', session_id(), 'general', text='Salut 🐧 <b>texte brut</b>')
    assert decode(encode(frame)) == frame
    for text in ['', 'a' * 2001, '\x00', '\ud800']:
        with pytest.raises(ProtocolError):
            encode({**frame, 'text': text})
    async def scenario():
        reader = asyncio.StreamReader(limit=MAX_FRAME)
        task = asyncio.create_task(read_frame(reader, 1))
        data = encode(frame)
        reader.feed_data(data[:12])
        await asyncio.sleep(0)
        assert not task.done()
        reader.feed_data(data[12:])
        assert await task == frame
        reader.feed_data(b'x' * (MAX_FRAME + 1))
        with pytest.raises(ProtocolError):
            await read_frame(reader)
    asyncio.run(scenario())


def test_interface_selection_uses_effective_netmask():
    item = interface_record('enp4s0f0', '10.12.10.7', '255.255.0.0')
    assert item.broadcast == '10.12.255.255' and item.subnet == '10.12.0.0/16'
    home = interface_record('wlp3s0', '192.168.8.37', '255.255.255.128')
    assert home.broadcast == '192.168.8.127'
    assert not interface_record('docker0', '172.17.0.1', '255.255.0.0')
    assert not interface_record('virbr0', '192.168.122.1', '255.255.255.0', active=False)
    assert not interface_record('enp4s0f0', '10.12.10.7', '255.255.0.0', active=False)


def test_deployment_defaults_and_validation(tmp_path):
    cfg = NetworkConfig.load(tmp_path / 'missing.json')
    assert cfg.udp_port == 39741 and cfg.tcp_port == 39742 and cfg.mode == 'auto'
    assert not cfg.relay_host
    path = tmp_path / 'network.json'
    for invalid in [{'tcp_port': 22}, {'mode': 'relay'}, {'heartbeat': 1}, {'extra': True}, {'multicast_group': '10.12.10.7'}]:
        path.write_text(json.dumps(invalid))
        with pytest.raises((ValueError, TypeError)):
            NetworkConfig.load(path)


def test_local_bind_conflict_diagnostic():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        sock.listen()
        result = probe_ports(0, sock.getsockname()[1])
        assert 'déjà occupé' in result['tests'][1]
        assert 'non vérifiée' in result['tests'][0]


def test_single_instance_activation_and_restart(tmp_path, monkeypatch):
    monkeypatch.setattr('src.instance.tempfile.gettempdir', lambda: str(tmp_path))
    activated = threading.Event()
    first = SingleInstance(activated.set, 'test')
    second = SingleInstance(lambda: None, 'test')
    try:
        assert first.acquire()
        assert not second.acquire()
        assert activated.wait(2)
    finally:
        second.close()
        first.close()
    third = SingleInstance(lambda: None, 'test')
    try:
        assert third.acquire()
    finally:
        third.close()


@pytest.mark.parametrize('avatar', [None, '5', True, 24, -2])
def test_invalid_network_avatar(avatar):
    frame = {'v': 1, 'type': 'hello', 'sid': session_id(), 'nick': 'PixelFox42',
             'status': 'online', 'port': 39742, 'avatar': avatar}
    with pytest.raises(ProtocolError):
        encode(frame)
