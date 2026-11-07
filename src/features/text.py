"""Plain-text fenced code and stable-contact mentions, never executable markup."""
import re

MENTION = re.compile(r'(?<![\w@])@([A-Za-z0-9_-]{3,28})(?![\w-])')
FENCE = re.compile(r'^```([A-Za-z0-9_+#.-]{0,24})[^\S\n]*\n(.*?)^```[^\S\n]*(?:\n|$)', re.M | re.S)


def text_parts(text):
    offset = 0
    for match in FENCE.finditer(text):
        if match.start() > offset: yield ('text', '', text[offset:match.start()])
        yield ('code', match[1], match[2].rstrip('\n'))
        offset = match.end()
    if offset < len(text): yield ('text', '', text[offset:])


def mention_ids(text, peers):
    names = {match[1].casefold() for kind, _, body in text_parts(text) if kind == 'text' for match in MENTION.finditer(body)}
    return list(dict.fromkeys(p['contact_id'] for p in peers if p['nick'].casefold() in names))[:32]
