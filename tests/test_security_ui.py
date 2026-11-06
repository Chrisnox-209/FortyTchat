from PySide6.QtWidgets import QApplication
from src.networking.client import ChatClient
from src.security import fingerprint
from src.ui.security import SecurityDialog
from src.ui.private_window import PrivateWindow
from src.ui.theme import apply_theme


def test_fingerprint_comparison_and_changed_key_disable_composer():
    qt = QApplication.instance() or QApplication([])
    apply_theme(qt, 'dark')
    own = ChatClient('Alice42', discovery=False)
    other = ChatClient('Bob42', discovery=False)
    own.port = other.port = 39742
    own.observe(other.hello(), '127.0.0.1')
    peer = own.peers[other.sid].public()
    dialog = SecurityDialog(own.identity.public, peer)
    values = []
    dialog.verify_requested.connect(lambda *value: values.append(value))
    view = PrivateWindow(other.sid, peer['nick'], [], '')
    try:
        dialog.show(); qt.processEvents()
        dialog.expected.setText('0000'); dialog.compare()
        assert not values and 'correspondent pas' in dialog.feedback.text()
        dialog.expected.setText(fingerprint(other.identity.public).lower())
        dialog.compare()
        assert values == [(other.sid, ''.join(fingerprint(other.identity.public).split()))]
        dialog.verified()
        assert not dialog.verify.isEnabled()
        view.set_peer(peer)
        assert view.input.isEnabled()
        view.set_peer({**peer, 'security': 'changed', 'encrypted': False})
        assert not view.input.isEnabled() and not view.send_button.isEnabled()
        assert view.security_button.isEnabled()
    finally:
        dialog.close(); view.close(); qt.processEvents()


import os
import subprocess
import sys
from pathlib import Path
import pytest


@pytest.mark.parametrize('scale', ['1', '1.5', '2'])
def test_security_dialog_is_readable_on_small_scaled_screens(scale):
    code = '''
from PySide6.QtWidgets import QApplication, QLabel, QScrollArea
from src.networking.client import ChatClient
from src.ui.security import SecurityDialog
from src.ui.theme import apply_theme
qt = QApplication([])
a = ChatClient('Alice42', discovery=False)
b = ChatClient('Bob42', discovery=False)
a.port = b.port = 39742
a.observe(b.hello(), '127.0.0.1')
for theme in ('dark', 'light'):
    apply_theme(qt, theme)
    dialog = SecurityDialog(a.identity.public, a.peers[b.sid].public())
    dialog.resize(440, 420)
    dialog.show()
    qt.processEvents()
    area = dialog.findChild(QScrollArea)
    assert area.horizontalScrollBar().maximum() == 0
    for text in area.widget().findChildren(QLabel):
        if text.wordWrap() and text.text():
            assert text.height() >= text.heightForWidth(text.width()), text.text()
    area.ensureWidgetVisible(dialog.verify)
    qt.processEvents()
    assert dialog.verify.height() >= dialog.verify.sizeHint().height()
    assert dialog.expected.height() >= dialog.expected.sizeHint().height()
    dialog.close()
    qt.processEvents()
'''
    result = subprocess.run([sys.executable, '-c', code],
                            cwd=Path(__file__).resolve().parents[1],
                            env={**os.environ, 'QT_QPA_PLATFORM': 'offscreen', 'QT_SCALE_FACTOR': scale},
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
