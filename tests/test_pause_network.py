"""Real TCP checks for pauses in DND and per-contact invitation flood protection."""
import asyncio

from src.identity import session_id
from src.networking.client import ChatClient
from src.networking.protocol import event


def recorder(nickname, **options):
    events = []
    client = ChatClient(nickname, lambda kind, data: events.append((kind, data)),
                        discovery=False, tcp_port=0, **options)
    return client, events


def bootstrap(clients):
    for receiver in clients:
        for sender in clients:
            if receiver is not sender:
                receiver.observe(sender.hello(), '127.0.0.1')


def frames(events, kind):
    return [data['frame'] for name, data in events
            if name == 'event' and data['frame']['type'] == kind]


def test_dnd_keeps_pause_invites_and_replies_but_refuses_wizz_over_tcp():
    async def scenario():
        alice, alice_events = recorder('Alice42')
        bob, bob_events = recorder('Bob42')
        clients = [alice, bob]
        try:
            await asyncio.gather(*(client.start() for client in clients))
            bootstrap(clients)
            # Both ends can be in DND: neither invitation nor its answer is lost.
            alice.preferences(wizz_enabled=True, dnd=True)
            bob.preferences(wizz_enabled=True, dnd=True)
            requests = [await alice.send('break_invite', bob.sid) for _ in range(3)]
            assert all(requests) and len(set(requests)) == 3
            assert len(frames(bob_events, 'break_invite')) == 3
            answer = await bob.send('break_reply', alice.sid,
                                    request=requests[0], answer='accepted')
            assert answer is not None
            assert frames(alice_events, 'break_reply')[0]['answer'] == 'accepted'
            # A fourth rapid invitation is blocked at the sender, with feedback.
            assert await alice.send('break_invite', bob.sid) is None
            assert len(frames(bob_events, 'break_invite')) == 3
            notices = [data['text'].lower() for kind, data in alice_events if kind == 'notice']
            assert any('5' in text and 'min' in text for text in notices)
            assert await alice.send('message', bob.sid, text='Le chat reste disponible.')
            assert frames(bob_events, 'message')[-1]['text'] == 'Le chat reste disponible.'
            assert not await alice._send_direct(alice.peers[bob.sid],
                                                event('wizz', alice.sid, bob.sid))
            assert not frames(bob_events, 'wizz')
            assert alice.sid not in bob.blocked  # Invite cooldown never blocks the person.
        finally:
            await asyncio.gather(*(client.stop() for client in clients))
    asyncio.run(scenario())


def test_incoming_pause_protection_survives_rename_and_reconnect_by_contact():
    async def scenario():
        stable_contact = session_id()
        alice, alice_events = recorder('Alice42', contact_id=stable_contact)
        bob, bob_events = recorder('Bob42')
        charlie, charlie_events = recorder('Charlie42')
        clients = [alice, bob, charlie]
        clock = [0.0]
        try:
            await asyncio.gather(*(client.start() for client in clients))
            bootstrap(clients)
            bob.incoming_break_limit.clock = lambda: clock[0]
            bob.preferences(wizz_enabled=True, dnd=True)
            # Bypass the sender limiter deliberately; the receiving app is authoritative.
            first = event('break_invite', alice.sid, bob.sid)
            assert await alice._send_direct(alice.peers[bob.sid], first)
            assert await alice._send_direct(alice.peers[bob.sid], first)
            assert len(frames(bob_events, 'break_invite')) == 1  # ACK retry is not another invite.
            for _ in range(2):
                assert await alice._send_direct(alice.peers[bob.sid],
                                                event('break_invite', alice.sid, bob.sid))
            assert not await alice._send_direct(alice.peers[bob.sid],
                                                event('break_invite', alice.sid, bob.sid))
            assert len(frames(bob_events, 'break_invite')) == 3
            # Another student is unaffected by Alice's pause-only block.
            assert await charlie._send_direct(charlie.peers[bob.sid],
                                              event('break_invite', charlie.sid, bob.sid))
            assert len(frames(bob_events, 'break_invite')) == 4
            assert await alice.send('message', bob.sid, text='Un blocage des pauses uniquement.')
            assert frames(bob_events, 'message')[-1]['text'] == 'Un blocage des pauses uniquement.'
            alice.rename('NouveauPseudo42')
            bob.observe(alice.hello(), '127.0.0.1')
            assert not await alice._send_direct(alice.peers[bob.sid],
                                                event('break_invite', alice.sid, bob.sid))
            # A new process/SID with the same persisted profile remains temporarily blocked.
            bob.observe(alice.hello('bye'), '127.0.0.1')
            await alice.stop()
            replacement, replacement_events = recorder('AutrePseudo42', contact_id=stable_contact)
            clients.append(replacement)
            await replacement.start()
            bootstrap([bob, charlie, replacement])
            clock[0] = 299
            assert not await replacement._send_direct(replacement.peers[bob.sid],
                                                      event('break_invite', replacement.sid, bob.sid))
            clock[0] = 300
            assert await replacement._send_direct(replacement.peers[bob.sid],
                                                  event('break_invite', replacement.sid, bob.sid))
            assert len(frames(bob_events, 'break_invite')) == 5
            assert frames(bob_events, 'break_invite')[-1]['from'] == replacement.sid
            assert replacement.sid not in bob.blocked
        finally:
            await asyncio.gather(*(client.stop() for client in clients))
    asyncio.run(scenario())


def test_outgoing_pause_limit_resets_after_five_minutes_for_same_contact():
    async def scenario():
        alice, alice_events = recorder('Alice42')
        bob, bob_events = recorder('Bob42')
        clients = [alice, bob]
        clock = [0.0]
        try:
            await asyncio.gather(*(client.start() for client in clients))
            bootstrap(clients)
            alice.outgoing_break_limit.clock = lambda: clock[0]
            bob.incoming_break_limit.clock = lambda: clock[0]
            assert all([await alice.send('break_invite', bob.sid) for _ in range(3)])
            assert await alice.send('break_invite', bob.sid) is None
            clock[0] = 299
            assert await alice.send('break_invite', bob.sid) is None
            clock[0] = 300
            # Outgoing protection doesn't transmit its fourth attempt, so the
            # receiver's idle series also ends after these five minutes.
            assert await alice.send('break_invite', bob.sid)
            assert len(frames(bob_events, 'break_invite')) == 4
        finally:
            await asyncio.gather(*(client.stop() for client in clients))
    asyncio.run(scenario())


def test_real_relay_pause_limits_dnd_retry_reconnect_and_message_fairness():
    from src.networking.relay import RelayServer

    async def until(predicate):
        for _ in range(400):
            if predicate():
                return
            await asyncio.sleep(0.01)
        raise AssertionError('Le relais n’a pas livré l’événement attendu')

    async def scenario():
        server = RelayServer()
        port = await server.start(port=0)
        clock = [0.0]
        server.break_limit.clock = lambda: clock[0]
        stable_contact = session_id()
        options = dict(relay_host='127.0.0.1', relay_port=port, tls=False)
        alice, alice_events = recorder('RelayAlice42', contact_id=stable_contact, **options)
        bob, bob_events = recorder('RelayBob42', **options)
        clients = [alice, bob]
        try:
            await asyncio.gather(*(client.start() for client in clients))
            await until(lambda: len(server.clients) == 2 and len(alice.peers) == len(bob.peers) == 1)
            alice.outgoing_break_limit.clock = lambda: clock[0]
            bob.incoming_break_limit.clock = lambda: clock[0]
            alice.preferences(wizz_enabled=True, dnd=True)
            bob.preferences(wizz_enabled=True, dnd=True)
            requests = [await alice.send('break_invite', bob.sid) for _ in range(3)]
            assert all(requests)
            await until(lambda: len(frames(bob_events, 'break_invite')) == 3)
            # A repeated accepted frame is acknowledged without spending another
            # invitation slot or showing another popup at the receiver.
            original = next(data['frame'] for kind, data in alice_events
                            if kind == 'sent' and data['frame']['id'] == requests[0])
            assert await alice._send_relay(original)
            await asyncio.sleep(0.01)
            assert len(frames(bob_events, 'break_invite')) == 3
            assert await alice.send('break_invite', bob.sid) is None
            # Bypass the local limiter: the relay independently refuses the fourth.
            assert not await alice._send_relay(event('break_invite', alice.sid, bob.sid))
            assert await bob.send('break_reply', alice.sid, request=requests[1], answer='declined')
            await until(lambda: len(frames(alice_events, 'break_reply')) == 1)
            assert frames(alice_events, 'break_reply')[0]['answer'] == 'declined'
            assert await alice.send('message', bob.sid, text='Toujours présent malgré la limite des pauses.')
            await until(lambda: len(frames(bob_events, 'message')) == 1)
            await alice.stop()
            await until(lambda: alice.sid not in server.clients and alice.sid not in bob.peers)
            # The relay's key includes the stable sender and recipient contact IDs,
            # so restarting with a different nickname/SID cannot erase the block.
            replacement, replacement_events = recorder('RelayNewName42', contact_id=stable_contact, **options)
            clients.append(replacement)
            await replacement.start()
            await until(lambda: replacement.sid in server.clients and replacement.sid in bob.peers
                        and bob.sid in replacement.peers)
            clock[0] = 299
            assert not await replacement._send_relay(event('break_invite', replacement.sid, bob.sid))
            assert await replacement.send('message', bob.sid, text='Une pause bloquée ne bloque pas le chat.')
            await until(lambda: len(frames(bob_events, 'message')) == 2)
            assert len(frames(bob_events, 'break_invite')) == 3
            clock[0] = 300
            assert await replacement._send_relay(event('break_invite', replacement.sid, bob.sid))
            await until(lambda: len(frames(bob_events, 'break_invite')) == 4)
            assert frames(bob_events, 'break_invite')[-1]['from'] == replacement.sid
        finally:
            await asyncio.gather(*(client.stop() for client in clients))
            await server.stop()
    asyncio.run(scenario())


def test_already_blocked_pause_attempts_leave_message_buckets_available():
    """A pause-only ban cannot be turned into a temporary ban of ordinary chat."""
    from src.networking.relay import RelayServer

    async def scenario(route):
        server = None
        options = {}
        if route == 'relay':
            server = RelayServer()
            port = await server.start(port=0)
            options = dict(relay_host='127.0.0.1', relay_port=port, tls=False)
        alice, alice_events = recorder('AliceFair42', **options)
        bob, bob_events = recorder('BobFair42', **options)
        clients = [alice, bob]
        try:
            await asyncio.gather(*(client.start() for client in clients))
            if route == 'relay':
                for _ in range(400):
                    if len(alice.peers) == len(bob.peers) == 1:
                        break
                    await asyncio.sleep(0.01)
                assert len(alice.peers) == len(bob.peers) == 1
            else:
                bootstrap(clients)

            async def invite():
                if route == 'sender':
                    return await alice.send('break_invite', bob.sid)
                frame = event('break_invite', alice.sid, bob.sid)
                if route == 'receiver':
                    return await alice._send_direct(alice.peers[bob.sid], frame)
                return await alice._send_relay(frame)

            assert all([await invite() for _ in range(3)])
            assert not await invite()
            for _ in range(20):
                assert not await invite()
            message_id = await alice.send('message', bob.sid, text='Les pauses sont limitées, le chat continue.')
            assert message_id is not None, route
            for _ in range(400):
                if frames(bob_events, 'message'):
                    break
                await asyncio.sleep(0.01)
            assert len(frames(bob_events, 'message')) == 1, route
            assert frames(bob_events, 'message')[0]['id'] == message_id
        finally:
            await asyncio.gather(*(client.stop() for client in clients))
            if server:
                await server.stop()

    for route in ('sender', 'receiver', 'relay'):
        asyncio.run(scenario(route))
