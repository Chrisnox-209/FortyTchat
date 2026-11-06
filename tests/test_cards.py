import asyncio
import json
import os
import time

import pytest

from src.features.cards import CardTemplates, EMOJIS, answer_card
from src.identity import session_id
from src.networking.client import ChatClient
from src.networking.protocol import ProtocolError, encode, event
from src.networking.relay import RelayServer

CARD = {'title': 'On bouge ?', 'text': 'Un petit café entre potes ?', 'emoji': '1f923'}


def test_card_protocol_rejects_urls_oversized_text_and_unsolicited_answers():
    a, b = session_id(), session_id()
    assert encode(event('card_invite', a, b, **CARD))
    for change in ({'emoji': '../evil.gif'}, {'emoji': 'https://example.com/a.gif'},
                   {'title': ''}, {'title': 'a' * 81}, {'text': 'a' * 401}, {'text': '\ud800'}, {'text': '\0'}):
        with pytest.raises(ProtocolError): encode(event('card_invite', a, b, **{**CARD, **change}))
    with pytest.raises(ProtocolError): encode(event('card_invite', a, 'general', **CARD))
    with pytest.raises(ProtocolError): encode(event('card_reply', a, b, request='bad', answer='custom', **CARD))
    with pytest.raises(ProtocolError): encode(event('card_reply', a, b, request=session_id(), answer='other', **CARD))
    with pytest.raises(ProtocolError): encode(event('card_invite', a, b, **CARD, url='https://example.com'))


def test_personal_templates_are_validated_bounded_private_and_reusable(tmp_path):
    models = CardTemplates(tmp_path)
    assert not models.path.exists()
    first = models.save(CARD, 'invite')
    assert os.stat(models.path).st_mode & 0o777 == 0o600
    assert CardTemplates(tmp_path).items[0]['emoji'] == '1f923'
    models.save({**CARD, 'text': 'On se retrouve dehors !'}, 'invite', first)
    assert len(models.items) == 1 and models.items[0]['text'] == 'On se retrouve dehors !'
    for i in range(11): models.save({**CARD, 'title': f'Modèle {i}'}, 'reply')
    with pytest.raises(ValueError): models.save(CARD, 'invite')
    models.remove(first)
    assert len(CardTemplates(tmp_path).items) == 11
    models.path.write_text('[{"bad":true}]')
    broken = CardTemplates(tmp_path)
    assert not broken.items and broken.error
    models.path.write_text('[' * 1500 + ']' * 1500)
    assert CardTemplates(tmp_path).error
    assert not list(tmp_path.glob('.notifications-*'))


async def until(predicate, timeout=7):
    end = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > end: raise AssertionError('Notification non reçue')
        await asyncio.sleep(.01)


def recorder(name, **options):
    events = []
    client = ChatClient(name, lambda kind, data: events.append((kind, data)), discovery=False, tcp_port=0, **options)
    return client, events


def frames(events, kind):
    return [data['frame'] for name, data in events if name == 'event' and data['frame']['type'] == kind]


async def setup(clients, server):
    await asyncio.gather(*(client.start() for client in clients))
    if server:
        await until(lambda: all(len(client.peers) == len(clients) - 1 and client.relay_cards for client in clients))
    else:
        for client in clients:
            for peer in clients:
                if client is not peer: client.observe(peer.hello(), '127.0.0.1')


@pytest.mark.parametrize('relay', [False, True])
def test_cards_and_custom_pause_replies_are_private_bound_and_rate_limited(relay):
    async def scenario():
        server = RelayServer() if relay else None
        options = {}
        if server:
            options = dict(relay_host='127.0.0.1', relay_port=await server.start(port=0), tls=False)
        a, ae = recorder('Alice42', **options)
        b, be = recorder('Bob42', **options)
        c, ce = recorder('Chris42', **options)
        clients = [a, b, c]
        try:
            await setup(clients, server)
            request = await a.send('card_invite', b.sid, **CARD)
            await until(lambda: bool(frames(be, 'card_invite')))
            assert not frames(ce, 'card_invite')
            assert frames(be, 'card_invite')[0]['text'] == CARD['text']
            await b.send('card_reply', a.sid, request=request, answer='accepted', **answer_card('accepted'))
            await until(lambda: bool(frames(ae, 'card_reply')))
            assert frames(ae, 'card_reply')[0]['emoji'] == '1f44d'
            assert await b.send('card_reply', a.sid, request=request, answer='accepted', **answer_card('accepted')) is None
            request = await a.send('card_invite', b.sid, **CARD)
            await until(lambda: len(frames(be, 'card_invite')) == 2)
            custom = {'title': 'Dans deux minutes !', 'text': 'Je finis ma fonction et je viens.', 'emoji': '1f4aa'}
            await b.send('card_reply', a.sid, request=request, answer='custom', **custom)
            await until(lambda: len(frames(ae, 'card_reply')) == 2)
            assert frames(ae, 'card_reply')[-1]['text'] == custom['text']
            # A classic pause can receive a custom animated answer from a new client.
            request = await a.send('break_invite', b.sid)
            await until(lambda: bool(frames(be, 'break_invite')))
            forged = event('card_reply', c.sid, a.sid, request=request, answer='custom', **custom)
            assert not a._receive(forged)[0]
            await b.send('card_reply', a.sid, request=request, answer='custom', **custom)
            await until(lambda: len(frames(ae, 'card_reply')) == 3)
            assert await a.send('card_invite', b.sid, **CARD) is None
            assert await a.send('message', b.sid, text='Les messages restent disponibles.')
            await until(lambda: bool(frames(be, 'message')))
            assert len(frames(be, 'card_invite')) == 2
        finally:
            await asyncio.gather(*(client.stop() for client in clients))
            if server: await server.stop()
    asyncio.run(scenario())


@pytest.mark.parametrize('relay', [False, True])
def test_custom_cards_respect_dnd_legacy_clients_and_request_expiration(relay):
    async def scenario():
        server = RelayServer() if relay else None
        options = {}
        if server: options = dict(relay_host='127.0.0.1', relay_port=await server.start(port=0), tls=False)
        a, ae = recorder('Alice42', **options)
        b, be = recorder('Bob42', **options)
        old, oe = recorder('Old42', **options)
        original_hello = old.hello
        def hello(kind='hello'):
            data = original_hello(kind)
            data['caps'] = [cap for cap in data['caps'] if cap != 'cards']
            return data
        old.hello = hello
        clients = [a, b, old]
        try:
            await setup(clients, server)
            assert await a.send('card_invite', old.sid, **CARD) is None
            assert not frames(oe, 'card_invite')
            b.preferences(wizz_enabled=True, dnd=True)
            if server: await until(lambda: server.clients[b.sid].hello['status'] == 'dnd')
            await a.send('card_invite', b.sid, **CARD)
            await asyncio.sleep(.04)
            assert not frames(be, 'card_invite')
            request = await a.send('break_invite', b.sid)
            await until(lambda: bool(frames(be, 'break_invite')))
            await b.send('card_reply', a.sid, request=request, answer='custom', **CARD)
            await until(lambda: bool(frames(ae, 'card_reply')))
            assert frames(ae, 'card_reply')[-1]['answer'] == 'custom'
            a.outgoing_invites.add('e' * 32, b.sid)
            a.outgoing_invites.pending['e' * 32] = (b.sid, 0)
            expired = event('card_reply', b.sid, a.sid, request='e' * 32, answer='custom', **CARD)
            assert not a._receive(expired)[0]
            assert await a.send('message', old.sid, text='Ancien client compatible pour le texte.')
            await until(lambda: bool(frames(oe, 'message')))
        finally:
            await asyncio.gather(*(client.stop() for client in clients))
            if server: await server.stop()
    asyncio.run(scenario())
