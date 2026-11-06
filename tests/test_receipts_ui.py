"""Actual Qt focus, scrolling and preferences decide when a read receipt is sent."""
import time
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QApplication

from src.app import Application
from src.networking.settings import NetworkConfig

ROOT = Path(__file__).resolve().parents[1]


def until(qt, predicate, timeout=6):
    end = time.monotonic() + timeout
    while not predicate():
        qt.processEvents()
        if time.monotonic() >= end: raise AssertionError('État de lecture Qt non atteint')
        time.sleep(.01)
    qt.processEvents()


def settle(qt, seconds=.55):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        qt.processEvents(); time.sleep(.01)


def test_real_ui_delivery_read_hidden_minimized_scrolled_and_disabled(tmp_path, monkeypatch):
    qt = QApplication.instance() or QApplication([])
    qt.setQuitOnLastWindowClosed(False)
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path / 'config'))
    monkeypatch.setattr(NetworkConfig, 'load', classmethod(lambda cls, path: NetworkConfig(mode='relay', relay_host='')))
    apps = [Application(ROOT, SimpleNamespace(dev='read-' + name + '-' + uuid.uuid4().hex[:8],
                                             background=False, insecure_local_relay=None)) for name in ('a','b')]
    a, b = apps
    try:
        until(qt, lambda: a.worker.client.port > 0 and b.worker.client.port > 0)
        ac, bc = a.worker.client, b.worker.client
        a.worker.loop.call_soon_threadsafe(ac.observe, bc.hello(), '127.0.0.1')
        b.worker.loop.call_soon_threadsafe(bc.observe, ac.hello(), '127.0.0.1')
        until(qt, lambda: bc.sid in a.window.peers and ac.sid in b.window.peers)
        av = a.open_private(bc.sid)
        def send(text):
            mid = a.worker.send('message', bc.sid, text=text).result(4)
            until(qt, lambda: a.history.find(bc.sid, mid) is not None and b.history.find(ac.sid, mid) is not None)
            return mid
        def state(mid): return a.history.find(bc.sid, mid).delivery_recipients[bc.sid]['state']
        mid = send('READ_SECRET_1')
        until(qt, lambda: state(mid) == 'delivered')
        settle(qt)
        assert state(mid) == 'delivered'  # No recipient conversation is open.
        bv = b.open_private(ac.sid)
        until(qt, lambda: state(mid) == 'read')
        assert '✓✓ Vu' in av.chat.delivery_labels[mid].text()
        assert '#69b6ff' in av.chat.delivery_labels[mid].styleSheet()
        bv.showMinimized(); settle(qt, .1)
        minimized = send('READ_SECRET_MINIMIZED')
        until(qt, lambda: state(minimized) == 'delivered')
        settle(qt)
        assert state(minimized) == 'delivered'
        bv.open_window()
        until(qt, lambda: state(minimized) == 'read')
        # A visible conversation behind another active window does not count.
        av.open_window(); settle(qt, .1)
        inactive = send('READ_SECRET_INACTIVE')
        until(qt, lambda: state(inactive) == 'delivered')
        settle(qt)
        assert state(inactive) == 'delivered'
        bv.open_window(); until(qt, lambda: state(inactive) == 'read')
        for _ in range(12): b.system_message(ac.sid, 'Contenu de démonstration\n' * 6)
        settle(qt, .1)
        bar = bv.chat.scroll.verticalScrollBar()
        assert bar.maximum() > 1000
        bar.setValue(0); settle(qt, .1)
        scrolled = send('READ_SECRET_BELOW_VIEWPORT')
        until(qt, lambda: state(scrolled) == 'delivered')
        settle(qt)
        assert state(scrolled) == 'delivered'
        assert scrolled not in bv.chat.visible_incoming()
        bv.chat.bottom()
        until(qt, lambda: state(scrolled) == 'read')
        b.change_preference('read_receipts', False)
        disabled = send('READ_SECRET_DISABLED')
        until(qt, lambda: state(disabled) == 'delivered')
        settle(qt)
        assert state(disabled) == 'delivered'
        assert state(mid) == 'read'  # Disabling does not retract a shared receipt.
        b.change_preference('read_receipts', True)
        try:
            until(qt, lambda: state(disabled) == 'read')
        except AssertionError:
            raise AssertionError((qt.activeWindow() is bv, bv.isVisible(), bv.isMinimized(), bv.isActiveWindow(),
                                  disabled in bv.chat.visible_incoming(), bc.read_receipts,
                                  bc.receipts.incoming.get(disabled),
                                  a.history.find(bc.sid, disabled).delivery_recipients))
        av.close(); qt.processEvents()
        av = a.open_private(bc.sid)
        assert '✓✓ Vu' in av.chat.delivery_labels[disabled].text()
        for app in apps:
            for path in app.store.root.glob('*.json'):
                assert 'READ_SECRET' not in path.read_text(), path
    finally:
        for app in apps:
            app.cleanup(); app.window.close(); app.deleteLater()
        qt.processEvents()
