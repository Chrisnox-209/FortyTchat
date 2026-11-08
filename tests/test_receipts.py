"""Delivery and reading confirmations over authenticated direct/relay routes."""
import asyncio
import time

import pytest

from src.config import ConfigStore
from src.features.receipts import ReceiptLedger, delivery_label, update_recipient, READ_BATCH, PER_CHAT
from src.identity import session_id
from src.memory import Message
from src.networking.client import ChatClient
from src.networking.protocol import ProtocolError, event, encode
from src.networking.relay import RelayServer


async def until(predicate, timeout=6):
    end = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > end: raise AssertionError('Confirmation non reçue')
        await asyncio.sleep(.01)


def test_receipt_validation_is_private_bounded_and_strict():
    a, b, mid = session_id(), session_id(), session_id()
    valid = event('read_receipt', a, b, messages=[mid])
    encode(valid)
    for extra in [dict(messages=[]), dict(messages=[mid, mid]), dict(messages=['bad']),
                  dict(messages=[session_id() for _ in range(READ_BATCH + 1)]),
                  dict(to='general'), dict(text='draft'), dict(messages=mid)]:
        with pytest.raises(ProtocolError): encode({**valid, **extra})


def test_statuses_are_monotonic_and_group_counts_do_not_claim_everyone_read():
    a, b = session_id(), session_id()
    message = Message('self', 'Me42', 'secret', outgoing=True, delivery_recipients={
        a: {'nick': 'Alice42', 'state': 'sent', 'reads': True},
        b: {'nick': 'Bob42', 'state': 'sent', 'reads': True}})
    assert delivery_label(message)[0].startswith('✓ Envoyé')
    assert update_recipient(message, a, 'read')  # May arrive before its ACK callback.
    assert not update_recipient(message, a, 'unconfirmed')
    assert not update_recipient(message, a, 'delivered')
    assert not delivery_label(message)[1]
    update_recipient(message, b, 'delivered')
    assert delivery_label(message)[0].startswith('✓✓ Livré') and not delivery_label(message)[1]
    update_recipient(message, b, 'read')
    assert delivery_label(message)[0].startswith('✓✓ Vu') and delivery_label(message)[1]
    assert not update_recipient(message, session_id(), 'read')
    assert '2/2' in delivery_label(message)[2]


def test_ledger_is_bounded_and_confirmations_only_match_addressed_sessions():
    ledger = ReceiptLedger()
    sender, recipient, other = session_id(), session_id(), session_id()
    class Peer: sid = recipient
    ids = []
    for _ in range(PER_CHAT + 1):
        frame = event('message', sender, 'general', text='secret')
        ids.append(frame['id']); ledger.sent(frame, [Peer()]); ledger.received(frame)
    assert len(ledger.outgoing) == len(ledger.incoming) == PER_CHAT
    assert ids[0] not in ledger.outgoing
    assert not ledger.read(other, [ids[-1]])
    assert ledger.read(recipient, [ids[-1]]) == [(ids[-1], 'general')]
    assert not ledger.read(recipient, [ids[-1]])
    ledger.clear()
    assert not ledger.outgoing and not ledger.incoming


def test_reading_preference_is_migrated_and_preserved(tmp_path):
    config = ConfigStore(tmp_path)
    assert config.preferences.read_receipts
    config.preferences.read_receipts = False
    config.save()
    assert not ConfigStore(tmp_path).preferences.read_receipts


@pytest.mark.parametrize('relay', [False, True])
def test_real_private_group_reading_disable_dnd_and_spoofing(relay):
    async def scenario():
        server = RelayServer() if relay else None
        options = {}
        if server: options = dict(relay_host='127.0.0.1', relay_port=await server.start(port=0), tls=False)
        logs = [[], [], []]
        clients = [ChatClient(name, lambda kind, data, i=i: logs[i].append((kind, data)),
                              discovery=False, tcp_port=0, **options)
                   for i, name in enumerate(['Alice42', 'Bob42', 'Chris42'])]
        a, b, c = clients
        def reads(): return [data for kind, data in logs[0] if kind == 'message_read']
        try:
            await asyncio.gather(*(client.start() for client in clients))
            if server: await until(lambda: all(len(client.peers) == 2 for client in clients))
            else:
                for client in clients:
                    for other in clients:
                        if other is not client: client.observe(other.hello(), '127.0.0.1')
            mid = await a.send('message', b.sid, text='DELIVERY_READ_SECRET')
            assert any(kind == 'message_delivery' and data['ok'] for kind, data in logs[0])
            assert not reads()
            # A third participant cannot mark another conversation as read.
            assert await c._send_to(c.peers[a.sid], event('read_receipt', c.sid, a.sid, messages=[mid]))
            assert not reads()
            budget = a.peers[b.sid].bucket.tokens
            await b.mark_read([mid])
            await until(lambda: len(reads()) == 1)
            assert reads()[0] == dict(id=mid, target=b.sid, peer=b.sid)
            assert a.peers[b.sid].bucket.tokens == budget
            await b.mark_read([mid])
            assert len(reads()) == 1
            group = await a.send('message', 'general', text='Pour tous')
            await b.mark_read([group]); await c.mark_read([group])
            await until(lambda: len(reads()) == 3)
            assert {item['peer'] for item in reads() if item['id'] == group} == {b.sid, c.sid}
            assert all(item['target'] == 'general' for item in reads() if item['id'] == group)
            b.preferences(wizz_enabled=True, dnd=True, read_receipts=False)
            private = await a.send('message', b.sid, text='Pas de vu si désactivé')
            await b.mark_read([private])
            assert len(reads()) == 3
            b.preferences(wizz_enabled=True, dnd=True, read_receipts=True)
            await b.mark_read([private])
            await until(lambda: len(reads()) == 4)
            assert reads()[-1]['id'] == private
            assert await a.send('read_receipt', b.sid, messages=[private]) is None
        finally:
            await asyncio.gather(*(client.stop() for client in clients))
            if server: await server.stop()
        assert not a.receipts.outgoing and not b.receipts.incoming
    asyncio.run(scenario())


def test_legacy_encrypted_client_keeps_delivery_without_unknown_read_frames():
    async def scenario():
        events = []
        a = ChatClient('New42', lambda kind, data: events.append((kind, data)), discovery=False, tcp_port=0)
        b = ChatClient('Old42', discovery=False, tcp_port=0)
        original = b.hello
        def hello(kind='hello'):
            data = original(kind)
            data['caps'] = [cap for cap in data['caps'] if cap != 'receipts']
            return b.crypto.sign_presence(data)
        b.hello = hello
        try:
            await asyncio.gather(a.start(), b.start())
            a.observe(b.hello(), '127.0.0.1'); b.observe(a.hello(), '127.0.0.1')
            mid = await b.send('message', a.sid, text='Ancien client chiffré')
            await a.mark_read([mid])
            assert not b.receipts.outgoing[mid]['read']
            assert not a.receipts.incoming[mid]['done']
            own = await a.send('message', b.sid, text='Toujours livré')
            assert any(kind == 'message_delivery' and data['id'] == own and data['ok'] for kind, data in events)
        finally: await asyncio.gather(a.stop(), b.stop())
    asyncio.run(scenario())


def test_read_batches_pending_deduplication_and_retry_backoff():
    async def scenario():
        a, b = ChatClient('Alice42', discovery=False), ChatClient('Bob42', discovery=False)
        a.port = b.port = 39742
        a.observe(b.hello(), '127.0.0.1')
        clock = [0.0]
        a.receipts.clock = lambda: clock[0]
        references = []
        for _ in range(70):
            frame = event('message', b.sid, a.sid, text='local synthetic message')
            a.receipts.received(frame); references.append(frame['id'])
        calls, success = [], [False]
        async def route(peer, frame):
            encode(frame)
            if frame['type'] == 'read_receipt':calls.append(frame)
            await asyncio.sleep(.01)
            return success[0]
        a._send_to = route
        await asyncio.gather(a.mark_read(references), a.mark_read(references))
        assert [len(frame['messages']) for frame in calls] == [32, 32, 6]
        assert not any(item['pending'] for item in a.receipts.incoming.values())
        await a.mark_read(references)
        assert len(calls) == 3
        clock[0], success[0] = 5, True
        await a.mark_read(references)
        assert len(calls) == 6 and all(item['done'] for item in a.receipts.incoming.values())
        assert all('text' not in item for item in a.receipts.incoming.values())
    asyncio.run(scenario())
