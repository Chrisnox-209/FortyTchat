"""Bounded ephemeral interactions. No Qt, files of conversations or drafts."""
from collections import OrderedDict
from functools import lru_cache
import json
from pathlib import Path
import time

SOCIAL_EVENTS = {'reaction', 'typing'}
TYPING_LIFETIME = 5


class SocialOrder:
    """Ignore a delayed older state (including a removed reaction)."""
    def __init__(self):
        self.latest = OrderedDict()

    @staticmethod
    def key(frame):
        return frame['from'], frame['to'], frame['type'], frame.get('message', '')

    def newer(self, frame):
        return frame['seq'] > self.latest.get(self.key(frame), -1)

    def remember(self, frame):
        key = self.key(frame)
        self.latest[key] = frame['seq']
        self.latest.move_to_end(key)
        while len(self.latest) > 4096:
            self.latest.popitem(last=False)


@lru_cache(maxsize=1)
def reaction_emojis():
    path = Path(__file__).resolve().parents[2] / 'assets/emoji/catalog.json'
    return frozenset(row[0] for row in json.loads(path.read_text(encoding='utf-8'))['entries'])


def valid_reaction(value):
    return (isinstance(value, str) and len(value) <= 32
            and (value == '' or value in reaction_emojis()))


def preview(text, maximum=160):
    value = ' '.join(text.split())
    return value if len(value) <= maximum else value[:maximum - 1] + '…'


class TypingState:
    def __init__(self, clock=time.monotonic):
        self.clock, self.peers = clock, {}

    def update(self, target, sender, nickname, active):
        self.prune()
        key = (target, sender)
        if active and (key in self.peers or len(self.peers) < 512):
            self.peers[key] = (nickname, self.clock() + TYPING_LIFETIME)
        else:
            self.peers.pop(key, None)

    def prune(self):
        now = self.clock()
        self.peers = {key: value for key, value in self.peers.items() if value[1] > now}

    def remove(self, sid):
        self.peers = {key: value for key, value in self.peers.items() if sid not in key}

    def names(self, target):
        self.prune()
        return [value[0] for key, value in self.peers.items() if key[0] == target]


class PendingReactions:
    """A reaction can arrive before its message over independent TCP routes."""
    def __init__(self, clock=time.monotonic):
        self.clock, self.items = clock, OrderedDict()

    def add(self, target, reference, actor, emoji, nickname):
        self.prune()
        key = (target, reference, actor)
        self.items[key] = (emoji, nickname, self.clock() + 10)
        self.items.move_to_end(key)
        while len(self.items) > 256:
            self.items.popitem(last=False)

    def prune(self):
        now = self.clock()
        self.items = OrderedDict((key, value) for key, value in self.items.items() if value[2] > now)

    def take(self, target, reference):
        self.prune()
        found = []
        for key, value in list(self.items.items()):
            if key[:2] == (target, reference):
                found.append((key[2], value[0], value[1]))
                del self.items[key]
        return found
