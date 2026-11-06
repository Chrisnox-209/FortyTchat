"""Notification identity and long Wizz effects, including Wayland fallback."""
from pathlib import Path

import pytest
from PySide6.QtCore import QAbstractAnimation
from PySide6.QtWidgets import QApplication, QMainWindow, QWidget, QSystemTrayIcon, QPushButton, QPlainTextEdit

from src.config import Preferences
from src.memory import Conversations
from src.ui.attention import shake_window, WIZZ_DURATION
from src.ui.main_window import MainWindow
from src.ui.settings import SettingsDialog
from src.ui.tray import Tray

ROOT = Path(__file__).resolve().parents[1]
ICON = str(ROOT / 'assets/icons/fortytchat.svg')


@pytest.fixture(scope='module')
def qt():
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    return app


def icon_background(tray):
    image = tray.icon.icon().pixmap(64, 64).toImage()
    return image.pixelColor(image.width() // 5, image.height() // 5)


def test_entire_tray_turns_red_then_black_and_double_click_opens(qt):
    tray = Tray(ICON)
    opened = []
    tray.open_requested.connect(lambda: opened.append(True))
    before = icon_background(tray)
    assert max(before.red(), before.green(), before.blue()) < 20
    tray.update_unread(1)
    color = icon_background(tray)
    assert color.red() > 200 and color.green() < 100 and color.blue() < 110
    assert '1 non lu' in tray.icon.toolTip()
    tray.update_unread(120)
    assert '120' in tray.unread_action.text()
    tray.update_unread(0)
    after = icon_background(tray)
    assert max(after.red(), after.green(), after.blue()) < 20
    tray._activated(QSystemTrayIcon.ActivationReason.DoubleClick)
    tray.menu.defaultAction().trigger()
    assert opened == [True, True]
    tray.icon.hide()
    tray.timer.stop()
    tray.deleteLater()


def test_preferences_and_main_window_offer_no_network_controls(qt):
    preferences = Preferences(nickname='Student42')
    dialog = SettingsDialog(preferences, True, {'relay_host': 'sensitive.cluster.example'})
    assert [dialog.tabs.tabText(i) for i in range(dialog.tabs.count())] == ['Général', 'À propos']
    assert not dialog.findChildren(QPlainTextEdit)
    assert not any('réseau' in button.text().casefold() for button in dialog.findChildren(QPushButton))
    window = MainWindow(preferences, Conversations(), ICON)
    assert not any('réseau' in button.toolTip().casefold() for button in window.findChildren(QPushButton))
    assert window.network_label.isHidden()
    # Receiving service state cannot accidentally recreate a diagnostic panel.
    dialog.update_network({'relay_host': 'sensitive.cluster.example'})
    dialog.diagnostic_result(['sensitive internal address'])
    assert dialog.tabs.count() == 2
    dialog.close()
    dialog.deleteLater()
    window.quitting_now = True
    window.close()
    window.deleteLater()


@pytest.mark.parametrize('platform,native', [('xcb', True), ('wayland', False)])
def test_wizz_moves_both_axes_for_seconds_and_restores(qt, monkeypatch, platform, native):
    monkeypatch.setattr(QApplication, 'platformName', lambda: platform)
    window = QMainWindow()
    content = QWidget()
    window.setCentralWidget(content)
    window.resize(400, 300)
    window.move(80, 100)
    window.show()
    qt.processEvents()
    animation = shake_window(window, content)
    target = window if native else content
    assert animation.targetObject() is target
    assert animation.duration() == WIZZ_DURATION >= 2500
    origin = animation.origin
    animation.setCurrentTime(45)
    assert target.pos().x() != origin.x()
    assert target.pos().y() != origin.y()
    # Restart while running: first position is restored rather than accumulating.
    replacement = shake_window(window, content)
    assert replacement.origin == origin
    replacement.setCurrentTime(90)
    assert target.pos() != origin
    replacement.stop()
    assert target.pos() == origin
    assert replacement.state() == QAbstractAnimation.State.Stopped
    final = shake_window(window, content)
    final.setCurrentTime(final.duration())
    assert target.pos() == origin
    window.close()
    window.deleteLater()
