"""Group authority, persistence and shared popup spam budget across real E2EE links."""
import asyncio
import copy
import time
import pytest
from src.identity import session_id
from src.security import CryptoIdentity, SecurityError
from src.features.collaboration import group_key
from src.features.group_profile import GroupStore, new_profile, signed, POPUP_INTERVAL, popup_permit
from src.features.cards import answer_card
from src.networking.client import ChatClient
from src.networking.protocol import event, encode, ProtocolError
from src.networking.relay import RelayServer

CARD = dict(title='On souffle ?', text='Le groupe se retrouve au café.', emoji='1f44d')


def records(log, kind): return [d for k,d in log if k == kind]
def cards(log): return [d['frame'] for k,d in log if k == 'event' and d['frame']['type'] == 'card_invite']


async def until(predicate):
    for _ in range(600):
        if predicate(): return
        await asyncio.sleep(.01)
    raise AssertionError('Synchronisation de groupe incomplète')


async def connect(clients):
    await asyncio.gather(*(c.start() for c in clients))
    for c in clients:
        for p in clients:
            if p is not c: c.observe(p.hello(), '127.0.0.1')
    await asyncio.sleep(.03)


def test_private_group_store_is_signed_cas_merged_and_cooldown_survives_restart(tmp_path):
    owner = CryptoIdentity(session_id(), tmp_path)
    a,b = GroupStore(owner), GroupStore(owner)
    p = new_profile(owner, session_id(), 'Équipe', [owner.contact_id, session_id()])
    a.save(p, 'joined', 0)
    p2 = signed(owner, dict(p, revision=2, name='Piscine', popup_at=time.time(), popup_id=session_id(), popup_sender=owner.contact_id))
    a.save(p2, 'joined', 1)
    with pytest.raises(SecurityError): b.save(p2, 'joined', 1)
    with pytest.raises(SecurityError): a.save(p, 'joined')
    permit = popup_permit(owner,p2)
    a.receive_popup(p['id'],permit)
    restored = GroupStore(owner)
    assert restored.records[p['id']]['profile']['name'] == 'Piscine'
    with pytest.raises(SecurityError): restored.receive_popup(p['id'], permit)
    assert not (tmp_path/'group_profiles.json').stat().st_mode & 0o077
    broken = copy.deepcopy(p2); broken['name'] = 'Usurpé'
    with pytest.raises(SecurityError): a.save(broken, 'joined')
    (tmp_path/'group_profiles.json').write_text('{bad')
    damaged = GroupStore(owner)
    assert damaged.error
    with pytest.raises(SecurityError): damaged.save(p2,'joined')


@pytest.mark.parametrize('relay', [False, True])
def test_dynamic_groups_roles_members_and_final_choice_polls(relay, tmp_path):
    async def run():
        server = RelayServer() if relay else None
        options = dict(relay_host='127.0.0.1',relay_port=await server.start(port=0),tls=False) if server else {}
        logs = [[],[],[],[]]
        clients = [ChatClient(n,lambda k,d,i=i:logs[i].append((k,d)),discovery=False,tcp_port=0,**options)
                   for i,n in enumerate(['Owner42','Admin42','Member42','New42'])]
        a,b,c,d = clients
        try:
            await connect(clients)
            if server: await until(lambda:all(len(x.peers)==3 for x in clients))
            gid = await a.create_group('Projet',[b.contact_id,c.contact_id]); key=group_key(gid)
            await b.answer_group(gid, True); await c.answer_group(gid, True)
            assert not await c.update_group(gid, {'name':'Interdit'})
            assert await a.update_group(gid, {'promote':b.contact_id})
            assert b.contact_id in c.rooms[gid].profile['admins']
            assert await b.update_group(gid, {'name':'La team'})
            await until(lambda:c.rooms[gid].name == 'La team')
            assert not await b.update_group(gid, {'remove':a.contact_id})
            assert not await b.update_group(gid, {'promote':c.contact_id})
            assert await b.update_group(gid, {'add':d.contact_id})
            await until(lambda:gid in d.rooms)
            assert d.rooms[gid].state == 'pending'
            await d.answer_group(gid,True)
            await until(lambda:a.group_access(a.peers[d.sid],gid))
            poll = await a.send('poll_create',key,question='Pause ?',options=['Café','Thé','Chocolat'])
            await c.send('poll_vote',key,poll=poll,option=2)
            assert a.polls.items[poll]['votes'] == {c.sid:2}
            assert await c.send('poll_vote',key,poll=poll,option=0) is None
            assert a.polls.items[poll]['votes']=={c.sid:2}
            file = await a.offer_file(key,memory=b'Private group file',name='group.txt')
            assert (file,a.sid) in c.files.incoming
            assert await b.update_group(gid, {'remove':c.contact_id})
            await until(lambda:c.rooms[gid].state == 'removed')
            assert (file,a.sid) not in c.files.incoming
            assert not a.group_access(a.peers[c.sid],gid)
            assert await c.send('message',key,text='Removed') is None
            mid = await a.send('message',key,text='After removal')
            assert not any(d['frame']['id']==mid for d in records(logs[2],'event'))
            assert await a.update_group(gid, {'demote':b.contact_id})
            assert not await b.update_group(gid, {'name':'Refused'})
            with pytest.raises(ProtocolError): encode(event('poll_vote',a.sid,b.sid,poll=poll,seq=3,options=[0,0]))
            with pytest.raises(ProtocolError): encode(event('card_invite',a.sid,'general',**CARD))
        finally:
            await asyncio.gather(*(c.stop() for c in clients))
            if server: await server.stop()
    asyncio.run(run())


@pytest.mark.parametrize('relay', [False, True])
def test_one_group_popup_shared_by_all_senders_replies_private_and_restart_limit(relay,tmp_path):
    async def run():
        server=RelayServer() if relay else None
        options=dict(relay_host='127.0.0.1',relay_port=await server.start(port=0),tls=False) if server else {}
        logs=[[],[],[]]
        ids=[session_id() for _ in logs]
        clients=[ChatClient(n,lambda k,d,i=i:logs[i].append((k,d)),contact_id=ids[i],security_root=tmp_path/str(i),discovery=False,tcp_port=0,**options)
                 for i,n in enumerate(['Owner42','Bob42','Chris42'])]
        a,b,c=clients
        restored=None
        try:
            await connect(clients)
            gid=await a.create_group('Popup team',[b.contact_id,c.contact_id]); key=group_key(gid)
            await b.answer_group(gid,True); await c.answer_group(gid,True)
            await until(lambda:all(len(x.rooms[gid].joined)==3 for x in clients))
            results=await asyncio.gather(b.send('card_invite',key,**CARD),c.send('card_invite',key,**CARD))
            assert sum(bool(r) for r in results)==1
            winner=b if results[0] else c
            losers=[x for x in clients if x is not winner]
            for dest in losers:
                frame=cards(logs[clients.index(dest)])[-1]
                assert frame['from']==winner.sid and frame['group']==gid
                await dest.send('card_reply',winner.sid,request=frame['id'],answer='accepted',**answer_card('accepted'))
                duplicate=event('card_invite',winner.sid,dest.sid,**CARD,group=gid,permit=popup_permit(a.identity,a.rooms[gid].profile))
                assert not await winner._send_to(winner.peers[dest.sid],duplicate)
            replies=[d for d in records(logs[clients.index(winner)],'event') if d['frame']['type']=='card_reply']
            assert len(replies)==2 and not any(d['frame']['type']=='card_reply' for d in records(logs[clients.index(losers[0])],'event'))
            assert await a.send('card_invite',key,**CARD) is None
            # A second owner process sharing this profile cannot reserve another slot.
            restored=ChatClient('OwnerAgain42',contact_id=a.contact_id,security_root=tmp_path/'0',discovery=False,tcp_port=0)
            assert restored.rooms[gid].profile['popup_at'] == a.rooms[gid].profile['popup_at']
            with pytest.raises(SecurityError): restored.reserve_group_popup(restored.rooms[gid],a.contact_id,session_id())
            assert await a.send('card_invite','general',**CARD) is None
        finally:
            await asyncio.gather(*(x.stop() for x in clients))
            if restored: await restored.stop()
            if server: await server.stop()
    asyncio.run(run())


def test_joined_groups_restore_without_messages_and_offline_removal_disables_chat(tmp_path):
    async def run():
        a=ChatClient('Alice42',contact_id=session_id(),security_root=tmp_path/'a',discovery=False,tcp_port=0)
        b=ChatClient('Bob42',contact_id=session_id(),security_root=tmp_path/'b',discovery=False,tcp_port=0)
        clients=[a,b]
        try:
            await connect(clients)
            gid=await a.create_group('Retrouvailles',[b.contact_id]); await b.answer_group(gid,True)
            await a.send('message',group_key(gid),text='EPHEMERAL_NEVER_SAVED')
            aid,bid=a.contact_id,b.contact_id
            await asyncio.gather(*(c.stop() for c in clients))
            a=ChatClient('Alice42',contact_id=aid,security_root=tmp_path/'a',discovery=False,tcp_port=0)
            b=ChatClient('Bob42',contact_id=bid,security_root=tmp_path/'b',discovery=False,tcp_port=0)
            clients=[a,b]
            assert a.rooms[gid].state=='joined' and b.rooms[gid].state=='restoring'
            assert not a.book.items and not b.book.items
            assert all('EPHEMERAL_NEVER_SAVED' not in p.read_text() for p in tmp_path.rglob('*.json'))
            await connect(clients)
            await until(lambda:b.rooms[gid].state=='joined' and a.group_access(a.peers[b.sid],gid) and b.group_access(b.peers[a.sid],gid))
            await b.stop()
            a._remove_peer(b.sid)
            assert await a.update_group(gid,{'remove':bid})
            b=ChatClient('Bob42',contact_id=bid,security_root=tmp_path/'b',discovery=False,tcp_port=0)
            clients=[a,b]
            await b.start(); a.observe(b.hello(),'127.0.0.1'); b.observe(a.hello(),'127.0.0.1')
            await until(lambda:b.rooms[gid].state=='removed')
            assert await b.send('message',group_key(gid),text='Refused') is None
        finally: await asyncio.gather(*(c.stop() for c in clients))
    asyncio.run(run())


def test_shared_popup_reopens_at_42_minutes_and_other_groups_are_independent(tmp_path,monkeypatch):
    from src.features.collaboration import Room
    from src.features.group_profile import validate_permit
    import src.networking.groups as network_groups
    owner=ChatClient('Owner42',security_root=tmp_path,contact_id=session_id(),discovery=False,tcp_port=0)
    stamp=time.time();clock=[stamp]
    monkeypatch.setattr(network_groups.time,'time',lambda:clock[0])
    rooms=[]
    for name in ['Team one','Team two']:
        gid=session_id();p=new_profile(owner.identity,gid,name,[owner.contact_id,session_id()])
        room=Room(gid,name,owner.contact_id,tuple(p['members']),state='joined',joined={owner.contact_id},profile=p)
        owner.rooms[gid]=room;owner.group_store.save(p,'joined',0);rooms.append(room)
    first=owner.reserve_group_popup(rooms[0],owner.contact_id,session_id())
    assert owner.reserve_group_popup(rooms[1],owner.contact_id,session_id())
    clock[0]=stamp+POPUP_INTERVAL-.01
    with pytest.raises(SecurityError): owner.reserve_group_popup(rooms[0],owner.contact_id,session_id())
    clock[0]=stamp+POPUP_INTERVAL
    second=owner.reserve_group_popup(rooms[0],owner.contact_id,session_id())
    assert second['id']!=first['id'] and second['at']==clock[0]
    validate_permit(second,rooms[0].profile,owner.contact_id)
    forged=dict(second,sender=session_id())
    with pytest.raises(SecurityError): validate_permit(forged,rooms[0].profile,forged['sender'])
    clock[0]+=121
    with pytest.raises(SecurityError): validate_permit(second,rooms[0].profile,owner.contact_id)


def test_maximum_signed_group_photo_roster_and_roles_fit_encrypted_envelope():
    from src.security import b64
    a,b=ChatClient('Alice42'),ChatClient('Bob42')
    a.port=b.port=39742
    a.observe(b.hello(),'127.0.0.1');b.observe(a.hello(),'127.0.0.1')
    members=[a.contact_id,b.contact_id]+[session_id() for _ in range(30)]
    profile=new_profile(a.identity,session_id(),'🧩'*64,members)
    # Largest byte-bounded JPEG-shaped metadata; UI decoding is verified separately.
    profile=signed(a.identity,dict(profile,photo=b64(b'\xff\xd8'+b'x'*8188+b'\xff\xd9'),admins=members[1:]))
    frame=event('group_state',a.sid,b.sid,room=profile['id'],profile=profile)
    envelope=a.crypto.seal(a.peers[b.sid],frame)
    assert len(encode(envelope))<32768
    assert b.crypto.open(b.peers[a.sid],envelope)==frame
