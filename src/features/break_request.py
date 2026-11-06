import time

INVITE_INTERVAL = 60
INVITE_LIFETIME = 120


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
