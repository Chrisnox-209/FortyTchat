import json
import time
import uuid
from pathlib import Path
from types import SimpleNamespace

from PySide6.QtCore import QMimeData,Qt,QEvent
from PySide6.QtGui import QImage,QColor,QKeyEvent
from PySide6.QtWidgets import QApplication,QInputDialog,QMessageBox,QListWidget,QPushButton,QLabel
from src.app import Application
from src.config import ConfigStore,Preferences
from src.features.collaboration import group_key
from src.memory import Conversations,Message
from src.networking.settings import NetworkConfig
from src.ui.images import ImageSendDialog,decode_preview,encode_preview,encoded_image
from src.ui.groups import GroupEditor
from src.ui.polls import PollEditor,PollCard

ROOT=Path(__file__).resolve().parents[1]


def until(qt,predicate,timeout=6):
    end=time.monotonic()+timeout
    while not predicate():
        qt.processEvents()
        if time.monotonic()>end:raise AssertionError('Parcours de collaboration Qt non terminé')
        time.sleep(.01)
    qt.processEvents()


def test_memory_changes_update_quotes_and_deleted_messages_cannot_revive():
    history=Conversations()
    base=Message('a','Alice42','Before',id='one')
    quote=Message('b','Bob42','Reply',id='two',reply_to='one',reply_text='Before')
    history.append('general',base);history.append('general',quote)
    history.change('general','one','After')
    assert base.edited and quote.reply_text=='After'
    history.react('general','one','b','👍','Bob42')
    history.change('general','one',delete=True)
    assert base.deleted and not base.reactions and base.text==quote.reply_text=='Message supprimé'
    assert history.change('general','one','Revive') is None
    assert history.react('general','one','b','👍','Bob42') is None


def test_image_preview_and_clipboard_cancel_are_bounded_and_strip_metadata():
    qt=QApplication.instance() or QApplication([])
    image=QImage(640,360,QImage.Format.Format_RGB32);image.fill(QColor('#7891ff'));image.setText('PRIVATE_METADATA','SHOULD_NOT_TRAVEL')
    preview=encode_preview(image)
    decoded=decode_preview(preview)
    assert not decoded.isNull() and decoded.width()<=320 and decoded.height()<=320
    assert decode_preview('INVALID').isNull()
    # A valid JPEG payload with oversized dimensions must be rejected before decoding.
    from src.ui.images import clean_image
    import base64
    assert decode_preview(base64.b64encode(encoded_image(image,'JPEG',10)).decode()).isNull()
    editor=ImageSendDialog(image)
    payloads=[];editor.submitted.connect(payloads.append)
    editor.submit()
    assert len(payloads)==1 and payloads[0]['memory'].startswith(b'\x89PNG')
    assert b'PRIVATE_METADATA' not in payloads[0]['memory'] and b'SHOULD_NOT_TRAVEL' not in payloads[0]['memory']
    assert 'path' not in payloads[0]
    cancelled=ImageSendDialog(image);cancelled.submitted.connect(payloads.append);cancelled.reject()
    assert len(payloads)==1
    editor.deleteLater();cancelled.deleteLater();qt.processEvents()


def test_new_profile_block_preferences_are_validated_bounded_and_private(tmp_path):
    store=ConfigStore(tmp_path)
    store.preferences.blocked_contacts={'a'*32:'Blocked42'};store.save()
    assert ConfigStore(tmp_path).preferences.blocked_contacts=={'a'*32:'Blocked42'}
    data=json.loads(store.path.read_text());data['blocked_contacts']={'bad':'Invalid42','b'*32:'<invalid>','c'*32:'Valid42'}
    store.path.write_text(json.dumps(data));assert ConfigStore(tmp_path).preferences.blocked_contacts=={'c'*32:'Valid42'}
    assert not (store.path.stat().st_mode & 0o077)


def test_real_controllers_create_join_edit_delete_poll_clipboard_and_block(tmp_path,monkeypatch):
    qt=QApplication.instance() or QApplication([]);qt.setQuitOnLastWindowClosed(False)
    monkeypatch.setenv('XDG_CONFIG_HOME',str(tmp_path/'config'))
    monkeypatch.setattr(NetworkConfig,'load',classmethod(lambda cls,path:NetworkConfig(mode='relay',relay_host='')))
    monkeypatch.setattr(QMessageBox,'question',lambda *a,**kw:QMessageBox.StandardButton.Yes)
    apps=[Application(ROOT,SimpleNamespace(dev='collab-'+uuid.uuid4().hex[:10],background=False,insecure_local_relay=None)) for _ in range(2)]
    a,b=apps
    try:
        until(qt,lambda:a.worker.client.port>0 and b.worker.client.port>0)
        ac,bc=a.worker.client,b.worker.client
        a.worker.loop.call_soon_threadsafe(ac.observe,bc.hello(),'127.0.0.1')
        b.worker.loop.call_soon_threadsafe(bc.observe,ac.hello(),'127.0.0.1')
        until(qt,lambda:bc.sid in a.window.peers and ac.sid in b.window.peers)
        editor=a.show_group_editor()
        assert isinstance(editor,GroupEditor)
        editor.name.setText('Notre projet')
        editor.members.item(0).setCheckState(Qt.CheckState.Checked)
        editor.submit()
        until(qt,lambda:bool(a.group_windows) and bool(b.groups))
        key=next(iter(a.group_windows));gid=key[6:]
        assert b.groups[key]['state']=='pending' and not b.group_windows
        waiting=a.group_windows[key]
        waiting.input.setPlainText('GROUP_QT_WAITING');waiting.send_button.click()
        until(qt,lambda:any(m.text=='GROUP_QT_WAITING' for m in a.history.get(key)))
        assert next(m for m in a.history.get(key) if m.text=='GROUP_QT_WAITING').delivery_local
        assert not b.history.get(key)
        b.open_private(key)
        until(qt,lambda:key in b.group_windows and b.groups[key]['state']=='joined')
        assert not b.history.get(key)
        av,bv=a.group_windows[key],b.group_windows[key]
        assert not av.friend_button.isVisible() and av.composer.poll_button.isVisible()
        av.input.setPlainText('GROUP_QT_ORIGINAL');av.send_button.click()
        until(qt,lambda:any(m.text=='GROUP_QT_ORIGINAL' for m in b.history.get(key)))
        original=next(m for m in a.history.get(key) if m.text=='GROUP_QT_ORIGINAL')
        monkeypatch.setattr(QInputDialog,'getMultiLineText',lambda *args:('GROUP_QT_EDITED',True))
        av.chat.edit_requested.emit(original.id)
        until(qt,lambda:b.history.find(key,original.id).text=='GROUP_QT_EDITED')
        assert a.history.find(key,original.id).edited
        assert any(label.text()=='Modifié' for label in bv.chat.findChildren(QLabel))
        av.chat.delete_requested.emit(original.id)
        until(qt,lambda:b.history.find(key,original.id).deleted)
        assert a.history.find(key,original.id).text=='Message supprimé'
        poll=a.show_poll_editor(key)
        poll.question.setText('On choisit quoi ?');poll.options[0].setText('Café');poll.options[1].setText('Thé');poll.submit()
        until(qt,lambda:bool(bv.chat.poll_cards))
        pid=next(iter(bv.chat.poll_cards))
        bv.chat.poll_cards[pid].options[1].click()
        until(qt,lambda:a.history.find(key,pid).poll['votes'].get(bc.sid)==1)
        assert bv.chat.poll_cards[pid].options[1].isChecked()
        assert av.chat.poll_cards[pid].total.text().startswith('1/2 participant')
        # A general poll is also a real encrypted chat event.
        general=a.show_poll_editor('general');general.question.setText('General ?')
        general.options[0].setText('Oui');general.options[1].setText('Non');general.submit()
        until(qt,lambda:bool(b.window.chat.poll_cards))
        image=QImage(320,180,QImage.Format.Format_RGB32);image.fill(QColor('#ffbb88'))
        mime=QMimeData();mime.setImageData(image)
        assert av.input.canInsertFromMimeData(mime)
        av.input.insertFromMimeData(mime)
        image_dialog=next(d for d in a.collaboration_dialogs if isinstance(d,ImageSendDialog))
        assert not a.file_transfers and not b.file_transfers
        image_dialog.reject();qt.processEvents()
        assert not a.file_transfers
        if qt.platformName() in ('offscreen','minimal'):
            qt.clipboard().setImage(image)
            QApplication.sendEvent(av.input,QKeyEvent(QEvent.Type.KeyPress,Qt.Key.Key_V,Qt.KeyboardModifier.ControlModifier))
            qt.clipboard().clear()
        else:
            av.input.insertFromMimeData(mime)
        image_dialog=next(d for d in a.collaboration_dialogs if isinstance(d,ImageSendDialog))
        image_dialog.submit()
        until(qt,lambda:bool(b.file_transfers))
        metadata=next(iter(b.file_transfers.values()))
        assert metadata['target']==key and metadata.get('preview') and 'path' not in metadata
        assert bv.chat.file_cards[metadata['id']][1].preview.isVisible()
        target=tmp_path/'capture.png'
        b.worker.accept_file(metadata['id'],ac.sid,str(target)).result(4)
        until(qt,lambda:metadata['status']=='complete')
        assert target.read_bytes().startswith(b'\x89PNG')
        av.chat.delete_requested.emit(metadata['id'])
        until(qt,lambda:b.history.find(key,metadata['id']).deleted)
        assert target.exists() and b.history.find(key,metadata['id']).attachment is None
        assert not b.private_windows  # Neither a group nor a pasted capture creates an accidental DM.
        b.block_profile(a.preferences.contact_id,True)
        until(qt,lambda:ac.sid in bc.blocked)
        assert a.preferences.contact_id in ConfigStore(b.store.root).preferences.blocked_contacts
        private=b.open_private(ac.sid)
        assert not private.input.isEnabled()
        b.show_settings()
        manager=b.show_blocked_contacts()
        listing=manager.findChild(QListWidget)
        assert listing.count()==1
        listing.setCurrentRow(0)
        next(control for control in manager.findChildren(QPushButton) if control.text()=='Débloquer la sélection').click()
        until(qt,lambda:ac.sid not in bc.blocked)
        assert not ConfigStore(b.store.root).preferences.blocked_contacts and private.input.isEnabled()
        # No message, question or source image is written to the profile.
        for app in apps:
            for path in app.store.root.glob('*.json'):
                assert all(secret not in path.read_text() for secret in ('GROUP_QT','On choisit quoi','capture-'))
    finally:
        for app in apps:
            app.cleanup();app.window.close();app.deleteLater()
        qt.processEvents()


import pytest
import subprocess
import os
import sys


@pytest.mark.parametrize('scale',['1','1.5','2'])
def test_group_and_poll_controls_remain_readable_on_small_scaled_screens(scale):
    code='''
from PySide6.QtWidgets import QApplication,QLabel,QScrollArea
from src.ui.polls import PollEditor,PollCard
from src.ui.groups import GroupEditor,GroupWindow
from src.ui.theme import apply_theme
from src.memory import Message
qt=QApplication([])
for theme in ('dark','light'):
    apply_theme(qt,theme)
    editor=PollEditor();editor.resize(380,400);editor.show();qt.processEvents()
    page=editor.findChild(QScrollArea)
    assert page.horizontalScrollBar().maximum()==0
    for text in editor.findChildren(QLabel):
        if text.wordWrap() and text.text():assert text.height()>=text.heightForWidth(text.width()),text.text()
    page.ensureWidgetVisible(editor.options[-1]);qt.processEvents()
    assert editor.options[-1].height()>=editor.options[-1].sizeHint().height()
    room=dict(id='a'*32,name='Un groupe avec un nom long',owner='b'*32,members=['b'*32,'c'*32],joined=['b'*32,'c'*32],left=[],state='joined')
    view=GroupWindow(room,[],'');view.resize(480,540);view.chat.session_id='b'*32;view.show()
    poll=dict(id='d'*32,question='Une question '+('longue '*20),options=['A'*80,'B'*80],audience=['b'*32,'c'*32],votes={})
    view.chat.append(Message('x','Demo42',poll['question'],id=poll['id'],poll=poll))
    for _ in range(6):qt.processEvents()
    assert view.chat.scroll.horizontalScrollBar().maximum()==0
    card=view.chat.poll_cards[poll['id']]
    assert card.width()<=view.chat.scroll.viewport().width()
    for control in card.options:
        assert control.width()<=card.width() and control.height()>=control.sizeHint().height()
        assert control.toolTip().startswith(('A'*80,'B'*80))
    view.close();editor.close();qt.processEvents()
'''
    result=subprocess.run([sys.executable,'-c',code],cwd=ROOT,env={**os.environ,'QT_QPA_PLATFORM':'offscreen','QT_SCALE_FACTOR':scale},capture_output=True,text=True,timeout=20)
    assert result.returncode==0,result.stdout+result.stderr


def test_real_group_manager_custom_popup_multi_poll_mentions_and_file_batch(tmp_path,monkeypatch):
    from src.ui.groups import GroupManager
    from src.features.cards import answer_card
    from PySide6.QtCore import QUrl
    qt=QApplication.instance() or QApplication([]);qt.setQuitOnLastWindowClosed(False)
    monkeypatch.setenv('XDG_CONFIG_HOME',str(tmp_path/'config'))
    monkeypatch.setattr(NetworkConfig,'load',classmethod(lambda cls,path:NetworkConfig(mode='relay',relay_host='')))
    monkeypatch.setattr(QMessageBox,'question',lambda *args,**kwargs:QMessageBox.StandardButton.Yes)
    apps=[Application(ROOT,SimpleNamespace(dev='extended-'+uuid.uuid4().hex[:10],background=False,insecure_local_relay=None)) for _ in range(2)]
    a,b=apps
    try:
        until(qt,lambda:a.worker.client.port>0 and b.worker.client.port>0)
        ac,bc=a.worker.client,b.worker.client
        a.worker.loop.call_soon_threadsafe(ac.observe,bc.hello(),'127.0.0.1')
        b.worker.loop.call_soon_threadsafe(bc.observe,ac.hello(),'127.0.0.1')
        until(qt,lambda:bc.sid in a.window.peers and ac.sid in b.window.peers)
        gid=a.worker.submit(ac.create_group('Groupe des tests',[bc.contact_id])).result(4)
        key=group_key(gid)
        until(qt,lambda:key in b.groups and key in a.group_windows)
        b.open_group(gid)
        until(qt,lambda:key in b.group_windows and b.groups[key]['state']=='joined')
        av,bv=a.group_windows[key],b.group_windows[key]
        manager=a.manage_group(gid);assert isinstance(manager,GroupManager)
        manager.name.setText('Le nouveau nom');manager.rename_button.click()
        until(qt,lambda:b.groups[key]['name']=='Le nouveau nom')
        image=QImage(128,128,QImage.Format.Format_RGB32);image.fill(QColor('#bbccff'))
        from src.ui.avatar_image import encode_photo
        photo=encode_photo(image)
        manager.changed.emit({'photo':photo})
        until(qt,lambda:bv.avatar.image_data==photo)
        assert av.composer.card_button.isVisible() and not a.window.composer.card_button.isVisible()
        until(qt,lambda:bc.nickname in av.input.mention_model.stringList())
        av.input.setPlainText('@'+bc.nickname+' voici mon code :\n```c\nprintf("safe");\n```');av.send_button.click()
        until(qt,lambda:any(m.mentions==[bc.contact_id] for m in b.history.get(key)))
        assert bv.chat.findChild(__import__('PySide6.QtWidgets',fromlist=['QPlainTextEdit']).QPlainTextEdit).isReadOnly()
        poll=a.show_poll_editor(key);poll.question.setText('Les boissons ?')
        poll.options[0].setText('Café');poll.options[1].setText('Thé');poll.multiple.setChecked(True);poll.submit()
        until(qt,lambda:bool(bv.chat.poll_cards))
        pid=next(iter(bv.chat.poll_cards));card=bv.chat.poll_cards[pid]
        card.options[0].click();card.options[1].click();card.apply.click()
        until(qt,lambda:a.history.find(key,pid).poll['votes'].get(bc.sid)==[0,1])
        editor=a.show_card_editor(key);assert editor is not None
        editor.submitted.emit(dict(title='Tous ensemble',text='Une pause ?',emoji='1f44d'))
        until(qt,lambda:bool(b.popups))
        request,popup=next(iter(b.popups.items()))
        assert popup[0]==ac.sid
        b.send_card_answer(ac.sid,request,'card_invite','accepted')
        until(qt,lambda:bool(a.reply_popups))
        until(qt,lambda:not av.composer.card_button.isEnabled())
        assert '42' in av.composer.card_button.toolTip()
        assert a.show_card_editor('general') is None
        # The actual input receives two file URLs; queue worker emits separate E2EE offers.
        files=[tmp_path/'one.txt',tmp_path/'two.txt']
        for path in files:path.write_text(path.stem)
        mime=QMimeData();mime.setUrls([QUrl.fromLocalFile(str(path)) for path in files])
        av.input.insertFromMimeData(mime)
        until(qt,lambda:len([m for m in b.history.get(key) if m.attachment])==2)
        assert all(path.read_text()==path.stem for path in files)
        manager.reject()
    finally:
        for app in apps:app.cleanup();app.window.close();app.deleteLater()
        qt.processEvents()
