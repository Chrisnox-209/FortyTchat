"""Compact, keyboard-accessible painting tools for the screen-share viewer."""
from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QButtonGroup, QFrame, QHBoxLayout, QToolButton, QVBoxLayout, QWidget
from ..features.screen_annotations import COLORS, DEFAULT_WIDTH, WIDTHS
from .theme import colors
from .widgets import button, label


class AnnotationChoice(QToolButton):
    def __init__(self, kind, value, text, parent=None):
        super().__init__(parent)
        self.kind, self.value = kind, value
        self.setObjectName('annotationChoice');self.setCheckable(True)
        self.setAccessibleName(text);self.setToolTip(text)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(QSize(38, 40) if kind == 'tool' else QSize(32, 32))

    def paintEvent(self, event):
        super().paintEvent(event)
        palette = colors(); painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.translate(self.width()/2 - 12, self.height()/2 - 12)
        ink = QColor(palette['accentText' if self.isChecked() else 'text'] if self.isEnabled() else palette['subtle'])
        painter.setPen(QPen(ink, 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        if self.kind == 'color':
            painter.setPen(Qt.PenStyle.NoPen);painter.setBrush(QColor(COLORS[self.value]))
            painter.drawEllipse(QRectF(2, 2, 20, 20))
            if self.isChecked():
                painter.setPen(QPen(QColor('#17202b'), 2.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
                path = QPainterPath(QPointF(7, 12));path.lineTo(10, 15);path.lineTo(17, 8);painter.drawPath(path)
        elif self.kind == 'width':
            painter.setPen(QPen(ink, self.value, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            painter.drawLine(QPointF(5, 12), QPointF(19, 12))
        elif self.value == 'pointer':
            path = QPainterPath(QPointF(5, 2))
            for point in ((6, 20), (11, 15), (15, 22), (19, 20), (15, 13), (22, 12)):
                path.lineTo(*point)
            path.closeSubpath();painter.drawPath(path)
        elif self.value == 'pen':
            path = QPainterPath(QPointF(4, 21))
            for point in ((6, 14), (17, 3), (22, 8), (11, 19), (4, 21)):
                path.lineTo(*point)
            painter.drawPath(path);painter.drawLine(QPointF(15, 5), QPointF(20, 10))
        elif self.value == 'line':painter.drawLine(QPointF(4, 20), QPointF(20, 4))
        elif self.value == 'ellipse':painter.drawEllipse(QRectF(3, 3, 18, 18))
        else:painter.drawRoundedRect(QRectF(3, 5, 18, 14), 1, 1)
        painter.end()


class AnnotationToolbar(QFrame):
    options_changed = Signal()
    clear_requested = Signal()

    def __init__(self, parent=None, *, outgoing=False):
        super().__init__(parent)
        self.setObjectName('annotationToolbar')
        self.tool = 'pointer';self.color = 0;self.stroke_width = DEFAULT_WIDTH
        layout = QVBoxLayout(self);layout.setContentsMargins(10, 10, 10, 10);layout.setSpacing(8)
        top = QHBoxLayout();top.setSpacing(8)
        self.tools = QWidget();tool_row = QHBoxLayout(self.tools);tool_row.setContentsMargins(0, 0, 0, 0);tool_row.setSpacing(4)
        self.tool_buttons = {};self.tool_group = QButtonGroup(self)
        for key, text in (('pointer', 'Pointeur'), ('pen', 'Crayon'), ('line', 'Trait'), ('ellipse', 'Cercle'), ('rectangle', 'Rectangle')):
            item = AnnotationChoice('tool', key, text)
            self.tool_group.addButton(item);tool_row.addWidget(item);self.tool_buttons[key] = item
            item.clicked.connect(lambda checked=False, value=key:self.choose('tool', value))
        self.tool_buttons[self.tool].setChecked(True)
        self.tools.setVisible(not outgoing);top.addWidget(self.tools);top.addStretch()
        self.clear_button = button('Effacer', 'soft', 'Effacer les dessins des deux côtés')
        self.clear_button.clicked.connect(self.clear_requested);top.addWidget(self.clear_button);layout.addLayout(top)
        self.styles = QWidget();styles = QHBoxLayout(self.styles);styles.setContentsMargins(0, 0, 0, 0);styles.setSpacing(18)
        self.width_controls = QWidget();width_box = QVBoxLayout(self.width_controls);width_box.setContentsMargins(0, 0, 0, 0);width_box.setSpacing(4)
        self.width_label = label('Épaisseur · 3 px', 'caption');width_box.addWidget(self.width_label)
        width_row = QHBoxLayout();width_row.setSpacing(4);self.width_buttons = {};self.width_group = QButtonGroup(self)
        for value in WIDTHS:
            item = AnnotationChoice('width', value, f'Épaisseur : {value} px')
            self.width_group.addButton(item);width_row.addWidget(item);self.width_buttons[value] = item
            item.clicked.connect(lambda checked=False, value=value:self.choose('width', value))
        width_box.addLayout(width_row);styles.addWidget(self.width_controls)
        palette = QVBoxLayout();palette.setSpacing(4);self.color_label = label('Couleur · Rose', 'caption');palette.addWidget(self.color_label)
        color_row = QHBoxLayout();color_row.setSpacing(4);self.color_buttons = {};self.color_group = QButtonGroup(self)
        for index, text in enumerate(('Rose', 'Jaune', 'Bleu', 'Vert')):
            item = AnnotationChoice('color', index, text)
            self.color_group.addButton(item);color_row.addWidget(item);self.color_buttons[index] = item
            item.clicked.connect(lambda checked=False, value=index:self.choose('color', value))
        palette.addLayout(color_row);styles.addLayout(palette);styles.addStretch()
        self.styles.setVisible(not outgoing);layout.addWidget(self.styles)
        self.color_buttons[self.color].setChecked(True)
        self.set_width_support(False)

    def choose(self, option, value):
        setattr(self, 'stroke_width' if option == 'width' else option, value)
        if option == 'width':self.width_label.setText(f'Épaisseur · {value} px')
        if option == 'color':self.color_label.setText('Couleur · '+('Rose', 'Jaune', 'Bleu', 'Vert')[value])
        self.options_changed.emit()

    def set_width_support(self, supported):
        self.width_controls.setEnabled(supported)
        if not supported:self.stroke_width = DEFAULT_WIDTH
        self.width_buttons[self.stroke_width].setChecked(True)
        self.width_label.setText(f'Épaisseur · {self.stroke_width} px')
        self.width_controls.setToolTip('Choisis l’épaisseur des tracés.' if supported else 'Épaisseur de 3 px avec cet ancien client ; deux versions 2.6.0 ou plus récentes permettent de la régler.')
