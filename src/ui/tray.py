import os
import time
from PySide6.QtCore import Qt, QObject, Signal, QTimer, QRectF, QEvent
from PySide6.QtGui import QIcon, QPainter, QColor, QPixmap, QFont, QPainterPath
from PySide6.QtWidgets import QSystemTrayIcon, QMenu, QApplication


class Tray(QObject):
    open_requested = Signal()
    settings_requested = Signal()
    quit_requested = Signal()
    status_requested = Signal(str)

    def __init__(self, icon_path, parent=None, clock=time.monotonic):
        super().__init__(parent)
        self.clock = clock
        self.menu_double_click = 'GNOME' in os.environ.get('XDG_CURRENT_DESKTOP', '').upper()
        self._menu_opened, self._context_at, self._last_open = None, -100, -100
        self._menu_used = False
        self.base = QIcon(icon_path)
        self.icon = QSystemTrayIcon(self.base, self)
        self.menu = QMenu()
        self.menu.installEventFilter(self)
        self.open_action = self.menu.addAction('Ouvrir FortyTchat', self.open_chat)
        self.menu.setDefaultAction(self.open_action)
        self.unread_action = self.menu.addAction('Aucun message non lu')
        self.unread_action.setEnabled(False)
        self.menu.addSeparator()
        statuses = self.menu.addMenu('Statut')
        for text, key in [('En ligne', 'online'), ('Absent·e', 'away'), ('Ne pas déranger', 'dnd')]:
            statuses.addAction(text, lambda checked=False, s=key: self.status_requested.emit(s))
        self.menu.addAction('Paramètres', self.settings_requested.emit)
        self.menu.addSeparator()
        self.menu.addAction('Quitter complètement', self.quit_requested.emit)
        self.menu.aboutToShow.connect(self.menu_opened)
        self.menu.aboutToHide.connect(self.menu_closed)
        self.menu.hovered.connect(self.menu_used)
        self.menu.triggered.connect(self.menu_used)
        self.icon.setContextMenu(self.menu)
        self.icon.activated.connect(self._activated)
        self.update_unread(0)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(5000)
        self.refresh()

    @property
    def available(self):
        return QSystemTrayIcon.isSystemTrayAvailable()

    def refresh(self):
        if self.available and not self.icon.isVisible():
            self.icon.show()
        elif not self.available and self.icon.isVisible():
            self.icon.hide()

    def _activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Context:
            self._context_at = self.clock()
        elif reason in (QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick,
                        QSystemTrayIcon.ActivationReason.MiddleClick):
            self.open_chat()

    def open_chat(self):
        now = self.clock()
        self._last_open = now
        self._menu_used = True
        self.menu.close()
        self.open_requested.emit()

    def menu_opened(self):
        self._menu_opened, self._menu_used = self.clock(), False

    def menu_used(self, action):
        self._menu_used = True

    def eventFilter(self, watched, event):
        if watched is self.menu and event.type() == QEvent.Type.KeyPress:
            self._menu_used = True  # Escape/navigation in a local Qt menu is not a second icon click.
        return super().eventFilter(watched, event)

    def menu_closed(self):
        opened = self._menu_opened
        self._menu_opened = None
        if not self.menu_double_click or opened is None:
            return
        interval = QApplication.doubleClickInterval() / 1000
        now = self.clock()
        if (self._menu_used or not 0 <= now - opened <= interval
                or now - self._context_at <= interval or now - self._last_open <= interval):
            return
        # Older GNOME indicators may report only "menu opened/closed" when
        # the second click closes their menu. Defer until triggered actions run.
        QTimer.singleShot(0, lambda: self.open_chat() if not self._menu_used and self._menu_opened is None else None)

    def update_unread(self, count):
        self.unread_action.setText(f'{count} message(s) non lu(s)' if count else 'Aucun message non lu')
        self.icon.setToolTip(f'FortyTchat · {count} non lu(s)' if count else 'FortyTchat · Le cluster, en direct')
        if count:
            # Paint in logical pixels on a sharp 2x canvas, regardless of screen DPI.
            pixmap = QPixmap(128, 128)
            pixmap.setDevicePixelRatio(2)
            pixmap.fill(Qt.GlobalColor.transparent)
            painter = QPainter(pixmap)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            # The whole FT background turns red; the white mark remains legible
            # even on GNOME bars too small to read the unread counter.
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor('#e04453'))
            painter.drawRoundedRect(QRectF(0, 0, 64, 64), 15, 15)
            painter.save()
            painter.scale(2 / 3, 2 / 3)
            mark = QPainterPath()
            mark.moveTo(18, 26)
            for x, y in ((44, 26), (44, 35), (28, 35), (28, 47), (42, 47),
                         (42, 56), (28, 56), (28, 72), (18, 72)):
                mark.lineTo(x, y)
            mark.closeSubpath()
            mark.moveTo(49, 26)
            for x, y in ((79, 26), (79, 36), (69, 36), (69, 72),
                         (59, 72), (59, 36), (49, 36)):
                mark.lineTo(x, y)
            mark.closeSubpath()
            painter.fillPath(mark, QColor('white'))
            painter.restore()
            painter.setBrush(QColor('white'))
            painter.setPen(QColor('#e04453'))
            painter.drawEllipse(39, 0, 25, 25)
            painter.setPen(QColor('#c12e40'))
            font = QFont('DejaVu Sans')
            font.setWeight(QFont.Weight.Bold)
            font.setPixelSize(14 if count < 10 else 11)
            painter.setFont(font)
            painter.drawText(39, 0, 25, 25, Qt.AlignmentFlag.AlignCenter, '99+' if count > 99 else str(count))
            painter.end()
            self.icon.setIcon(QIcon(pixmap))
        else:
            self.icon.setIcon(self.base)
