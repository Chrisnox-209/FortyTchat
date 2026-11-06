"""E2EE boundaries: identity, tampering, replay, opaque relay and real files."""
import asyncio
import base64
import copy
import json
import os
import stat
import time

import pytest

from src.identity import session_id
from src.networking.client import ChatClient
from src.networking.protocol import encode, event, read_frame
from src.networking.relay import RelayServer
from src.security import CryptoIdentity, SecureSession, SecurityError, fingerprint, REPLAY_WINDOW


def pair():
    a = ChatClient('Alice42', discovery=False, tcp_port=0)
    b = ChatClient('Bob42', discovery=False, tcp_port=0)
    a.port = b.port = 39742
    a.observe(b.hello(), '127.0.0.1')
    b.observe(a.hello(), '127.0.0.1')
    return a, b


def test_encryption_identity_persists_privately_and_session_keys_rotate(tmp_path):
    cid = session_id()
    first = CryptoIdentity(cid, tmp_path)
    second = CryptoIdentity(cid, tmp_path)
    assert first.public == second.public
    assert stat.S_IMODE((tmp_path / 'identity.json').stat().st_mode) == 0o600
    assert stat.S_IMODE((tmp_path / 'trusted_contacts.json').stat().st_mode) == 0o600
    a, b = SecureSession(first, session_id()), SecureSession(second, session_id())
    assert bytes(a.private) != bytes(b.private)
    (tmp_path / 'identity.json').write_text('{broken')
    with pytest.raises(SecurityError): CryptoIdentity(cid, tmp_path)
    assert (tmp_path / 'identity.json').read_text() == '{broken'


def test_insecure_or_wrong_profile_keys_fail_closed(tmp_path):
    cid = session_id()
    CryptoIdentity(cid, tmp_path)
    with pytest.raises(SecurityError): CryptoIdentity(session_id(), tmp_path)
    (tmp_path / 'identity.json').chmod(0o644)
    with pytest.raises(SecurityError): CryptoIdentity(cid, tmp_path)
    (tmp_path / 'identity.json').chmod(0o600)
    (tmp_path / 'trusted_contacts.json').write_text('{broken')
    with pytest.raises(SecurityError): CryptoIdentity(cid, tmp_path)


def test_signed_presence_and_pinned_key_change_require_matching_fingerprint():
    a, b = pair()
    original = b.hello()
    for field, value in [('nick', 'Fake42'), ('sid', session_id()), ('port', 12345), ('status', 'dnd')]:
        bad = {**original, field: value}
        with pytest.raises(SecurityError): SecureSession.check_presence(bad)
    first = a.peers[b.sid]
    assert first.secure and first.security == 'unverified'
    with pytest.raises(SecurityError): a.verify_contact(b.sid, '0000')
    a.verify_contact(b.sid, fingerprint(b.identity.public))
    assert first.security == 'verified'
    a.observe(b.hello(), '127.0.0.1')
    assert first.security == 'verified'
    impostor = ChatClient('Impostor42', discovery=False)
    impostor.sid, impostor.port = b.sid, 39742
    a.observe(impostor.hello(), '127.0.0.1')
    assert impostor.contact_id not in a.identity.trusted
    assert a.peers[b.sid].identity_key == b.identity.public and first.secure
    replacement = ChatClient('BobRenamed42', contact_id=b.contact_id, discovery=False)
    replacement.port = 39742
    a.observe(replacement.hello(), '127.0.0.1')
    peer = a.peers[replacement.sid]
    assert not peer.secure and peer.security == 'changed'
    with pytest.raises(SecurityError): a.crypto.seal(peer, event('message', a.sid, peer.sid, text='secret'))
    with pytest.raises(SecurityError): a.verify_contact(peer.sid, fingerprint(b.identity.public))
    a.verify_contact(peer.sid, fingerprint(replacement.identity.public))
    assert peer.secure and peer.security == 'verified'
    assert not first.secure  # The superseded key cannot remain authorized.


def test_ciphertext_tampering_recipient_header_binding_and_replay():
    a, b = pair()
    frame = event('message', a.sid, b.sid, text='secret 🌈' * 50)
    sealed = a.crypto.seal(a.peers[b.sid], frame)
    assert b'secret' not in encode(sealed)
    for key, value in [('id', session_id()), ('to', session_id()), ('channel', 'stream')]:
        with pytest.raises(SecurityError): b.crypto.open(b.peers[a.sid], {**sealed, key: value})
    raw = bytearray(base64.b64decode(sealed['data'])); raw[-1] ^= 1
    with pytest.raises(SecurityError):
        b.crypto.open(b.peers[a.sid], {**sealed, 'data': base64.b64encode(raw).decode()})
    assert b.crypto.open(b.peers[a.sid], sealed) == frame
    with pytest.raises(SecurityError): b.crypto.open(b.peers[a.sid], sealed)
    # Replay remains forbidden after the bounded replay window has advanced.
    a.crypto.sent[b.sid] += REPLAY_WINDOW
    latest = a.crypto.seal(a.peers[b.sid], frame)
    assert b.crypto.open(b.peers[a.sid], latest) == frame
    with pytest.raises(SecurityError): b.crypto.open(b.peers[a.sid], sealed)
    # Frames legitimately reordered by concurrent TCP connections still work once.
    x = a.crypto.seal(a.peers[b.sid], frame)
    y = a.crypto.seal(a.peers[b.sid], frame)
    b.crypto.open(b.peers[a.sid], y)
    b.crypto.open(b.peers[a.sid], x)
    with pytest.raises(SecurityError): b.crypto.open(b.peers[a.sid], x)
    outsider = ChatClient('Eve42', discovery=False)
    outsider.port = 39742
    outsider.observe(a.hello(), '127.0.0.1')
    with pytest.raises(SecurityError): outsider.crypto.open(outsider.peers[a.sid], sealed)


def test_invalid_inner_sender_and_recipient_are_rejected():
    a, b = pair()
    for frame in [event('message', session_id(), b.sid, text='spoof'),
                  event('message', a.sid, session_id(), text='wrong recipient')]:
        with pytest.raises(SecurityError):
            b.crypto.open(b.peers[a.sid], a.crypto.seal(a.peers[b.sid], frame))


async def until(predicate, timeout=8):
    end = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > end: raise AssertionError('Opération chiffrée non terminée')
        await asyncio.sleep(.01)


@pytest.mark.parametrize('relay', [False, True])
def test_real_encrypted_messages_files_and_no_plaintext_fallback(tmp_path, relay):
    async def scenario():
        server = RelayServer() if relay else None
        events, traffic = [[], [], []], []
        options = {}
        if server:
            port = await server.start(port=0)
            options = dict(relay_host='127.0.0.1', relay_port=port, tls=False)
            original_put = server.put
            def capture(connection, frame):
                traffic.append(copy.deepcopy(frame))
                return original_put(connection, frame)
            server.put = capture
        clients = [ChatClient(name, lambda kind, data, i=i: events[i].append((kind, data)),
                              discovery=False, tcp_port=0, **options)
                   for i, name in enumerate(['Alice42', 'Bob42', 'Eve42'])]
        a, b, c = clients
        source, destination = tmp_path / 'secret-filename.txt', tmp_path / 'saved.txt'
        source.write_bytes(b'FILE-CONTENT-SECRET' * 1500)
        def incoming(i): return [data['frame'] for kind, data in events[i] if kind == 'event']
        try:
            await asyncio.gather(*(client.start() for client in clients))
            if server:
                await until(lambda: all(len(client.peers) == 2 and client.relay_e2ee for client in clients))
            else:
                for client in clients:
                    for other in clients:
                        if client is not other: client.observe(other.hello(), '127.0.0.1')
            await a.send('message', b.sid, text='PRIVATE-MESSAGE-SECRET')
            assert incoming(1)[0]['text'] == 'PRIVATE-MESSAGE-SECRET'
            assert not incoming(2)
            await a.send('message', 'general', text='GENERAL-MESSAGE-SECRET')
            assert incoming(1)[-1]['text'] == incoming(2)[-1]['text'] == 'GENERAL-MESSAGE-SECRET'
            request = await a.offer_file(b.sid, source)
            await until(lambda: any(kind == 'file_offer' for kind, _ in events[1]))
            await b.accept_file(request, a.sid, destination)
            await until(lambda: any(kind == 'file_complete' for kind, _ in events[0]))
            assert destination.read_bytes() == source.read_bytes()
            assert not any(kind == 'file_offer' for kind, _ in events[2])
            # Even a valid public hello never authorizes a plaintext event.
            reader, writer = await asyncio.open_connection('127.0.0.1', b.port)
            writer.write(encode(a.hello()) + encode(event('message', a.sid, b.sid, text='PLAINTEXT')))
            await writer.drain()
            assert not await asyncio.wait_for(reader.read(), 2)
            writer.close(); await writer.wait_closed()
            assert all(frame.get('text') != 'PLAINTEXT' for frame in incoming(1))
            # Capturing all relay traffic discloses neither content nor filenames.
            if server:
                wire = json.dumps(traffic, ensure_ascii=False)
                for secret in ('PRIVATE-MESSAGE-SECRET', 'GENERAL-MESSAGE-SECRET', 'secret-filename.txt',
                               base64.b64encode(source.read_bytes()[:6144]).decode()):
                    assert secret not in wire
                assert not any(frame['type'].startswith('file_') or frame['type'] == 'message' for frame in traffic)
                await server.stop()
        finally:
            await asyncio.gather(*(client.stop() for client in clients))
            if server: await server.stop()
    asyncio.run(scenario())


def test_receipt_must_come_from_the_expected_recipient():
    from src.networking.protocol import ack
    async def scenario():
        a, b = pair()
        c = ChatClient('Eve42', discovery=False)
        c.port = 39742
        c.observe(a.hello(), '127.0.0.1')
        a.observe(c.hello(), '127.0.0.1')
        mid = session_id()
        future = asyncio.get_running_loop().create_future()
        a.acks[mid], a.ack_peers[mid] = future, b.sid
        forged = c.crypto.seal(c.peers[a.sid], ack(mid), 'receipt')
        await a._receive_secure(forged)
        assert not future.done()
        actual = b.crypto.seal(b.peers[a.sid], ack(mid), 'receipt')
        await a._receive_secure(actual)
        assert future.result() is True
    asyncio.run(scenario())


def test_legacy_peer_and_relay_never_enable_plaintext():
    class LegacyRelay(RelayServer):
        def put(self, connection, frame):
            if frame['type'] == 'pong': frame = {**frame, 'caps': []}
            return super().put(connection, frame)
    async def scenario():
        a, b = pair()
        legacy = b.hello(); legacy.pop('crypto')
        a.peers.pop(b.sid)
        a.observe(legacy, '127.0.0.1')
        assert b.sid not in a.peers
        assert await a.send('message', b.sid, text='never plaintext') is None
        relay = LegacyRelay()
        port = await relay.start(port=0)
        client = ChatClient('Updated42', discovery=False, tcp_port=0,
                            relay_host='127.0.0.1', relay_port=port, tls=False)
        try:
            await client.start()
            await until(lambda: bool(client.errors))
            assert client.relay_writer is None and not client.relay_e2ee
            assert not await client._send_relay(event('message', client.sid, b.sid, text='never plaintext'))
        finally:
            await client.stop(); await relay.stop()
    asyncio.run(scenario())


def test_trust_pins_survive_restart_and_missing_files_do_not_reset(tmp_path):
    cid, contact = session_id(), session_id()
    own = CryptoIdentity(cid, tmp_path)
    friend = CryptoIdentity(contact)
    own.remember(contact, friend.public, verified=True)
    loaded = CryptoIdentity(cid, tmp_path)
    assert loaded.trusted[contact] == {'key': friend.public, 'verified': True}
    assert loaded.remember(contact, CryptoIdentity(contact).public) == 'changed'
    assert loaded.trusted[contact]['key'] == friend.public
    (tmp_path / 'trusted_contacts.json').unlink()
    with pytest.raises(SecurityError): CryptoIdentity(cid, tmp_path)
    assert not (tmp_path / 'trusted_contacts.json').exists()


@pytest.mark.parametrize('text', ['😀' * 2000, 'é' * 2000, '"' * 2000])
def test_maximum_unicode_messages_fit_authenticated_envelopes(text):
    a, b = pair()
    message = event('message', a.sid, b.sid, text=text)
    sealed = a.crypto.seal(a.peers[b.sid], message)
    encode(sealed)
    assert b.crypto.open(b.peers[a.sid], sealed) == message


def test_concurrent_profile_copies_merge_trust_without_erasing_newer_pins(tmp_path):
    cid = session_id()
    a, b = CryptoIdentity(cid, tmp_path), CryptoIdentity(cid, tmp_path)
    friend, other = CryptoIdentity(session_id()), CryptoIdentity(session_id())
    a.remember(friend.contact_id, friend.public, verified=True)
    b.remember(other.contact_id, other.public)
    loaded = CryptoIdentity(cid, tmp_path)
    assert len(loaded.trusted) == 2
    assert loaded.trusted[friend.contact_id]['verified']
    replacement = CryptoIdentity(friend.contact_id)
    b.remember(friend.contact_id, replacement.public, verified=True, replace=True)
    assert a.remember(friend.contact_id, friend.public) == 'changed'
    assert CryptoIdentity(cid, tmp_path).trusted[friend.contact_id]['key'] == replacement.public


def test_relay_queue_ack_cannot_forge_endpoint_delivery():
    from src.networking.protocol import ack
    class DishonestRelay(RelayServer):
        def route(self, connection, frame):
            return ack(frame['id'])  # Claims success while dropping the ciphertext.
    async def scenario():
        server = DishonestRelay()
        port = await server.start(port=0)
        options = dict(discovery=False, tcp_port=0, relay_host='127.0.0.1', relay_port=port, tls=False)
        a, b = ChatClient('Alice42', **options), ChatClient('Bob42', **options)
        sending = None
        try:
            await asyncio.gather(a.start(), b.start())
            await until(lambda: b.sid in a.peers and a.sid in b.peers)
            sending = asyncio.create_task(a._send_relay(event('message', a.sid, b.sid, text='secret')))
            await until(lambda: bool(a.acks))
            await asyncio.sleep(.1)
            assert not sending.done()
            assert all(not future.done() for future in a.acks.values())
            assert not b.seen.ids
        finally:
            if sending:
                sending.cancel()
                await asyncio.gather(sending, return_exceptions=True)
            await asyncio.gather(a.stop(), b.stop())
            await server.stop()
    asyncio.run(scenario())
