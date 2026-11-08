import time
import math

INVITE_LIFETIME = 120
INVITE_LIMIT = 2
INVITE_SERIES_WINDOW = 5 * 60
INVITE_BLOCK_TIME = 5 * 60
INVITE_REPEAT_BLOCK = 15 * 60
INVITE_REPEAT_WINDOW = 15 * 60


class BreakRateLimit:
    """Two private invitations, then 5 min; repeated bursts block for 15 min.

    Stable contact IDs share the budget across sessions/nicknames. Blocked
    attempts never extend it. Fifteen quiet minutes after a block clear the tier.
    A single unpaired invitation expires after five minutes. Replies stay allowed.
    """
    def __init__(self, clock=time.monotonic, maximum=1024):
        self.clock, self.maximum, self.states = clock, maximum, {}

    def prune(self):
        now = self.clock()
        self.states = {key: state for key, state in self.states.items()
                       if (now < state['until'] + INVITE_REPEAT_WINDOW if state['until']
                           else now - state['last'] < INVITE_SERIES_WINDOW)}

    def remaining(self, key):
        self.prune()
        return max(0.0, self.states.get(key, {}).get('until', 0) - self.clock())

    def notice(self, key):
        return f'Popups et pauses bloquées pendant {max(1, math.ceil(self.remaining(key) / 60))} minutes. Les messages restent disponibles.'

    def allow(self, key):
        self.prune(); now = self.clock()
        state = self.states.get(key)
        if state and state['until'] > now: return False
        if state is None:
            if len(self.states) >= self.maximum: return False
            state = dict(count=0, last=now, until=0, repeat=False)
            self.states[key] = state
        elif state['until']:
            state.update(count=0, until=0)
        state['count'] += 1; state['last'] = now
        if state['count'] == INVITE_LIMIT:
            state['until'] = now + (INVITE_REPEAT_BLOCK if state['repeat'] else INVITE_BLOCK_TIME)
            state['repeat'] = True
        return True


class Invitations:
    def __init__(self, clock=time.monotonic):
        self.pending, self.clock = {}, clock

    def prune(self):
        now = self.clock()
        self.pending = {k: v for k, v in self.pending.items() if v[1] > now}

    def add(self, request_id, peer):
        self.prune()
        if len(self.pending) >= 100:
            return False
        self.pending[request_id] = (peer, self.clock() + INVITE_LIFETIME)
        return True

    def consume(self, request_id, peer):
        self.prune()
        value = self.pending.get(request_id)
        if value and value[0] == peer:
            del self.pending[request_id]
            return True
        return False
