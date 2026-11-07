"""Bounded room sessions, message ownership and ephemeral polls."""
from collections import OrderedDict
from dataclasses import dataclass, field
import re
import time

CAPABILITY = 'rooms2'
GROUP_EVENTS = {'group_invite', 'group_join', 'group_leave', 'group_state', 'group_update', 'group_sync', 'group_popup_request', 'group_popup_grant', 'group_removed'}
CHANGE_EVENTS = {'message_edit', 'message_delete'}
POLL_EVENTS = {'poll_create', 'poll_vote'}
COLLAB_EVENTS = GROUP_EVENTS | CHANGE_EVENTS | POLL_EVENTS
MAX_GROUPS, MAX_MEMBERS, MAX_POLLS, MAX_VOTERS = 32, 32, 128, 256


def identifier(value):
    return isinstance(value, str) and re.fullmatch(r'[a-f0-9]{32}', value) is not None


def plain(value, maximum):
    return (isinstance(value, str) and bool(value.strip()) and len(value) <= maximum
            and not any(ord(c) < 32 and c not in '\n\t' or 0xd800 <= ord(c) <= 0xdfff for c in value))


def group_key(reference):
    return 'group:' + reference


def group_id(target):
    return target[6:] if isinstance(target, str) and target.startswith('group:') and identifier(target[6:]) else ''


def chat_target(frame, own_sid):
    return group_key(frame['group']) if frame.get('group') else (
        frame['to'] if frame['from'] == own_sid or frame['to'] == 'general' else frame['from'])


@dataclass
class Room:
    id: str
    name: str
    owner: str
    members: tuple
    state: str = 'pending'
    joined: set = field(default_factory=set)
    left: set = field(default_factory=set)
    sessions: dict = field(default_factory=dict)

    profile: dict = field(default_factory=dict)

    def public(self):
        return dict(**{k: v for k, v in self.profile.items() if k not in ('id', 'name', 'owner', 'members', 'key', 'signature')}, id=self.id, name=self.name, owner=self.owner, members=list(self.members),
                    state=self.state, joined=sorted(self.joined), left=sorted(self.left))


class MessageBook:
    """Only the original signed session can edit/delete its own message in that chat."""
    def __init__(self, clock=time.monotonic):
        self.items, self.pending = OrderedDict(), OrderedDict()
        self.clock = clock

    def remember(self, frame, chat, recipients=()):
        if frame['id'] in self.items:
            return []
        self.items[frame['id']] = dict(author=frame['from'], chat=chat, recipients=set(recipients),
                                       seq=-1, deleted=False, kind=frame['type'])
        same = [key for key, item in self.items.items() if item['chat'] == chat]
        for key in same[:-200]:
            self.items.pop(key, None)
        while len(self.items) > 4000:
            self.items.popitem(last=False)
        changes = []
        for key, (change, expiry) in list(self.pending.items()):
            if expiry <= self.clock():
                self.pending.pop(key, None)
            elif key[:3] == (frame['from'], chat, frame['id']):
                self.pending.pop(key, None)
                if self.change(change, chat) == 'apply':
                    changes.append(change)
        return changes

    def change(self, frame, chat):
        item = self.items.get(frame['message'])
        if item is None:
            self.pending[(frame['from'], chat, frame['message'], frame['seq'])] = (dict(frame), self.clock() + 10)
            while len(self.pending) > 256:
                self.pending.popitem(last=False)
            return 'pending'
        if item['author'] != frame['from'] or item['chat'] != chat:
            return 'refused'
        if item['deleted'] or frame['seq'] <= item['seq']:
            return 'old'
        if frame['type'] == 'message_edit' and item['kind'] != 'message':
            return 'refused'
        item['seq'], item['deleted'] = frame['seq'], frame['type'] == 'message_delete'
        return 'apply'


class PollBook:
    def __init__(self):
        self.items = OrderedDict()

    def create(self, frame, chat):
        poll = dict(id=frame['id'], author=frame['from'], chat=chat, question=frame['question'],
                    options=list(frame['options']), multiple=frame.get('multiple', False), audience=list(frame['audience']), votes={}, sequences={}, deleted=False)
        self.items[frame['id']] = poll
        while len(self.items) > MAX_POLLS:
            self.items.popitem(last=False)
        return poll

    def vote(self, frame, chat):
        poll = self.items.get(frame['poll'])
        if not poll or poll['deleted'] or poll['chat'] != chat or frame['from'] not in poll['audience']:
            return False
        value = frame.get('options') if poll['multiple'] else frame.get('option')
        if poll['multiple']:
            if (not isinstance(value, list) or len(value) > len(poll['options'])
                    or any(type(i) is not int or not 0 <= i < len(poll['options']) for i in value) or len(set(value)) != len(value)):
                return False
            value = sorted(value)
        elif type(value) is not int or not -1 <= value < len(poll['options']):
            return False
        if frame['seq'] <= poll['sequences'].get(frame['from'], -1):
            return True
        poll['sequences'][frame['from']] = frame['seq']
        if value == -1 or value == []:
            poll['votes'].pop(frame['from'], None)
        else:
            poll['votes'][frame['from']] = value
        return True

    def public(self, reference):
        poll = self.items.get(reference)
        return {key: value.copy() if isinstance(value, dict) else list(value) if isinstance(value, list) else value
                for key, value in poll.items() if key != 'sequences'} if poll else None
