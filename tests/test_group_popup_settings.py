"""Configurable shared group cooldown: signed policy, roles, migration and UI."""
import asyncio
import copy
import time
import pytest
from src.identity import session_id
from src.security import CryptoIdentity, SecurityError
from src.features.collaboration import Room, group_key
from src.features.group_profile import (GroupStore, new_profile, signed, validate_profile, popup_permit,
    validate_permit, popup_interval, popup_remaining, POPUP_INTERVAL)
from src.networking.client import ChatClient
from src.networking.protocol import event, encode, ProtocolError
from src.networking.relay import RelayServer


@pytest.mark.parametrize('value', [True, False, 0, -60, 1, 59, 61, 86401, 86460, 60.0, '60', None])
def test_invalid_delays_refused_in_profile_update_and_signed_grant(value):
    identity=CryptoIdentity(session_id())
    profile=new_profile(identity,session_id(),'Team',[identity.contact_id,session_id()])
    with pytest.raises(SecurityError): validate_profile(signed(identity,dict(profile,popup_interval=value)))
    with pytest.raises(ProtocolError): encode(event('group_update',session_id(),session_id(),room=profile['id'],revision=1,changes={'popup_interval':value}))
    profile=signed(identity,dict(profile,popup_at=time.time(),popup_id=session_id(),popup_sender=identity.contact_id))
    permit=popup_permit(identity,profile)
    # Even an otherwise correctly signed grant cannot carry an out-of-range delay.
    from src.features.group_profile import PERMIT_DOMAIN
    permit=signed(identity,dict(permit,interval=value),PERMIT_DOMAIN)
    with pytest.raises(SecurityError): validate_permit(permit,profile,identity.contact_id)


def test_existing_210_group_signatures_restore_with_default_then_save_new_setting(tmp_path):
    identity=CryptoIdentity(session_id(),tmp_path)
    profile=new_profile(identity,session_id(),'Groupe existant',[identity.contact_id,session_id()])
    del profile['popup_interval'];profile=signed(identity,profile)
    original=copy.deepcopy(profile)
    store=GroupStore(identity);store.save(profile,'joined')
    restored=GroupStore(identity)
    assert not restored.error and restored.records[profile['id']]['profile']==original
    assert popup_interval(original)==POPUP_INTERVAL
    modern=signed(identity,dict(profile,popup_interval=5*60,revision=2))
    restored.save(modern,'joined',1)
    assert GroupStore(identity).records[profile['id']]['profile']['popup_interval']==5*60


def test_delay_change_recalculates_last_popup_and_grant_handles_reordered_state(tmp_path,monkeypatch):
    import src.networking.groups as group_network
    owner=ChatClient('Owner42',security_root=tmp_path/'owner',discovery=False,tcp_port=0)
    receiver_identity=CryptoIdentity(session_id(),tmp_path/'receiver')
    receiver_identity.remember(owner.contact_id,owner.identity.public)
    receiver=GroupStore(receiver_identity)
    clock=[time.time()];monkeypatch.setattr(group_network.time,'time',lambda:clock[0])
    profile=new_profile(owner.identity,session_id(),'Réglable',[owner.contact_id,receiver_identity.contact_id])
    room=Room(profile['id'],profile['name'],profile['owner'],tuple(profile['members']),state='joined',joined={owner.contact_id},profile=profile)
    owner.rooms[room.id]=room;owner.group_store.save(profile,'joined')
    first=owner.reserve_group_popup(room,owner.contact_id,session_id())
    receiver.save(room.profile,'joined');receiver.receive_popup(room.id,first)
    clock[0]+=120
    assert popup_remaining(room.profile)==40*60
    assert owner.commit_group_update(room,owner.contact_id,room.profile['revision'],{'popup_interval':60}) is not None
    assert popup_remaining(room.profile)==0
    second=owner.reserve_group_popup(room,owner.contact_id,session_id())
    # The card arrives before the group-state update: its signed policy must suffice.
    assert popup_interval(receiver.records[room.id]['profile'])==POPUP_INTERVAL
    receiver.receive_popup(room.id,second)
    with pytest.raises(SecurityError): receiver.receive_popup(room.id,second)
    assert owner.commit_group_update(room,owner.contact_id,room.profile['revision'],{'popup_interval':86400}) is not None
    receiver.save(room.profile,'joined')
    assert popup_remaining(room.profile)==86400
    assert GroupStore(receiver_identity).records[room.id]['profile']['popup_interval']==86400
    clock[0]+=86399
    with pytest.raises(SecurityError): owner.reserve_group_popup(room,owner.contact_id,session_id())
    clock[0]+=1
    third=owner.reserve_group_popup(room,owner.contact_id,session_id())
    receiver.receive_popup(room.id,third)
    assert third['interval']==86400
    forged=dict(third,interval=60)
    with pytest.raises(SecurityError): validate_permit(forged,room.profile,owner.contact_id)


@pytest.mark.parametrize('relay',[False,True])
def test_admin_delay_change_is_shared_signed_persisted_and_member_cannot_change(relay,tmp_path):
    async def until(test):
        for _ in range(600):
            if test(): return
            await asyncio.sleep(.01)
        raise AssertionError('Délai de groupe non synchronisé')
    async def run():
        server=RelayServer() if relay else None
        options=dict(relay_host='127.0.0.1',relay_port=await server.start(port=0),tls=False) if server else {}
        logs=[[],[],[]]
        clients=[ChatClient(n,lambda k,d,i=i:logs[i].append((k,d)),security_root=tmp_path/str(i),discovery=False,tcp_port=0,**options)
                 for i,n in enumerate(['Owner42','Admin42','Member42'])]
        a,b,c=clients
        try:
            await asyncio.gather(*(x.start() for x in clients))
            for x in clients:
                for y in clients:
                    if x is not y:x.observe(y.hello(),'127.0.0.1')
            gid=await a.create_group('Délai partagé',[b.contact_id,c.contact_id])
            await b.answer_group(gid,True);await c.answer_group(gid,True)
            assert await a.update_group(gid,{'promote':b.contact_id})
            assert not await c.update_group(gid,{'popup_interval':60})
            assert await b.update_group(gid,{'popup_interval':300})
            await until(lambda:all(x.rooms[gid].profile['popup_interval']==300 for x in clients))
            for x in clients:
                assert GroupStore(x.identity).records[gid]['profile']['popup_interval']==300
            card=dict(title='On souffle ?',text='Au café !',emoji='1f44d')
            assert await b.send('card_invite',group_key(gid),**card)
            assert await c.send('card_invite',group_key(gid),**card) is None
            assert '5 minutes' in [d['text'] for k,d in logs[2] if k=='notice'][-1] or any('5 minutes' in d['text'] for k,d in logs[0] if k=='notice')
            assert await a.update_group(gid,{'popup_interval':60})
            await until(lambda:all(x.rooms[gid].profile['popup_interval']==60 for x in clients))
            assert await c.send('card_invite',group_key(gid),**card) is None
            with pytest.raises(ProtocolError):encode(event('card_invite',a.sid,'general',**card))
        finally:
            await asyncio.gather(*(x.stop() for x in clients))
            if server:await server.stop()
    asyncio.run(run())


def test_group_settings_ui_permissions_and_countdown_follow_setting():
    from PySide6.QtWidgets import QApplication
    from src.ui.groups import GroupManager,GroupWindow
    qt=QApplication.instance() or QApplication([])
    owner,admin,member=session_id(),session_id(),session_id()
    room=dict(id=session_id(),name='Team',owner=owner,members=[owner,admin,member],admins=[admin],joined=[owner,admin,member],left=[],state='joined',popup_at=time.time()-120,popup_interval=300)
    managers=[GroupManager(room,cid,{},[]) for cid in (owner,admin,member)]
    view=GroupWindow(room,[],'assets/icons/fortytchat.svg')
    try:
        for manager in managers[:2]:
            values=[];manager.changed.connect(values.append)
            assert manager.popup_delay.value()==5 and manager.popup_delay.isEnabled()
            manager.popup_delay.setValue(1);manager.popup_delay_button.click()
            assert values==[{'popup_interval':60}]
        assert managers[2].popup_delay.value()==5 and not managers[2].popup_delay.isEnabled() and not managers[2].popup_delay_button.isEnabled()
        assert not view.composer.card_button.isEnabled() and '5 min' in view.composer.card_button.toolTip()
        view.set_group(dict(room,popup_interval=60))
        assert view.composer.card_button.isEnabled() and '1 min' in view.composer.card_button.toolTip()
        view.set_group(dict(room,popup_interval=86400))
        assert not view.composer.card_button.isEnabled() and '1440 min' in view.composer.card_button.toolTip()
        assert managers[0].popup_delay.minimum()==1 and managers[0].popup_delay.maximum()==1440
    finally:
        for widget in [*managers,view]:widget.close();widget.deleteLater()
        qt.processEvents()
