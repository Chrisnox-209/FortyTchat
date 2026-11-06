from pathlib import Path
from PySide6.QtWidgets import QApplication, QSystemTrayIcon
from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QKeyEvent
from src.ui.tray import Tray

ICON = str(Path(__file__).resolve().parents[1] / 'assets/icons/fortytchat.svg')


def test_gnome_menu_second_click_opens_without_hijacking_actions_or_context_menu(monkeypatch):
    qt = QApplication.instance() or QApplication([])
    monkeypatch.setenv('XDG_CURRENT_DESKTOP', 'ubuntu:GNOME')
    clock = [10.0]
    tray = Tray(ICON, clock=lambda: clock[0])
    opened = []
    tray.open_requested.connect(lambda: opened.append(True))
    try:
        tray.menu.aboutToShow.emit()
        assert not opened  # A single click keeps the menu.
        clock[0] += .15
        tray.menu.aboutToHide.emit()
        qt.processEvents()
        assert len(opened) == 1
        # A slow close is an ordinary dismissal, not a double click.
        clock[0] = 12
        tray.menu.aboutToShow.emit(); clock[0] += 1
        tray.menu.aboutToHide.emit(); qt.processEvents()
        assert len(opened) == 1
        # An explicit context menu remains a context menu.
        clock[0] = 15
        tray._activated(QSystemTrayIcon.ActivationReason.Context)
        tray.menu.aboutToShow.emit(); clock[0] += .1
        tray.menu.aboutToHide.emit(); qt.processEvents()
        assert len(opened) == 1
        # Qt can notify aboutToHide before triggering the selected action.
        clock[0] = 18
        tray.menu.aboutToShow.emit(); clock[0] += .1
        tray.menu.aboutToHide.emit(); tray.menu.triggered.emit(tray.unread_action)
        qt.processEvents()
        assert len(opened) == 1
        # Hovering/using a menu is never interpreted as a second icon click.
        clock[0] = 21
        tray.menu.aboutToShow.emit(); tray.menu.hovered.emit(tray.open_action)
        clock[0] += .1; tray.menu.aboutToHide.emit(); qt.processEvents()
        assert len(opened) == 1
        clock[0] = 22.5
        tray.menu.aboutToShow.emit()
        tray.eventFilter(tray.menu, QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier))
        clock[0] += .1; tray.menu.aboutToHide.emit(); qt.processEvents()
        assert len(opened) == 1
        clock[0] = 24
        tray._activated(QSystemTrayIcon.ActivationReason.MiddleClick)
        assert len(opened) == 2
        tray.menu_double_click = False
        clock[0] = 27
        tray.menu.aboutToShow.emit(); clock[0] += .1
        tray.menu.aboutToHide.emit(); qt.processEvents()
        assert len(opened) == 2
    finally:
        tray.timer.stop(); tray.icon.hide(); tray.deleteLater(); qt.processEvents()
