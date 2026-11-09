from pathlib import Path
from PySide6.QtWidgets import QApplication
from src.features.cards import CardTemplates, default_responses
from src.ui.card_editor import CardEditor
from src.ui.break_popup import CardPopup
from src.ui.theme import apply_theme

CARD = {'title': 'Une petite info', 'text': 'La correction est terminée.', 'emoji': '2705'}


def test_editor_choice_buttons_preview_templates_and_silent_close(tmp_path):
    qt = QApplication.instance() or QApplication([])
    apply_theme(qt, 'dark')
    models = CardTemplates(tmp_path)
    editor = CardEditor('Alice42', models)
    editor.show(); qt.processEvents()
    try:
        assert 'responses' in editor.payload()
        chooser = editor.responses
        chooser.configure_button.click(); qt.processEvents()
        child = chooser.editor
        assert child.isWindow() and not child.findChildren(CardEditor)
        child.add_button.click(); qt.processEvents()
        row = child.rows[2]
        row.text.setText('Dans cinq minutes')
        row.select_emoji('1f4aa')
        child.apply_button.click(); qt.processEvents()
        assert len(chooser.policy()['buttons']) == 3
        assert chooser.policy()['buttons'][2]['emoji'] == '1f4aa'
        editor.save_template()
        assert CardTemplates(tmp_path).items[0]['responses']['buttons'][2]['label'] == 'Dans cinq minutes'
        replies = []
        popup = CardPopup('Alice42', editor.payload(), replies.append)
        popup.show(); qt.processEvents()
        popup.choice_buttons[2][0].click(); qt.processEvents()
        assert replies == [{'answer':'custom', 'title':'Dans cinq minutes', 'text':'', 'emoji':'1f4aa', 'choice':2}]
        popup.deleteLater()
        escaped = editor.payload()
        escaped['responses']['buttons'][0]['label'] = '<b>Mon bouton</b>'
        popup = CardPopup('Alice42', escaped, lambda value: None)
        assert popup.choice_buttons[0][0].toolTip() == '<qt>&lt;b&gt;Mon bouton&lt;/b&gt;</qt>'
        popup.expire(); popup.deleteLater()
        chooser.configure_button.click(); qt.processEvents()
        chooser.editor.close_only.setChecked(True)
        chooser.editor.close_text.setText('Bien reçu !')
        chooser.editor.apply_button.click(); qt.processEvents()
        assert editor.payload()['responses'] == {'buttons':[], 'free':False}
        assert editor.payload()['close_label'] == 'Bien reçu !'
        editor.preview(); qt.processEvents()
        assert not editor.preview_window.choice_buttons and not editor.preview_window.custom_button.isVisible()
        editor.preview_window.close()
        replies = []
        popup = CardPopup('Alice42', editor.payload(), replies.append)
        popup.show(); qt.processEvents()
        assert popup.close_button.text() == 'Bien reçu !'
        popup.close_button.click(); qt.processEvents()
        assert not replies and not popup.isVisible() and popup.answered
        popup.deleteLater()
        for close in ('reject','close','expire'):
            popup = CardPopup('Alice42', editor.payload(), replies.append)
            popup.show(); qt.processEvents(); getattr(popup, close)(); qt.processEvents()
            assert not replies
            popup.deleteLater()
    finally:
        editor.close(); editor.deleteLater(); qt.processEvents()


def test_legacy_editor_does_not_silently_downgrade_a_close_only_template(tmp_path):
    qt = QApplication.instance() or QApplication([])
    models = CardTemplates(tmp_path)
    mid = models.save({**CARD, 'responses':{'buttons':[], 'free':False}}, 'invite')
    editor = CardEditor('Old42', models, allow_choices=False)
    editor.show(); qt.processEvents()
    try:
        emitted = []
        editor.submitted.connect(emitted.append)
        editor.models.setCurrentIndex(editor.models.findData(mid))
        editor.send()
        assert not emitted and '1.7.0' in editor.feedback.text()
    finally: editor.close(); editor.deleteLater(); qt.processEvents()


def test_cancel_new_choice_keeps_existing_buttons_and_saves_close_mode(tmp_path):
    qt = QApplication.instance() or QApplication([])
    models = CardTemplates(tmp_path)
    editor = CardEditor('Alice42', models)
    editor.show(); qt.processEvents()
    try:
        editor.responses.configure_button.click(); qt.processEvents()
        editor.responses.editor.add_button.click(); qt.processEvents()
        editor.responses.editor.title.setText('Brouillon annulé')
        editor.responses.editor.reject(); qt.processEvents()
        assert len(editor.responses.policy()['buttons']) == 2
        assert editor.title.text() == 'On fait une pause ?'
        editor.responses.configure_button.click(); qt.processEvents()
        editor.responses.editor.close_only.setChecked(True)
        editor.responses.editor.close_text.setText('Compris')
        editor.responses.editor.apply_button.click(); qt.processEvents()
        editor.save_template()
        stored = CardTemplates(tmp_path).items[0]
        assert stored['responses'] == {'buttons': [], 'free': False}
        assert stored['close_label'] == 'Compris'
        editor.responses.set_policy(default_responses())
        editor.load_template(editor.models.currentIndex())
        assert editor.responses.policy() == {'buttons': [], 'free': False}
        assert editor.responses.close_label == 'Compris'
    finally: editor.close(); editor.deleteLater(); qt.processEvents()


def test_real_application_sends_author_preset_and_closes_information_silently(tmp_path, monkeypatch):
    import time, uuid
    from types import SimpleNamespace
    from src.app import Application
    from src.networking.client import ChatClient
    from src.networking.settings import NetworkConfig
    qt = QApplication.instance() or QApplication([])
    qt.setQuitOnLastWindowClosed(False)
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path / 'config'))
    monkeypatch.setattr(NetworkConfig, 'load', classmethod(lambda cls, path: NetworkConfig(mode='relay', relay_host='')))
    root = Path(__file__).resolve().parents[1]
    app = Application(root, SimpleNamespace(dev='choices-' + uuid.uuid4().hex[:8], background=False, insecure_local_relay=None))
    events = []
    peer = ChatClient('Alice42', lambda kind, data: events.append((kind, data)), discovery=False, tcp_port=0)
    def until(predicate):
        end = time.monotonic() + 6
        while not predicate():
            qt.processEvents(); time.sleep(.01)
            assert time.monotonic() < end
        qt.processEvents()
    def replies(): return [data['frame'] for kind, data in events if kind == 'event' and data['frame']['type'] == 'card_reply']
    try:
        app.worker.submit(peer.start()).result(4)
        client = app.worker.client
        until(lambda: client.port > 0)
        def connect():
            client.observe(peer.hello(), '127.0.0.1'); peer.observe(client.hello(), '127.0.0.1')
        app.worker.loop.call_soon_threadsafe(connect)
        until(lambda: peer.sid in app.window.peers)
        policy = {'buttons': [{'label':'Je termine mon exercice', 'answer':'custom',
                   'title':'Dans cinq minutes !', 'text':'Je te rejoins ensuite.', 'emoji':'1f4aa'}], 'free':False}
        request = app.worker.submit(peer.send('card_invite', client.sid, **CARD, responses=policy)).result(4)
        until(lambda: request in app.popups)
        popup = app.popups[request][1]
        assert not popup.custom_button.isVisible()
        popup.choice_buttons[0][0].click()
        until(lambda: bool(replies()))
        assert replies()[0]['choice'] == 0 and replies()[0]['text'] == 'Je te rejoins ensuite.'
        assert replies()[0]['emoji'] == '1f4aa' and request not in app.popups
        until(lambda:'close_label' in peer.peers[client.sid].features)
        request = app.worker.submit(peer.send('card_invite', client.sid, **CARD, responses={'buttons':[],'free':False},close_label='Bien reçu !')).result(4)
        until(lambda: request in app.popups)
        assert app.popups[request][1].close_button.text() == 'Bien reçu !'
        app.popups[request][1].close_button.click()
        until(lambda: request not in app.popups)
        end = time.monotonic() + .15
        while time.monotonic() < end: qt.processEvents(); time.sleep(.01)
        assert len(replies()) == 1
    finally:
        app.worker.submit(peer.stop()).result(4)
        app.cleanup(); app.window.close(); app.deleteLater(); qt.processEvents()


import os
import subprocess
import sys
import pytest


@pytest.mark.parametrize('scale', ['1','1.5','2'])
def test_choice_cards_remain_scrollable_at_screen_scales(scale):
    source = '''
from PySide6.QtWidgets import QApplication, QLabel
from src.ui.break_popup import CardPopup
from src.ui.theme import apply_theme
from src.features.cards import default_responses
qt=QApplication([])
policy=default_responses()
policy['buttons'].append(dict(label='W'*32, answer='custom', title='W'*80, text='W'*400, emoji='1f4aa'))
for theme in ('dark','light'):
    apply_theme(qt,theme)
    popup=CardPopup('Alice42',dict(title='W'*80,text='W'*400,emoji='1f923',responses=policy,close_label='W'*32),lambda value:None)
    popup.show()
    for _ in range(8):qt.processEvents()
    assert abs(popup.content.width() - popup.scroll.viewport().width()) <= 1
    assert popup.scroll.horizontalScrollBar().maximum()==0
    for text in popup.findChildren(QLabel):
        if text.wordWrap() and text.text():assert text.height()>=text.heightForWidth(text.width()),text.text()
    popup.scroll.ensureWidgetVisible(popup.close_button)
    for _ in range(4):qt.processEvents()
    assert popup.close_button.height()>=popup.close_button.sizeHint().height()
    assert popup.choice_buttons[2][0].toolTip()=='W'*32
    assert popup.close_button.accessibleName()=='W'*32
    popup.close();popup.deleteLater();qt.processEvents()
'''
    result = subprocess.run([sys.executable,'-c',source],cwd=Path(__file__).resolve().parents[1],
                            env={**os.environ,'QT_QPA_PLATFORM':'offscreen','QT_SCALE_FACTOR':scale},
                            text=True,capture_output=True,timeout=20)
    assert result.returncode==0,result.stdout+result.stderr


def test_simple_settings_validate_remove_and_preserve_old_rich_replies(tmp_path):
    qt=QApplication.instance() or QApplication([])
    models=CardTemplates(tmp_path)
    editor=CardEditor('Alice42',models);editor.show();qt.processEvents()
    try:
        editor.responses.configure_button.click();qt.processEvents();dialog=editor.responses.editor
        assert len(dialog.rows)==2 and dialog.apply_button.isEnabled()
        dialog.add_button.click();qt.processEvents()
        assert len(dialog.rows)==3 and not dialog.add_button.isEnabled() and not dialog.apply_button.isEnabled()
        dialog.rows[2].text.setText('Oui');assert not dialog.apply_button.isEnabled()
        dialog.rows[2].text.setText('Je te rejoins !');assert dialog.apply_button.isEnabled()
        dialog.rows[2].kind.setCurrentIndex(dialog.rows[2].kind.findData('declined'))
        assert dialog.rows[2].item['answer']=='declined'
        dialog.rows[1].remove.click();qt.processEvents();assert len(dialog.rows)==2
        dialog.title.setText('Ma question');dialog.apply_button.click();qt.processEvents()
        assert editor.title.text()=='Ma question' and len(editor.payload()['responses']['buttons'])==2
        policy=default_responses();policy['buttons'][0].update(title='Ancien titre',text='Ancien texte riche',emoji='1f618')
        editor.responses.set_policy(policy)
        editor.responses.configure_button.click();qt.processEvents()
        editor.responses.editor.apply_button.click();qt.processEvents()
        assert editor.payload()['responses']==policy
    finally:editor.close();editor.deleteLater();qt.processEvents()


@pytest.mark.parametrize('scale',['1','1.5','2'])
def test_simple_response_window_readable_at_scales(scale):
    source='''
from PySide6.QtWidgets import QApplication,QLabel,QScrollArea
from PySide6.QtGui import QFont
from src.ui.response_choices import ResponseSettings,simple_responses
from src.ui.theme import apply_theme
app=QApplication([])
for theme in ('dark','light'):
    apply_theme(app,theme)
    app.setStyleSheet(app.styleSheet()+' QWidget { font-size:20px; }')
    dialog=ResponseSettings('On fait une pause ?',simple_responses())
    dialog.resize(430,500);dialog.show()
    for _ in range(8):app.processEvents()
    scroll=dialog.findChild(QScrollArea)
    assert scroll.horizontalScrollBar().maximum()==0
    for text in dialog.findChildren(QLabel):
        if text.wordWrap() and text.text():assert text.height()>=text.heightForWidth(text.width()),text.text()
    for row in dialog.rows:
        assert row.text.height()>=row.text.fontMetrics().height()+16
    dialog.close_only.setChecked(True);dialog.close_text.setText('D’accord !')
    for _ in range(8):app.processEvents()
    assert dialog.apply_button.isEnabled() and not dialog.answers.isVisible()
    assert dialog.apply_button.height()>=dialog.apply_button.sizeHint().height()
    dialog.reject();dialog.deleteLater();app.processEvents()
'''
    result=subprocess.run([sys.executable,'-c',source],cwd=Path(__file__).resolve().parents[1],
        env={**os.environ,'QT_QPA_PLATFORM':'offscreen','QT_SCALE_FACTOR':scale},capture_output=True,text=True,timeout=20)
    assert result.returncode==0,result.stdout+result.stderr
