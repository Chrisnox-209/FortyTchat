from collections import deque
import math
import time

WIZZ_INTERVAL = 42 * 60


class TokenBucket:
    def __init__(self, capacity=8, rate=0.75, clock=time.monotonic):
        self.capacity, self.rate, self.clock = capacity, rate, clock
        self.tokens, self.updated = float(capacity), clock()

    def allow(self):
        now = self.clock()
        self.tokens = min(self.capacity, self.tokens + max(0, now - self.updated) * self.rate)
        self.updated = now
        if self.tokens < 1:
            return False
        self.tokens -= 1
        return True


class Cooldown:
    def __init__(self, seconds, clock=time.monotonic):
        self.seconds, self.clock, self.last = seconds, clock, None

    def remaining(self):
        return max(0.0, self.seconds - (self.clock() - self.last)) if self.last is not None else 0.0

    def take(self):
        if self.remaining():
            return False
        self.last = self.clock()
        return True


def wizz_label(seconds):
    return f'Prochain Wizz dans {math.ceil(seconds / 60)} min' if seconds > 0 else 'ϟ  Wizz'


class SeenMessages:
    def __init__(self, maximum=12000):
        self.maximum, self.ids, self.order = maximum, set(), deque()

    def add(self, key):
        if key in self.ids:
            return False
        self.ids.add(key)
        self.order.append(key)
        if len(self.order) > self.maximum:
            self.ids.remove(self.order.popleft())
        return True
