"""Visual-only annotation input and an input-transparent X11 overlay."""
import os
from PySide6.QtCore import QObject, QPointF, QRectF, Qt, Signal, QTimer
from PySide6.QtGui import QColor, QGuiApplication, QPainter, QPainterPath, QPainterPathStroker, QPen, QRegion
from PySide6.QtWidgets import QWidget
from ..features.screen_annotations import AnnotationBoard, COLORS, MAX_POINTS, SCALE


def mapped(point, rect):
    return QPointF(rect.left() + point[0] * max(1, rect.width()-1) / SCALE,
                   rect.top() + point[1] * max(1, rect.height()-1) / SCALE)


def shape_path(action, points, rect):
    path = QPainterPath(); first = mapped(points[0], rect)
    if action == 'pen':
        path.moveTo(first)
        for point in points[1:]: path.lineTo(mapped(point, rect))
    else:
        bounds = QRectF(first, mapped(points[-1], rect)).normalized()
        if action == 'ellipse': path.addEllipse(bounds)
        else: path.addRect(bounds)
    return path


def pointer_path(point, rect):
    start = mapped(point, rect)
    path = QPainterPath(); path.moveTo(start)
    for dx, dy in ((3, 23), (9, 17), (15, 28), (20, 25), (14, 14), (23, 13), (0, 0)):
        path.lineTo(start + QPointF(dx, dy))
    path.closeSubpath(); return path


def paint_marks(painter, board, rect, draft=None):
    painter.save(); painter.setClipRect(rect); painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    for action, points, color in board.shapes + ([draft] if draft else []):
        path = shape_path(action, points, rect)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        for shade, width in (('#151922', 6), (COLORS[color], 3)):
            painter.setPen(QPen(QColor(shade), width, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
            painter.drawPath(path)
    if board.pointer:
        point, color, when = board.pointer
        painter.setPen(QPen(QColor('white'), 1.5)); painter.setBrush(QColor(COLORS[color]))
        painter.drawPath(pointer_path(point, rect))
    painter.restore()


class AnnotationLayer(QObject):
    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent); self.board = AnnotationBoard()
        self.timer = QTimer(self); self.timer.setInterval(250); self.timer.timeout.connect(self.expire)

    def apply(self, payload):
        if self.board.apply(payload): self.changed.emit()

    def clear(self):
        self.board.clear(); self.changed.emit()

    def expire(self):
        if self.board.expire_pointer(): self.changed.emit()


class AnnotationCanvas(QWidget):
    annotation_submitted = Signal(object)
    pointer_changed = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.layer = None; self.annotating = False; self.tool = 'pointer'; self.color = 0
        self.drag = []; self.drag_tool = ''; self.drag_color = 0
        self.setMouseTracking(True)

    def image_rect(self): return self.rect()

    def normalized(self, position, clamp=False):
        rect = self.image_rect()
        if rect.isEmpty() or not clamp and not QRectF(rect).contains(position): return None
        return [round(max(0, min(1, (position.x()-rect.x()) / max(1,rect.width()-1))) * SCALE),
                round(max(0, min(1, (position.y()-rect.y()) / max(1,rect.height()-1))) * SCALE)]

    def configure(self, layer, active):
        self.layer = layer; self.annotating = active; self.drag = []
        self.setCursor(Qt.CursorShape.CrossCursor if active else Qt.CursorShape.ArrowCursor)
        self.update()

    def submit(self, action, points, color=None):
        if not self.annotating: return
        payload = dict(action=action, points=[list(p) for p in points], color=self.color if color is None else color)
        self.layer.apply(payload); self.annotation_submitted.emit(payload)

    def mousePressEvent(self, event):
        point = self.normalized(event.position())
        if self.annotating and point is not None and event.button() == Qt.MouseButton.LeftButton:
            if self.tool != 'pointer':
                self.drag = [point]; self.drag_tool = self.tool; self.drag_color = self.color
                self.update()
            event.accept(); return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if not self.annotating: return super().mouseMoveEvent(event)
        point = self.normalized(event.position())
        self.pointer_changed.emit(dict(action='pointer', points=[point], color=self.color) if point is not None
                                  else dict(action='hide', points=[], color=self.color))
        if self.drag:
            point = self.normalized(event.position(), True)
            if point != self.drag[-1]:
                if self.drag_tool == 'pen':
                    self.drag.append(point)
                    if len(self.drag) == MAX_POINTS:
                        self.submit('pen', self.drag, self.drag_color); self.drag = [point]
                else: self.drag = [self.drag[0], point]
                self.update()
        event.accept()

    def mouseReleaseEvent(self, event):
        if self.annotating and self.drag and event.button() == Qt.MouseButton.LeftButton:
            point = self.normalized(event.position(), True)
            if self.drag_tool == 'pen':
                if point != self.drag[-1]: self.drag.append(point)
            else: self.drag = [self.drag[0], point]
            if len(self.drag) >= 2 and any(point != self.drag[0] for point in self.drag[1:]):
                self.submit(self.drag_tool, self.drag, self.drag_color)
            self.drag = []; self.update(); event.accept(); return
        super().mouseReleaseEvent(event)

    def leaveEvent(self, event):
        if self.annotating: self.pointer_changed.emit(dict(action='hide', points=[], color=self.color))
        super().leaveEvent(event)

    def paint_annotations(self, painter, rect):
        if self.layer:
            draft = (self.drag_tool, self.drag, self.drag_color) if len(self.drag) > 1 else None
            paint_marks(painter, self.layer.board, QRectF(rect), draft)


class AnnotationOverlay(QWidget):
    """Has no input handlers. Independent of the minimized sharing controls."""
    @staticmethod
    def supported():
        return QGuiApplication.platformName() == 'xcb' and os.environ.get('XDG_SESSION_TYPE') != 'wayland'

    def __init__(self, layer, screen):
        super().__init__(None, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.WindowTransparentForInput
                         | Qt.WindowType.WindowDoesNotAcceptFocus)
        self.layer = layer; self.target_screen = screen
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setWindowTitle('Annotations du partage · FortyTchat')
        self.setScreen(screen)
        self.setGeometry(screen.geometry())
        screen.geometryChanged.connect(self.geometry_changed)
        layer.changed.connect(self.refresh)
        self.refresh()

    def geometry_changed(self,geometry):
        self.setGeometry(geometry);self.refresh()

    def refresh(self):
        # A shaped window also leaves the desktop visible without a compositor.
        rect = QRectF(self.rect()); region = QRegion(); stroker = QPainterPathStroker(); stroker.setWidth(9)
        for action, points, color in self.layer.board.shapes:
            region |= QRegion(stroker.createStroke(shape_path(action, points, rect)).toFillPolygon().toPolygon())
        if self.layer.board.pointer:
            path = pointer_path(self.layer.board.pointer[0], rect)
            region |= QRegion(path.united(stroker.createStroke(path)).toFillPolygon().toPolygon())
        if region.isEmpty(): self.hide()
        else:
            self.setMask(region)
            if not self.isVisible(): self.show()
            self.update()

    def paintEvent(self, event):
        painter = QPainter(self); paint_marks(painter, self.layer.board, QRectF(self.rect()))

    def closeEvent(self, event):
        self.layer.changed.disconnect(self.refresh)
        try:self.target_screen.geometryChanged.disconnect(self.geometry_changed)
        except RuntimeError:pass  # A removed screen may already have been destroyed by Qt.
        event.accept()
