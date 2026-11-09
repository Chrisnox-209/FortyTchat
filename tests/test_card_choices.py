import asyncio
from copy import deepcopy
import os
import time

import pytest

from src.features.cards import CardTemplates, default_responses, valid_responses
from src.identity import session_id
from src.networking.client import ChatClient
from src.networking.protocol import ProtocolError, encode, event
from src.networking.relay import RelayServer

CARD = {'title': 'On va manger ?', 'text': 'Une petite pause ensemble ?', 'emoji': '1f923'}


def test_choice_schema_and_templates_roundtrip(tmp_path):
    policy = default_responses()
    policy['buttons'].append(dict(label='Dans cinq minutes', answer='custom', title='Je finis ma fonction', text='Je te rejoins bientôt !', emoji='1f4aa'))
    assert valid_responses(policy)
    models = CardTemplates(tmp_path)
    models.save({**CARD, 'responses': policy}, 'invite')
    assert CardTemplates(tmp_path).items[0]['responses'] == policy
    assert os.stat(models.path).st_mode & 0o777 == 0o600
    assert valid_responses({'buttons': [], 'free': False})
    a, b = session_id(), session_id()
    encode(event('card_invite', a, b, **CARD, responses=policy))
    for kind, payload in [('break_invite', {}), ('wizz', {}), ('message', {'text': 'test'})]:
        with pytest.raises(ProtocolError): encode(event(kind, a, b, **payload, responses=policy))
        with pytest.raises(ProtocolError): encode(event(kind, a, b, **payload, choice=0))
    for bad in [dict(policy, extra=True), dict(policy, free='yes'), dict(policy, buttons=policy['buttons'] * 2),
                dict(policy, buttons=[policy['buttons'][0]] * 2),
                dict(policy, buttons=[dict(policy['buttons'][0], label='')]),
                dict(policy, buttons=[dict(policy['buttons'][0], emoji='https://example.org/a.gif')])]:
        assert not valid_responses(bad)
        with pytest.raises(ProtocolError): encode(event('card_invite', a, b, **CARD, responses=bad))
    for value in (True, -1, 3, '1'):
        with pytest.raises(ProtocolError): encode(event('card_reply', b, a, request=session_id(), answer='custom', choice=value, **CARD))
    huge = deepcopy(policy)
    for i, item in enumerate(huge['buttons']):
        item.update(label=f'Choix {i}', text='😀' * 400, title='😀' * 80)
    previous = models.path.read_bytes()
    for i in range(12):
        try:
            models.save({**CARD, 'title':'😀' * 80, 'text':'😀' * 400, 'responses':huge}, 'invite')
            previous = models.path.read_bytes()
        except ValueError as exc:
            assert 'volumineux' in str(exc)
            assert models.path.read_bytes() == previous
            assert len(previous) <= 65536 and not CardTemplates(tmp_path).error
            break
    else:
        raise AssertionError('Le quota de modèles doit être appliqué avant une écriture trop grande')


async def until(predicate):
    end = time.monotonic() + 6
    while not predicate():
        if time.monotonic() > end: raise AssertionError('Choix de notification non livré')
        await asyncio.sleep(.01)


@pytest.mark.parametrize('relay', [False, True])
def test_author_choices_enforced_private_and_close_only_rejects_answers(relay):
    async def scenario():
        server = RelayServer() if relay else None
        options = dict(relay_host='127.0.0.1', relay_port=await server.start(port=0), tls=False) if server else {}
        logs = [[], [], []]
        clients = [ChatClient(nick, lambda kind, data, i=i: logs[i].append((kind, data)), discovery=False, tcp_port=0, **options)
                   for i, nick in enumerate(['Alice42', 'Bob42', 'Chris42'])]
        a, b, c = clients
        def frames(index, kind): return [data['frame'] for name, data in logs[index] if name == 'event' and data['frame']['type'] == kind]
        try:
            await asyncio.gather(*(client.start() for client in clients))
            if server: await until(lambda: all(len(client.peers) == 2 for client in clients))
            else:
                for client in clients:
                    for peer in clients:
                        if client is not peer: client.observe(peer.hello(), '127.0.0.1')
            policy = default_responses()
            policy['free'] = False
            policy['buttons'][1].update(label='Je reste coder', title='Pas maintenant', text='Je bosse encore.', emoji='1f44e')
            request = await a.send('card_invite', b.sid, **CARD, responses=policy)
            await until(lambda: bool(frames(1, 'card_invite')))
            assert frames(1, 'card_invite')[0]['responses'] == policy and not frames(2, 'card_invite')
            selected = {key: policy['buttons'][1][key] for key in ('answer','title','text','emoji')}
            assert not a._receive(event('card_reply', c.sid, a.sid, request=request, choice=1, **selected))[0]
            assert not a._receive(event('card_reply', b.sid, a.sid, request=request, choice=1, **{**selected, 'text':'Different'}))[0]
            assert not a._receive(event('card_reply', b.sid, a.sid, request=request, answer='custom', **CARD))[0]
            assert request in a.outgoing_invites.pending
            assert await b.send('card_reply', a.sid, request=request, choice=1, **selected)
            await until(lambda: bool(frames(0, 'card_reply')))
            assert frames(0, 'card_reply')[0]['title'] == 'Pas maintenant'
            assert not a.card_policies['outgoing'] and not b.card_policies['incoming']
            request = await a.send('card_invite', b.sid, **CARD, responses={'buttons': [], 'free':False})
            await until(lambda: len(frames(1, 'card_invite')) == 2)
            assert await b.send('card_reply', a.sid, request=request, answer='custom', **CARD) is None
            assert not a._receive(event('break_reply', b.sid, a.sid, request=request, answer='dismissed'))[0]
            request2 = await c.send('card_invite', b.sid, **CARD, responses={'buttons': [], 'free':True})
            await until(lambda: len(frames(1, 'card_invite')) == 3)
            assert await b.send('card_reply', c.sid, request=request2, answer='custom', **CARD)
            await until(lambda: len(frames(2, 'card_reply')) == 1)
            a.outgoing_invites.pending[request] = (b.sid, 0)
            a.prune_card_policies()
            assert request not in a.card_policies['outgoing']
        finally:
            await asyncio.gather(*(client.stop() for client in clients))
            if server: await server.stop()
    asyncio.run(scenario())


def test_old_encrypted_clients_keep_classic_cards_but_do_not_receive_choices():
    async def scenario():
        a, b = ChatClient('Alice42', discovery=False, tcp_port=0), ChatClient('Old42', discovery=False, tcp_port=0)
        original = b.hello
        def hello(kind='hello'):
            data = original(kind); data['caps'] = [cap for cap in data['caps'] if cap != 'choices']
            return b.crypto.sign_presence(data)
        b.hello = hello
        try:
            await asyncio.gather(a.start(), b.start())
            a.observe(b.hello(), '127.0.0.1'); b.observe(a.hello(), '127.0.0.1')
            assert await a.send('card_invite', b.sid, **CARD, responses=default_responses()) is None
            assert not a.outgoing_invites.pending
            assert await a.send('card_invite', b.sid, **CARD)
        finally: await asyncio.gather(a.stop(), b.stop())
    asyncio.run(scenario())


def test_custom_close_label_is_bounded_and_saved_outside_legacy_templates(tmp_path):
    import json
    closed={'buttons':[],'free':False}
    a,b=session_id(),session_id()
    card={**CARD,'responses':closed,'close_label':'Bien reçu !'}
    encode(event('card_invite',a,b,**card))
    for bad in ('', 'x'*33, 'Fermer\nOui', True, None):
        with pytest.raises(ProtocolError):encode(event('card_invite',a,b,**{**card,'close_label':bad}))
    with pytest.raises(ProtocolError):encode(event('card_invite',a,b,**CARD,close_label='Fermer'))
    with pytest.raises(ProtocolError):encode(event('message',a,b,text='Test',close_label='Fermer'))
    models=CardTemplates(tmp_path);mid=models.save(card,'invite')
    assert 'close_label' not in json.loads(models.path.read_text())[0]
    assert CardTemplates(tmp_path).items[0]['close_label']=='Bien reçu !'
    assert models.labels_path.stat().st_mode&0o777==0o600
    # Invalid new metadata never makes the old model file unreadable.
    models.labels_path.write_text('{broken')
    loaded=CardTemplates(tmp_path)
    assert len(loaded.items)==1 and 'close_label' not in loaded.items[0]
    loaded.remove(mid)
    assert not CardTemplates(tmp_path).items


@pytest.mark.parametrize('relay',[False,True])
def test_new_peers_deliver_custom_close_labels_private_and_group(relay):
    async def run():
        server=RelayServer() if relay else None
        options=dict(relay_host='127.0.0.1',relay_port=await server.start(port=0),tls=False) if relay else {}
        logs=[[],[]]
        a,b=[ChatClient(name,lambda kind,data,i=i:logs[i].append((kind,data)),discovery=False,tcp_port=0,**options)
             for i,name in enumerate(('Alice42','Bob42'))]
        def cards():return [data['frame'] for kind,data in logs[1] if kind=='event' and data['frame']['type']=='card_invite']
        try:
            await a.start();await b.start()
            if relay:await until(lambda:a.sid in b.peers and b.sid in a.peers)
            else:a.observe(b.hello(),'127.0.0.1');b.observe(a.hello(),'127.0.0.1')
            await until(lambda:'close_label' in a.peers[b.sid].features)
            payload={**CARD,'responses':{'buttons':[],'free':False},'close_label':'Ça roule !'}
            assert await a.send('card_invite',b.sid,**payload)
            assert cards()[-1]['close_label']=='Ça roule !'
            gid=await a.create_group('Le groupe',[b.contact_id]);await b.answer_group(gid,True)
            assert await a.send('card_invite','group:'+gid,**payload)
            assert len(cards())==2 and cards()[-1]['close_label']=='Ça roule !' and cards()[-1]['group']==gid
        finally:
            await a.stop();await b.stop()
            if server:await server.stop()
    asyncio.run(run())
