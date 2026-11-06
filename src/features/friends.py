"""Profile contacts persist; routing always uses the current temporary session ID."""
from ..identity import valid_contact_id

MAX_FRIENDS = 256


def friend_key(contact_id):
    return 'friend:' + contact_id


def contact_from_key(key):
    if isinstance(key, str) and key.startswith('friend:') and valid_contact_id(key[7:]):
        return key[7:]
    return ''


def online_contact(peers, contact_id, preferred=''):
    candidates = [p for p in peers if p.get('contact_id') == contact_id]
    return next((p for p in candidates if p['sid'] == preferred), None) or next(
        iter(sorted(candidates, key=lambda p: (p['status'] != 'online', p['sid']))), None)
