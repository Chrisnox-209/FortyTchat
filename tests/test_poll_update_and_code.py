import asyncio
from types import SimpleNamespace
import pytest
from PySide6.QtCore import QTimer,QMimeData
from PySide6.QtWidgets import QApplication,QPushButton,QLabel
from PySide6.QtGui import QDesktopServices
from src.features.collaboration import PollBook
from src.features.text import text_parts,mention_ids
from src.features.versions import version_tuple,UPDATE_URL
from src.networking.client import ChatClient
from src.networking.protocol import event,encode,ProtocolError
from src.ui.composer import Composer
from src.ui.groups import GroupManager
from src.ui.message_text import CodeEditor
from src.collaboration import CollaborationController


def test_final_vote_cannot_be_changed_retracted_or_replayed_as_other_choice():
    a,b='a'*32,'b'*32
    book=PollBook();poll=event('poll_create',a,'general',question='Pause ?',options=['Oui','Non'],audience=[a,b])
    book.create(poll,'general')
    vote=event('poll_vote',b,'general',poll=poll['id'],option=1,seq=1)
    assert book.vote(vote,'general')
    assert not book.vote(dict(vote,option=0,seq=2),'general')
    assert not book.vote(dict(vote,option=-1,seq=3),'general')
    assert book.vote(dict(vote,seq=4),'general')
    assert book.items[poll['id']]['votes']=={b:1}
    assert not book.vote(dict(vote,**{'from':'c'*32}),'general')
    assert not book.vote(vote,'group:'+'e'*32)


def test_equal_fences_and_visible_code_button_prepare_plain_code_without_execution():
    qt=QApplication.instance() or QApplication([])
    composer=Composer();composer.show();qt.processEvents()
    source='printf("hello");\n// @Alice42'
    def fill():
        editor=qt.activeModalWidget()
        assert isinstance(editor,CodeEditor)
        editor.language.setText('c')
        mime=QMimeData();mime.setText(source);editor.code.insertFromMimeData(mime)
        next(button for button in editor.findChildren(QPushButton) if button.text()=='Insérer dans le message').click()
    QTimer.singleShot(0,fill);composer.code_button.click()
    value=composer.input.toPlainText()
    assert value.strip()=='====c\n'+source+'\n===='
    assert list(text_parts(value.strip()))==[('code','c',source)]
    assert mention_ids(value,[{'nick':'Alice42','contact_id':'a'*32}])==[]
    composer.close();composer.deleteLater();qt.processEvents()


def test_group_delay_draft_survives_presence_and_feedback_confirms_saved_setting():
    qt=QApplication.instance() or QApplication([])
    owner='a'*32
    room=dict(id='b'*32,name='Team',owner=owner,members=[owner],admins=[],state='joined',joined=[owner],left=[],popup_interval=2520)
    manager=GroupManager(room,owner,{},[])
    manager.popup_delay.setText('15')
    manager.set_group(dict(room,joined=[owner]))
    assert manager.popup_delay.text()=='15' and manager.popup_delay_button.isEnabled()
    manager.popup_delay_button.click();assert manager.pending_delay==900
    manager.set_group(dict(room,popup_interval=900))
    assert manager.pending_delay is None and 'enregistré' in manager.feedback.text()
    manager.popup_delay.setText('0');assert not manager.popup_delay_button.isEnabled()
    manager.close();manager.deleteLater();qt.processEvents()


def test_higher_authenticated_peer_version_shows_banner_and_fixed_github_link(monkeypatch):
    from src.config import Preferences
    from src.memory import Conversations
    from src.ui.main_window import MainWindow
    qt=QApplication.instance() or QApplication([])
    window=MainWindow(Preferences(nickname='User42'),Conversations(),'assets/icons/fortytchat.svg')
    controller=SimpleNamespace(window=window)
    assert CollaborationController.collaboration_event(controller,'peer_version',{'version':'2.2.0'})
    assert window.update_banner.isHidden()
    CollaborationController.collaboration_event(controller,'peer_version',{'version':'2.4.0'})
    assert not window.update_banner.isHidden() and '2.4.0' in window.update_text.text()
    CollaborationController.collaboration_event(controller,'peer_version',{'version':'2.3.1'})
    assert '2.4.0' in window.update_text.text()
    opened=[];monkeypatch.setattr(QDesktopServices,'openUrl',lambda url:opened.append(url.toString()) or True)
    window.update_button.click();assert opened==[UPDATE_URL]
    for bad in ['2.4.0; echo bad','https://example.com','1.0',None]:assert version_tuple(bad) is None
    window.quitting_now=True;window.close();window.deleteLater();qt.processEvents()


def test_client_info_is_encrypted_and_old_peers_do_not_receive_unknown_control_frames():
    async def run():
        from src.features.versions import modern_caps
        logs=[[],[]]
        a,b=[ChatClient(n,lambda k,d,i=i:logs[i].append((k,d)),discovery=False,tcp_port=0) for i,n in enumerate(['New42','Other42'])]
        try:
            await a.start();await b.start();a.observe(b.hello(),'127.0.0.1');b.observe(a.hello(),'127.0.0.1')
            for _ in range(400):
                if any(k=='peer_version' for k,d in logs[0]):break
                await asyncio.sleep(.01)
            assert any(k=='peer_version' for k,d in logs[0])
            from src import __version__
            assert modern_caps(a.peers[b.sid].caps) and a.peers[b.sid].version==__version__
            info=event('client_info',b.sid,a.sid,version='2.4.0',features=['version_notice'])
            assert await b._send_to(b.peers[a.sid],info)
            assert logs[0][-2][0]=='peer_version' or any(k=='peer_version' and d['version']=='2.4.0' for k,d in logs[0])
            with pytest.raises(ProtocolError):encode(dict(info,command='echo no'))
        finally:await a.stop();await b.stop()
    asyncio.run(run())
