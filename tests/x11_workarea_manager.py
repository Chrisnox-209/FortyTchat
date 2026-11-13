"""Test-only X11 manager: constrain managed windows as a dock/panel desktop does."""
import ctypes as C
from ctypes.util import find_library
import os

if os.environ.get('FORTYTCHAT_ISOLATED_X11')!='1':
    raise SystemExit('Ce gestionnaire est réservé aux écrans virtuels des tests.')


class MapRequest(C.Structure):
    _fields_=[('type',C.c_int),('serial',C.c_ulong),('send_event',C.c_int),('display',C.c_void_p),
              ('parent',C.c_ulong),('window',C.c_ulong)]


class ConfigureRequest(C.Structure):
    _fields_=[('type',C.c_int),('serial',C.c_ulong),('send_event',C.c_int),('display',C.c_void_p),
              ('parent',C.c_ulong),('window',C.c_ulong),('x',C.c_int),('y',C.c_int),
              ('width',C.c_int),('height',C.c_int),('border_width',C.c_int),('above',C.c_ulong),
              ('detail',C.c_int),('value_mask',C.c_ulong)]


class Event(C.Union):
    _fields_=[('type',C.c_int),('map',MapRequest),('configure',ConfigureRequest),('pad',C.c_long*24)]


x=C.CDLL(find_library('X11'))
x.XOpenDisplay.argtypes=[C.c_char_p];x.XOpenDisplay.restype=C.c_void_p
x.XDefaultRootWindow.argtypes=[C.c_void_p];x.XDefaultRootWindow.restype=C.c_ulong
x.XDefaultScreen.argtypes=[C.c_void_p];x.XDefaultScreen.restype=C.c_int
x.XDisplayWidth.argtypes=[C.c_void_p,C.c_int];x.XDisplayWidth.restype=C.c_int
x.XDisplayHeight.argtypes=[C.c_void_p,C.c_int];x.XDisplayHeight.restype=C.c_int
x.XSelectInput.argtypes=[C.c_void_p,C.c_ulong,C.c_long]
x.XInternAtom.argtypes=[C.c_void_p,C.c_char_p,C.c_int];x.XInternAtom.restype=C.c_ulong
x.XChangeProperty.argtypes=[C.c_void_p,C.c_ulong,C.c_ulong,C.c_ulong,C.c_int,C.c_int,C.POINTER(C.c_ubyte),C.c_int]
x.XMoveResizeWindow.argtypes=[C.c_void_p,C.c_ulong,C.c_int,C.c_int,C.c_uint,C.c_uint]
x.XMapWindow.argtypes=[C.c_void_p,C.c_ulong]
x.XNextEvent.argtypes=[C.c_void_p,C.POINTER(Event)]
x.XSync.argtypes=[C.c_void_p,C.c_int];x.XFlush.argtypes=[C.c_void_p]
display=x.XOpenDisplay(None)
assert display
root=x.XDefaultRootWindow(display);screen=x.XDefaultScreen(display)
width,height=x.XDisplayWidth(display,screen),x.XDisplayHeight(display,screen)
x.XSelectInput(display,root,(1<<20)|(1<<19));x.XSync(display,False)


def property(name,kind,values):
    data=(C.c_ulong*len(values))(*values)
    x.XChangeProperty(display,root,x.XInternAtom(display,name.encode(),False),
                      x.XInternAtom(display,kind.encode(),False),32,0,C.cast(data,C.POINTER(C.c_ubyte)),len(values))


property('_NET_WORKAREA','CARDINAL',[64,24,width-64,height-24])
property('_NET_CURRENT_DESKTOP','CARDINAL',[0])
property('_NET_NUMBER_OF_DESKTOPS','CARDINAL',[1])
x.XSync(display,False)
print('ready',flush=True)
event=Event()
while True:
    x.XNextEvent(display,C.byref(event))
    if event.type not in (20,23):continue
    window=event.map.window if event.type==20 else event.configure.window
    x.XMoveResizeWindow(display,window,64,24,width-64,height-24)
    if event.type==20:x.XMapWindow(display,window)
    x.XFlush(display)
