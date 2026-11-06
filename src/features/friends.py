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
    candidates = [p for p in peers if p.get('contact_id') == contact_id and p.get('encrypted', True)
                  and p.get('security') != 'changed']
    return next((p for p in candidates if p['sid'] == preferred), None) or next(
        iter(sorted(candidates, key=lambda p: (p['status'] != 'online', p['sid']))), None)


def friend_rows(friends, peers, blocked=(), preferred=None, popup_available=None):
    """Local menu data. Actions use the contact ID, never a cached network route."""
    peers, blocked, preferred = list(peers), set(blocked), preferred or {}
    rows = []
    for cid, saved in friends.items():
        peer = online_contact(peers, cid, preferred.get(cid, ''))
        changed = next((p for p in peers if p.get('contact_id') == cid and p.get('security') == 'changed'), None)
        online = peer is not None
        masked = bool(peer and peer['sid'] in blocked)
        if peer:
            status = 'Masqué' if masked else {'online':'En ligne', 'away':'Absent·e', 'dnd':'Ne pas déranger'}.get(peer['status'], 'En ligne')
        else:
            status = 'Clé modifiée · vérifier' if changed else 'Hors ligne'
        can_popup = bool(peer and not masked and 'cards' in peer.get('caps', ())
                         and (popup_available(peer) if popup_available else peer.get('direct') or peer.get('relay')))
        rows.append({'contact_id':cid, 'nick':peer['nick'] if peer else saved,
                     'online':online, 'status':status, 'popup':can_popup})
    return sorted(rows, key=lambda row: (not row['online'], row['nick'].casefold(), row['contact_id']))


class FriendPresence:
    """Connection transitions only; no alerts for rename, extra devices or startup roster."""
    def __init__(self, friends=(), clock=None, startup_grace=5, cooldown=60, offline_grace=5):
        import time
        self.clock = clock or time.monotonic
        self.started, self.startup_grace = self.clock(), startup_grace
        self.cooldown, self.offline_grace = cooldown, offline_grace
        self.friends, self.online = set(friends), set()
        self.offline_since, self.notified = {}, {}

    def update(self, friends, peers, blocked=(), enabled=True):
        friends, peers, blocked = set(friends), list(peers), set(blocked)
        now = self.clock()
        live = {cid:online_contact(peers, cid) for cid in friends}
        online = {cid for cid, peer in live.items() if peer}
        added = friends - self.friends
        for cid in self.online - online:
            self.offline_since[cid] = now
        result = []
        for cid in online - self.online - added:
            if (not enabled or now - self.started < self.startup_grace
                    or now - self.notified.get(cid, -1e30) < self.cooldown
                    or cid in self.offline_since and now - self.offline_since[cid] < self.offline_grace):
                continue
            peer = online_contact([p for p in peers if p['sid'] not in blocked], cid)
            if peer:
                result.append(peer)
                self.notified[cid] = now
        self.friends, self.online = friends, online
        self.offline_since = {cid:stamp for cid,stamp in self.offline_since.items() if cid in friends}
        self.notified = {cid:stamp for cid,stamp in self.notified.items() if cid in friends}
        return sorted(result, key=lambda peer:peer['nick'].casefold())
