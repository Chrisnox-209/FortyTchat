from PySide6.QtCore import Qt, QObject, Signal, QTimer
from PySide6.QtGui import QIcon, QPainter, QColor, QPixmap, QFont
from PySide6.QtWidgets import QSystemTrayIcon, QMenu


class Tray(QObject):
    open_requested = Signal()
    settings_requested = Signal()
    quit_requested = Signal()
    status_requested = Signal(str)

    def __init__(self, icon_path, parent=None):
        super().__init__(parent)
        self.base = QIcon(icon_path)
        self.icon = QSystemTrayIcon(self.base, self)
        self.menu = QMenu()
        self.menu.addAction('Ouvrir FortyTchat', self.open_requested.emit)
        self.unread_action = self.menu.addAction('Aucun message non lu')
        self.unread_action.setEnabled(False)
        self.menu.addSeparator()
        statuses = self.menu.addMenu('Statut')
        for text, key in [('En ligne', 'online'), ('Absent·e', 'away'), ('Ne pas déranger', 'dnd')]:
            statuses.addAction(text, lambda checked=False, s=key: self.status_requested.emit(s))
        self.menu.addAction('Paramètres', self.settings_requested.emit)
        self.menu.addSeparator()
        self.menu.addAction('Quitter complètement', self.quit_requested.emit)
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
        if reason in (QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick):
            self.open_requested.emit()

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
            self.base.paint(painter, 0, 0, 64, 64)
            painter.setBrush(QColor('#7891ff'))
            painter.setPen(QColor('#000000'))
            painter.drawEllipse(37, 0, 27, 27)
            painter.setPen(QColor('white'))
            font = QFont('DejaVu Sans')
            font.setWeight(QFont.Weight.Bold)
            font.setPixelSize(15)
            painter.setFont(font)
            painter.drawText(37, 0, 27, 27, Qt.AlignmentFlag.AlignCenter, str(min(99, count)))
            painter.end()
            self.icon.setIcon(QIcon(pixmap))
        else:
            self.icon.setIcon(self.base)
