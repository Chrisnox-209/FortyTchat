"""One-click updates use the official source and keep the installed app recoverable."""
import base64
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest
from nacl.signing import SigningKey

from src.app import Application
from src.features import releases
from src.features.updates import installed_app
from tools import update_user


def app_tree(root, version, script='pass\n', requirements='PySide6\n', *, key):
    (root / 'src').mkdir(parents=True)
    (root / 'assets').mkdir()
    (root / 'tools').mkdir()
    for filename in releases.SOURCE_FILES:
        (root / filename).touch()
    (root / 'src' / '__init__.py').write_text(f'__version__ = {version!r}\n')
    (root / 'requirements.txt').write_text(requirements)
    (root / 'main.py').write_text(script)
    digest = releases.source_digest(root)
    notes = 'Corrections de stabilité.'
    notes_digest = hashlib.sha256(notes.encode('utf-8')).digest()
    signature = key.sign(releases.signing_bytes(version, digest, notes_digest)).signature
    proof = base64.b64encode(digest + notes_digest + signature).decode('ascii')
    (root / releases.METADATA).write_text(json.dumps({'version': version, 'proof': proof, 'notes': notes}))


@pytest.fixture
def publisher(monkeypatch):
    key = SigningKey.generate()
    monkeypatch.setattr(releases, 'PUBLIC_KEY', bytes(key.verify_key).hex())
    return key


@pytest.fixture
def installation(monkeypatch, tmp_path, publisher):
    monkeypatch.setenv('XDG_DATA_HOME', str(tmp_path / 'data home'))
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path / 'profile'))
    app = installed_app()
    app_tree(app, '2.8.0', 'import time\ntime.sleep(30)\n', key=publisher)
    return app


def test_download_uses_fixed_https_source_and_no_prompt(monkeypatch, tmp_path):
    monkeypatch.setenv('GIT_CONFIG_COUNT', '17')
    calls = []
    monkeypatch.setattr(update_user.subprocess, 'run', lambda args, **opts: calls.append((args, opts)))
    destination = tmp_path / 'source with spaces'
    update_user.fetch_release(destination)
    args, options = calls[0]
    assert args[-2:] == ['https://github.com/Chrisnox-209/FortyTchat.git', str(destination)]
    assert '--branch=main' in args and 'protocol.https.allow=always' in args
    assert options['env']['GIT_TERMINAL_PROMPT'] == '0' and 'GIT_CONFIG_COUNT' not in options['env']


def test_new_copy_replaces_stopped_app_and_reopens_with_same_settings(installation, monkeypatch, tmp_path, publisher):
    app = installation
    restarted = tmp_path / 'reopened'
    (app / 'network.json').write_text('{"mode":"relay"}')
    profile = tmp_path / 'profile' / 'fortytchat' / 'config.json'
    profile.parent.mkdir(parents=True)
    profile.write_text('{"nickname":"Alice42","autostart":false}')
    process = subprocess.Popen([sys.executable, str(app / 'main.py')])

    def fetch(destination):
        assert process.poll() is None
        app_tree(destination, '2.8.1', f'from pathlib import Path\nPath({str(restarted)!r}).write_text("2.8.1")\n', key=publisher)

    monkeypatch.setattr(update_user, 'fetch_release', fetch)
    try:
        assert update_user.update(app, lambda _: None) is True
        assert process.wait(timeout=2) != 0
        assert restarted.read_text() == '2.8.1'
        assert (app / 'network.json').read_text() == '{"mode":"relay"}'
        assert profile.read_text() == '{"nickname":"Alice42","autostart":false}'
        assert releases.verified_tree(app.parent / 'recovery').version == '2.8.1'
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=2)


@pytest.mark.parametrize('case', ['offline', 'same-version', 'older', 'dependencies', 'tampered'])
def test_preparation_failure_or_same_version_does_not_stop_app(case, installation, monkeypatch, publisher):
    def fetch(destination):
        if case == 'offline':
            raise update_user.UpdateError('Téléchargement impossible')
        versions = {'same-version': '2.8.0', 'older': '2.7.1', 'dependencies': '2.8.1', 'tampered': '2.8.1'}
        app_tree(destination, versions[case], requirements='NewDependency\n', key=publisher)
        if case == 'tampered':
            (destination / 'main.py').write_text('modified\n')

    def unexpected(*_):
        pytest.fail('Preparing the update must leave the installed app running')

    monkeypatch.setattr(update_user, 'fetch_release', fetch)
    monkeypatch.setattr(update_user, 'stop_installed', unexpected)
    if case in ('same-version', 'older'):
        assert update_user.update(installation, lambda _: None) is False
    else:
        with pytest.raises(update_user.UpdateError, match='Téléchargement|dépendances|publication'):
            update_user.update(installation, lambda _: None)
    assert update_user.read_version(installation / 'src' / '__init__.py') == '2.8.0'


def test_failed_swap_restores_and_reopens_previous_copy(installation, monkeypatch, publisher):
    app = installation
    real_rename = Path.rename
    reopened = []

    def fail_swap(path, destination):
        if path.name.startswith('.app-'):
            raise OSError('simulated failure replacing the installed copy')
        return real_rename(path, destination)

    monkeypatch.setattr(Path, 'rename', fail_swap)
    monkeypatch.setattr(update_user, 'fetch_release', lambda path: app_tree(path, '2.8.1', key=publisher))
    monkeypatch.setattr(update_user, 'stop_installed', lambda _: None)
    monkeypatch.setattr(update_user, 'restart', lambda path: reopened.append(update_user.read_version(path / 'src' / '__init__.py')))
    with pytest.raises(update_user.UpdateError, match='version précédente'):
        update_user.update(app, lambda _: None)
    assert reopened == ['2.8.0']
    assert (app / 'main.py').is_file()


def test_restore_reverts_modified_version_offline_and_preserves_settings(installation, monkeypatch, publisher):
    app = installation
    app_tree(app.parent / 'recovery', '2.8.0', key=publisher)
    (app / 'src' / '__init__.py').write_text("__version__ = '9999.0.0'\n")
    (app / 'network.json').write_text('{"mode":"relay"}')
    monkeypatch.setattr(update_user, 'fetch_release', lambda _: pytest.fail('restauration hors ligne attendue'))
    monkeypatch.setattr(update_user, 'stop_installed', lambda _: None)
    monkeypatch.setattr(update_user, 'restart', lambda _: None)
    assert update_user.update(app, lambda _: None, restore=True)
    assert releases.verified_tree(app, '2.8.0') is not None
    assert (app / 'network.json').read_text() == '{"mode":"relay"}'


def test_update_button_runs_helper_once_without_a_peer_supplied_argument(monkeypatch, installation):
    from PySide6.QtWidgets import QApplication
    from src.config import Preferences
    from src.memory import Conversations
    from src.ui.main_window import MainWindow

    qt = QApplication.instance() or QApplication([])
    window = MainWindow(Preferences(nickname='User42'), Conversations(), 'assets/icons/fortytchat.svg')
    controller = SimpleNamespace(root=installation, update_process=None)
    (installation / 'tools' / 'update_user.py').touch()
    calls = []

    def popen(args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(poll=lambda: None)

    monkeypatch.setattr(subprocess, 'Popen', popen)
    window.start_update = lambda: Application.start_update(controller)
    window.show_update_banner('999.0.0')
    window.update_button.click()
    window.update_button.click()
    assert len(calls) == 1 and window.update_dialog is None
    assert calls[0][0] == [sys.executable, str(installation / 'tools' / 'update_user.py')]
    assert calls[0][1]['start_new_session']
    window.quitting_now = True
    window.close()
    window.deleteLater()
    qt.processEvents()


def test_updater_closes_its_progress_window_after_success():
    result = subprocess.run([sys.executable, '-c', '''
from tools import update_user

def update(app, status, *, restore=False):
    status('Installation…')
    return True

update_user.update = update
raise SystemExit(update_user.main())
'''], capture_output=True, text=True, timeout=8)
    assert result.returncode == 0, result.stderr
