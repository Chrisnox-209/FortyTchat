import asyncio
import time
import pytest
from src.features.collaboration import MessageBook, PollBook, group_key
from src.identity import session_id
from src.networking.client import ChatClient
from src.networking.protocol import event, encode, ProtocolError
from src.networking.relay import RelayServer


async def until(predicate, timeout=6):
    end = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > end: raise AssertionError('État de collaboration non atteint')
        await asyncio.sleep(.01)


def events(log, name):
    return [value for kind, value in log if kind == name]


def messages(log):
    return [d['frame'] for d in events(log, 'event') if d['frame']['type'] == 'message']


def test_collaboration_validation_and_bounded_authorized_changes():
    a,b,g,mid=session_id(),session_id(),session_id(),session_id()
    encode(event('group_invite',a,b,room=g,name='Projet',members=[a,b]))
    for patch in [dict(members=[a,a]),dict(name=''),dict(members=['bad',b]),dict(group=g)]:
        with pytest.raises(ProtocolError):encode(event('group_invite',a,b,**dict(room=g,name='Projet',members=[a,b],**{})) | patch)
    for patch in [dict(seq=True),dict(seq=-1),dict(message='bad'),dict(text='')]:
        with pytest.raises(ProtocolError):encode(event('message_edit',a,b,message=mid,seq=1,text='New') | patch)
    for patch in [dict(options=['A','a']),dict(audience=[b]),dict(options=['Only']),dict(question='')]:
        with pytest.raises(ProtocolError):encode(event('poll_create',a,'general',question='Pause ?',options=['Oui','Non'],audience=[a,b]) | patch)
    book=MessageBook()
    original=event('message',a,b,text='Before');mid=original['id']
    book.remember(original,a)
    change=event('message_edit',a,b,message=mid,seq=2,text='After')
    assert book.change(change,b)=='refused'
    assert book.change(dict(change,**{'from':b}),a)=='refused'
    assert book.change(change,a)=='apply'
    assert book.change(dict(change,seq=1),a)=='old'
    assert book.change(event('message_delete',a,b,message=mid,seq=3),a)=='apply'
    assert book.change(dict(change,seq=4),a)=='old'
    delayed=event('message',a,b,text='Delayed')
    pending=event('message_edit',a,b,message=delayed['id'],seq=1,text='Corrected')
    assert book.change(pending,a)=='pending'
    assert book.remember(delayed,a)==[pending]
    for i in range(300):book.remember(event('message',a,b,text='RAM'),a)
    assert len(book.items)==200


@pytest.mark.parametrize('relay',[False,True])
def test_rooms_changes_polls_and_files_are_private_authenticated_and_ephemeral(relay,tmp_path):
    async def scenario():
        server=RelayServer() if relay else None
        options={}
        captured=[]
        if server:
            port=await server.start(port=0);options=dict(relay_host='127.0.0.1',relay_port=port,tls=False)
            original_route=server.route
            def route(connection,data):
                captured.append(dict(data));return original_route(connection,data)
            server.route=route
        logs=[[],[],[],[]]
        clients=[ChatClient(name,lambda kind,data,i=i:logs[i].append((kind,data)),discovery=False,tcp_port=0,**options)
                 for i,name in enumerate(['Alice42','Bob42','Chris42','Outside42'])]
        a,b,c,d=clients;ae,be,ce,de=logs
        try:
            await asyncio.gather(*(client.start() for client in clients))
            if relay:await until(lambda:all(len(client.peers)==3 for client in clients))
            else:
                for client in clients:
                    for other in clients:
                        if client is not other:client.observe(other.hello(),'127.0.0.1')
            gid=await a.create_group('GROUP_SECRET',[b.contact_id,c.contact_id])
            assert gid and gid in b.rooms and gid in c.rooms and not d.rooms
            await b.answer_group(gid,True)
            key=group_key(gid)
            mid=await a.send('message',key,text='ROOM_SECRET_FIRST')
            assert mid and any(f['id']==mid for f in messages(be))
            assert not messages(ce) and not messages(de)
            await a.send('reaction',key,message=mid,emoji='👍')
            await a.send('typing',key,active=True)
            assert any(e['frame']['type']=='reaction' and e['frame']['group']==gid for e in events(be,'event'))
            assert any(e['frame']['type']=='typing' and e['frame']['group']==gid for e in events(be,'event'))
            assert not events(de,'event') and not events(ce,'event')
            await c.answer_group(gid,True)
            later=await a.send('message',key,text='ROOM_SECRET_LATER')
            assert any(f['id']==later for f in messages(ce))
            assert not any(f['id']==mid for f in messages(ce))
            assert not messages(de)
            await b.mark_read([later])
            await until(lambda:any(e['id']==later for e in events(ae,'message_read')))
            assert not await d._send_to(d.peers[b.sid],event('message',d.sid,b.sid,group=gid,text='OUTSIDER'))
            # A different author, or the right author in a different chat, cannot edit.
            assert not await c._send_to(c.peers[b.sid],event('message_edit',c.sid,b.sid,group=gid,message=later,seq=1,text='FORGED'))
            assert not await a._send_to(a.peers[b.sid],event('message_edit',a.sid,b.sid,message=later,seq=1,text='WRONG_SCOPE'))
            await a.send('message_edit',key,message=later,text='ROOM_SECRET_EDITED')
            await until(lambda:bool(events(be,'message_changed')) and bool(events(ce,'message_changed')))
            assert events(be,'message_changed')[-1]['frame']['text']=='ROOM_SECRET_EDITED'
            await a.send('message_delete',key,message=later)
            assert events(be,'message_changed')[-1]['frame']['type']=='message_delete'
            assert not events(de,'message_changed')
            poll=await a.send('poll_create',key,question='POLL_SECRET',options=['Café','Thé'])
            assert poll and poll in b.polls.items and poll in c.polls.items and not d.polls.items
            await b.send('poll_vote',key,poll=poll,option=1)
            assert a.polls.items[poll]['votes']=={b.sid:1} and c.polls.items[poll]['votes']=={b.sid:1}
            await b.send('poll_vote',key,poll=poll,option=0)
            assert a.polls.items[poll]['votes']=={b.sid:0}
            await b.send('poll_vote',key,poll=poll,option=-1)
            assert not a.polls.items[poll]['votes']
            assert not await d._send_to(d.peers[a.sid],event('poll_vote',d.sid,a.sid,group=gid,poll=poll,option=0,seq=100))
            # A clipboard-like image can be offered from memory, without a source file.
            raw=b'RAM_CAPTURE_CONTENT'*100
            offer=await a.offer_file(key,memory=raw,name='capture.png')
            assert offer and events(be,'file_offer') and not events(de,'file_offer')
            await b.accept_file(offer,a.sid,tmp_path/'received.png')
            await until(lambda:bool(events(be,'file_complete')))
            assert (tmp_path/'received.png').read_bytes()==raw
            await a.send('message_delete',key,message=offer)
            assert b.book.items[offer]['deleted'] and (offer,a.sid) not in c.files.incoming
            assert (tmp_path/'received.png').read_bytes()==raw
            await c.answer_group(gid,False)
            after=await a.send('message',key,text='AFTER_LEAVE')
            assert not any(f['id']==after for f in messages(ce))
            assert await c.send('message',key,text='SHOULD_FAIL') is None
            assert captured or not relay
            if captured:
                assert all(frame['type']=='sealed' for frame in captured)
                assert all(secret not in repr(captured) for secret in ('GROUP_SECRET','ROOM_SECRET','POLL_SECRET','RAM_CAPTURE_CONTENT'))
        finally:
            await asyncio.gather(*(client.stop() for client in clients))
            assert all(not client.rooms and not client.book.items and not client.polls.items for client in clients)
            if server:await server.stop()
    asyncio.run(scenario())


def test_general_poll_and_persistent_profile_block_survive_rename_and_new_session():
    async def scenario():
        logs=[[],[],[]]
        clients=[ChatClient(name,lambda kind,data,i=i:logs[i].append((kind,data)),discovery=False,tcp_port=0)
                 for i,name in enumerate(['Alice42','Bob42','Chris42'])]
        a,b,c=clients
        again=None
        try:
            await asyncio.gather(*(client.start() for client in clients))
            for client in clients:
                for other in clients:
                    if client is not other:client.observe(other.hello(),'127.0.0.1')
            poll=await a.send('poll_create','general',question='Pause ?',options=['Oui','Non'])
            await c.send('poll_vote','general',poll=poll,option=0)
            assert a.polls.items[poll]['votes']==b.polls.items[poll]['votes']=={c.sid:0}
            inbound=await b.offer_file(a.sid,memory=b'BEFORE_BLOCK',name='from-b.txt')
            outbound=await a.offer_file(b.sid,memory=b'BEFORE_BLOCK',name='from-a.txt')
            assert (inbound,b.sid) in a.files.incoming and outbound in a.files.outgoing
            a.block_contact(b.contact_id)
            await until(lambda:outbound not in a.files.outgoing)
            assert (inbound,b.sid) not in a.files.incoming
            assert not await b._send_to(b.peers[a.sid],event('message',b.sid,a.sid,text='BLOCKED'))
            b.rename('ChangedBob42');a.observe(b.hello(),'127.0.0.1')
            assert b.sid in a.blocked
            again=ChatClient('NewBob42',contact_id=b.contact_id,crypto_identity=b.identity,discovery=False,tcp_port=0)
            await again.start();a.observe(again.hello(),'127.0.0.1');again.observe(a.hello(),'127.0.0.1')
            assert again.sid in a.blocked
            assert not await again._send_to(again.peers[a.sid],event('message',again.sid,a.sid,text='BLOCKED_NEW_SESSION'))
            a.block_contact(b.contact_id,False)
            assert again.sid not in a.blocked and b.sid not in a.blocked
            assert await again._send_to(again.peers[a.sid],event('message',again.sid,a.sid,text='UNBLOCKED'))
        finally:
            await asyncio.gather(*(client.stop() for client in clients))
            if again:await again.stop()
    asyncio.run(scenario())


def test_legacy_contacts_receive_original_messages_without_unknown_collaboration_events():
    async def scenario():
        logs=[[],[],[]]
        clients=[ChatClient(name,lambda k,d,i=i:logs[i].append((k,d)),discovery=False,tcp_port=0)
                 for i,name in enumerate(['New42','OtherNew42','Legacy42'])]
        a,b,old=clients
        original=old.hello
        def hello(kind='hello'):
            frame=original(kind);frame['caps']=[c for c in frame['caps'] if c!='rooms']
            return old.crypto.sign_presence(frame)
        old.hello=hello
        try:
            await asyncio.gather(*(c.start() for c in clients))
            for c in clients:
                for p in clients:
                    if p is not c:c.observe(p.hello(),'127.0.0.1')
            mid=await a.send('message','general',text='Original')
            assert any(f['id']==mid for f in messages(logs[2]))
            await a.send('message_edit','general',message=mid,text='Edited')
            assert events(logs[1],'message_changed') and not events(logs[2],'message_changed')
            poll=await a.send('poll_create','general',question='Pause ?',options=['Oui','Non'])
            assert poll in b.polls.items and poll not in old.polls.items
            assert any('anciens' in item['text'] for item in events(logs[0],'notice'))
        finally:await asyncio.gather(*(c.stop() for c in clients))
    asyncio.run(scenario())
