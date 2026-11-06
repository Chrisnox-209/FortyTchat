import asyncio
import base64
import hashlib
import os
import time

import pytest

from src.features.file_transfer import CHUNK_SIZE, MAX_FILE_SIZE
from src.networking.client import ChatClient
from src.networking.protocol import ProtocolError, encode, event
from src.networking.relay import RelayServer


async def until(predicate, timeout=8):
    end = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > end:
            raise AssertionError('Le transfert ne s’est pas terminé dans le délai attendu')
        await asyncio.sleep(.01)


def make_client(name, **kwargs):
    events = []
    client = ChatClient(name, lambda kind, data: events.append((kind, data)),
                        discovery=False, tcp_port=0, **kwargs)
    return client, events


def events_of(events, kind):
    return [data for name, data in events if name == kind]


def bootstrap(clients):
    for a in clients:
        for b in clients:
            if a is not b:
                a.observe(b.hello(), '127.0.0.1')


@pytest.mark.parametrize('relay', [False, True])
def test_real_file_transfer_is_opt_in_hash_checked_and_private(tmp_path, relay):
    async def scenario():
        server = RelayServer() if relay else None
        kwargs = {}
        if server:
            port = await server.start(port=0)
            kwargs = dict(relay_host='127.0.0.1', relay_port=port, tls=False)
        a, ae = make_client('Alice42', **kwargs)
        b, be = make_client('Bob42', **kwargs)
        c, ce = make_client('Chris42', **kwargs)
        clients = [a, b, c]
        source = tmp_path / 'source.bin'
        content = os.urandom(CHUNK_SIZE * 7 + 29)
        source.write_bytes(content)
        destination = tmp_path / 'received.bin'
        try:
            await asyncio.gather(*(x.start() for x in clients))
            if relay:
                await until(lambda: all(len(x.peers) == 2 and x.relay_files for x in clients))
            else:
                bootstrap(clients)
            request = await a.offer_file(b.sid, source)
            await until(lambda: bool(events_of(be, 'file_offer')))
            assert not events_of(ce, 'file_offer')
            assert not destination.exists()
            assert not list(tmp_path.glob('*.part'))
            assert not list(tmp_path.glob('.fortytchat-*'))
            assert events_of(be, 'file_offer')[0]['size'] == len(content)
            await b.accept_file(request, a.sid, destination)
            await until(lambda: bool(events_of(ae, 'file_complete')) and bool(events_of(be, 'file_complete')))
            assert destination.read_bytes() == content
            assert not list(tmp_path.glob('.fortytchat-*'))
            assert not a.files.outgoing and not b.files.incoming
            assert events_of(be, 'file_complete')[0]['path'] == str(destination)
            assert events_of(ae, 'file_complete')[0]['outgoing'] is True
            assert not events_of(ae, 'file_failed') and not events_of(be, 'file_failed')
        finally:
            await asyncio.gather(*(x.stop() for x in clients))
            if server:
                await server.stop()
    asyncio.run(scenario())


@pytest.mark.parametrize('relay', [False, True])
def test_general_offer_accept_decline_and_empty_file(tmp_path, relay):
    async def scenario():
        server = RelayServer() if relay else None
        kwargs = {}
        if server:
            port = await server.start(port=0)
            kwargs = dict(relay_host='127.0.0.1', relay_port=port, tls=False)
        a, ae = make_client('Alice42', **kwargs)
        b, be = make_client('Bob42', **kwargs)
        c, ce = make_client('Chris42', **kwargs)
        clients = [a, b, c]
        source = tmp_path / 'empty.txt'
        source.write_bytes(b'')
        destination = tmp_path / 'empty-copy.txt'
        try:
            await asyncio.gather(*(x.start() for x in clients))
            if relay:
                await until(lambda: all(len(x.peers) == 2 and x.relay_files for x in clients))
            else:
                bootstrap(clients)
            request = await a.offer_file('general', source)
            await until(lambda: bool(events_of(be, 'file_offer')) and bool(events_of(ce, 'file_offer')))
            assert set(events_of(ae, 'file_offer')[0]['recipients']) == {b.sid, c.sid}
            await c.decline_file(request, a.sid)
            await until(lambda: bool(events_of(ae, 'file_declined')))
            await b.accept_file(request, a.sid, destination)
            await until(lambda: bool(events_of(ae, 'file_complete')))
            assert destination.read_bytes() == b''
            assert not c.files.incoming
            assert not a.files.outgoing
        finally:
            await asyncio.gather(*(x.stop() for x in clients))
            if server:
                await server.stop()
    asyncio.run(scenario())


def test_protocol_rejects_oversize_path_names_and_invalid_chunks():
    valid = event('file_offer', 'a' * 32, 'b' * 32, name='code.c', size=3,
                  sha256=hashlib.sha256(b'123').hexdigest())
    assert encode(valid)
    for changes in ({'name': '漢' * 86}, {'name': '../secret'}, {'name': 'x\\secret'}, {'name': 'x\x00'},
                    {'size': MAX_FILE_SIZE + 1}, {'size': True}, {'sha256': 'bad'}, {'path': '/tmp/x'}):
        with pytest.raises(ProtocolError):
            encode({**valid, **changes})
    for raw in ('!!!!', '', base64.b64encode(b'a' * (CHUNK_SIZE + 1)).decode()):
        with pytest.raises(ProtocolError):
            encode(event('file_chunk', 'a' * 32, 'b' * 32, request='c' * 32, offset=0, data=raw))
    with pytest.raises(ProtocolError):
        encode(event('file_chunk', 'a' * 32, 'general', request='c' * 32, offset=0, data='YQ=='))


def test_unaccepted_corrupt_out_of_order_and_cancel_leave_no_files(tmp_path):
    async def scenario():
        a, ae = make_client('Alice42')
        b, be = make_client('Bob42')
        try:
            await a.start()
            await b.start()
            bootstrap([a, b])
            request = 'c' * 32
            data = b'secret'
            offer = event('file_offer', a.sid, b.sid, name='photo.jpg', size=len(data), sha256=hashlib.sha256(data).hexdigest())
            offer['id'] = request
            chunk = event('file_chunk', a.sid, b.sid, request=request, offset=0, data=base64.b64encode(data).decode())
            assert not (await b.files.receive(chunk))[0]
            assert (await b.files.receive(offer))[0]
            # Avoid a real sender: accept stores consent, network function acknowledges the offer only.
            async def delivered(frame, peers=None):
                return True
            b.deliver_file = delivered
            destination = tmp_path / 'photo.jpg'
            await b.accept_file(request, a.sid, destination)
            assert not destination.exists()
            corrupted = {**chunk, 'data': base64.b64encode(b'wrong!').decode()}
            assert (await b.files.receive(corrupted))[0]
            assert not (await b.files.receive(event('file_end', a.sid, b.sid, request=request)))[0]
            assert not destination.exists() and not list(tmp_path.glob('.fortytchat-*'))
            offer['id'] = 'd' * 32
            assert (await b.files.receive(offer))[0]
            await b.accept_file(offer['id'], a.sid, destination)
            assert not (await b.files.receive(event('file_chunk', a.sid, b.sid, request=offer['id'], offset=1, data='YQ==')))[0]
            assert not destination.exists() and not list(tmp_path.glob('.fortytchat-*'))
            offer['id'] = 'e' * 32
            assert (await b.files.receive(offer))[0]
            await b.accept_file(offer['id'], a.sid, destination)
            await b.cancel_file(offer['id'], a.sid)
            assert not destination.exists() and not list(tmp_path.glob('.fortytchat-*'))
        finally:
            await a.stop()
            await b.stop()
    asyncio.run(scenario())


def test_existing_destination_and_shutdown_cleanup(tmp_path):
    async def scenario():
        a, _ = make_client('Alice42')
        b, be = make_client('Bob42')
        try:
            await a.start()
            await b.start()
            bootstrap([a, b])
            async def delivered(frame, peers=None):
                return True
            b.deliver_file = delivered
            offer = event('file_offer', a.sid, b.sid, name='x.txt', size=4, sha256=hashlib.sha256(b'test').hexdigest())
            assert (await b.files.receive(offer))[0]
            existing = tmp_path / 'existing.txt'
            existing.write_bytes(b'KEEP')
            await b.accept_file(offer['id'], a.sid, existing)
            assert existing.read_bytes() == b'KEEP'
            assert events_of(be, 'file_deferred')
            await b.accept_file(offer['id'], a.sid, tmp_path / 'new.txt')
            assert list(tmp_path.glob('.fortytchat-*'))
            await b.stop()
            assert not list(tmp_path.glob('.fortytchat-*'))
            assert not (tmp_path / 'new.txt').exists()
        finally:
            await a.stop()
            if not b._closing:
                await b.stop()
    asyncio.run(scenario())


def test_general_partial_delivery_keeps_successful_offer_and_cancel_skips_completed(tmp_path):
    async def scenario():
        a, ae = make_client('Alice42')
        b, be = make_client('Bob42')
        c, ce = make_client('Chris42')
        clients = [a, b, c]
        source = tmp_path / 'source.txt'
        source.write_bytes(b'general-file')
        destination = tmp_path / 'bob.txt'
        try:
            await asyncio.gather(*(x.start() for x in clients))
            bootstrap(clients)
            original = a._send_direct
            async def partially_delivered(peer, frame):
                if frame['type'] == 'file_offer' and peer.sid == c.sid:
                    return False
                return await original(peer, frame)
            a._send_direct = partially_delivered
            request = await a.offer_file('general', source)
            assert events_of(be, 'file_offer') and not events_of(ce, 'file_offer')
            assert events_of(ae, 'file_failed')[0]['peer'] == c.sid
            assert request in a.files.outgoing
            await b.accept_file(request, a.sid, destination)
            await until(lambda: bool(events_of(ae, 'file_complete')))
            assert destination.read_bytes() == source.read_bytes()
            assert not a.files.outgoing
            # Next offer: Bob completes, Chris stays pending, cancellation skips Bob.
            a._send_direct = original
            request = await a.offer_file('general', source)
            destination2 = tmp_path / 'bob2.txt'
            await b.accept_file(request, a.sid, destination2)
            await until(lambda: len(events_of(ae, 'file_complete')) == 2)
            await a.cancel_file(request, 'general')
            failed = [item for item in events_of(ae, 'file_failed') if item['id'] == request]
            assert [item['peer'] for item in failed] == [c.sid]
            assert destination2.read_bytes() == source.read_bytes()
            assert not a.files.outgoing and not c.files.incoming
        finally:
            await asyncio.gather(*(x.stop() for x in clients))
    asyncio.run(scenario())


def test_source_limit_and_non_regular_files_are_rejected_without_reading(tmp_path):
    from src.features.file_transfer import prepare_source
    oversized = tmp_path / 'huge.bin'
    with oversized.open('wb') as target:
        target.truncate(MAX_FILE_SIZE + 1)
    with pytest.raises(ValueError, match='20 Mo'):
        prepare_source(oversized)
    if hasattr(os, 'mkfifo'):
        fifo = tmp_path / 'pipe'
        os.mkfifo(fifo)
        with pytest.raises(ValueError, match='fichier ordinaire'):
            prepare_source(fifo)


def test_modified_source_fails_and_removes_accepted_temporary_file(tmp_path):
    async def scenario():
        a, ae = make_client('Alice42')
        b, be = make_client('Bob42')
        source = tmp_path / 'source.txt'
        source.write_bytes(b'before')
        destination = tmp_path / 'received.txt'
        try:
            await a.start()
            await b.start()
            bootstrap([a, b])
            request = await a.offer_file(b.sid, source)
            source.write_bytes(b'after-change')
            await b.accept_file(request, a.sid, destination)
            await until(lambda: bool(events_of(ae, 'file_failed')) and bool(events_of(be, 'file_failed')))
            assert not destination.exists()
            assert not list(tmp_path.glob('.fortytchat-*'))
            assert not a.files.outgoing and not b.files.incoming
        finally:
            await a.stop()
            await b.stop()
    asyncio.run(scenario())


@pytest.mark.parametrize('cancel_during_write', [False, True])
def test_concurrent_duplicate_chunks_are_serialized_and_cancel_waits_for_disk(tmp_path, monkeypatch, cancel_during_write):
    import src.features.file_transfer as files_module
    async def scenario():
        a, _ = make_client('Alice42')
        b, be = make_client('Bob42')
        a.port = b.port = 39742
        bootstrap([a, b])
        async def delivered(frame, peers=None):
            return True
        b.deliver_file = delivered
        raw = b'only-once'
        offer = event('file_offer', a.sid, b.sid, name='data.txt', size=len(raw), sha256=hashlib.sha256(raw).hexdigest())
        request = offer['id']
        assert (await b.files.receive(offer))[0]
        destination = tmp_path / 'download.txt'
        await b.accept_file(request, a.sid, destination)
        state = b.files.incoming[(request, a.sid)]
        handle = state['file']
        entered, release = asyncio.Event(), asyncio.Event()
        original_disk = files_module.disk
        write_calls = []
        async def delayed_disk(function, *args):
            if getattr(function, '__name__', '') == 'write_all':
                write_calls.append(args[1])
                entered.set()
                await release.wait()
            return await original_disk(function, *args)
        monkeypatch.setattr(files_module, 'disk', delayed_disk)
        chunk = event('file_chunk', a.sid, b.sid, request=request, offset=0, data=base64.b64encode(raw).decode())
        first = asyncio.create_task(b.files.receive(chunk))
        await entered.wait()
        duplicate = asyncio.create_task(b.files.receive(dict(chunk)))
        await asyncio.sleep(0)
        assert len(write_calls) == 1 and not duplicate.done()
        if cancel_during_write:
            await b.cancel_file(request, a.sid)
            assert not handle.closed  # In-flight write retains its valid descriptor.
        release.set()
        results = await asyncio.gather(first, duplicate)
        assert len(write_calls) == 1
        if cancel_during_write:
            assert not any(ok for ok, reason in results)
            assert handle.closed and not destination.exists()
            assert not list(tmp_path.glob('.fortytchat-*'))
            assert not events_of(be, 'file_complete')
        else:
            assert all(ok for ok, reason in results)
            assert (await b.files.receive(event('file_end', a.sid, b.sid, request=request)))[0]
            assert destination.read_bytes() == raw
        for task in list(b.tasks):
            await task
        b.files.stop()
    asyncio.run(scenario())


def test_partial_writes_are_completed_and_zero_or_missing_writes_fail():
    from src.features.file_transfer import write_all
    class ShortWriter:
        def __init__(self):
            self.content = bytearray()
        def write(self, data):
            chunk = data[:2]
            self.content.extend(chunk)
            return len(chunk)
    writer = ShortWriter()
    write_all(writer, b'full-content')
    assert writer.content == b'full-content'
    class FailedWriter:
        def __init__(self, result):
            self.result = result
        def write(self, data):
            return self.result
    for result in (0, None):
        with pytest.raises(OSError):
            write_all(FailedWriter(result), b'content')
