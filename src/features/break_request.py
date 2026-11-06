import time

INVITE_LIFETIME = 120
INVITE_LIMIT = 3
INVITE_SERIES_WINDOW = 5 * 60
INVITE_BLOCK_TIME = 5 * 60


class BreakRateLimit:
    """Allow three invites per peer; block the fourth and further ones for 5 min.

    Five minutes without an allowed invite ends a series. Attempts during a block
    do not prolong it. Use a stable contact ID as key when one is available.
    Only invitations pass through this gate; messages and Wizz are independent.
    """

    def __init__(self, clock=time.monotonic, maximum=1024):
        self.clock, self.maximum = clock, maximum
        self.states = {}

    def prune(self):
        now = self.clock()
        self.states = {
            key: state for key, state in self.states.items()
            if state[2] > now or (not state[2] and now - state[1] < INVITE_SERIES_WINDOW)
        }

    def remaining(self, key):
        self.prune()
        return max(0.0, self.states.get(key, (0, 0, 0))[2] - self.clock())

    def allow(self, key):
        self.prune()
        now = self.clock()
        count, last, blocked_until = self.states.get(key, (0, now, 0))
        if blocked_until > now:
            return False
        if key not in self.states and len(self.states) >= self.maximum:
            return False
        if count >= INVITE_LIMIT:
            self.states[key] = (count, last, now + INVITE_BLOCK_TIME)
            return False
        self.states[key] = (count + 1, now, 0)
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
