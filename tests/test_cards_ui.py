import time
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide6.QtCore import Qt, QPoint
from PySide6.QtGui import QImageReader, QMovie
from PySide6.QtWidgets import QApplication, QLabel

from src.app import Application
from src.features.cards import CardTemplates, EMOJIS, answer_card
from src.networking.client import ChatClient
from src.networking.settings import NetworkConfig
from src.ui.animated import AnimatedEmoji, ASSETS
from src.ui.break_popup import CardPopup
from src.ui.card_editor import CardEditor
from src.ui.theme import apply_theme

ROOT = Path(__file__).resolve().parents[1]
CARD = {'title': 'On fait une pause ?', 'text': 'On se retrouve en bas ?', 'emoji': '1f923'}


@pytest.fixture(scope='module')
def qt():
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    return app


def until(qt, predicate, timeout=7):
    end = time.monotonic() + timeout
    while not predicate():
        qt.processEvents()
        if time.monotonic() > end: raise AssertionError('Interaction animée non terminée')
        time.sleep(.01)
    qt.processEvents()


def test_all_eleven_bundled_gifs_decode_and_play_with_bounded_cache(qt):
    for code, glyph, name in EMOJIS:
        reader = QImageReader(str(ASSETS / f'{code}.gif'))
        assert reader.canRead() and reader.supportsAnimation() and reader.imageCount() > 1
    emoji = AnimatedEmoji('1f44d', 80)
    emoji.show()
    until(qt, lambda: emoji.animation.currentFrameNumber() > 0)
    assert emoji.animation.cacheMode() == QMovie.CacheMode.CacheNone
    assert emoji.animation.currentPixmap().width() == 80
    emoji.set_code('1f92f')
    until(qt, lambda: emoji.animation.currentFrameNumber() > 0)
    with pytest.raises(ValueError): emoji.set_code('../remote.gif')
    emoji.hide()
    assert emoji.animation.state() == QMovie.MovieState.NotRunning
    emoji.deleteLater()


def test_editor_and_long_cards_remain_scrollable_and_readable(qt, tmp_path):
    templates = CardTemplates(tmp_path)
    for theme in ('dark', 'light'):
        apply_theme(qt, theme)
        editor = CardEditor('Alice42', templates)
        editor.resize(430, 440)
        editor.title.setText('W' * 80)
        editor.text.setPlainText('W' * 450)
        assert len(editor.text.toPlainText()) == 400
        editor.show()
        for _ in range(8): qt.processEvents()
        editor.save_button.click()
        editor.close()
        editor.deleteLater()
        popup = CardPopup('Alice42', {'title': 'W' * 80, 'text': 'W' * 400, 'emoji': '1f44d'}, lambda value: None)
        popup.show()
        for _ in range(8): qt.processEvents()
        assert popup.content.width() <= popup.scroll.viewport().width()
        for text in popup.findChildren(QLabel):
            if text.wordWrap(): assert text.height() >= text.heightForWidth(text.width())
        popup.scroll.ensureWidgetVisible(popup.custom_button)
        for _ in range(4):qt.processEvents()
        pos = popup.custom_button.mapTo(popup.scroll.viewport(), QPoint(0, 0))
        assert pos.y() >= 0 and pos.y() + popup.custom_button.height() <= popup.scroll.viewport().height()
        assert popup.frameGeometry().height() <= popup.screen().availableGeometry().height()
        popup.expire()
        popup.deleteLater()


def test_models_can_be_created_offline_for_notifications_and_replies(qt, tmp_path):
    models = CardTemplates(tmp_path)
    editor = CardEditor('', models, manage=True)
    emitted = []
    editor.submitted.connect(emitted.append)
    editor.show()
    editor.title.setText('Café après la correction')
    editor.save_button.click()
    editor.types.setCurrentIndex(1)
    editor.title.setText('Je te rejoins')
    editor.choices.buttons['1f4aa'].click()
    editor.save_button.click()
    assert {item['kind'] for item in CardTemplates(tmp_path).items} == {'invite', 'reply'}
    editor.send_button.click()
    assert not emitted and not editor.isVisible()
    editor.deleteLater()


def test_real_editor_send_yes_and_custom_replies_and_saved_templates(qt, tmp_path, monkeypatch):
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path / 'config'))
    monkeypatch.setattr(NetworkConfig, 'load', classmethod(lambda cls, path: NetworkConfig(mode='relay', relay_host='')))
    controller = Application(ROOT, SimpleNamespace(dev='cards-' + uuid.uuid4().hex[:8], background=False, insecure_local_relay=None))
    events = []
    peer = ChatClient('Alice42', lambda kind, data: events.append((kind, data)), discovery=False, tcp_port=0)
    def frames(kind): return [data['frame'] for name, data in events if name == 'event' and data['frame']['type'] == kind]
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
        view.composer.card_button.click()
        editor = controller.card_editors[(peer.sid, '')]
        editor.title.setText('Mon petit message')
        editor.text.setPlainText('Ça te dit de bouger ?')
        editor.choices.buttons['1f923'].click()
        assert editor.animation.code == '1f923'
        editor.save_button.click()
        assert CardTemplates(controller.store.root).items[0]['title'] == 'Mon petit message'
        editor.preview()
        assert editor.preview_window.isVisible()
        editor.preview_window.close()
        editor.send_button.click()
        until(qt, lambda: bool(frames('card_invite')))
        request = frames('card_invite')[0]['id']
        custom = {'title': 'Dans deux minutes !', 'text': 'REPLY_REMOTE_SECRET', 'emoji': '1f4aa'}
        controller.worker.submit(peer.send('card_reply', client.sid, request=request, answer='custom', **custom)).result(4)
        until(qt, lambda: bool(controller.reply_popups))
        response = next(iter(controller.reply_popups.values()))
        assert response.illustration.code == '1f4aa'
        response.close()

        # Cancel an editor, then answer Yes: no stale editor callback survives.
        incoming = controller.worker.submit(peer.send('card_invite', client.sid, **{**CARD, 'text': 'REMOTE_CARD_SECRET'})).result(4)
        until(qt, lambda: incoming in controller.popups)
        popup = controller.popups[incoming][1]
        popup.custom_button.click()
        reply_editor = controller.card_editors[(peer.sid, incoming)]
        reply_editor.close()
        qt.processEvents()
        popup.yes_button.click()
        until(qt, lambda: any(frame['request'] == incoming for frame in frames('card_reply')))
        assert next(frame for frame in frames('card_reply') if frame['request'] == incoming)['answer'] == 'accepted'

        incoming = controller.worker.submit(peer.send('card_invite', client.sid, **CARD)).result(4)
        until(qt, lambda: incoming in controller.popups)
        popup = controller.popups[incoming][1]
        popup.custom_button.click()
        reply_editor = controller.card_editors[(peer.sid, incoming)]
        reply_editor.title.setText('Je suis chaud !')
        reply_editor.text.setPlainText('Je te rejoins devant le cluster.')
        reply_editor.choices.buttons['1f618'].click()
        reply_editor.save_button.click()
        reply_editor.send_button.click()
        until(qt, lambda: any(frame['request'] == incoming and frame['answer'] == 'custom' for frame in frames('card_reply')))
        reply = next(frame for frame in frames('card_reply') if frame['request'] == incoming)
        assert reply['emoji'] == '1f618' and reply['text'] == 'Je te rejoins devant le cluster.'
        assert incoming not in controller.popups

        controller.change_preference('dnd', True)
        incoming = controller.worker.submit(peer.send('break_invite', client.sid)).result(4)
        until(qt, lambda: incoming in controller.popups)
        popup = controller.popups[incoming][1]
        assert popup.custom_button.isVisible()
        popup.custom_button.click()
        reply_editor = controller.card_editors[(peer.sid, incoming)]
        reply_editor.text.setPlainText('Je finis mon exercice.')
        reply_editor.send_button.click()
        until(qt, lambda: any(frame['request'] == incoming for frame in frames('card_reply')))
        assert len([frame for frame in frames('card_reply') if frame['request'] == incoming]) == 1
        contents = controller.card_templates.path.read_text() + controller.store.path.read_text()
        assert 'REMOTE_CARD_SECRET' not in contents and 'REPLY_REMOTE_SECRET' not in contents
        assert len(CardTemplates(controller.store.root).items) == 2
    finally:
        controller.worker.submit(peer.stop()).result(4)
        controller.cleanup()
        controller.window.close()
        controller.deleteLater()
