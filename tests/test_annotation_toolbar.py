"""Exercise toolbar choices through real drags, legacy projection and painting."""
import pytest
from PySide6.QtCore import QEvent, QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QMouseEvent, QPainter
from PySide6.QtWidgets import QApplication, QComboBox
from src.features.screen_annotations import AnnotationBoard, COLORS
from src.ui.screen_annotations import paint_marks
from src.ui.screen_share import ScreenWindow, encode_screen_image


@pytest.fixture(scope='module')
def qt():
    return QApplication.instance() or QApplication([])


def mouse(canvas, kind, point, button=Qt.MouseButton.NoButton, buttons=Qt.MouseButton.NoButton):
    QApplication.sendEvent(canvas, QMouseEvent(kind, point, point, button, buttons, Qt.KeyboardModifier.NoModifier))


def ready(qt, widths=True):
    view=ScreenWindow('Alice42','assets/icons/fortytchat.svg');view.show()
    view.set_state('a'*32,'active',True,widths)
    image=QImage(1000,500,QImage.Format.Format_RGB32);image.fill(QColor('#254b84'))
    assert view.receive_frame(*encode_screen_image(image));qt.processEvents()
    return view


@pytest.mark.parametrize('tool',['pen','line','ellipse','rectangle'])
def test_selected_buttons_produce_matching_colored_strokes_and_keep_drag_snapshot(qt,tool):
    view=ready(qt)
    try:
        tools=view.annotation_tools;canvas=view.canvas;sent=[];canvas.annotation_submitted.connect(sent.append)
        assert not tools.findChildren(QComboBox)
        tools.tool_buttons[tool].click();tools.color_buttons[2].click();tools.width_buttons[10].click()
        assert sum(b.isChecked() for b in tools.tool_buttons.values())==1
        assert tools.tool_buttons[tool].isChecked() and tools.color_buttons[2].isChecked() and tools.width_buttons[10].isChecked()
        rect=canvas.image_rect();start=QPointF(rect.left()+60,rect.top()+50);end=QPointF(rect.right()-60,rect.bottom()-50)
        mouse(canvas,QEvent.Type.MouseButtonPress,start,Qt.MouseButton.LeftButton,Qt.MouseButton.LeftButton)
        for point in (start+QPointF(30,15),end):mouse(canvas,QEvent.Type.MouseMove,point,buttons=Qt.MouseButton.LeftButton)
        # Switching a toolbar option mid-drag changes the next stroke only.
        tools.color_buttons[1].click();tools.width_buttons[2].click()
        mouse(canvas,QEvent.Type.MouseButtonRelease,end,Qt.MouseButton.LeftButton)
        assert sent[-1]['action']==('pen' if tool=='line' else tool)
        assert sent[-1]['width']==10 and sent[-1]['color']==2
        assert len(sent[-1]['points'])==(3 if tool=='pen' else 2)
        assert view.annotation_layer.board.shapes[-1][-2:]==(2,10)
        remote=AnnotationBoard();assert remote.apply(sent[-1]);assert remote.shapes==view.annotation_layer.board.shapes
        tools.clear_button.click();assert not view.annotation_layer.board.shapes
        assert view.annotation_queue[-1]==dict(action='clear',points=[],color=0)
    finally:view.close();view.deleteLater();qt.processEvents()


def test_legacy_toolbar_keeps_all_tools_and_colors_but_fixes_width_to_three(qt):
    view=ready(qt,False)
    try:
        tools=view.annotation_tools;canvas=view.canvas;sent=[];canvas.annotation_submitted.connect(sent.append)
        assert not tools.width_controls.isEnabled() and tools.width_buttons[3].isChecked()
        tools.width_buttons[10].click();assert canvas.stroke_width==3
        tools.tool_buttons['line'].click();tools.color_buttons[3].click()
        rect=canvas.image_rect();start=QPointF(rect.center());end=start+QPointF(80,60)
        mouse(canvas,QEvent.Type.MouseButtonPress,start,Qt.MouseButton.LeftButton,Qt.MouseButton.LeftButton)
        mouse(canvas,QEvent.Type.MouseButtonRelease,end,Qt.MouseButton.LeftButton)
        assert sent[-1]==dict(action='pen',points=[canvas.normalized(start),canvas.normalized(end)],color=3)
        assert view.annotation_layer.board.shapes[-1][-1]==3
        assert 'ancien client' in view.annotation_hint.text()
        view.finish('Fin');assert not canvas.annotating and tools.isHidden()
    finally:view.close();view.deleteLater();qt.processEvents()


def test_pointer_and_color_buttons_are_keyboard_accessible_and_never_send_a_width(qt):
    from PySide6.QtTest import QTest
    view=ready(qt)
    try:
        tools=view.annotation_tools;canvas=view.canvas;sent=[];canvas.pointer_changed.connect(sent.append)
        tools.tool_buttons['rectangle'].click()
        pointer=tools.tool_buttons['pointer'];pointer.setFocus();QTest.keyClick(pointer,Qt.Key.Key_Space)
        assert canvas.tool=='pointer' and pointer.isChecked()
        tools.color_buttons[1].click();mouse(canvas,QEvent.Type.MouseMove,QPointF(canvas.image_rect().center()))
        assert sent[-1]['color']==1 and sent[-1]['action']=='pointer' and 'width' not in sent[-1]
        assert all(b.accessibleName() and b.toolTip() for b in (*tools.tool_buttons.values(),*tools.width_buttons.values(),*tools.color_buttons.values()))
    finally:view.close();view.deleteLater();qt.processEvents()


def test_renderer_uses_received_width_and_legacy_default_without_shifting_geometry(qt):
    def render(width=None):
        board=AnnotationBoard();payload=dict(action='pen',points=[[1000,5000],[9000,5000]],color=0)
        if width is not None:payload['width']=width
        assert board.apply(payload)
        image=QImage(201,201,QImage.Format.Format_RGB32);image.fill(QColor('#254b84'))
        painter=QPainter(image);paint_marks(painter,board,QRectF(0,0,201,201));painter.end()
        return image
    thin,thick,legacy=render(2),render(10),render()
    assert thick.pixelColor(100,104).name()==COLORS[0]
    assert thin.pixelColor(100,104).name()=='#254b84'
    assert legacy==render(3)
    assert all(image.pixelColor(100,100).name()==COLORS[0] for image in (thin,thick,legacy))
