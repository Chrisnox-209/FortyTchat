"""Reproduce the reported clipped settings fields at different screen scales."""
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('scale', ['1', '1.5', '2'])
def test_settings_text_remains_readable_when_window_is_small(scale):
    # Qt reads the screen scale before QApplication starts, hence an isolated process.
    source = r'''
from PySide6.QtCore import Qt, QPoint
from PySide6.QtWidgets import QApplication, QLabel, QPushButton, QCheckBox, QScrollArea
from src.config import Preferences
from src.ui.settings import SettingsDialog
from src.ui.theme import apply_theme
app = QApplication([])
for theme in ('dark', 'light'):
    apply_theme(app, theme)
    dialog = SettingsDialog(Preferences(nickname='CosmicAxolotl6414'), True, {})
    dialog.show()
    for width, height in ((570, 590), (460, 420)):
        dialog.resize(width, height)
        for tab in range(dialog.tabs.count()):
            dialog.tabs.setCurrentIndex(tab)
            for _ in range(4):
                app.processEvents()
            page = dialog.tabs.currentWidget()
            assert isinstance(page, QScrollArea)
            content = page.widget()
            assert content.width() <= page.viewport().width(), ('horizontal clipping', theme, tab)
            for label in content.findChildren(QLabel):
                if label.text() and label.wordWrap():
                    assert label.hasHeightForWidth(), ('wrapped text must participate in layout', label.text())
                    needed = label.heightForWidth(label.width())
                    assert label.height() >= needed, (theme, tab, label.text(), label.height(), needed)
            for control in content.findChildren(QPushButton) + content.findChildren(QCheckBox):
                assert control.height() >= control.sizeHint().height(), ('clipped control', control.text())
            if tab == 0:
                for field in (dialog.nickname, dialog.theme):
                    assert field.height() >= field.sizeHint().height(), ('clipped field', field.size(), field.sizeHint())
                page.ensureWidgetVisible(dialog.theme)
                for _ in range(3):
                    app.processEvents()
                position = dialog.theme.mapTo(page.viewport(), QPoint(0, 0))
                assert position.y() >= 0
                assert position.y() + dialog.theme.height() <= page.viewport().height()
                assert page.verticalScrollBar().maximum() > 0
            if tab == dialog.tabs.count() - 1:
                paragraphs = [x for x in content.findChildren(QLabel) if x.objectName() == 'muted']
                last = paragraphs[-1]
                page.ensureWidgetVisible(last)
                for _ in range(3):
                    app.processEvents()
                position = last.mapTo(page.viewport(), QPoint(0, 0))
                assert position.y() >= 0
                assert position.y() + last.height() <= page.viewport().height()
            assert dialog.rect().contains(dialog.layout().itemAt(dialog.layout().count()-1).widget().geometry())
            required = content.layout().heightForWidth(content.width())
            assert content.height() <= max(page.viewport().height(), required) + 1, 'Unnecessary blank scroll area'
    dialog.close()
print('Layout verified')
'''
    env = {**os.environ, 'QT_QPA_PLATFORM': 'offscreen', 'QT_SCALE_FACTOR': scale,
           'QT_SCREEN_SCALE_FACTORS': '', 'QT_FONT_DPI': '96'}
    result = subprocess.run([sys.executable, '-c', source], cwd=ROOT, env=env,
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'Layout verified' in result.stdout
