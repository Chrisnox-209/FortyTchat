"""Version notices are information only: never run a command supplied by a peer."""
import re
def version_tuple(value):
    if not isinstance(value,str) or not re.fullmatch(r'(?:0|[1-9][0-9]{0,3})\.(?:0|[1-9][0-9]{0,3})\.(?:0|[1-9][0-9]{0,3})',value):return None
    return tuple(map(int,value.split('.')))


def modern_caps(caps):
    return 'rooms2' in caps and 'rooms3' in caps


def room_capable(caps):
    return any(c in caps for c in ('rooms','rooms2','rooms3'))


def group_capable(caps):
    return 'rooms2' in caps or 'rooms3' in caps
