"""Exercise user actions against the actual controller and real TCP peers."""
import time
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QApplication, QFileDialog

from src.app import Application
from src.memory import Message
from src.networking.client import ChatClient
from src.networking.settings import NetworkConfig

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope='module')
def qt():
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    return app


def until(qt, predicate, timeout=8):
    deadline = time.monotonic() + timeout
    while not predicate():
        qt.processEvents()
        if time.monotonic() > deadline:
            raise AssertionError('Interaction non terminée')
        time.sleep(.01)
    qt.processEvents()


def test_files_pause_replies_and_wizz_from_actual_ui(qt, tmp_path, monkeypatch):
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path / 'config'))
    monkeypatch.setattr(NetworkConfig, 'load', classmethod(lambda cls, path: NetworkConfig(mode='relay', relay_host='')))
    controller = Application(ROOT, SimpleNamespace(dev='improve-' + uuid.uuid4().hex[:10],
                                                   background=False, insecure_local_relay=None))
    peer_events = []
    peer = ChatClient('Ami42', lambda k, d: peer_events.append((k, d)), discovery=False, tcp_port=0)
    source, saved = tmp_path / ('nom' * 45 + '.txt'), tmp_path / 'recu.txt'
    source.write_text('Bonjour le cluster!\n' * 15000)
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

        # The actual plus button offers a file to this private recipient.
        monkeypatch.setattr(QFileDialog, 'getOpenFileName', lambda *a, **k: (str(source), ''))
        view.composer.file_button.click()
        until(qt, lambda: any(k == 'file_offer' for k, d in peer_events))
        outgoing = next(d for k, d in peer_events if k == 'file_offer')
        destination = tmp_path / 'remote.txt'
        controller.worker.submit(peer.accept_file(outgoing['id'], client.sid, destination)).result(4)
        until(qt, lambda: controller.file_transfers.get(outgoing['id'], {}).get('status') == 'complete')
        assert destination.read_bytes() == source.read_bytes()

        incoming_id = controller.worker.submit(peer.offer_file(client.sid, source)).result(4)
        until(qt, lambda: incoming_id in view.chat.file_cards)
        card = view.chat.file_cards[incoming_id][1]
        assert card.accept.isVisible() and not saved.exists()
        assert card.name.toolTip() == source.name
        assert card.name.fontMetrics().horizontalAdvance(card.name.text()) <= card.name.width()
        monkeypatch.setattr(QFileDialog, 'getSaveFileName', lambda *a, **k: (str(saved), ''))
        card.accept.click()
        until(qt, lambda: controller.file_transfers[incoming_id]['status'] == 'complete')
        assert saved.read_bytes() == source.read_bytes()
        view.close()
        view = controller.open_private(peer.sid)
        assert not view.chat.file_cards[incoming_id][1].accept.isVisible()
        assert view.chat.file_cards[incoming_id][1].metadata['status'] == 'complete'

        # A general offer is opt-in and refusal does not create a download.
        general_id = controller.worker.submit(peer.offer_file('general', source)).result(4)
        until(qt, lambda: general_id in controller.window.chat.file_cards)
        general_card = controller.window.chat.file_cards[general_id][1]
        general_card.decline.click()
        until(qt, lambda: any(k == 'file_declined' and d['id'] == general_id for k, d in peer_events))
        assert controller.file_transfers[general_id]['status'] == 'declined'

        controller.change_preference('dnd', True)
        request = controller.worker.submit(peer.send('break_invite', client.sid)).result(4)
        until(qt, lambda: request in controller.popups)
        popup = controller.popups[request][1]
        controller.apply_network_preferences()
        qt.processEvents()
        assert popup.isVisible()
        popup.yes_button.click()
        until(qt, lambda: any(k == 'event' and d['frame'].get('request') == request for k, d in peer_events))
        request = controller.worker.send('break_invite', peer.sid).result(4)
        controller.worker.submit(peer.send('break_reply', client.sid, request=request, answer='declined')).result(4)
        until(qt, lambda: bool(controller.reply_popups))
        response = next(iter(controller.reply_popups.values()))
        assert response.isVisible() and 'c63150' in response.styleSheet()
        response.close()

        controller.change_preference('dnd', False)
        until(qt, lambda: not client.dnd)
        for n in range(40):
            controller.add_message(peer.sid, Message(peer.sid, 'Ami42', f'Message {n} ' + 'long ' * 30))
        qt.processEvents()
        view.chat.scroll.verticalScrollBar().setValue(0)
        view.hide()
        controller.worker.submit(peer.send('wizz', client.sid)).result(4)
        until(qt, lambda: view.isVisible() and hasattr(view.window(), '_fortytchat_shake'))
        until(qt, lambda: view.chat.scroll.verticalScrollBar().value() == view.chat.scroll.verticalScrollBar().maximum())
        assert view.window()._fortytchat_shake.duration() >= 2500
        assert not list(tmp_path.glob('.fortytchat-*'))
    finally:
        controller.worker.submit(peer.stop()).result(4)
        controller.cleanup()
        controller.window.close()
        controller.deleteLater()


def test_general_expiration_closes_pending_cards_without_losing_completed_receivers():
    metadata = {'id': 'offer', 'name': 'fichier.txt', 'size': 42, 'outgoing': True, 'target': 'general',
                'status': 'offered', 'recipients_state': {
                    'alice': {'status': 'complete', 'received': 42},
                    'bob': {'status': 'offered', 'received': 0}}}
    controller = SimpleNamespace(file_transfers={'offer': metadata}, refresh_file_card=lambda item: None)
    Application.file_event(controller, 'file_failed', {'id': 'offer', 'peer': 'general', 'reason': 'Expiré'})
    assert metadata['status'] == 'complete'
    assert metadata['recipients_state']['bob']['status'] == 'failed'
    assert metadata['recipients_state']['alice']['status'] == 'complete'
