"""Screen transport and consent tests; every image is synthetic."""
import asyncio
import base64
from copy import deepcopy
import time

import pytest
from src.features.screen_share import FEATURE, MAX_IMAGE, MAX_PARTS, CHUNK_SIZE
from src.identity import session_id
from src.networking.client import ChatClient
from src.networking.protocol import ProtocolError, encode, event
from src.networking.relay import RelayServer
from src.networking.screen_share import ScreenSession


async def until(predicate,timeout=7):
    end=time.monotonic()+timeout
    while not predicate():
        assert time.monotonic()<end,'État du partage non atteint'
        await asyncio.sleep(.01)


def test_screen_protocol_is_private_strict_and_bounded():
    a,b,share=session_id(),session_id(),session_id()
    base=event('screen_chunk',a,b,share=share,seq=1,part=0,total=1,width=1280,height=720,data=base64.b64encode(b'test').decode())
    encode(base)
    for kind,payload in [('screen_offer',{}),('screen_stop',{}),('screen_answer',{'accepted':True})]:
        frame=event(kind,a,b,share=share,**payload);encode(frame)
        for bad in (dict(frame,to='general'),dict(frame,to='group:'+share),dict(frame,group=share),dict(frame,command='anything')):
            with pytest.raises(ProtocolError):encode(bad)
    for change in [dict(total=MAX_PARTS+1),dict(part=1),dict(seq=True),dict(seq=0),dict(width=1281),dict(height=721),
                   dict(data='!'),dict(data=''),dict(data=base64.b64encode(b'x'*(CHUNK_SIZE+1)).decode()),
                   dict(total=2),dict(share='bad'),dict(width=1.0)]:
        with pytest.raises(ProtocolError):encode({**base,**change})
    with pytest.raises(ProtocolError):encode(event('screen_answer',a,b,share=share,accepted=1))


@pytest.mark.parametrize('relay',[False,True])
def test_real_screen_consent_frames_privacy_stop_and_chat_coexistence(relay):
    async def run():
        server=RelayServer() if relay else None
        options=dict(relay_host='127.0.0.1',relay_port=await server.start(port=0),tls=False) if relay else {}
        logs=[[],[],[]]
        clients=[ChatClient(nick,lambda kind,data,i=i:logs[i].append((kind,data)),discovery=False,tcp_port=0,**options)
                 for i,nick in enumerate(('Alice42','Bob42','Chris42'))]
        a,b,c=clients
        images=lambda:[d for k,d in logs[1] if k=='screen_frame']
        try:
            await asyncio.gather(*(client.start() for client in clients))
            if relay:await until(lambda:all(len(client.peers)==2 for client in clients))
            else:
                for client in clients:
                    for peer in clients:
                        if client is not peer:client.observe(peer.hello(),'127.0.0.1')
            await until(lambda:all(FEATURE in a.peers[p.sid].features for p in (b,c)))
            share=await a.screens.offer(b.sid);assert share and b.screens.state.phase=='waiting'
            data=b'PRIVATE_SCREEN_PIXELS_'+bytes(range(256))*350
            assert not await a.screens.send_frame(share,data,1000,600)
            assert not images()
            # Neither a third party nor a guessed session can authorize it.
            assert not a.screens.receive(a.peers[c.sid],event('screen_answer',c.sid,a.sid,share=share,accepted=True))[0]
            assert not await b.screens.answer(session_id(),True)
            assert await b.screens.answer(share,True)
            assert a.screens.state.phase=='active'
            assert await a.screens.send_frame(share,data,1000,600)
            await until(lambda:len(images())==1)
            assert images()[0]['data']==data and images()[0]['width']==1000
            assert not [d for k,d in logs[2] if k=='screen_frame']
            assert b.screens.state.buffer==bytearray()
            writer=a.screens.writer
            assert await a.send('message',b.sid,text='Le tchat reste disponible')
            assert await a.screens.send_frame(share,b'NEXT_SCREEN_IMAGE',1000,600)
            assert len(images())==2
            if not relay:assert a.screens.writer is writer and writer is not None
            assert await b.screens.stop(share)
            await until(lambda:a.screens.state is None)
            assert not await a.screens.send_frame(share,data,1000,600)
            assert a.screens.writer is None and b.screens.state is None
            assert await a.send('message',b.sid,text='Après le partage')
            assert len([d for k,d in logs[1] if k=='event' and d['frame']['type']=='message'])==2
            # The regular chat API cannot bypass the consent machinery.
            assert await a.send('screen_offer',c.sid,share=session_id()) is None
        finally:
            await asyncio.gather(*(client.stop() for client in clients))
            if server:await server.stop()
    asyncio.run(run())


def test_screen_rejection_dnd_expiry_block_and_session_lock():
    async def run():
        a,b=ChatClient('Alice42',discovery=False,tcp_port=0),ChatClient('Bob42',discovery=False,tcp_port=0)
        try:
            await a.start();await b.start();a.observe(b.hello(),'127.0.0.1');b.observe(a.hello(),'127.0.0.1')
            await until(lambda:FEATURE in a.peers[b.sid].features)
            b.preferences(wizz_enabled=True,dnd=True)
            assert await a.screens.offer(b.sid) is None and b.screens.state is None
            b.preferences(wizz_enabled=True,dnd=False)
            share=await a.screens.offer(b.sid);assert share
            assert await b.screens.answer(share,False)
            await until(lambda:a.screens.state is None)
            b.screens.invites.tokens=2
            share=await a.screens.offer(b.sid);assert await b.screens.answer(share,True)
            a.set_screen_locked(True)
            await until(lambda:b.screens.state is None)
            assert a.screens.state is None
            a.set_screen_locked(False);b.screens.invites.tokens=2
            share=await a.screens.offer(b.sid)
            assert await b.screens.answer(share,True)
            b.block(a.sid)
            assert b.screens.state is None
            assert not await a.screens.send_frame(share,b'blocked',100,100)
            assert a.screens.state is None
            b.block(a.sid,False);b.screens.invites.tokens=2
            share=await a.screens.offer(b.sid);assert share
            b.screens.state.deadline=b.screens.clock()-1;b.screens.tick()
            await until(lambda:a.screens.state is None)
            assert not await b.screens.answer(share,True)
        finally:await a.stop();await b.stop()
    asyncio.run(run())


def test_screen_assembly_rejects_wrong_order_stale_stream_and_over_budget():
    async def run():
        a,b=ChatClient('Alice42',discovery=False,tcp_port=0),ChatClient('Bob42',discovery=False,tcp_port=0)
        try:
            await a.start();await b.start();a.observe(b.hello(),'127.0.0.1');b.observe(a.hello(),'127.0.0.1')
            await until(lambda:FEATURE in a.peers[b.sid].features)
            share=await a.screens.offer(b.sid);await b.screens.answer(share,True)
            chunk=event('screen_chunk',a.sid,b.sid,share=share,seq=1,part=0,total=2,width=100,height=100,
                        data=base64.b64encode(b'x'*CHUNK_SIZE).decode())
            encode(chunk);assert b.screens.receive(b.peers[a.sid],chunk)[0]
            assert len(b.screens.state.buffer)==CHUNK_SIZE
            bad={**chunk,'seq':2,'part':1,'data':base64.b64encode(b'end').decode()}
            assert not b.screens.receive(b.peers[a.sid],bad)[0]
            assert b.screens.state is None
            await until(lambda:a.screens.state is None)
            b.screens.invites.tokens=2
            share2=await a.screens.offer(b.sid);await b.screens.answer(share2,True)
            b.screens.receive(b.peers[a.sid],event('screen_stop',a.sid,b.sid,share=share))
            assert b.screens.state.share==share2
            b.screens.chunks.tokens=0
            b.screens.chunks.rate=0
            assert not b.screens.receive(b.peers[a.sid],{**chunk,'share':share2})[0]
            assert b.screens.state is None
        finally:await a.stop();await b.stop()
    asyncio.run(run())
