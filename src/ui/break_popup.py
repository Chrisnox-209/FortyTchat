"""Screen-centred, silent invitation cards, independent of the chat window."""
from PySide6.QtCore import Qt, QTimer, QRectF, QPointF
from PySide6.QtGui import (QColor, QCursor, QFont, QGuiApplication, QLinearGradient,
                           QPainter, QPainterPath, QPen)
from PySide6.QtWidgets import (QApplication, QDialog, QFrame, QGraphicsDropShadowEffect,
                               QHBoxLayout, QVBoxLayout, QWidget)

from .widgets import button, label
from ..features.break_request import INVITE_LIFETIME


class EnvelopeIllustration(QWidget):
    """A sharp native illustration: no downloaded image or emoji font needed."""

    def __init__(self, tone='blue', badge='1', parent=None):
        super().__init__(parent)
        self.tone, self.badge = tone, badge
        self.setFixedSize(166, 118)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(41, 76, 152, 20))
        painter.drawEllipse(QRectF(24, 101, 119, 12))
        red = self.tone == 'red'
        top, bottom = ('#ff8290', '#e24965') if red else ('#61b4ff', '#3867ed')
        gradient = QLinearGradient(25, 24, 137, 98)
        gradient.setColorAt(0, QColor(top))
        gradient.setColorAt(1, QColor(bottom))
        painter.setBrush(gradient)
        painter.drawRoundedRect(QRectF(24, 24, 120, 77), 12, 12)
        fold = QPainterPath(QPointF(25, 28))
        fold.lineTo(78, 70)
        fold.quadTo(84, 75, 90, 70)
        fold.lineTo(143, 28)
        painter.setBrush(QColor('#ffa5ad' if red else '#91ceff'))
        painter.drawPath(fold)
        painter.setPen(QPen(QColor(255, 255, 255, 65), 2))
        painter.drawLine(QPointF(27, 96), QPointF(65, 66))
        painter.drawLine(QPointF(141, 96), QPointF(103, 66))
        painter.setPen(QPen(QColor('white'), 3))
        painter.setBrush(QColor('#e84866' if red or self.badge == '1' else '#3269eb'))
        painter.drawEllipse(QRectF(123, 6, 36, 36))
        font = QFont(self.font())
        font.setPixelSize(22)
        font.setWeight(QFont.Weight.Bold)
        painter.setFont(font)
        painter.setPen(QColor('white'))
        painter.drawText(QRectF(123, 5, 36, 36), Qt.AlignmentFlag.AlignCenter, self.badge)


class _PauseCard(QDialog):
    def __init__(self, title, tone='blue', badge='1', parent=None):
        super().__init__(parent)
        self.setObjectName('breakPopup')
        self.setWindowTitle(title + ' · FortyTchat')
        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint
                            | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setModal(False)
        self.setMinimumWidth(300)
        red = tone == 'red'
        accent, hover, soft = ('#df4563', '#c63150', '#fff0f3') if red else ('#3269eb', '#2454cc', '#eef5ff')
        self.setStyleSheet('''
            QDialog#breakPopup { background: transparent; }
            QFrame#pauseCard {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                                            stop:0 #ffffff, stop:1 SOFT);
                border: 1px solid #ffffff; border-radius: 30px;
            }
            QDialog#breakPopup QLabel { background: transparent; color: #26344f; }
            QDialog#breakPopup QLabel#pauseEyebrow { color: ACCENT; font-size: 10px; font-weight: 700; letter-spacing: 1.8px; }
            QDialog#breakPopup QLabel#pauseTitle { color: #1c2942; font-size: 27px; font-weight: 700; }
            QDialog#breakPopup QLabel#pauseBody { color: #62718a; font-size: 15px; }
            QDialog#breakPopup QLabel#pauseCaption { color: #7d8aa0; font-size: 11px; }
            QDialog#breakPopup QPushButton { min-height: 26px; padding: 10px 20px; border-radius: 15px; font-size: 15px; font-weight: 650; }
            QDialog#breakPopup QPushButton#pausePrimary {
                background: qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 ACCENT,stop:1 HOVER);
                color: white; border: 1px solid ACCENT;
            }
            QDialog#breakPopup QPushButton#pausePrimary:hover { background: HOVER; }
            QDialog#breakPopup QPushButton#pauseSecondary { background: #e3ebfa; color: #44618c; border: 1px solid #d6e0f2; }
            QDialog#breakPopup QPushButton#pauseSecondary:hover { background: #d6e2f7; }
        '''.replace('ACCENT', accent).replace('HOVER', hover).replace('SOFT', soft))
        outer = QVBoxLayout(self)
        outer.setContentsMargins(18, 18, 18, 22)
        self.card = QFrame()
        self.card.setObjectName('pauseCard')
        shadow = QGraphicsDropShadowEffect(self.card)
        shadow.setBlurRadius(30)
        shadow.setOffset(0, 9)
        shadow.setColor(QColor(25, 48, 99, 65))
        self.card.setGraphicsEffect(shadow)
        outer.addWidget(self.card)
        self.box = QVBoxLayout(self.card)
        self.box.setContentsMargins(28, 18, 28, 27)
        self.box.setSpacing(15)
        self.box.addWidget(EnvelopeIllustration(tone, badge), 0, Qt.AlignmentFlag.AlignHCenter)
        self._screen = None

    def add_text(self, text, name):
        item = label(text, name, True)
        item.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.box.addWidget(item)
        return item

    def showEvent(self, event):
        # The chat may be hidden in the tray. Use the active screen, never the
        # hidden parent's old window coordinates.
        active = QApplication.activeWindow()
        self._screen = (active.screen() if active and active is not self and active.isVisible() else None)
        self._screen = self._screen or QGuiApplication.screenAt(QCursor.pos()) or self.screen()
        if self._screen:
            available = self._screen.availableGeometry()
            self.resize(min(454, max(300, available.width() - 32)), self.sizeHint().height())
            self.move(available.center() - self.rect().center())
        super().showEvent(event)
        QTimer.singleShot(0, self.present)

    def present(self):
        if not self.isVisible():
            return
        if self._screen:
            self.move(self._screen.availableGeometry().center() - self.rect().center())
        self.raise_()
        self.activateWindow()
        if self.windowHandle():
            self.windowHandle().requestActivate()


class BreakPopup(_PauseCard):
    def __init__(self, nickname, callback, parent=None):
        super().__init__('Une pause ?', parent=parent)
        self.callback, self.answered = callback, False
        self.add_text('ÇA TE DIT ?', 'pauseEyebrow')
        self.add_text('On fait une pause ?', 'pauseTitle')
        self.add_text(f'{nickname} te propose de décrocher un peu.\nOn bouge ?', 'pauseBody')
        controls = QHBoxLayout()
        controls.setSpacing(12)
        self.no_button = button('Non je bosse !!', 'pauseSecondary')
        self.yes_button = button('yessss !!', 'pausePrimary')
        self.no_button.clicked.connect(lambda: self.answer('declined'))
        self.yes_button.clicked.connect(lambda: self.answer('accepted'))
        controls.addWidget(self.no_button, 1)
        controls.addWidget(self.yes_button, 1)
        self.box.addLayout(controls)
        self.add_text('Invitation valable 2 minutes · sans son', 'pauseCaption')
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.expire)
        self.timer.start(INVITE_LIFETIME * 1000)

    def answer(self, value):
        if not self.answered:
            self.answered = True
            self.callback(value)
        self.close()

    def reject(self):
        self.answer('dismissed')

    def expire(self):
        self.answered = True
        self.close()

    def closeEvent(self, event):
        if not self.answered:
            self.answered = True
            self.callback('dismissed')
        self.timer.stop()
        super().closeEvent(event)


class BreakReplyPopup(_PauseCard):
    def __init__(self, nickname, answer, parent=None):
        accepted = answer == 'accepted'
        super().__init__('Réponse à ta pause', 'blue' if accepted else 'red',
                         '✓' if accepted else '×', parent)
        self.add_text('UNE PETITE PAUSE, UNE RÉPONSE', 'pauseEyebrow')
        self.add_text('yessss !!' if accepted else 'Non je bosse !!' if answer == 'declined' else 'Une prochaine fois !', 'pauseTitle')
        text = (f'{nickname} est chaud pour une pause.\nÀ tout de suite !' if accepted else
                f'{nickname} est en plein boulot.\nOn remet ça à plus tard !' if answer == 'declined' else
                f'{nickname} a fermé l’invitation.\nCette pause n’a pas été confirmée.')
        self.add_text(text, 'pauseBody')
        self.close_button = button('Super !' if accepted else 'Compris', 'pausePrimary')
        self.close_button.clicked.connect(self.accept)
        self.box.addWidget(self.close_button)
        self.add_text('FortyTchat · les petites pauses font du bien', 'pauseCaption')
