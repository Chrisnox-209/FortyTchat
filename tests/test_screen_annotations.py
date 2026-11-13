"""Annotations are bounded, encrypted, consented, and never input commands."""
import asyncio
import pytest
from src.features.screen_annotations import FEATURE, WIDTH_FEATURE, EVENT, MAX_POINTS, MAX_SHAPES, AnnotationBoard
from src.identity import session_id
from src.networking.client import ChatClient
from src.networking.protocol import ProtocolError, encode, event
from src.networking.relay import RelayServer


async def until(predicate):
    for _ in range(500):
        if predicate():return
        await asyncio.sleep(.01)
    raise AssertionError('Annotation non reçue')


def test_annotation_protocol_rejects_input_commands_unbounded_or_invalid_geometry():
    a,b,share=session_id(),session_id(),session_id()
    frame=event(EVENT,a,b,share=share,seq=1,action='rectangle',points=[[0,0],[10000,10000]],color=0)
    encode(frame)
    for change in (dict(to='general'),dict(to='group:'+share),dict(to=a),dict(seq=True),dict(seq=0),dict(seq=2**31),
                   dict(action='click'),dict(action=[]),dict(command='x'),dict(points=[[True,0],[1,2]]),
                   dict(points=[[0.,0],[1,2]]),dict(points=[[-1,0],[1,2]]),dict(points=[[0,10001],[1,2]]),
                   dict(points=[[0,0,0],[1,2]]),dict(points=[]),dict(color=True),dict(color=4)):
        with pytest.raises(ProtocolError):encode(dict(frame,**change))
    for action,points in [('pointer',[[123,456]]),('pen',[[0,0]]*MAX_POINTS),('ellipse',[[1,2],[3,4]]),('hide',[]),('clear',[])]:
        encode(dict(frame,action=action,points=points))
    with pytest.raises(ProtocolError):encode(dict(frame,action='pen',points=[[0,0]]*(MAX_POINTS+1)))


def test_stroke_width_is_bounded_and_only_applies_to_shapes():
    frame=event(EVENT,session_id(),session_id(),share=session_id(),seq=1,action='pen',points=[[1,2],[3,4]],color=0)
    for width in (1,2,3,6,10,12):
        encode(dict(frame,width=width))
        board=AnnotationBoard();assert board.apply(dict(action='pen',points=[[1,2],[3,4]],color=0,width=width))
        assert board.shapes[-1][-1]==width
    for width in (True,False,None,'6',3.0,0,-1,13,[],{}):
        with pytest.raises(ProtocolError):encode(dict(frame,width=width))
    for action,points in (('pointer',[[1,2]]),('hide',[]),('clear',[])):
        with pytest.raises(ProtocolError):encode(dict(frame,action=action,points=points,width=6))


def test_annotation_board_is_ephemeral_bounded_and_copies_mutable_payloads():
    clock=[0.];board=AnnotationBoard(lambda:clock[0])
    payload=dict(action='pen',points=[[1,2],[3,4]],color=0)
    for _ in range(MAX_SHAPES+10):assert board.apply(payload)
    payload['points'][0][0]=5000
    assert len(board.shapes)==MAX_SHAPES and board.shapes[0][1][0]==(1,2)
    assert board.shapes[0][-1]==3  # Legacy payloads keep their original stroke width.
    assert board.apply(dict(action='pointer',points=[[2000,3000]],color=2))
    clock[0]=4.;assert board.expire_pointer() and board.pointer is None
    assert not board.apply(dict(action='click',points=[],color=0))
    assert board.apply(dict(action='clear',points=[],color=0)) and not board.shapes


@pytest.mark.parametrize('relay',[False,True])
def test_annotations_require_active_share_correct_roles_and_are_private_and_ordered(relay):
    async def run():
        server=RelayServer() if relay else None;wire=[]
        if server:
            route=server.route
            def observe(connection,data):wire.append(dict(data));return route(connection,data)
            server.route=observe
        options=dict(relay_host='127.0.0.1',relay_port=await server.start(port=0),tls=False) if relay else {}
        logs=[[],[],[]]
        clients=[ChatClient(n,lambda k,d,i=i:logs[i].append((k,d)),discovery=False,tcp_port=0,**options)
                 for i,n in enumerate(('Alice42','Bob42','Chris42'))]
        a,b,c=clients
        marks=lambda i:[d['payload'] for k,d in logs[i] if k=='screen_annotation']
        pointer=dict(action='pointer',points=[[2500,5000]],color=0)
        rectangle=dict(action='rectangle',points=[[100,200],[9000,8000]],color=2,width=10)
        clear=dict(action='clear',points=[],color=0)
        try:
            await asyncio.gather(*(client.start() for client in clients))
            if relay:await until(lambda:all(len(client.peers)==2 for client in clients))
            else:
                for client in clients:
                    for peer in clients:
                        if client is not peer:client.observe(peer.hello(),'127.0.0.1')
            await until(lambda:all(FEATURE in a.peers[p.sid].features for p in (b,c)))
            assert not await b.screens.annotate(session_id(),pointer)
            share=await a.screens.offer(b.sid)
            assert not await b.screens.annotate(share,pointer)
            assert await b.screens.answer(share,True)
            assert await b.screens.annotate(share,pointer)
            assert await b.screens.annotate(share,rectangle)
            assert marks(0)==[pointer,rectangle] and not marks(2)
            forged=event(EVENT,c.sid,a.sid,share=share,seq=1,**rectangle)
            assert not await c._send_to(c.peers[a.sid],forged)
            assert not await a.screens.annotate(share,rectangle)
            reverse=event(EVENT,a.sid,b.sid,share=share,seq=1,**rectangle)
            assert not await a._send_to(a.peers[b.sid],reverse)
            stale=event(EVENT,b.sid,a.sid,share=share,seq=1,**clear)
            assert await b._send_to(b.peers[a.sid],stale)
            assert marks(0)==[pointer,rectangle]
            assert await a.screens.annotate(share,clear) and marks(1)==[clear]
            # A width field is accepted only from a peer that advertised support.
            features=a.peers[b.sid].features
            a.peers[b.sid].features=tuple(f for f in features if f!=WIDTH_FEATURE)
            assert not await b._send_to(b.peers[a.sid],event(EVENT,b.sid,a.sid,share=share,seq=100,**rectangle))
            a.peers[b.sid].features=features
            batch=[dict(action='ellipse',points=[[i,20],[200+i,800]],color=i%4) for i in range(10)]
            assert all(await asyncio.gather(*(b.screens.annotate(share,item) for item in batch)))
            assert marks(0)[-10:]==batch
            a.screens.annotation_receiving.tokens=0;a.screens.annotation_receiving.rate=0
            assert not await b.screens.annotate(share,pointer)
            assert a.screens.state is not None  # A rejected visual mark does not inject input or stop the video.
            a.set_screen_locked(True);await until(lambda:b.screens.state is None)
            assert not await b.screens.annotate(share,clear)
            if relay:
                assert any(frame['type']=='sealed' for frame in wire)
                assert not any(frame['type']==EVENT or 'points' in frame for frame in wire)
        finally:
            await asyncio.gather(*(client.stop() for client in clients))
            if server:await server.stop()
    asyncio.run(run())
