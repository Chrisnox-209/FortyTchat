"""Ephemeral delivery/reading metadata. No messages, files, timestamps on disk."""
from collections import OrderedDict
import time

READ_EVENTS = {'read_receipt'}
READ_BATCH = 32
MAX_TRACKED = 4000
PER_CHAT = 200


class ReceiptLedger:
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.outgoing, self.incoming = OrderedDict(), OrderedDict()

    @staticmethod
    def remember(book, reference, record):
        book[reference] = record
        book.move_to_end(reference)
        same = [mid for mid, item in book.items() if item['chat'] == record['chat']]
        for mid in same[:-PER_CHAT]:
            book.pop(mid, None)
        while len(book) > MAX_TRACKED:
            book.popitem(last=False)

    def sent(self, frame, peers):
        self.remember(self.outgoing, frame['id'], {'chat': frame['to'],
                      'recipients': {peer.sid for peer in peers}, 'read': set()})

    def received(self, frame):
        self.remember(self.incoming, frame['id'], {'sender': frame['from'],
                      'chat': 'general' if frame['to'] == 'general' else frame['from'],
                      'done': False, 'pending': False, 'retry': 0})

    def read(self, sender, references):
        changed = []
        for reference in references:
            item = self.outgoing.get(reference)
            if item and sender in item['recipients'] and sender not in item['read']:
                item['read'].add(sender)
                changed.append((reference, item['chat']))
        return changed

    def eligible(self, references):
        now = self.clock()
        return [(mid, self.incoming[mid]) for mid in dict.fromkeys(references)
                if mid in self.incoming and not self.incoming[mid]['done']
                and not self.incoming[mid]['pending'] and self.incoming[mid]['retry'] <= now]

    def finish(self, references, ok):
        for mid in references:
            item = self.incoming.get(mid)
            if item:
                item.update(pending=False, done=ok, retry=0 if ok else self.clock() + 5)

    def clear(self):
        self.outgoing.clear()
        self.incoming.clear()


RANK = {'sent': 0, 'unconfirmed': 1, 'delivered': 2, 'read': 3}


def update_recipient(message, peer, state):
    item = message.delivery_recipients.get(peer)
    if not message.outgoing or item is None or state not in RANK:
        return False
    if RANK[state] <= RANK.get(item['state'], -1):
        return False
    item['state'] = state
    return True


def delivery_label(message):
    """Text, is-blue, plain-text tooltip. A read receipt also proves delivery."""
    recipients = list(message.delivery_recipients.values())
    if not recipients:
        return ('○ Local', False, 'Aucun destinataire : visible uniquement ici.') if message.delivery_local else ('', False, '')
    total = len(recipients)
    delivered = sum(item['state'] in ('delivered', 'read') for item in recipients)
    read = sum(item['state'] == 'read' for item in recipients)
    failed = sum(item['state'] == 'unconfirmed' for item in recipients)
    if read == total:
        text, blue = '✓✓ Vu', True
    elif delivered == total:
        text, blue = '✓✓ Livré', False
    elif failed and not delivered:
        text, blue = '! Non confirmé', False
    else:
        text, blue = '✓ Envoyé', False
    if total > 1:
        text += f' · {read}/{total} vus' if read else f' · {delivered}/{total} livrés'
    details = [f'Livré : {delivered}/{total} · Vu : {read}/{total}']
    names = {'sent': 'envoyé, réception en attente', 'unconfirmed': 'livraison non confirmée',
             'delivered': 'livré, lecture non confirmée', 'read': 'vu'}
    for item in recipients[:8]:
        state = names[item['state']]
        if not item['reads']:
            state += ' (confirmations de lecture indisponibles sur ce client)'
        details.append(f'{item["nick"]} : {state}')
    if total > 8:
        details.append(f'… et {total - 8} autre(s) destinataire(s)')
    details.append('« Vu » signifie affiché dans une conversation active, sans prouver une lecture attentive.')
    return text, blue, '\n'.join(details)
