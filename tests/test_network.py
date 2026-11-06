import asyncio
import contextlib
import socket
import time

import pytest

from src.config import ConfigStore
from src.networking.client import ChatClient
from src.networking.protocol import event, read_frame, encode
from src.networking.relay import RelayServer


async def until(predicate, timeout=8):
    end = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() >= end:
            raise AssertionError('Condition réseau non satisfaite avant expiration')
        await asyncio.sleep(0.02)


def recorder(nick, **kwargs):
    events = []
    client = ChatClient(nick, lambda kind, data: events.append((kind, data)), tcp_port=0, **kwargs)
    return client, events


def received(events, kind='message'):
    return [data['frame'] for name, data in events if name == 'event' and data['frame']['type'] == kind]


def bootstrap(clients):
    for target in clients:
        for source in clients:
            if source is not target:
                target.observe(source.hello(), '127.0.0.1')


def test_three_real_tcp_clients_general_private_rename_block_and_wizz(tmp_path):
    async def scenario():
        store = ConfigStore(tmp_path)
        a, ae = recorder('Alice42', discovery=False, persist_wizz=store.reserve_wizz)
        b, be = recorder('Bob42', discovery=False)
        c, ce = recorder('Chris42', discovery=False)
        clients = [a, b, c]
        try:
            await asyncio.gather(*(x.start() for x in clients))
            bootstrap(clients)
            await a.send('message', 'general', text='Bonjour le cluster !')
            assert len(received(be)) == len(received(ce)) == 1
            await a.send('message', b.sid, text='Privé pour Bob')
            assert len(received(be)) == 2 and len(received(ce)) == 1
            b.rename('NeonOtter42')
            a.observe(b.hello(), '127.0.0.1')
            assert a.peers[b.sid].nick == 'NeonOtter42'
            await a.send('wizz', b.sid)
            assert len(received(be, 'wizz')) == 1
            assert await a.send('wizz', c.sid) is None
            assert not received(ce, 'wizz')
            assert ConfigStore(tmp_path).preferences.last_wizz_at > 0
            fresh = ChatClient('New42', discovery=False, last_wizz_at=store.preferences.last_wizz_at)
            assert fresh.wizz_cooldown.remaining() > 2500
            b.block(a.sid)
            await a.send('message', b.sid, text='Masqué')
            assert len(received(be)) == 2
            b.block(a.sid, False)
            invitation = await a.send('break_invite', b.sid)
            assert len(received(be, 'break_invite')) == 1
            await b.send('break_reply', a.sid, request=invitation, answer='accepted')
            assert received(ae, 'break_reply')[0]['answer'] == 'accepted'
            assert await a.send('break_invite', b.sid) is None
            b.preferences(wizz_enabled=False, dnd=True)
            forged_wizz = event('wizz', a.sid, b.sid)
            assert not b._receive(forged_wizz)[0]
            assert not b._receive(event('break_invite', c.sid, b.sid))[0]
        finally:
            await asyncio.gather(*(x.stop() for x in clients))
    asyncio.run(scenario())


def test_real_udp_multicast_discovery_and_goodbye():
    async def scenario():
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as temporary:
            temporary.bind(('', 0))
            port = temporary.getsockname()[1]
        a, ae = recorder('UdpAlice42', discovery_port=port)
        b, be = recorder('UdpBob42', discovery_port=port)
        try:
            await asyncio.gather(a.start(), b.start())
            assert a.discovery and b.discovery
            await until(lambda: b.sid in a.peers and a.sid in b.peers, 6)
            a.preferences(wizz_enabled=True, dnd=False, avatar=7)
            await until(lambda: b.peers[a.sid].avatar == 7, 3)
            await a.send('message', b.sid, text='Découverte automatique puis TCP réel')
            assert len(received(be)) == 1
            await b.stop()
            await until(lambda: b.sid not in a.peers, 3)
        finally:
            await a.stop()
            if not b._closing:
                await b.stop()
    asyncio.run(scenario())


def test_lost_ack_retry_deduplicates_received_message():
    class LostAck(ChatClient):
        lost = False
        async def _handle_direct(self, reader, writer):
            if not self.lost:
                self.lost = True
                frame = await read_frame(reader)
                if frame['type'] == 'hello':
                    self.observe(frame, '127.0.0.1')
                    frame = await read_frame(reader)
                assert self._receive(frame)[0]
                writer.close()  # Simulate lost acknowledgment after successful reception.
                await writer.wait_closed()
            else:
                await super()._handle_direct(reader, writer)
    async def scenario():
        a, ae = recorder('Sender42', discovery=False)
        be = []
        b = LostAck('Receiver42', lambda k, d: be.append((k, d)), discovery=False, tcp_port=0)
        try:
            await a.start()
            await b.start()
            bootstrap([a, b])
            mid = await a.send('message', b.sid, text='Livré exactement une fois malgré un ACK perdu')
            assert len(received(be)) == 1 and b.lost
            assert any(k == 'delivery' and d['id'] == mid and d['ok'] for k, d in ae)
        finally:
            await a.stop()
            await b.stop()
    asyncio.run(scenario())


def test_relay_40_clients_fanout_privacy_and_trusted_rate_limits():
    async def scenario():
        server = RelayServer()
        port = await server.start(port=0)
        pairs = [recorder(f'Student{i:03}', discovery=False, relay_host='127.0.0.1', relay_port=port, tls=False) for i in range(40)]
        clients = [x[0] for x in pairs]
        a, b, c = clients[:3]
        try:
            await asyncio.gather(*(x.start() for x in clients))
            await until(lambda: len(server.clients) == 40 and all(len(x.peers) == 39 for x in clients), 10)
            await a.send('message', 'general', text='Un message pour quarante sessions')
            await until(lambda: all(len(received(ev)) == 1 for _, ev in pairs[1:]))
            await a.send('message', b.sid, text='Privé relayé')
            await until(lambda: len(received(pairs[1][1])) == 2)
            assert all(len(received(ev)) == 1 for _, ev in pairs[2:])
            await a.send('wizz', b.sid)
            await until(lambda: len(received(pairs[1][1], 'wizz')) == 1)
            # Bypass the sender UI and client limiter: the relay still rejects another target.
            assert not await a._send_relay(event('wizz', a.sid, c.sid))
            assert not received(pairs[2][1], 'wizz')
            # Bind sender identity to the registered connection.
            assert not await a._send_relay(event('message', b.sid, c.sid, text='Spoof attempt'))
            request = await a.send('break_invite', b.sid)
            await until(lambda: len(received(pairs[1][1], 'break_invite')) == 1)
            await b.send('break_reply', a.sid, request=request, answer='declined')
            await until(lambda: len(received(pairs[0][1], 'break_reply')) == 1)
            for _ in range(20):
                await a._send_relay(event('message', a.sid, b.sid, text='Burst'))
            assert server.clients[a.sid].bucket.tokens < 1
        finally:
            await asyncio.gather(*(x.stop() for x in clients))
            await server.stop()
        assert not server.clients
    asyncio.run(scenario())


def test_relay_restart_reconnect_new_joiner_gets_no_history(tmp_path):
    async def scenario():
        server = RelayServer()
        port = await server.start(port=0)
        a, ae = recorder('Alice42', discovery=False, relay_host='127.0.0.1', relay_port=port, tls=False)
        b, be = recorder('Bob42', discovery=False, relay_host='127.0.0.1', relay_port=port, tls=False)
        c = None
        try:
            await a.start()
            await b.start()
            await until(lambda: len(a.peers) == len(b.peers) == 1)
            await a.send('message', 'general', text='SECRET_BEFORE_RESTART')
            await until(lambda: len(received(be)) == 1)
            await b.stop()
            await server.stop()
            await until(lambda: not a.relay_writer and not a.peers)
            server = RelayServer()
            await server.start(port=port)
            c, ce = recorder('Charlie42', discovery=False, relay_host='127.0.0.1', relay_port=port, tls=False)
            await c.start()
            await until(lambda: a.sid in c.peers and c.sid in a.peers, 10)
            assert not received(ce)
            assert c.sid != b.sid and b.sid not in a.peers
            await a.send('message', 'general', text='Après reconnexion')
            await until(lambda: len(received(ce)) == 1)
            assert received(ce)[0]['text'] == 'Après reconnexion'
            assert not list(tmp_path.iterdir())  # Network service has no storage path.
        finally:
            await a.stop()
            if not b._closing:
                await b.stop()
            if c:
                await c.stop()
            await server.stop()
    asyncio.run(scenario())


def test_occupied_tcp_port_is_reported_not_silently_replaced():
    async def scenario():
        blocker = await asyncio.start_server(lambda r, w: w.close(), '127.0.0.1', 0)
        port = blocker.sockets[0].getsockname()[1]
        errors = []
        client = ChatClient('PortTest42', lambda k, d: errors.append((k, d)), tcp_port=port, discovery=False)
        try:
            await client.start()
            assert client.server is None and client.port == port
            assert any('déjà occupé' in d.get('text', '') for k, d in errors if k == 'notice')
        finally:
            await client.stop()
            blocker.close()
            await blocker.wait_closed()
    asyncio.run(scenario())


def test_unknown_sender_and_truncated_tcp_are_rejected():
    async def scenario():
        client, events = recorder('Target42', discovery=False)
        try:
            await client.start()
            r, w = await asyncio.open_connection('127.0.0.1', client.port)
            w.write(encode(event('message', 'a' * 32, client.sid, text='Unannounced')))
            await w.drain()
            assert not await r.read()
            w.close()
            await w.wait_closed()
            r, w = await asyncio.open_connection('127.0.0.1', client.port)
            w.write(b'{"v":1')
            w.write_eof()
            await w.drain()
            assert not await r.read()
            w.close()
            await w.wait_closed()
            assert not received(events)
        finally:
            await client.stop()
    asyncio.run(scenario())


def test_relay_wizz_limit_survives_same_session_reconnect():
    async def scenario():
        server = RelayServer()
        port = await server.start(port=0)
        a, ae = recorder('Alice42', discovery=False, relay_host='127.0.0.1', relay_port=port, tls=False)
        b, be = recorder('Bob42', discovery=False, relay_host='127.0.0.1', relay_port=port, tls=False)
        try:
            await a.start()
            await b.start()
            await until(lambda: b.sid in a.peers)
            assert await a._send_relay(event('wizz', a.sid, b.sid))
            old = a.relay_writer
            old.close()
            await until(lambda: a.relay_writer is not None and a.relay_writer is not old, 8)
            await until(lambda: b.sid in a.peers)
            assert not await a._send_relay(event('wizz', a.sid, b.sid))
            assert len(received(be, 'wizz')) == 1
        finally:
            await a.stop()
            await b.stop()
            await server.stop()
    asyncio.run(scenario())


def test_collision_converges_without_changing_session_ids():
    async def scenario():
        a, _ = recorder('SameName42', discovery=False)
        b, _ = recorder('SameName42', discovery=False)
        try:
            await a.start()
            await b.start()
            sid_a, sid_b = a.sid, b.sid
            bootstrap([a, b])
            bootstrap([a, b])
            assert a.nickname != b.nickname
            assert a.sid == sid_a and b.sid == sid_b
            await a.send('message', b.sid, text='Identités distinctes malgré le même pseudo initial')
        finally:
            await a.stop()
            await b.stop()
    asyncio.run(scenario())
