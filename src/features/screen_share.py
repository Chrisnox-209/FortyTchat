"""Private, consent-based screen frames. No capture, Qt or disk access here."""
import base64
import binascii
from ..identity import valid_contact_id
from .file_transfer import CHUNK_SIZE

FEATURE = 'screen_share_v1'
SCREEN_EVENTS = {'screen_offer', 'screen_answer', 'screen_stop', 'screen_chunk'}
MAX_IMAGE = 128 * 1024
MAX_WIDTH, MAX_HEIGHT, FPS = 1280, 720, 5
MAX_PARTS = (MAX_IMAGE + CHUNK_SIZE - 1) // CHUNK_SIZE
INVITE_TTL, FIRST_FRAME_TTL, FRAME_TTL = 45, 60, 15


def valid_screen_frame(frame):
    kind=frame.get('type')
    fields={'share'}
    if kind=='screen_answer':fields.add('accepted')
    elif kind=='screen_chunk':fields |= {'seq','part','total','data','width','height'}
    elif kind not in ('screen_offer','screen_stop'):return False
    if set(frame)!={'v','type','id','from','to'}|fields:return False
    if not all(valid_contact_id(frame.get(key)) for key in ('id','from','to','share')):return False
    if frame['from']==frame['to']:return False
    if kind=='screen_answer':return type(frame['accepted']) is bool
    if kind!='screen_chunk':return True
    for key,minimum,maximum in [('seq',1,2**31-1),('total',1,MAX_PARTS),('part',0,MAX_PARTS-1),
                                 ('width',1,MAX_WIDTH),('height',1,MAX_HEIGHT)]:
        if type(frame[key]) is not int or not minimum<=frame[key]<=maximum:return False
    if frame['part']>=frame['total']:return False
    data=frame['data']
    if not isinstance(data,str) or not 0<len(data)<=((CHUNK_SIZE+2)//3)*4:return False
    try:raw=base64.b64decode(data,validate=True)
    except (ValueError,binascii.Error):return False
    if not 0<len(raw)<=CHUNK_SIZE or base64.b64encode(raw).decode('ascii')!=data:return False
    if frame['part']<frame['total']-1 and len(raw)!=CHUNK_SIZE:return False
    return frame['part']*CHUNK_SIZE+len(raw)<=MAX_IMAGE
