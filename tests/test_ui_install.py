import json
import os
from pathlib import Path
import subprocess
import shutil
import sys
import time
from types import SimpleNamespace
import uuid

import pytest

from PySide6.QtCore import Qt, QEvent
from PySide6.QtGui import QKeyEvent, QImage, QColor
from PySide6.QtWidgets import QApplication

from src.app import Application
from src import __version__
from src.config import Preferences
from src.features.emojis import EmojiPicker
from src.memory import Conversations, Message
from src.networking.client import ChatClient
from src.networking.settings import NetworkConfig
from src.ui.main_window import MainWindow
from src.ui.avatar_picker import AvatarPicker
from src.ui.avatar_image import encode_photo
from src.ui.settings import SettingsDialog
from src.ui.theme import stylesheet

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope='module')
def qt():
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    return app


def process_until(qt, condition, timeout=8):
    end = time.monotonic() + timeout
    while not condition():
        qt.processEvents()
        if time.monotonic() > end:
            raise AssertionError('Interaction Qt non terminée')
        time.sleep(0.01)
    qt.processEvents()


def test_window_plain_text_emoji_search_theme_and_minimize(qt):
    preferences = Preferences(nickname='CosmicOtter42')
    history = Conversations()
    window = MainWindow(preferences, history, str(ROOT / 'assets/icons/fortytchat.svg'))
    qt.setStyleSheet(stylesheet('dark'))
    window.show()
    qt.processEvents()
    avatar_requests = []
    window.avatar_requested.connect(lambda: avatar_requests.append(True))
    window.sidebar.avatar.clicked.emit()
    assert avatar_requests == [True]
    sent = []
    window.send_requested.connect(lambda target, text: sent.append((target, text)))
    window.input.setPlainText('<script>texte littéral 😀</script>')
    QApplication.sendEvent(window.input, QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Return, Qt.KeyboardModifier.NoModifier))
    assert sent == [('general', '<script>texte littéral 😀</script>')]
    window.send_button.setEnabled(True)
    window.input.clear()
    QApplication.sendEvent(window.input, QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Return, Qt.KeyboardModifier.ShiftModifier))
    assert window.input.toPlainText() == '\n'
    window.chat.append(Message('a', 'PixelFox42', '<b>Ce texte reste brut</b>'))
    labels = [x for x in window.chat.findChildren(__import__('PySide6.QtWidgets', fromlist=['QLabel']).QLabel) if x.objectName() == 'messageText']
    assert labels[-1].textFormat() == Qt.TextFormat.PlainText
    picker = EmojiPicker(window)
    picker.search.setText('café')
    picker.populate()
    assert any(entry[0] == '☕' for entry in picker.model.entries)
    chosen = []
    picker.chosen.connect(chosen.append)
    picker.choose('☕')
    assert chosen == ['☕']
    qt.setStyleSheet(stylesheet('light'))
    qt.processEvents()
    window.close()
    assert window.isMinimized()  # No tray host in this offscreen test.
    window.quitting_now = True
    window.close()
    picker.deleteLater()
    window.deleteLater()


def test_full_controller_real_tcp_unread_settings_and_no_history(qt, tmp_path, monkeypatch):
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path / 'config'))
    # Disable UDP in this test; real UDP discovery is tested separately.
    monkeypatch.setattr(NetworkConfig, 'load', classmethod(lambda cls, path: NetworkConfig(mode='relay', relay_host='')))
    profile = 'ui-' + uuid.uuid4().hex[:10]
    controller = Application(ROOT, SimpleNamespace(dev=profile, background=False, insecure_local_relay=None))
    peer_events = []
    peer = ChatClient('PixelFox42', lambda k, d: peer_events.append((k, d)), tcp_port=0, discovery=False)
    try:
        controller.worker.submit(peer.start()).result(4)
        client = controller.worker.client
        process_until(qt, lambda: client.server is not None and client.port > 0)
        def connect():
            peer.observe(client.hello(), '127.0.0.1')
            client.observe(peer.hello(), '127.0.0.1')
        controller.worker.loop.call_soon_threadsafe(connect)
        process_until(qt, lambda: peer.sid in controller.window.peers)
        controller.window.hide()
        controller.worker.submit(peer.send('message', client.sid, text='GUI_SECRET_TRANSIENT')).result(4)
        process_until(qt, lambda: controller.window.sidebar.unread.get(peer.sid) == 1)
        assert '1' in controller.tray.icon.toolTip()
        # Click the actual online row: a separate native window must open.
        row = next(controller.window.sidebar.list.item(i) for i in range(controller.window.sidebar.list.count())
                   if (controller.window.sidebar.list.item(i).data(Qt.ItemDataRole.UserRole) or {}).get('sid') == peer.sid)
        controller.window.sidebar.list.itemClicked.emit(row)
        assert peer.sid in controller.private_windows
        direct = controller.private_windows[peer.sid]
        assert direct is not controller.window and direct.isWindow()
        assert direct.current == peer.sid and controller.window.current == 'general'
        assert direct.isVisible()
        assert not controller.window.sidebar.unread
        assert '1 non lu' not in controller.tray.icon.toolTip()
        assert controller.history.get(peer.sid)[0].text == 'GUI_SECRET_TRANSIENT'
        controller.change_preference('avatar', 5)
        assert controller.window.sidebar.avatar.avatar == 5
        direct.input.setPlainText('Réponse depuis la nouvelle fenêtre')
        QApplication.sendEvent(direct.input, QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Return, Qt.KeyboardModifier.NoModifier))
        process_until(qt, lambda: any(k == 'event' and d['frame'].get('text') == 'Réponse depuis la nouvelle fenêtre' for k, d in peer_events))
        process_until(qt, lambda: not direct.input.toPlainText())
        assert peer.peers[client.sid].avatar == 5
        direct.close()
        assert peer.sid not in controller.private_windows
        controller.open_private(peer.sid)
        assert len(controller.history.get(peer.sid)) == 2
        # A photo chosen locally reaches the peer without being part of a chat message.
        own_image = QImage(200, 140, QImage.Format.Format_RGB32)
        own_image.fill(QColor('#7891ff'))
        own_photo = encode_photo(own_image)
        controller.change_preference('avatar_image', own_photo)
        assert controller.window.sidebar.avatar.image_data == own_photo
        def announce_own():
            peer.observe(client.hello(), '127.0.0.1')
        controller.worker.loop.call_soon_threadsafe(announce_own)
        process_until(qt, lambda: peer.peers[client.sid].avatar_image == own_photo)
        remote_image = QImage(180, 180, QImage.Format.Format_RGB32)
        remote_image.fill(QColor('#72b4a0'))
        remote_photo = encode_photo(remote_image)
        def announce_remote():
            peer.preferences(wizz_enabled=True, dnd=False, avatar_image=remote_photo)
            client.observe(peer.hello(), '127.0.0.1')
        controller.worker.loop.call_soon_threadsafe(announce_remote)
        process_until(qt, lambda: controller.private_windows[peer.sid].avatar.image_data == remote_photo)
        assert controller.history.get(peer.sid)[0].avatar_image == remote_photo
        controller.show_settings(0)
        assert controller.settings.avatar_preview.image_data == own_photo
        controller.rename('NeonPanda42')
        process_until(qt, lambda: controller.preferences.nickname == 'NeonPanda42')
        controller.change_preference('dnd', True)
        controller.change_preference('theme', 'light')
        assert controller.preferences.dnd
        controller.worker.submit(peer.stop()).result(4)
        controller.close_settings(0)
        prefs = controller.store.path.read_text()
        assert 'GUI_SECRET_TRANSIENT' not in prefs
        assert json.loads(prefs)['nickname'] == 'NeonPanda42'
        assert json.loads(prefs)['avatar'] == 5
        assert json.loads(prefs)['avatar_image'] == own_photo
        assert remote_photo not in prefs
        assert not (tmp_path / 'config/autostart').exists()
    finally:
        if not peer._closing:
            controller.worker.submit(peer.stop()).result(4)
        controller.cleanup()
        controller.window.close()
        controller.deleteLater()
    assert not controller.history.chats


def test_reinstall_preserves_profile_in_isolated_xdg_paths(qt, tmp_path):
    data = tmp_path / 'data space $ literal'
    config = tmp_path / 'config'
    bindir = tmp_path / 'bin'
    env = {**os.environ, 'XDG_DATA_HOME': str(data), 'XDG_CONFIG_HOME': str(config)}
    command = ['sh', str(ROOT / 'install.sh'), '--use-current-python', '--no-launch', '--bin-dir', str(bindir)]
    result = subprocess.run(command, env=env, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    target = data / 'fortytchat/app/main.py'
    assert target.is_file()
    assert (target.parent / 'uninstall.sh').is_file()
    assert (target.parent / 'diagnostic-session.sh').read_text() == (ROOT / 'diagnostic-session.sh').read_text()
    assert (target.parent / 'tools/diagnose_session.py').is_file()
    assert not (target.parent / 'update.sh').exists()
    network_path = target.parent / 'network.json'
    deployment = json.loads(network_path.read_text())
    assert deployment['mode'] == 'auto'
    deployment['udp_port'] = 39801
    network_path.write_text(json.dumps(deployment))
    menu_entry = data / 'applications/fr.fortytchat.FortyTchat.desktop'
    assert menu_entry.is_file()
    if shutil.which('desktop-file-validate'):
        validated = subprocess.run(['desktop-file-validate', str(menu_entry)], capture_output=True, text=True)
        assert validated.returncode == 0, validated.stdout + validated.stderr
    launcher = subprocess.run([str(bindir / 'fortytchat'), '--version'], capture_output=True, text=True, env=env, timeout=5)
    assert launcher.returncode == 0 and f'FortyTchat {__version__}' in launcher.stdout
    prefs_path = config / 'fortytchat/config.json'
    prefs = json.loads(prefs_path.read_text())
    assert prefs['autostart'] is True
    entry = config / 'autostart/fr.fortytchat.FortyTchat.desktop'
    entry.write_text(entry.read_text().replace('Hidden=false', 'Hidden=true').replace('enabled=true', 'enabled=false'))
    prefs['autostart'] = False
    prefs['nickname'] = 'MyStudent42'
    prefs['theme'] = 'light'
    prefs['avatar'] = 7
    own_image = QImage(128, 128, QImage.Format.Format_RGB32)
    own_image.fill(QColor('#72b4a0'))
    prefs['avatar_image'] = encode_photo(own_image)
    prefs_path.write_text(json.dumps(prefs))
    target.write_text('# ancienne version à remplacer\n')
    again = subprocess.run(command, env=env, capture_output=True, text=True, timeout=30)
    assert again.returncode == 0, again.stderr
    restored = json.loads(prefs_path.read_text())
    for key in ('nickname', 'theme', 'avatar', 'avatar_image', 'autostart'):
        assert restored[key] == prefs[key]
    assert target.read_text() == (ROOT / 'main.py').read_text()
    assert json.loads(network_path.read_text())['udp_port'] == 39801
    assert (target.parent / 'assets/icons/fortytchat.svg').read_text() == (ROOT / 'assets/icons/fortytchat.svg').read_text()
    assert 'Hidden=true' in entry.read_text()
    assert not list(data.rglob('.git'))


def test_avatar_picker_selection(qt):
    from PySide6.QtWidgets import QToolButton
    picker = AvatarPicker('CosmicOtter42', 2)
    selected = []
    picker.selected.connect(selected.append)
    tiles = picker.findChildren(QToolButton)
    assert len(tiles) == 25
    tiles[6].click()
    assert selected == [5] and picker.result() == 1
    picker.deleteLater()
