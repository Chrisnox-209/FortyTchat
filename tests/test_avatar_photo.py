"""User photo import, bounded transport, rendering and preference persistence."""
import asyncio
import base64
import json
import socket
import threading
import time

import pytest
from PySide6.QtCore import Qt, QTimer, QSize, QRectF
from PySide6.QtGui import QImage, QColor, QPainter
from PySide6.QtWidgets import QApplication, QFileDialog, QDialog

from src.avatar_data import avatar_bytes, avatar_hash, avatar_frame, AvatarError, MAX_AVATAR_BYTES
from src.config import ConfigStore
from src.networking.client import ChatClient
from src.networking.protocol import encode, decode, ProtocolError, MAX_FRAME
from src.networking.relay import RelayServer
from src.ui.avatar_image import read_photo, encode_photo, decoded_photo
from src.ui.avatar_picker import AvatarPicker
from src.ui.photo_crop import PhotoCropDialog
from src.ui.widgets import Avatar
from src.ui.theme import apply_theme


@pytest.fixture(scope='module')
def qt():
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    return app


@pytest.fixture
def photo(qt):
    image = QImage(500, 300, QImage.Format.Format_RGB32)
    image.fill(QColor('#8eb0d4'))
    painter = QPainter(image)
    painter.fillRect(0, 0, 200, 300, QColor('#d19b77'))
    painter.fillRect(200, 150, 300, 150, QColor('#69a89c'))
    painter.end()
    image.setText('PrivateMetadata', 'DO_NOT_SHARE_ORIGINAL_METADATA')
    return image


async def until(predicate, seconds=6):
    end = time.monotonic() + seconds
    while not predicate():
        if time.monotonic() >= end:
            raise AssertionError('La miniature attendue n’est pas arrivée')
        await asyncio.sleep(0.02)


def test_import_crop_encode_persist_and_render(qt, photo, tmp_path):
    path = tmp_path / 'my-photo.png'
    assert photo.save(str(path))
    imported = read_photo(path)
    assert imported.size() == QSize(500, 300)
    data = encode_photo(imported)
    assert len(avatar_bytes(data)) <= MAX_AVATAR_BYTES
    assert 'DO_NOT_SHARE_ORIGINAL_METADATA' not in avatar_bytes(data).decode('latin1')
    decoded = decoded_photo(data)
    assert decoded.size() == QSize(128, 128) and not decoded.isNull()
    assert not decoded.textKeys()
    store = ConfigStore(tmp_path / 'prefs')
    store.preferences.avatar_image = data
    store.preferences.autostart = False
    store.save()
    again = ConfigStore(tmp_path / 'prefs')
    assert again.preferences.avatar_image == data and not again.preferences.autostart
    # Only the thumbnail, never the original image path, is persisted.
    assert str(path) not in store.path.read_text()
    avatar = Avatar('Student42', 64, image_data=data)
    avatar.show()
    qt.processEvents()
    rendered = avatar.grab().toImage()
    expected = decoded.pixelColor(64, 64)
    actual = rendered.pixelColor(32, 32)
    assert abs(expected.red() - actual.red()) < 8
    avatar.close()
    avatar.deleteLater()


def test_crop_zoom_and_drag_changes_selected_pixels(photo):
    dialog = PhotoCropDialog(photo)
    before = dialog.crop.crop_rect()
    dialog.zoom.setValue(200)
    assert dialog.crop.crop_rect().width() == before.width() / 2
    dialog.crop.center_x, dialog.crop.center_y = 80, 80
    rect = dialog.crop.crop_rect()
    assert rect.left() >= 0 and rect.top() >= 0
    assert encode_photo(photo, rect) != encode_photo(photo)
    dialog.confirm()
    assert dialog.result() == QDialog.DialogCode.Accepted
    assert decoded_photo(dialog.encoded_image).size() == QSize(128, 128)
    dialog.deleteLater()


def test_real_import_button_preview_confirmation_and_cancel(qt, photo, tmp_path, monkeypatch):
    path = tmp_path / 'avatar.png'
    photo.save(str(path))
    monkeypatch.setattr(QFileDialog, 'getOpenFileName', lambda *a, **kw: (str(path), 'Images'))
    picker = AvatarPicker('PixelFox42')
    chosen = []
    picker.photo_selected.connect(chosen.append)
    def confirm():
        dialog = qt.activeModalWidget()
        if isinstance(dialog, PhotoCropDialog):
            dialog.zoom.setValue(130)
            dialog.use_button.click()
    timer = QTimer()
    timer.timeout.connect(confirm)
    timer.start(5)
    picker.import_button.click()
    deadline = time.monotonic() + 4
    while not chosen and time.monotonic() < deadline:
        qt.processEvents()
        time.sleep(0.005)
    timer.stop()
    assert len(chosen) == 1 and not decoded_photo(chosen[0]).isNull()
    assert picker.result() == QDialog.DialogCode.Accepted
    picker.deleteLater()
    canceled = AvatarPicker('PixelFox42')
    canceled.photo_selected.connect(chosen.append)
    rejected = []
    def reject_preview():
        dialog = qt.activeModalWidget()
        if isinstance(dialog, PhotoCropDialog):
            rejected.append(True)
            dialog.reject()
    timer.timeout.disconnect()
    timer.timeout.connect(reject_preview)
    timer.start(5)
    canceled.import_button.click()
    deadline = time.monotonic() + 4
    while not rejected and time.monotonic() < deadline:
        qt.processEvents()
        time.sleep(0.005)
    timer.stop()
    assert rejected
    assert len(chosen) == 1  # Cancel never applies an image.
    canceled.deleteLater()


def test_invalid_source_and_received_dimensions(qt, photo, tmp_path):
    path = tmp_path / 'not-really-a-photo.png'
    path.write_text('<svg xmlns="http://www.w3.org/2000/svg"/>')
    with pytest.raises(AvatarError):
        read_photo(path)
    path.write_bytes(b'not an image')
    with pytest.raises(AvatarError):
        read_photo(path)
    with pytest.raises(AvatarError):
        read_photo(tmp_path / 'missing.jpg')
    # A small JPEG with huge declared dimensions must be refused before pixel allocation.
    raw = bytearray(avatar_bytes(encode_photo(photo)))
    position = next(i for i in range(len(raw) - 10) if raw[i:i+2] in (b'\xff\xc0', b'\xff\xc2'))
    raw[position+5:position+9] = b'\x7f\xff\x7f\xff'
    malicious = base64.b64encode(raw).decode('ascii')
    assert decoded_photo(malicious).isNull()
    # Invalid preference content falls back without resetting other choices.
    prefs = tmp_path / 'prefs'
    prefs.mkdir()
    (prefs / 'config.json').write_text(json.dumps({'nickname': 'Saved42', 'autostart': False, 'avatar_image': 'not base64'}))
    store = ConfigStore(prefs)
    assert store.preferences.avatar_image == '' and store.preferences.nickname == 'Saved42'
    assert not store.preferences.autostart


def test_thumbnail_protocol_bounds_and_hash(qt, photo):
    data = encode_photo(photo)
    frame = avatar_frame('a' * 32, data)
    assert decode(encode(frame)) == frame and len(encode(frame)) < MAX_FRAME
    client = ChatClient('Student42', avatar_image=data)
    client.port = 39742
    hello = client.hello()
    assert hello['avatar_hash'] == avatar_hash(data) and len(encode(hello)) < 1024
    assert data not in encode(hello).decode()
    for bad in [dict(frame, hash='0' * 64), dict(frame, data='!'), dict(frame, data='A' * 12000), dict(frame, text='unexpected')]:
        with pytest.raises(ProtocolError):
            encode(bad)
    with pytest.raises(ProtocolError):
        encode({**hello, 'avatar_image': data})


def test_direct_photo_fetch_replace_and_remove(qt, photo):
    async def scenario():
        data = encode_photo(photo)
        alternate = QImage(128, 128, QImage.Format.Format_RGB32)
        alternate.fill(QColor('#ab5f76'))
        replacement = encode_photo(alternate)
        a = ChatClient('Alice42', tcp_port=0, discovery=False, avatar_image=data)
        b = ChatClient('Bob42', tcp_port=0, discovery=False)
        try:
            await a.start()
            await b.start()
            a.observe(b.hello(), '127.0.0.1')
            b.observe(a.hello(), '127.0.0.1')
            await until(lambda: b.peers[a.sid].avatar_image == data)
            a.preferences(wizz_enabled=True, dnd=False, avatar_image=replacement)
            b.observe(a.hello(), '127.0.0.1')
            await until(lambda: b.peers[a.sid].avatar_image == replacement)
            # An outdated transfer cannot overwrite the new advertised image.
            b._receive_avatar(avatar_frame(a.sid, data))
            assert b.peers[a.sid].avatar_image == replacement
            b.block(a.sid)
            assert not b.peers[a.sid].avatar_image
            b._receive_avatar(avatar_frame(a.sid, replacement))
            assert not b.peers[a.sid].avatar_image
            a.preferences(wizz_enabled=True, dnd=False, avatar=3, avatar_image='')
            b.observe(a.hello(), '127.0.0.1')
            assert b.peers[a.sid].avatar == 3 and not b.peers[a.sid].avatar_image
        finally:
            await a.stop()
            await b.stop()
    asyncio.run(scenario())


def test_relay_photo_existing_new_and_reconnecting_clients(qt, photo, tmp_path):
    async def scenario():
        data = encode_photo(photo)
        relay = RelayServer()
        port = await relay.start(port=0)
        options = dict(tcp_port=0, discovery=False, relay_host='127.0.0.1', relay_port=port, tls=False)
        a = ChatClient('Alice42', avatar_image=data, **options)
        b = ChatClient('Bob42', **options)
        c = None
        try:
            await a.start()
            await b.start()
            await until(lambda: a.sid in b.peers and b.peers[a.sid].avatar_image == data)
            c = ChatClient('Charlie42', **options)
            await c.start()
            await until(lambda: a.sid in c.peers and c.peers[a.sid].avatar_image == data)
            assert relay.clients[a.sid].avatar_image['data'] == data
            old = b.relay_writer
            old.close()
            await until(lambda: b.relay_writer is not None and b.relay_writer is not old, 8)
            await until(lambda: a.sid in b.peers and b.peers[a.sid].avatar_image == data)
            # A registered client cannot replace another session's image.
            b.relay_writer.write(encode(avatar_frame(a.sid, data)))
            await until(lambda: b.sid not in relay.clients)
            assert relay.clients[a.sid].avatar_image['data'] == data
            a.preferences(wizz_enabled=True, dnd=False, avatar=2, avatar_image='')
            await until(lambda: not c.peers[a.sid].avatar_image and not relay.clients[a.sid].avatar_image)
            assert not list(tmp_path.iterdir())
        finally:
            await a.stop()
            await b.stop()
            if c:
                await c.stop()
            await relay.stop()
        assert not relay.clients
    asyncio.run(scenario())


def test_real_udp_announces_only_hash_then_fetches_photo_over_tcp(qt, photo):
    async def scenario():
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as temporary:
            temporary.bind(('', 0))
            port = temporary.getsockname()[1]
        data = encode_photo(photo)
        a = ChatClient('PhotoAlice42', tcp_port=0, discovery_port=port, avatar_image=data)
        b = ChatClient('PhotoBob42', tcp_port=0, discovery_port=port)
        try:
            await a.start()
            await b.start()
            await until(lambda: a.sid in b.peers and b.peers[a.sid].avatar_image == data)
            assert len(encode(a.hello())) < 1024 and data not in encode(a.hello()).decode()
            assert b.peers[a.sid].direct and not b.peers[a.sid].relay
            a.preferences(wizz_enabled=True, dnd=False, avatar_image='', avatar=4)
            await until(lambda: not b.peers[a.sid].avatar_image and b.peers[a.sid].avatar == 4)
        finally:
            await a.stop()
            await b.stop()
    asyncio.run(scenario())


@pytest.mark.parametrize('suffix', ['png', 'jpg', 'webp'])
def test_each_advertised_source_format(qt, photo, tmp_path, suffix):
    path = tmp_path / ('picture.' + suffix)
    assert photo.save(str(path))
    result = encode_photo(read_photo(path))
    assert decoded_photo(result).size() == QSize(128, 128)


def test_closing_picker_while_photo_is_loading(qt, photo, tmp_path, monkeypatch):
    path = tmp_path / 'picture.png'
    photo.save(str(path))
    monkeypatch.setattr(QFileDialog, 'getOpenFileName', lambda *a, **kw: (str(path), 'Images'))
    started, release, finished = threading.Event(), threading.Event(), threading.Event()
    def delayed_read(_path):
        started.set()
        release.wait(2)
        finished.set()
        return photo
    monkeypatch.setattr('src.ui.avatar_image.read_photo', delayed_read)
    picker = AvatarPicker('PixelFox42')
    chosen = []
    picker.photo_selected.connect(chosen.append)
    picker.import_photo()
    try:
        assert started.wait(1)
        # The import returns immediately, and the rest of the UI remains responsive.
        assert not picker.import_button.isEnabled()
        picker.reject()
    finally:
        release.set()
    assert finished.wait(1)
    for _ in range(5):
        qt.processEvents()
        time.sleep(0.01)
    assert not chosen and not isinstance(qt.activeModalWidget(), PhotoCropDialog)
    picker.deleteLater()


def test_encrypted_relay_without_photo_capability_still_carries_chat(qt, photo):
    class OldRelay(RelayServer):
        def put(self, connection, data):
            if data['type'] == 'pong':
                data = {**data, 'caps': ['e2ee']}
            return super().put(connection, data)
    async def scenario():
        relay = OldRelay()
        port = await relay.start(port=0)
        options = dict(tcp_port=0, discovery=False, relay_host='127.0.0.1', relay_port=port, tls=False)
        events = []
        a = ChatClient('PhotoAlice42', avatar_image=encode_photo(photo), **options)
        b = ChatClient('PhotoBob42', lambda kind, data: events.append((kind, data)), **options)
        try:
            await a.start()
            await b.start()
            await until(lambda: a.sid in b.peers and b.sid in a.peers)
            assert not a.relay_images and not relay.clients[a.sid].avatar_image
            await a.send('message', b.sid, text='Message compatible')
            await until(lambda: any(kind == 'event' for kind, _ in events))
            assert not b.peers[a.sid].avatar_image
        finally:
            await a.stop()
            await b.stop()
            await relay.stop()
    asyncio.run(scenario())
