"""Bounded transient UI state. Intentionally has no filesystem or serialization code."""
from collections import OrderedDict, deque
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Message:
    sender: str
    nickname: str
    text: str
    outgoing: bool = False
    system: bool = False
    id: str = ''
    avatar: int = -1
    avatar_image: str = ''
    time: datetime = field(default_factory=datetime.now)
    attachment: dict | None = None
    reply_to: str = ''
    reply_name: str = ''
    reply_text: str = ''
    reactions: dict = field(default_factory=dict)
    delivery_recipients: dict = field(default_factory=dict)
    delivery_local: bool = False


class Conversations:
    def __init__(self, per_chat=200, max_chats=64, total_limit=4000):
        self.per_chat, self.max_chats, self.total_limit = per_chat, max_chats, total_limit
        self.chats = OrderedDict()

    def append(self, chat, message):
        if chat not in self.chats:
            if len(self.chats) >= self.max_chats:
                self.chats.popitem(last=False)
            self.chats[chat] = deque(maxlen=self.per_chat)
        self.chats.move_to_end(chat)
        self.chats[chat].append(message)
        while sum(map(len, self.chats.values())) > self.total_limit:
            oldest = next(iter(self.chats))
            self.chats[oldest].popleft()
            if not self.chats[oldest]:
                del self.chats[oldest]

    def get(self, chat):
        return list(self.chats.get(chat, ()))

    def clear(self):
        self.chats.clear()

    def find(self, chat, reference):
        return next((message for message in reversed(self.chats.get(chat, ()))
                     if message.id and message.id == reference and not message.system), None)

    def react(self, chat, reference, actor, emoji, nickname):
        message = self.find(chat, reference)
        if message is None:
            return None
        if emoji:
            if actor in message.reactions or len(message.reactions) < 512:
                message.reactions[actor] = {'emoji': emoji, 'nickname': nickname}
        else:
            message.reactions.pop(actor, None)
        return message

    def move(self, source, target):
        """Retarget a still-open friend's RAM-only conversation after reconnection."""
        if source == target:
            return
        messages = sorted(self.get(source) + self.get(target), key=lambda m: m.time)
        self.chats.pop(source, None)
        self.chats.pop(target, None)
        for message in messages[-self.per_chat:]:
            self.append(target, message)
