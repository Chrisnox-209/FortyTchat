"""Unsigned versions cannot masquerade as a new release on the local network."""
import asyncio
import base64
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest
from nacl.signing import SigningKey
from PySide6.QtWidgets import QApplication

from src import __version__
from src.features import releases
from src.networking.client import ChatClient
from src.networking.protocol import event
from src.networking.relay import RelayServer
from src.ui.recovery import RecoveryDialog

ROOT = Path(__file__).resolve().parents[1]


def copy_release(target):
    target.mkdir(parents=True)
    for directory in releases.SOURCE_TREES:
        shutil.copytree(ROOT / directory, target / directory, ignore=shutil.ignore_patterns('__pycache__'))
    for filename in releases.SOURCE_FILES:
        shutil.copy2(ROOT / filename, target / filename)


def test_publication_proof_rejects_edited_version_or_self_signed_key():
    proof = releases.verified_tree(ROOT, __version__)
    assert proof is not None
    wire_proof = releases.advertised_release(__version__, proof.features())
    assert wire_proof.token == proof.token and wire_proof.matches_notes(proof.notes)
    assert not wire_proof.matches_notes(proof.notes + '\n Faux ajout')
    assert releases.advertised_release('9999.0.0', proof.features()) is None
    assert releases.advertised_release(__version__, proof.features()[:-1]) is None
    assert releases.advertised_release(__version__, [releases.MARKER, *proof.features()]) is None
    key = SigningKey.generate()
    signature = key.sign(releases.signing_bytes('9999.0.0', proof.digest, proof.notes_digest)).signature
    invented = base64.b64encode(proof.digest + proof.notes_digest + signature).decode()
    assert releases.verify_release('9999.0.0', invented) is None


@pytest.mark.parametrize('route', ['direct', 'relay'])
@pytest.mark.parametrize('forgery', ['unsigned', 'copied-proof', 'changed-notes'])
def test_false_update_is_quarantined_on_actual_encrypted_connection(route, forgery, tmp_path):
    async def until(condition):
        for _ in range(500):
            if condition():
                return
            await asyncio.sleep(.01)
        raise AssertionError('Échange chiffré incomplet')

    async def run():
        relay = RelayServer() if route == 'relay' else None
        options = {'relay_host': '127.0.0.1', 'relay_port': await relay.start(port=0), 'tls': False} if relay else {}
        seen = []
        a = ChatClient('Moi42', lambda kind, data: seen.append((kind, data)), discovery=False,
                       tcp_port=0, security_root=tmp_path / 'a', **options)
        b = ChatClient('Pair42', discovery=False, tcp_port=0, security_root=tmp_path / 'b', **options)
        try:
            await asyncio.gather(a.start(), b.start())
            if not relay:
                a.observe(b.hello(), '127.0.0.1')
                b.observe(a.hello(), '127.0.0.1')
            await until(lambda: any(kind == 'peer_version' and data['verified'] and data['notes'] == b.release.notes
                                    for kind, data in seen))
            assert await b.send('message', a.sid, text='Avant le blocage')
            unsigned_legacy = event('client_info', b.sid, a.sid, version='2.8.0', features=['version_notice'])
            assert await b._send_to(b.peers[a.sid], unsigned_legacy)
            assert any(kind == 'peer_version' and data['version'] == '2.8.0' and data['verified'] is False
                       for kind, data in seen)
            features = ['version_notice'] if forgery == 'unsigned' else b.release.features()
            version = __version__ if forgery == 'changed-notes' else '9999.0.0'
            extras = {'release_notes': 'Fausse nouveauté'} if forgery == 'changed-notes' else {}
            false_info = event('client_info', b.sid, a.sid, version=version, features=features, **extras)
            assert not await b._send_to(b.peers[a.sid], false_info)
            assert a.peers[b.sid].public()['release_quarantined'] and b.sid in a.blocked
            a.block(b.sid, False)
            assert b.sid in a.blocked
            rejected = await b.send('message', a.sid, text='Après le blocage')
            assert not any(kind == 'event' and data['frame']['id'] == rejected for kind, data in seen)
            assert not any(kind == 'peer_version' and data['version'] == '9999.0.0' for kind, data in seen)
            assert not any(kind == 'peer_version' and data['notes'] == 'Fausse nouveauté' for kind, data in seen)
        finally:
            await asyncio.gather(a.stop(), b.stop())
            if relay:
                await relay.stop()

    asyncio.run(run())


def test_local_edit_requires_recovery_and_uses_signed_local_copy(tmp_path, monkeypatch):
    qt = QApplication.instance() or QApplication([])
    monkeypatch.setenv('XDG_DATA_HOME', str(tmp_path))
    app = tmp_path / 'fortytchat' / 'app'
    recovery = app.parent / 'recovery'
    for target in (app, recovery):
        copy_release(target)
    (app / 'src' / '__init__.py').write_text("__version__ = '9999.0.0'\n")
    assert releases.verified_tree(app) is None
    assert releases.verified_tree(recovery) is not None
    launches = []
    monkeypatch.setattr('src.ui.recovery.subprocess.Popen', lambda args, **_: launches.append(args))
    dialog = RecoveryDialog(app)
    dialog.show()
    dialog.reject()
    assert dialog.isVisible()
    dialog.restore_button.click()
    assert launches[0][1:] == [str(recovery / 'tools' / 'update_user.py'), '--restore']
    dialog.deleteLater()
    qt.processEvents()


def test_first_boot_after_old_updater_creates_backup_and_edited_app_cannot_start(tmp_path):
    app = tmp_path / 'fortytchat' / 'app'
    copy_release(app)
    script = '''
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication
from src import app
from src.ui import recovery
import main

def start(*_):
    print('APP_STARTED', flush=True)
    QTimer.singleShot(0, QApplication.instance().quit)

def request_recovery(*_):
    print('RECOVERY_REQUIRED', flush=True)
    return 0

app.Application = start
recovery.recovery_dialog = request_recovery
raise SystemExit(main.main())
'''
    env = {**os.environ, 'XDG_DATA_HOME': str(tmp_path), 'XDG_CONFIG_HOME': str(tmp_path / 'config')}

    def run():
        result = subprocess.run([sys.executable, '-c', script], cwd=app, env=env,
                                capture_output=True, text=True, timeout=8)
        assert result.returncode == 0, result.stderr
        return result.stdout.strip()

    assert run() == 'APP_STARTED'
    assert releases.verified_tree(app.parent / 'recovery', __version__) is not None
    (app / 'src' / '__init__.py').write_text("__version__ = '9999.0.0'\n")
    assert run() == 'RECOVERY_REQUIRED'
