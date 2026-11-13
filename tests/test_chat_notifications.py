"""Native event images and chat visibility policy, without personal banners."""
from pathlib import Path
from types import SimpleNamespace
import pytest
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication
from src.app import Application
from src.ui.notifications import Notifications, NOTIFICATION_ICONS, variant_string

ROOT=Path(__file__).resolve().parents[1]
ICON=str(ROOT/'assets/icons/fortytchat.svg')
GROUP='group:'+'a'*32


@pytest.fixture(scope='module')
def qt():
    return QApplication.instance() or QApplication([])


@pytest.mark.parametrize('target,poll,mentioned,kind,context',[
    ('b'*32,False,False,'message','conversation privée'),
    ('general',False,False,'message','#général'),
    (GROUP,False,False,'group','Les copains'),
    (GROUP,False,True,'group','Les copains'),
    ('general',True,False,'poll','#général'),
    (GROUP,True,False,'poll','Les copains'),
])
def test_normal_silent_banner_category_context_and_no_message_preview(target,poll,mentioned,kind,context):
    calls=[]
    state=SimpleNamespace(preferences=SimpleNamespace(dnd=False),is_viewed=lambda t:False,
        groups={GROUP:{'name':'Les copains'}},notifications=SimpleNamespace(show=lambda *a,**kw:calls.append((a,kw)) or True))
    assert Application.notify_chat(state,target,'Alice42',poll=poll,mentioned=mentioned)
    args,kwargs=calls[0]
    assert kwargs==dict(kind=kind,urgency='normal')
    assert context in args[1]
    assert ('te mentionne' in args[0])==mentioned
    assert ('sondage' in args[0])==poll


@pytest.mark.parametrize('dnd,viewed',[(True,False),(False,True),(True,True)])
def test_dnd_and_current_conversation_suppress_messages_and_polls(dnd,viewed):
    calls=[]
    state=SimpleNamespace(preferences=SimpleNamespace(dnd=dnd),is_viewed=lambda t:viewed,
        notifications=SimpleNamespace(show=lambda *a,**kw:calls.append((a,kw))))
    for target,poll in [('general',False),(GROUP,False),('general',True),(GROUP,True)]:
        assert not Application.notify_chat(state,target,'Alice42',poll=poll)
    assert not calls


@pytest.mark.parametrize('visible,active,minimized,expected',[(True,True,False,True),(True,True,True,False),
    (False,True,False,False),(True,False,False,False)])
def test_minimized_or_inactive_window_is_not_a_viewed_conversation(visible,active,minimized,expected):
    view=SimpleNamespace(isVisible=lambda:visible,isActiveWindow=lambda:active,isMinimized=lambda:minimized)
    assert Application.is_viewed(SimpleNamespace(view_for=lambda t:view),'general') is expected


@pytest.mark.parametrize('backend',['gdbus','notify-send'])
@pytest.mark.parametrize('kind',list(NOTIFICATION_ICONS))
def test_both_native_backends_receive_distinct_readable_event_images(qt,monkeypatch,backend,kind):
    calls=[]
    notifications=Notifications(ICON);notifications.headless=False
    notifications.gdbus='/test/gdbus' if backend=='gdbus' else None
    notifications.executable='/test/notify-send' if backend=='notify-send' else None
    monkeypatch.setattr(notifications,'start_backend',lambda choices:calls.extend(choices))
    assert notifications.show('Alice42','Un événement dans le cluster.',kind=kind,urgency='normal')
    executable,args=calls[0];path=ROOT/'assets/icons/notifications'/NOTIFICATION_ICONS[kind]
    assert not QImage(str(path)).isNull()
    assert len({(ROOT/'assets/icons/notifications'/name).read_bytes() for name in NOTIFICATION_ICONS.values()})==4
    if backend=='gdbus':
        assert variant_string(str(path)) in args
        assert "'image-path': <"+variant_string(path.as_uri())+">" in args[-2]
        assert "'urgency': <byte 1>" in args[-2] and "'suppress-sound': <true>" in args[-2]
    else:
        assert args[args.index('--icon')+1]==str(path)
        assert '--hint=string:image-path:'+path.as_uri() in args
        assert '--urgency=normal' in args and '--hint=boolean:suppress-sound:true' in args
    notifications.stop();notifications.deleteLater();qt.processEvents()


def test_missing_event_image_falls_back_to_app_icon_and_invalid_type_is_rejected(qt,monkeypatch,tmp_path):
    calls=[];icon=str(tmp_path/'old-icons/fortytchat.svg')
    notifications=Notifications(icon);notifications.headless=False;notifications.gdbus=None;notifications.executable='/test/notify-send'
    monkeypatch.setattr(notifications,'start_backend',lambda choices:calls.extend(choices))
    assert notifications.show('Compatible',kind='message',urgency='normal')
    args=calls[-1][1];assert args[args.index('--icon')+1]==icon
    assert not any('image-path' in arg for arg in args)
    with pytest.raises(ValueError):notifications.show('Invalid',kind='../../private.png')
    notifications.stop();notifications.deleteLater();qt.processEvents()
