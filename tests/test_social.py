"""Replies, reactions and transient typing over real TCP and relay connections."""
import asyncio
import time

import pytest

from src.features.social import TypingState, PendingReactions, SocialOrder
from src.identity import session_id
from src.memory import Conversations, Message
from src.networking.client import ChatClient
from src.networking.protocol import ProtocolError, encode, event
from src.networking.relay import RelayServer


async def until(predicate, timeout=6):
    end = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > end:
            raise AssertionError('Interaction réseau non terminée')
        await asyncio.sleep(.01)


def recorder(name, legacy=False, **options):
    events = []
    client = ChatClient(name, lambda kind, data: events.append((kind, data)), discovery=False, tcp_port=0, **options)
    if legacy:
        original = client.hello
        def hello(kind='hello'):
            data = original(kind)
            data['caps'] = [cap for cap in data['caps'] if cap != 'social']
            return data
        client.hello = hello
    return client, events


def received(events, kind):
    return [data['frame'] for name, data in events if name == 'event' and data['frame']['type'] == kind]


def test_social_validation_rejects_draft_text_unknown_emoji_and_bad_references():
    a, b, mid = session_id(), session_id(), session_id()
    for payload in ({'emoji': 'hello'}, {'emoji': '👍' * 40}, {'message': 'bad'}, {'seq': True}):
        with pytest.raises(ProtocolError):
            encode(event('reaction', a, b, **{'message': mid, 'emoji': '👍', 'seq': 1, **payload}))
    for payload in ({'active': 1}, {'active': True, 'text': 'PRIVATE_DRAFT'}, {'active': True, 'seq': -1}):
        with pytest.raises(ProtocolError):
            encode(event('typing', a, b, **{'seq': 1, **payload}))
    with pytest.raises(ProtocolError):
        encode(event('message', a, b, text='Salut', reply_to='invalid'))
    assert encode(event('reaction', a, 'general', message=mid, emoji='', seq=2))


def test_ephemeral_interactions_expire_stay_bounded_and_replace_per_person():
    now = [0]
    typing = TypingState(lambda: now[0])
    typing.update('general', 'a', 'Alice', True)
    assert typing.names('general') == ['Alice']
    assert typing.names('private') == []
    now[0] = 5
    assert not typing.names('general')
    pending = PendingReactions(lambda: now[0])
    for i in range(400): pending.add('general', str(i), 'actor', '👍', 'Alice')
    assert len(pending.items) == 256
    pending.add('general', '399', 'actor', '', 'Alice')
    assert pending.take('general', '399') == [('actor', '', 'Alice')]
    now[0] += 10
    pending.prune()
    assert not pending.items
    history = Conversations()
    history.append('general', Message('a', 'Alice', 'Salut', id='message'))
    assert history.react('private', 'message', 'a', '👍', 'Alice') is None
    history.react('general', 'message', 'a', '👍', 'Alice')
    message = history.react('general', 'message', 'a', '❤️', 'NouveauPseudo')
    assert message.reactions == {'a': {'emoji': '❤️', 'nickname': 'NouveauPseudo'}}
    history.react('general', 'message', 'a', '', 'NouveauPseudo')
    assert not message.reactions
    order = SocialOrder()
    frame = event('typing', session_id(), 'general', active=False, seq=2)
    order.remember(frame)
    assert not order.newer({**frame, 'active': True, 'seq': 1})


@pytest.mark.parametrize('relay', [False, True])
def test_replies_reactions_typing_are_routed_privately_and_preserve_legacy_messages(relay):
    async def scenario():
        server = RelayServer() if relay else None
        options = {}
        if server:
            port = await server.start(port=0)
            options = {'relay_host': '127.0.0.1', 'relay_port': port, 'tls': False}
        a, ae = recorder('Alice42', **options)
        b, be = recorder('Bob42', **options)
        c, ce = recorder('Chris42', **options)
        old, oe = recorder('Old42', legacy=True, **options)
        clients = [a, b, c, old]
        try:
            await asyncio.gather(*(client.start() for client in clients))
            if server:
                await until(lambda: all(len(client.peers) == 3 and client.relay_social for client in clients))
            else:
                for client in clients:
                    for peer in clients:
                        if client is not peer: client.observe(peer.hello(), '127.0.0.1')
            original = await a.send('message', 'general', text='On révise les pipes ?')
            reply = await b.send('message', 'general', text='Oui !', reply_to=original)
            await until(lambda: any(frame['id'] == reply for frame in received(ae, 'message')))
            assert next(frame for frame in received(ae, 'message') if frame['id'] == reply)['reply_to'] == original
            assert any(frame['text'] == 'Oui !' for frame in received(oe, 'message'))
            await c.send('reaction', 'general', message=original, emoji='👍')
            await until(lambda: bool(received(be, 'reaction')))
            await c.send('reaction', 'general', message=original, emoji='')
            await until(lambda: len(received(be, 'reaction')) == 2)
            assert [frame['emoji'] for frame in received(be, 'reaction')] == ['👍', '']
            b.preferences(wizz_enabled=True, dnd=True)
            await a.send('typing', b.sid, active=True)
            await until(lambda: bool(received(be, 'typing')))
            assert received(be, 'typing')[-1]['active'] is True
            assert not received(ce, 'typing') and not received(oe, 'typing')
            assert not received(oe, 'reaction')
            await a.send('typing', b.sid, active=False)
            await until(lambda: len(received(be, 'typing')) == 2)
            assert received(be, 'typing')[-1]['active'] is False
            b.block(a.sid)
            blocked_count = len(received(be, 'typing'))
            await a.send('typing', b.sid, active=True)
            await asyncio.sleep(.03)
            assert len(received(be, 'typing')) == blocked_count
            b.block(a.sid, False)
            # Older states arriving late cannot resurrect a removed reaction.
            first = event('reaction', c.sid, b.sid, message=original, emoji='', seq=100)
            delayed = event('reaction', c.sid, b.sid, message=original, emoji='😂', seq=99)
            assert b._receive(first)[0] and b._receive(delayed)[0]
            assert received(be, 'reaction')[-1]['emoji'] == ''
            # Typing spam consumes its own budget, leaving ordinary chat usable.
            for i in range(20):
                b._receive(event('typing', c.sid, b.sid, active=True, seq=200 + i))
            assert await c.send('message', b.sid, text='Le chat reste disponible')
            await until(lambda: any(frame['text'] == 'Le chat reste disponible' for frame in received(be, 'message')))
        finally:
            await asyncio.gather(*(client.stop() for client in clients))
            if server: await server.stop()
    asyncio.run(scenario())
