"""Update instructions are local, copyable, and stop before dependent failures."""
import os
from pathlib import Path
import re
import subprocess
from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QApplication, QLabel
from src.features.updates import DEFAULT_REPOSITORY, update_command
from src.ui.update import UpdateDialog
from src.ui.theme import stylesheet


ROOT = Path(__file__).resolve().parents[1]


def test_default_command_matches_the_documented_guarded_update():
    readme = (ROOT / 'README.md').read_text()
    section = readme.split('## Mettre à jour\n', 1)[1]
    block = re.search(r'```sh\n(.*?)\n```', section, re.S)[1]
    assert update_command() == block
    assert 'https://' not in block


@pytest.mark.parametrize('path', ['', '.', 'relative/repo', '~someone/repo', '~', '/tmp/a\nb', '/tmp/\x00b', '~/a\tb', '/tmp/\x7fb', None])
def test_invalid_repository_never_produces_copyable_shell(path):
    with pytest.raises(ValueError):
        update_command(path)


def test_copy_is_plain_text_feedback_is_visible_and_path_change_resets_it(monkeypatch):
    qt = QApplication.instance() or QApplication([])
    copied = []
    monkeypatch.setattr(QApplication, 'clipboard', lambda: SimpleNamespace(setText=copied.append))
    dialog = UpdateDialog(); dialog.show(); qt.processEvents()
    assert dialog.repository.text() == DEFAULT_REPOSITORY
    assert dialog.code.isReadOnly()
    dialog.copy_button.click()
    assert copied == [update_command()]
    assert dialog.copy_button.text() == 'Copié !' and 'Ctrl+Maj+V' in dialog.feedback.text()
    dialog.repository.setText('/tmp/another repo')
    assert dialog.copy_button.text() == 'Copier'
    dialog.copy_button.click()
    assert copied[-1] == update_command('/tmp/another repo')
    dialog.repository.setText('relative')
    assert not dialog.copy_button.isEnabled() and not dialog.code.toPlainText()
    dialog.copy_button.click(); assert len(copied) == 2
    dialog.close(); dialog.deleteLater(); qt.processEvents()


@pytest.mark.parametrize('theme,font_size', [('dark',14),('light',14),('dark',20)])
def test_update_controls_stay_accessible_with_large_text(theme,font_size):
    qt = QApplication.instance() or QApplication([])
    dialog = UpdateDialog()
    dialog.setStyleSheet(stylesheet(theme) + f'\nQWidget {{font-size:{font_size}px;}}')
    dialog.resize(460, 420); dialog.show(); qt.processEvents()
    assert dialog.code.width() > 300 and dialog.code.height() > 70
    assert dialog.rect().contains(dialog.copy_button.mapTo(dialog, dialog.copy_button.rect().topLeft()))
    assert dialog.rect().contains(dialog.copy_button.mapTo(dialog, dialog.copy_button.rect().bottomRight()))
    assert dialog.copy_button.height() >= dialog.copy_button.fontMetrics().height()
    assert any('Ctrl+Alt+T' in item.text() for item in dialog.findChildren(QLabel))
    dialog.close(); dialog.deleteLater(); qt.processEvents()


@pytest.mark.parametrize('stop,pull,install,expected', [
    (0,0,0,['stop','git:pull --ff-only','install']),
    (1,0,0,['stop']),
    (0,1,0,['stop','git:pull --ff-only']),
    (0,0,1,['stop','git:pull --ff-only','install']),
])
def test_shell_stops_at_the_first_failed_step(tmp_path,stop,pull,install,expected):
    repo = tmp_path / "repo with spaces ' $(touch SHOULD_NOT_EXIST)"
    repo.mkdir()
    log = tmp_path / 'steps'
    fake_bin = tmp_path / 'bin'; fake_bin.mkdir()
    scripts = {
        'python3': 'printf "%s\\n" stop >> "$FORTYTCHAT_UPDATE_TEST_LOG"\nexit "$FORTYTCHAT_UPDATE_TEST_STOP"\n',
        'git': 'printf "git:%s\\n" "$*" >> "$FORTYTCHAT_UPDATE_TEST_LOG"\nexit "$FORTYTCHAT_UPDATE_TEST_PULL"\n',
    }
    for name, body in scripts.items():
        target = fake_bin / name; target.write_text('#!/bin/sh\n' + body); target.chmod(0o755)
    (repo / 'install.sh').write_text('printf "%s\\n" install >> "$FORTYTCHAT_UPDATE_TEST_LOG"\nexit "$FORTYTCHAT_UPDATE_TEST_INSTALL"\n')
    env = dict(os.environ, PATH=str(fake_bin) + os.pathsep + os.environ['PATH'],
               FORTYTCHAT_UPDATE_TEST_LOG=str(log), FORTYTCHAT_UPDATE_TEST_STOP=str(stop),
               FORTYTCHAT_UPDATE_TEST_PULL=str(pull), FORTYTCHAT_UPDATE_TEST_INSTALL=str(install))
    result = subprocess.run(['sh','-c',update_command(str(repo))],cwd=tmp_path,env=env,capture_output=True,text=True,timeout=5)
    assert log.read_text().splitlines() == expected
    assert (result.returncode == 0) == (stop == pull == install == 0)
    assert ('Mise à jour terminée.' in result.stdout) == (result.returncode == 0)
    assert not list(tmp_path.rglob('SHOULD_NOT_EXIST'))


def test_missing_repository_stops_before_running_any_tool(tmp_path):
    result = subprocess.run(['sh','-c',update_command(str(tmp_path/'missing'))],capture_output=True,text=True,timeout=5)
    assert result.returncode != 0 and 'Dépôt introuvable' in result.stderr


def test_tilde_path_keeps_home_variable_and_quotes_only_the_literal_suffix():
    path = "~/repo ' $(touch SHOULD_NOT_EXIST)"
    from shlex import quote
    assert 'cd "$HOME"/' + quote(path[2:]) + ' ||' in update_command(path)
