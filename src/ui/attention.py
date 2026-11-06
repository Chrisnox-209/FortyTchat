"""Silent attention effects with a visible Wayland fallback."""
from PySide6.QtCore import QPoint, QPropertyAnimation, QEasingCurve
from PySide6.QtWidgets import QApplication

WIZZ_DURATION = 2800


def bring_to_front(window):
    """Ask the desktop to show a conversation; the compositor has final say."""
    if window.isMinimized():
        window.showNormal()
    else:
        window.show()
    window.raise_()
    window.activateWindow()


class WindowShake(QPropertyAnimation):
    def __init__(self, target, parent, duration=WIZZ_DURATION):
        super().__init__(target, b'pos', parent)
        self.widget = target
        self.origin = QPoint(target.pos())
        self.setDuration(duration)
        self.setEasingCurve(QEasingCurve.Type.Linear)
        # Two-dimensional, fast, irregular motion. There is no sound or flashing.
        offsets = ((-9, 4), (10, -6), (-6, -8), (8, 7), (-11, 0),
                   (5, -7), (10, 5), (-7, 8), (3, -5), (-5, 2))
        steps = max(2, duration // 45)
        self.setStartValue(self.origin)
        for step in range(1, steps):
            x, y = offsets[(step - 1) % len(offsets)]
            self.setKeyValueAt(step / steps, self.origin + QPoint(x, y))
        self.setEndValue(self.origin)
        self.finished.connect(self.restore)

    def restore(self):
        self.widget.move(self.origin)

    def stop(self):
        super().stop()
        self.restore()


def shake_window(window, content=None, duration=WIZZ_DURATION):
    """Shake the native window on X11; move its contents on Wayland.

    Returns the animation so callers can inspect or cancel it. Repeated Wizz
    effects first restore the original position, avoiding cumulative drift.
    """
    previous = getattr(window, '_fortytchat_shake', None)
    if previous is not None:
        previous.stop()
        previous.deleteLater()
    platform = QApplication.platformName().lower()
    can_move = platform in ('xcb', 'windows', 'cocoa')
    target = window if can_move else (content or window.centralWidget())
    if target is None:
        return None
    animation = WindowShake(target, window, duration)
    window._fortytchat_shake = animation
    animation.start()
    return animation
