import asyncio
import time
from pathlib import Path
from PySide6.QtCore import Qt, QMimeData, QUrl, QPointF
from PySide6.QtGui import QDragEnterEvent, QDropEvent
from PySide6.QtWidgets import QApplication, QPlainTextEdit, QLabel
from src.features.text import mention_ids, text_parts
from src.ui.composer import ComposeEdit
from src.ui.message_text import MessageText
from src.ui.polls import PollCard
from src.networking.client import ChatClient
from src.features.wizz import TokenBucket


def test_mentions_exclude_code_and_code_is_plain_selectable_text():
    qt=QApplication.instance() or QApplication([])
    peers=[{'nick':'Alice42','contact_id':'a'*32},{'nick':'Bob42','contact_id':'b'*32}]
    text='Hey @Alice42 👋\n```python\nprint("<script>")\n# @Bob42\n```\n@alice42'
    assert mention_ids(text,peers)==['a'*32]
    assert [p[0] for p in text_parts(text)]==['text','code','text']
    view=MessageText(text,True)
    code=view.findChild(QPlainTextEdit)
    assert code.isReadOnly() and '<script>' in code.toPlainText() and '@Bob42' in code.toPlainText()
    assert any('Tu es mentionné' in label.text() for label in view.findChildren(QLabel))
    view.deleteLater();qt.processEvents()


def test_input_completion_and_file_drop_copy_only(tmp_path):
    qt=QApplication.instance() or QApplication([])
    edit=ComposeEdit();edit.show();edit.setFocus();qt.processEvents()
    edit.set_mentions(['Bob42','Alice42'])
    edit.setPlainText('Salut @Al')
    cursor=edit.textCursor();cursor.movePosition(cursor.MoveOperation.End);edit.setTextCursor(cursor)
    assert edit.mention_prefix()=='Al'
    edit.insert_mention('Alice42')
    assert edit.toPlainText()=='Salut @Alice42 '
    path=tmp_path/'document.txt';path.write_text('Keep original')
    mime=QMimeData();mime.setUrls([QUrl.fromLocalFile(str(path))])
    received=[];edit.files_dropped.connect(received.append)
    enter=QDragEnterEvent(edit.rect().center(),Qt.DropAction.CopyAction|Qt.DropAction.MoveAction,mime,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier)
    edit.dragEnterEvent(enter)
    assert enter.isAccepted() and enter.dropAction()==Qt.DropAction.CopyAction
    drop=QDropEvent(QPointF(edit.rect().center()),Qt.DropAction.CopyAction|Qt.DropAction.MoveAction,mime,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier)
    edit.dropEvent(drop)
    assert drop.isAccepted() and drop.dropAction()==Qt.DropAction.CopyAction
    assert received==[[str(path)]] and path.read_text()=='Keep original'
    remote=QMimeData();remote.setUrls([QUrl('https://example.com/file')])
    event=QDropEvent(QPointF(10,10),Qt.DropAction.CopyAction,remote,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier)
    edit.dropEvent(event)
    assert not event.isAccepted() and received==[[str(path)]]
    edit.close();edit.deleteLater();qt.processEvents()


def test_multiple_poll_visual_results_and_explicit_selection_submit():
    qt=QApplication.instance() or QApplication([])
    poll=dict(id='1'*32,question='Une pause ?',options=['Café','Thé','Chocolat'],audience=['a','b','c'],multiple=True,votes={'a':[0,2],'b':[2]},deleted=False)
    card=PollCard(poll,'c');votes=[];card.voted.connect(votes.append)
    assert [bar.value() for bar in card.bars]==[50,0,100]
    card.options[0].click();card.options[1].click()
    assert not votes
    card.apply.click();assert votes==[[0,1]]
    card.options[0].click();card.options[1].click();card.apply.click();assert votes[-1]==[]
    card.deleteLater();qt.processEvents()


def test_ninth_queued_file_waits_for_slot_and_all_sources_remain(tmp_path):
    async def run():
        a=ChatClient('Sender42',discovery=False,tcp_port=0)
        b=ChatClient('Receiver42',discovery=False,tcp_port=0)
        queued=None
        try:
            await a.start();await b.start();a.observe(b.hello(),'127.0.0.1');b.observe(a.hello(),'127.0.0.1')
            b.peers[a.sid].bucket=TokenBucket(100,100)
            requests=[]
            for i in range(8): requests.append(await a.offer_file(b.sid,memory=b'file',name=f'{i}.txt'))
            source=tmp_path/'ninth.txt';source.write_text('Ninth stays on disk')
            queued=asyncio.create_task(a.offer_queued_file(b.sid,str(source)))
            await asyncio.sleep(.05);assert not queued.done() and len(a.files.outgoing)==8
            await b.decline_file(requests[0],a.sid)
            request=await asyncio.wait_for(queued,2)
            assert request and (request,a.sid) in b.files.incoming
            destination=tmp_path/'received.txt';await b.accept_file(request,a.sid,destination)
            for _ in range(400):
                if destination.exists():break
                await asyncio.sleep(.01)
            assert destination.read_text()==source.read_text()=='Ninth stays on disk'
        finally:
            if queued and not queued.done(): queued.cancel()
            await a.stop();await b.stop()
    asyncio.run(run())


# Separate processes exercise Qt's real scale setting and keep global GUI state isolated.
import subprocess
import os
import sys
import pytest


@pytest.mark.parametrize('scale', ['1','1.5','2'])
@pytest.mark.parametrize('theme', ['dark','light'])
def test_group_controls_and_rich_bubbles_remain_readable_at_small_sizes(scale,theme):
    code = '''
from PySide6.QtWidgets import QApplication,QPlainTextEdit
from PySide6.QtCore import Qt
from src.ui.theme import apply_theme
from src.ui.groups import GroupManager,GroupWindow
from src.memory import Message
app=QApplication([]);apply_theme(app, THEME)
owner,member='a'*32,'b'*32
room=dict(id='c'*32,name='Un nom très long pour la petite fenêtre du groupe',owner=owner,members=[owner,member],admins=[member],state='joined',joined=[owner,member],left=[],photo='',popup_at=0)
manager=GroupManager(room,owner,{owner:'Proprietaire42',member:'Administrateur42'},[])
manager.resize(390,500);manager.show()
window=GroupWindow(room,[Message('sender','Pote42','@Pote42 hello\\n```c\\n' + 'x'*100 + '\\n```')],'assets/icons/fortytchat.svg')
window.resize(480,660);window.show()
for _ in range(10):app.processEvents()
assert manager.width() <= 430
for control in (manager.name,manager.rename_button,manager.photo_button,manager.add_button,manager.promote):
    assert control.geometry().right() < manager.width()
    assert control.height() >= control.fontMetrics().height()
assert window.width()==480
editor=window.chat.findChild(QPlainTextEdit)
assert editor and editor.isReadOnly() and editor.width() < window.width()
for widget in (manager,window):widget.close()
'''.replace('THEME',repr(theme))
    result=subprocess.run([sys.executable,'-c',code],env={**os.environ,'QT_QPA_PLATFORM':'offscreen','QT_SCALE_FACTOR':scale},capture_output=True,text=True,timeout=20)
    assert result.returncode==0,result.stderr
