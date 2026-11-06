"""Invitation flood protection and actual popup layout/actions."""
import pytest

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QLabel, QWidget

from src.features.break_request import (BreakRateLimit, INVITE_BLOCK_TIME,
                                         INVITE_SERIES_WINDOW)
from src.ui.break_popup import BreakPopup, BreakReplyPopup
from src.ui.theme import apply_theme


def test_fourth_break_request_blocks_only_this_contact_for_five_minutes():
    now = [0.0]
    gate = BreakRateLimit(lambda: now[0])
    assert [gate.allow('stable-alice') for _ in range(4)] == [True, True, True, False]
    assert gate.remaining('stable-alice') == INVITE_BLOCK_TIME
    assert gate.allow('bob')
    now[0] = 299
    assert not gate.allow('stable-alice')
    assert gate.remaining('stable-alice') == 1  # Repeated attempts never prolong a block.
    now[0] = 300
    assert gate.remaining('stable-alice') == 0
    assert [gate.allow('stable-alice') for _ in range(4)] == [True, True, True, False]


def test_invitation_series_resets_after_idle_and_storage_is_bounded():
    now = [0.0]
    gate = BreakRateLimit(lambda: now[0], maximum=2)
    assert gate.allow('alice') and gate.allow('alice')
    now[0] += INVITE_SERIES_WINDOW - 1
    assert gate.allow('alice')
    now[0] += INVITE_SERIES_WINDOW
    assert [gate.allow('alice') for _ in range(3)] == [True, True, True]
    assert gate.allow('bob')
    assert not gate.allow('mallory')
    assert len(gate.states) == 2
    now[0] += INVITE_SERIES_WINDOW
    assert gate.allow('mallory') and len(gate.states) == 1


@pytest.fixture(scope='module')
def qt():
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    return app


def test_pause_cards_are_centred_readable_with_hidden_parent_and_theme(qt):
    parent = QWidget()
    parent.move(-900, -900)
    for theme in ('dark', 'light'):
        apply_theme(qt, theme)
        for popup in (BreakPopup('CosmicAxolotlAvec28Lettres42', lambda value: None, parent),
                      BreakReplyPopup('CosmicAxolotlAvec28Lettres42', 'accepted', parent),
                      BreakReplyPopup('CosmicAxolotlAvec28Lettres42', 'declined', parent)):
            popup.show()
            for _ in range(4):
                qt.processEvents()
            available = popup.screen().availableGeometry()
            distance = popup.frameGeometry().center() - available.center()
            assert abs(distance.x()) <= 2 and abs(distance.y()) <= 2
            assert not popup.isModal()
            assert popup.windowFlags() & Qt.WindowType.WindowStaysOnTopHint
            for text in popup.findChildren(QLabel):
                if text.wordWrap():
                    assert text.height() >= text.heightForWidth(text.width()), text.text()
            assert popup.card.geometry().bottom() < popup.height()
            popup.close()
            popup.deleteLater()
    parent.deleteLater()


def test_invite_buttons_escape_close_and_expiry_answer_at_most_once(qt):
    answers = []
    accepted = BreakPopup('Alice', answers.append)
    accepted.yes_button.click()
    accepted.close()
    assert answers == ['accepted']
    declined = BreakPopup('Alice', answers.append)
    declined.no_button.click()
    declined.close()
    assert answers == ['accepted', 'declined']
    dismissed = BreakPopup('Alice', answers.append)
    dismissed.show()
    dismissed.reject()  # Escape uses QDialog.reject, not always closeEvent.
    dismissed.close()
    assert answers == ['accepted', 'declined', 'dismissed']
    expired = BreakPopup('Alice', answers.append)
    expired.expire()
    expired.close()
    assert answers == ['accepted', 'declined', 'dismissed']
    assert all(not dialog.timer.isActive() for dialog in (accepted, declined, dismissed, expired))
    for dialog in (accepted, declined, dismissed, expired):
        dialog.deleteLater()


def test_reply_feedback_uses_blue_or_red_and_does_not_send_a_response(qt):
    accepted = BreakReplyPopup('Alice', 'accepted')
    declined = BreakReplyPopup('Alice', 'declined')
    assert '#3269eb' in accepted.styleSheet()
    assert '#df4563' in declined.styleSheet()
    assert any(text.text() == 'yessss !!' for text in accepted.findChildren(QLabel))
    assert any(text.text() == 'Non je bosse !!' for text in declined.findChildren(QLabel))
    accepted.close_button.click()
    declined.close_button.click()
    accepted.deleteLater()
    declined.deleteLater()
