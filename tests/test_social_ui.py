import time
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QApplication, QPushButton

from src.app import Application
from src.identity import session_id
from src.networking.client import ChatClient
from src.networking.settings import NetworkConfig
from src.ui.social import ReplyPreview

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope='module')
def qt():
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    return app


def until(qt, predicate, timeout=7):
    end = time.monotonic() + timeout
    while not predicate():
        qt.processEvents()
        if time.monotonic() > end: raise AssertionError('Interaction Qt non terminée')
        time.sleep(.01)
    qt.processEvents()


def test_real_ui_reply_reactions_typing_and_ephemeral_storage(qt, tmp_path, monkeypatch):
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path / 'config'))
    monkeypatch.setattr(NetworkConfig, 'load', classmethod(lambda cls, path: NetworkConfig(mode='relay', relay_host='')))
    controller = Application(ROOT, SimpleNamespace(dev='social-' + uuid.uuid4().hex[:8], background=False, insecure_local_relay=None))
    events = []
    peer = ChatClient('Alice42', lambda kind, data: events.append((kind, data)), discovery=False, tcp_port=0)
    try:
        controller.worker.submit(peer.start()).result(4)
        client = controller.worker.client
        until(qt, lambda: client.port > 0)
        def connect():
            peer.observe(client.hello(), '127.0.0.1')
            client.observe(peer.hello(), '127.0.0.1')
        controller.worker.loop.call_soon_threadsafe(connect)
        until(qt, lambda: peer.sid in controller.window.peers)
        view = controller.open_private(peer.sid)
        controller.worker.submit(peer.send('typing', client.sid, active=True)).result(4)
        until(qt, lambda: view.chat.typing.isVisible())
        assert view.chat.typing.names == ['Alice42']
        until(qt, lambda: view.chat.typing.dots.phase != 0)
        assert not controller.history.get(peer.sid)
        original = controller.worker.submit(peer.send('message', client.sid, text='<b>ORIGINAL_SOCIAL_SECRET</b>')).result(4)
        until(qt, lambda: controller.history.find(peer.sid, original) is not None)
        assert not view.chat.typing.isVisible()
        row, bubble, _ = view.chat.message_rows[original]
        reply = next(button for button in bubble.findChildren(QPushButton) if button.toolTip() == 'Répondre à ce message')
        reply.click()
        assert view.composer.reply_to == original
        view.input.setPlainText('Réponse depuis la fenêtre')
        until(qt, lambda: any(kind == 'event' and data['frame']['type'] == 'typing' and data['frame']['active'] for kind, data in events))
        view.composer.submit()
        until(qt, lambda: any(kind == 'event' and data['frame'].get('reply_to') == original for kind, data in events))
        until(qt, lambda: view.composer.reply_to == '')
        outgoing = controller.history.get(peer.sid)[-1]
        assert outgoing.reply_to == original and outgoing.reply_text == '<b>ORIGINAL_SOCIAL_SECRET</b>'
        assert outgoing.id in view.chat.quote_previews
        assert '&lt;b&gt;' in view.chat.quote_previews[outgoing.id].text.toolTip()
        view.chat.focus_message(original)
        assert 'border:' in bubble.styleSheet()

        view.chat.reaction_requested.emit(original, '👍')
        until(qt, lambda: controller.preferences.contact_id in controller.history.find(peer.sid, original).reactions)
        view.chat.reaction_bars[original].pills['👍'].click()
        until(qt, lambda: not controller.history.find(peer.sid, original).reactions)
        controller.worker.submit(peer.send('reaction', client.sid, message=original, emoji='😂')).result(4)
        until(qt, lambda: '😂' in view.chat.reaction_bars[original].pills)
        assert len(controller.history.get(peer.sid)) == 2

        # An independently routed reaction may beat its message to the receiver.
        future = session_id()
        controller.worker.submit(peer.send('reaction', client.sid, message=future, emoji='☕')).result(4)
        until(qt, lambda: bool(controller.pending_reactions.items))
        from src.networking.protocol import event
        frame = {**event('message', peer.sid, client.sid, text='LATE_SOCIAL_SECRET'), 'id': future}
        controller.worker.submit(peer._send_direct(peer.peers[client.sid], frame)).result(4)
        until(qt, lambda: controller.history.find(peer.sid, future) is not None)
        assert controller.history.find(peer.sid, future).reactions[peer.contact_id]['emoji'] == '☕'
        # Replies never fetch or disclose old text to a new session.
        missing = session_id()
        request = controller.worker.submit(peer.send('message', client.sid, text='Citation en attente', reply_to=missing)).result(4)
        until(qt, lambda: controller.history.find(peer.sid, request) is not None)
        assert controller.history.find(peer.sid, request).reply_text == 'Message plus disponible'
        late = {**event('message', peer.sid, client.sid, text='Source arrivée ensuite'), 'id': missing}
        controller.worker.submit(peer._send_direct(peer.peers[client.sid], late)).result(4)
        until(qt, lambda: controller.history.find(peer.sid, request).reply_text == 'Source arrivée ensuite')
        view.close()
        view = controller.open_private(peer.sid)
        assert '😂' in view.chat.reaction_bars[original].pills
        assert view.chat.findChildren(ReplyPreview)
        controller.store.save()
        stored = controller.store.path.read_text()
        assert 'ORIGINAL_SOCIAL_SECRET' not in stored and 'LATE_SOCIAL_SECRET' not in stored
        assert 'reactions' not in stored and 'reply_to' not in stored
    finally:
        controller.worker.submit(peer.stop()).result(4)
        controller.cleanup()
        controller.window.close()
        controller.deleteLater()
