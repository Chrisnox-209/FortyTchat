"""Real archived releases share E2EE routes with the current implementation."""
import asyncio
import importlib
import io
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import pytest
from src.networking.client import ChatClient
from src.features.collaboration import group_key
from src.features.cards import answer_card


@pytest.fixture(scope='module',params=[('1.8.1','5244fc7'),('2.0.0','4a5238e'),('2.1.0','cb2a586'),('2.2.0','e80222a')])
def legacy(request,tmp_path_factory):
    version,commit=request.param
    repo=Path(os.environ.get('FORTYTCHAT_COMPAT_REPO',Path(__file__).resolve().parents[1]))
    result=subprocess.run(['git','archive',commit,'src'],cwd=repo,capture_output=True)
    if result.returncode:pytest.skip('Historique Git de la version '+version+' indisponible dans ce checkout')
    root=tmp_path_factory.mktemp('legacy-'+version);package=root/('legacy_'+version.replace('.','_'));package.mkdir()
    with tarfile.open(fileobj=io.BytesIO(result.stdout)) as archive:
        for member in archive.getmembers():
            parts=Path(member.name).parts
            if member.isfile() and parts[0]=='src' and '..' not in parts:
                destination=package.joinpath(*parts[1:]);destination.parent.mkdir(parents=True,exist_ok=True)
                destination.write_bytes(archive.extractfile(member).read())
    sys.path.insert(0,str(root))
    module=importlib.import_module(package.name+'.networking.client')
    relay=importlib.import_module(package.name+'.networking.relay')
    try:store=importlib.import_module(package.name+'.features.group_profile').GroupStore
    except ModuleNotFoundError:store=None
    return version,module.ChatClient,relay.RelayServer,store


async def until(predicate):
    for _ in range(800):
        if predicate():return
        await asyncio.sleep(.01)
    raise AssertionError('Échange entre versions incomplet')


@pytest.mark.parametrize('relay',[False,True])
def test_real_older_release_chat_files_groups_popups_and_legacy_polls(legacy,relay,tmp_path):
    async def run():
        version,LegacyClient,LegacyRelay,LegacyStore=legacy
        server=LegacyRelay() if relay else None
        options=dict(relay_host='127.0.0.1',relay_port=await server.start(port=0),tls=False) if relay else {}
        logs=[[],[]]
        new=ChatClient('New42',lambda k,d:logs[0].append((k,d)),security_root=tmp_path/'new',discovery=False,tcp_port=0,**options)
        old=LegacyClient('Old42',lambda k,d:logs[1].append((k,d)),security_root=tmp_path/'old',discovery=False,tcp_port=0,**options)
        clients=[new,old]
        def frames(index,kind):return [d['frame'] for k,d in logs[index] if k=='event' and d['frame']['type']==kind]
        try:
            await asyncio.gather(*(c.start() for c in clients))
            if relay:await until(lambda:len(new.peers)==len(old.peers)==1)
            else:new.observe(old.hello(),'127.0.0.1');old.observe(new.hello(),'127.0.0.1')
            assert old.peers[new.sid].contact_id==new.contact_id
            new.set_screen_locked(True)
            if relay:await until(lambda:old.peers[new.sid].status=='away')
            else:old.observe(new.hello(),'127.0.0.1')
            assert old.peers[new.sid].status=='away'
            new.set_screen_locked(False)
            if relay:await until(lambda:old.peers[new.sid].status=='online')
            else:old.observe(new.hello(),'127.0.0.1')
            assert old.peers[new.sid].status=='online'
            mid=await new.send('message','general',text='NEW_TO_OLD_SECRET')
            assert frames(1,'message')[-1]['id']==mid
            await old.send('message',new.sid,text='OLD_TO_NEW_SECRET')
            assert frames(0,'message')[-1]['text']=='OLD_TO_NEW_SECRET'
            offer=await new.offer_file(old.sid,memory=b'MIXED_FILE_SECRET',name='test.txt')
            target=tmp_path/'received.txt';await old.accept_file(offer,new.sid,target)
            await until(target.exists);assert target.read_bytes()==b'MIXED_FILE_SECRET'
            if LegacyStore is None:
                private=dict(title='Compatible',text='Une pause ?',emoji='1f44d')
                assert await new.send('card_invite',old.sid,**private)
                assert frames(1,'card_invite')
                return
            gid=await new.create_group('Versions ensemble',[old.contact_id]);assert gid in old.rooms
            await old.answer_group(gid,True)
            key=group_key(gid);await new.send('message',key,text='GROUP_NEW_TO_OLD')
            assert frames(1,'message')[-1]['text']=='GROUP_NEW_TO_OLD'
            await old.send('message',key,text='GROUP_OLD_TO_NEW')
            assert frames(0,'message')[-1]['text']=='GROUP_OLD_TO_NEW'
            assert await new.update_group(gid,{'name':'Toujours ensemble'})
            assert old.rooms[gid].name=='Toujours ensemble'
            assert await new.update_group(gid,{'popup_interval':300})
            assert not LegacyStore(old.identity).error
            card=dict(title='On souffle ?',text='Compatible !',emoji='1f44d')
            assert await new.send('card_invite',key,**card)
            assert frames(1,'card_invite')
            request=frames(1,'card_invite')[-1]['id']
            assert await old.send('card_reply',new.sid,request=request,answer='accepted',**answer_card('accepted'))
            assert frames(0,'card_reply')[-1]['request']==request
            # The old creator's original profile and permits remain accepted.
            old_gid=await old.create_group('Créé avant',[new.contact_id]);assert old_gid in new.rooms
            await new.answer_group(old_gid,True)
            assert await old.send('card_invite',group_key(old_gid),**card)
            assert frames(0,'card_invite')[-1]['group']==old_gid
            # Old surveys keep their original protocol. New UI can cast one final local vote.
            poll=await old.send('poll_create','general',question='Ancien sondage ?',options=['Café','Thé'],multiple=True)
            assert poll in new.polls.items and not new.polls.items[poll]['final']
            assert await new.send('poll_vote','general',poll=poll,option=0)
            assert old.polls.items[poll]['votes'][new.sid]==[0]
            assert await new.send('poll_vote','general',poll=poll,option=1) is None
            await old.send('poll_vote','general',poll=poll,options=[1])
            assert new.polls.items[poll]['votes'][old.sid]==[1]
            await old.send('poll_vote','general',poll=poll,options=[])
            assert old.sid not in new.polls.items[poll]['votes']
            modern=await new.send('poll_create','general',question='Définitif ?',options=['Oui','Non'])
            assert modern not in old.polls.items
            await new.send('message',key,text='Le groupe continue après le sondage')
            assert frames(1,'message')[-1]['text']=='Le groupe continue après le sondage'
        finally:
            await asyncio.gather(*(c.stop() for c in clients))
            if server:await server.stop()
    asyncio.run(run())
