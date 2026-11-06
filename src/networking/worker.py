"""Qt receives queued signals; all socket operations run on a dedicated asyncio loop."""
import asyncio
import concurrent.futures
import threading

from PySide6.QtCore import QObject, Signal

from .client import ChatClient


class NetworkWorker(QObject):
    received = Signal(str, object)

    def __init__(self, nickname, **options):
        super().__init__()
        self.loop = asyncio.new_event_loop()
        self.client = ChatClient(nickname, lambda kind, data: self.received.emit(kind, data), **options)
        self.thread = threading.Thread(target=self._run, daemon=True, name='FortyTchat network')
        self.ready = threading.Event()

    def _run(self):
        asyncio.set_event_loop(self.loop)
        self.ready.set()
        try:
            self.loop.run_forever()
        finally:
            self.loop.close()

    def start(self):
        self.thread.start()
        self.ready.wait(2)
        return self.submit(self.client.start())

    def submit(self, coroutine):
        future = asyncio.run_coroutine_threadsafe(coroutine, self.loop)
        def finished(result):
            if not result.cancelled() and result.exception():
                self.received.emit('notice', {'text': 'Une opération réseau a échoué. Consultez le diagnostic.'})
        future.add_done_callback(finished)
        return future

    def call(self, method, *args, **kwargs):
        def invoke():
            try:
                getattr(self.client, method)(*args, **kwargs)
            except ValueError as exc:
                self.received.emit('notice', {'text': str(exc)})
        self.loop.call_soon_threadsafe(invoke)

    def send(self, kind, target, **payload):
        return self.submit(self.client.send(kind, target, **payload))

    def stop(self):
        if not self.thread.is_alive():
            return
        future = asyncio.run_coroutine_threadsafe(self.client.stop(), self.loop)
        try:
            future.result(timeout=4)
        except (TimeoutError, concurrent.futures.CancelledError):
            future.cancel()
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(timeout=2)
