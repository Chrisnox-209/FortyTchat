"""One bounded, encrypted screen session per client, with explicit consent."""
import asyncio
import base64
import contextlib
from dataclasses import dataclass, field
from functools import wraps
import time

from ..features.screen_share import (FEATURE, SCREEN_EVENTS, MAX_IMAGE, MAX_WIDTH, MAX_HEIGHT,
                                    CHUNK_SIZE, INVITE_TTL, FIRST_FRAME_TTL, FRAME_TTL)
from ..features.wizz import TokenBucket
from ..features.screen_annotations import FEATURE as ANNOTATION_FEATURE, WIDTH_FEATURE, EVENT as ANNOTATION_EVENT, valid_annotation
from ..identity import session_id, valid_contact_id
from ..security import SecurityError
from .protocol import event, encode, read_frame, MAX_FRAME, ProtocolError


def tracked(method):
    @wraps(method)
    async def operation(self,*args,**kwargs):
        task=asyncio.current_task();self.client.operations.add(task)
        try:return await method(self,*args,**kwargs)
        finally:self.client.operations.discard(task)
    return operation


@dataclass
class ScreenSession:
    share: str
    peer: str
    direction: str
    phase: str
    deadline: float
    seq: int = 0
    received_seq: int = 0
    partial_seq: int = 0
    part: int = 0
    total: int = 0
    width: int = 0
    height: int = 0
    buffer: bytearray = field(default_factory=bytearray)
    annotation_sent: int = 0
    annotation_received: int = 0


class ScreenSharing:
    def __init__(self,client,clock=None):
        self.client=client;self.clock=clock or time.monotonic
        self.state=None;self.recent={};self.reader=self.writer=None
        self.busy=False
        self.invites=TokenBucket(2,1/30)
        self.chunks=TokenBucket(128,160)
        self.images=TokenBucket(2,6)
        self.sending=TokenBucket(2,6)
        self.annotation_lock=asyncio.Lock()
        self.annotation_sending=TokenBucket(40,20,clock=self.clock)
        self.annotation_receiving=TokenBucket(40,20,clock=self.clock)

    def eligible(self,peer,feature=True):
        return bool(peer and peer.secure and peer.sid not in self.client.blocked
                    and (not feature or FEATURE in peer.features)
                    and (peer.direct or peer.relay and self.client.relay_writer and self.client.relay_e2ee))

    def emit(self,phase,ctx=None,reason=''):
        ctx=ctx or self.state
        peer=self.client.peers.get(ctx.peer) if ctx else None
        if ctx:self.client.emit('screen_state',share=ctx.share,peer=ctx.peer,direction=ctx.direction,phase=phase,reason=reason,
                                annotations=bool(peer and ANNOTATION_FEATURE in peer.features and phase=='active'),
                                annotation_widths=bool(peer and ANNOTATION_FEATURE in peer.features and WIDTH_FEATURE in peer.features and phase=='active'))

    def notice(self,text):self.client.emit('screen_notice',text=text)

    def close(self,reason,*,notify=False):
        ctx=self.state
        if not ctx:return None
        self.state=None;ctx.buffer.clear()
        self.recent[(ctx.peer,ctx.share)]=self.clock()+90
        if len(self.recent)>128:self.recent.pop(next(iter(self.recent)))
        if self.writer:
            writer=self.writer;writer.close()
            self.reader=self.writer=None
            async def release():
                try:
                    with contextlib.suppress(OSError):await writer.wait_closed()
                finally:self.client.connections.discard(writer)
            if not self.client._closing:self.client.task(release())
        self.emit('closed',ctx,reason)
        if notify and not self.client._closing:
            peer=self.client.peers.get(ctx.peer)
            if self.eligible(peer,False):self.client.task(self.control(peer,'screen_stop',ctx.share))
        return ctx

    def tick(self):
        now=self.clock()
        self.recent={key:until for key,until in self.recent.items() if until>now}
        ctx=self.state
        if not ctx:return
        peer=self.client.peers.get(ctx.peer)
        if not self.eligible(peer,False):self.close('Contact déconnecté ou bloqué.',notify=True)
        elif self.client.screen_locked:self.close('Session verrouillée.',notify=True)
        elif now>=ctx.deadline:self.close('Invitation expirée.' if ctx.phase=='waiting' else 'Le partage ne transmet plus d’images.',notify=True)
        elif ctx.direction=='in' and ctx.phase=='waiting' and self.client.dnd:self.close('Ne pas déranger.',notify=True)

    async def watchdog(self):
        while not self.client._closing:
            self.tick();await asyncio.sleep(1)

    async def control(self,peer,kind,share,**payload):
        if not self.eligible(peer,False):return False
        return await self.client._send_to(peer,event(kind,self.client.sid,peer.sid,share=share,**payload))

    @tracked
    async def offer(self,sid,share=None):
        self.tick();peer=self.client.peers.get(sid)
        if self.client._closing:return None
        if share is not None and not valid_contact_id(share):return None
        if not self.eligible(peer):
            self.notice('Le partage d’écran nécessite deux clients compatibles 2.4.0 ou plus récents.');return None
        if self.state or self.busy:
            self.notice('Termine le partage ou l’invitation en cours.');return None
        if self.client.screen_locked:
            self.notice('Déverrouille ta session avant de partager.');return None
        ctx=ScreenSession(share or session_id(),sid,'out','waiting',self.clock()+INVITE_TTL)
        self.state=ctx;self.emit('waiting')
        if not await self.control(peer,'screen_offer',ctx.share):
            if self.state is ctx:self.close('Invitation refusée ou contact indisponible.')
            return None
        return ctx.share if self.state is ctx else None

    @tracked
    async def answer(self,share,accepted):
        ctx=self.state
        if type(accepted) is not bool or not ctx or ctx.share!=share or ctx.direction!='in' or ctx.phase!='waiting':return False
        self.tick()
        if self.state is not ctx:return False
        peer=self.client.peers.get(ctx.peer)
        if accepted:
            self.chunks=TokenBucket(128,160,clock=self.clock);self.images=TokenBucket(2,6,clock=self.clock)
            ctx.phase='active';ctx.deadline=self.clock()+FIRST_FRAME_TTL;self.emit('active')
        else:self.close('Invitation refusée.')
        ok=await self.control(peer,'screen_answer',share,accepted=accepted)
        if not ok and self.state is ctx:self.close('Le contact ne répond plus.')
        return ok

    @tracked
    async def stop(self,share='',reason='Partage terminé.'):
        if not self.state or share and share!=self.state.share:return False
        ctx=self.close(reason)
        await self.control(self.client.peers.get(ctx.peer),'screen_stop',ctx.share)
        return True

    def receive(self,peer,frame):
        self.tick();kind=frame['type'];share=frame['share'];ctx=self.state
        if not self.eligible(peer,False):return False,'Contact indisponible'
        if kind=='screen_offer':
            if (peer.sid,share) in self.recent:return False,'Invitation terminée'
            if ctx:
                return (True,'') if (ctx.share==share and ctx.peer==peer.sid and ctx.direction=='in') else (False,'Un partage est déjà en cours')
            if self.client.dnd or self.client.screen_locked or not self.invites.allow():return False,'Invitation indisponible'
            self.state=ScreenSession(share,peer.sid,'in','waiting',self.clock()+INVITE_TTL)
            self.emit('offered');return True,''
        if not ctx or ctx.share!=share or ctx.peer!=peer.sid:
            return (True,'') if kind=='screen_stop' and (peer.sid,share) in self.recent else (False,'Partage non autorisé')
        if kind=='screen_stop':self.close('Le contact a arrêté le partage.');return True,''
        if kind==ANNOTATION_EVENT:
            if ctx.phase!='active' or ANNOTATION_FEATURE not in peer.features:return False,'Annotations indisponibles'
            if 'width' in frame and WIDTH_FEATURE not in peer.features:return False,'Épaisseur non négociée'
            payload={key:frame[key] for key in ('action','points','color','width') if key in frame}
            if not valid_annotation(payload) or ctx.direction=='in' and payload['action']!='clear':return False,'Annotation non autorisée'
            if frame['seq']<=ctx.annotation_received:return True,''
            if not self.annotation_receiving.allow():return False,'Annotations trop rapides'
            ctx.annotation_received=frame['seq']
            self.client.emit('screen_annotation',share=ctx.share,peer=peer.sid,payload=payload)
            return True,''
        if kind=='screen_answer':
            if ctx.direction!='out':return False,'Invitation incorrecte'
            if ctx.phase=='active':return (True,'') if frame['accepted'] else (False,'Réponse déjà reçue')
            if frame['accepted']:
                self.sending=TokenBucket(2,6,clock=self.clock)
                ctx.phase='active';ctx.deadline=self.clock()+FIRST_FRAME_TTL;self.emit('active')
            else:self.close('Invitation refusée.')
            return True,''
        if kind=='screen_chunk':return self.receive_chunk(peer,frame)
        return False,'Contrôle inconnu'

    @tracked
    async def annotate(self,share,payload):
        ctx=self.state
        if not ctx or ctx.share!=share or not valid_annotation(payload):return False
        async with self.annotation_lock:
            self.tick();peer=self.client.peers.get(ctx.peer)
            if (self.state is not ctx or ctx.phase!='active' or not self.eligible(peer)
                    or ANNOTATION_FEATURE not in peer.features
                    or ctx.direction=='out' and payload['action']!='clear'
                    or not self.annotation_sending.allow() or ctx.annotation_sent>=2**31-1):return False
            ctx.annotation_sent+=1
            # Old validators reject additional fields, even for the default width.
            if WIDTH_FEATURE not in peer.features:payload={key:value for key,value in payload.items() if key!='width'}
            return await self.control(peer,ANNOTATION_EVENT,share,seq=ctx.annotation_sent,**payload)

    def accepts(self,sid,share):
        ctx=self.state
        return bool(ctx and ctx.peer==sid and ctx.share==share and ctx.direction=='in' and ctx.phase=='active')

    def receive_chunk(self,peer,frame):
        ctx=self.state
        if not self.accepts(peer.sid,frame['share']):return False,'Partage non accepté'
        if not self.chunks.allow():self.close('Trop de données de partage.',notify=True);return False,'Débit dépassé'
        seq,part=frame['seq'],frame['part']
        if seq<=ctx.received_seq:return True,''
        if part==0:
            if seq<=ctx.partial_seq or not self.images.allow():return False,'Image trop rapide ou répétée'
            ctx.buffer.clear();ctx.partial_seq=seq;ctx.part=0
            ctx.total,ctx.width,ctx.height=frame['total'],frame['width'],frame['height']
        if (seq!=ctx.partial_seq or part!=ctx.part or frame['total']!=ctx.total
                or frame['width']!=ctx.width or frame['height']!=ctx.height):
            self.close('Ordre des images incorrect.',notify=True);return False,'Fragment incorrect'
        raw=base64.b64decode(frame['data'],validate=True)
        if len(ctx.buffer)+len(raw)>MAX_IMAGE:self.close('Image trop volumineuse.',notify=True);return False,'Image trop grande'
        ctx.buffer.extend(raw);ctx.part+=1;ctx.deadline=self.clock()+FRAME_TTL
        if ctx.part==ctx.total:
            data=bytes(ctx.buffer);ctx.buffer.clear();ctx.received_seq=seq
            self.client.emit('screen_frame',share=ctx.share,peer=peer.sid,data=data,width=ctx.width,height=ctx.height)
        return True,''

    async def direct_chunk(self,peer,frame):
        if not self.writer or self.writer.is_closing():
            self.reader,self.writer=await asyncio.wait_for(asyncio.open_connection(peer.host,peer.port,limit=MAX_FRAME),3)
            self.client.connections.add(self.writer)
            if not self.state or self.state.share!=frame['share']:
                self.writer.close()
                with contextlib.suppress(OSError):await self.writer.wait_closed()
                self.client.connections.discard(self.writer);self.reader=self.writer=None
                return False
            self.writer.write(encode(self.client.hello()))
        reader,writer=self.reader,self.writer
        outer=self.client.crypto.seal(peer,frame,'stream')
        writer.write(encode(outer));await asyncio.wait_for(writer.drain(),3)
        reply=await read_frame(reader,5);reply=self.client.crypto.open(peer,reply)
        return reply['type']=='ack' and reply['id']==outer['id'] and reply['ok']

    @tracked
    async def send_frame(self,share,data,width,height):
        ctx=self.state
        if (not ctx or ctx.share!=share or ctx.phase!='active' or ctx.direction!='out' or self.busy
                or not isinstance(data,bytes) or not 0<len(data)<=MAX_IMAGE
                or type(width) is not int or type(height) is not int or not 0<width<=MAX_WIDTH or not 0<height<=MAX_HEIGHT):return False
        self.tick()
        if self.state is not ctx or not self.sending.allow():return False
        peer=self.client.peers.get(ctx.peer);self.busy=True;ctx.seq+=1
        try:
            total=(len(data)+CHUNK_SIZE-1)//CHUNK_SIZE
            frames=[event('screen_chunk',self.client.sid,peer.sid,share=share,seq=ctx.seq,part=part,total=total,
                          width=width,height=height,data=base64.b64encode(data[offset:offset+CHUNK_SIZE]).decode('ascii'))
                    for part,offset in enumerate(range(0,len(data),CHUNK_SIZE))]
            if peer.relay and self.client.relay_writer and self.client.relay_e2ee:
                results=await asyncio.gather(*(self.client._send_relay(frame,peer) for frame in frames))
                ok=all(results)
            else:
                ok=True
                for frame in frames:
                    if self.state is not ctx or not await self.direct_chunk(peer,frame):ok=False;break
            if self.state is not ctx:return False
            if not ok:self.close('Transmission interrompue.',notify=True);return False
            ctx.deadline=self.clock()+FRAME_TTL;return True
        except (OSError,ConnectionError,SecurityError,ProtocolError,asyncio.TimeoutError):
            if self.state is ctx:self.close('Connexion du partage interrompue.',notify=True)
            return False
        finally:self.busy=False
