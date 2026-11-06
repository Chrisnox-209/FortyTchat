import json
import os
from pathlib import Path
import subprocess
import sys

from src.config import ConfigStore
from src.features.cards import CardTemplates
from src.identity import session_id

ROOT = Path(__file__).resolve().parents[1]


def test_uninstall_stops_only_installed_app_preserves_profile_then_purges(tmp_path):
    data, config, bindir = tmp_path/'data space', tmp_path/'config', tmp_path/'bin'
    env = {**os.environ, 'XDG_DATA_HOME':str(data), 'XDG_CONFIG_HOME':str(config)}
    installed = subprocess.run(['sh',str(ROOT/'install.sh'),'--use-current-python','--no-launch','--bin-dir',str(bindir)],
                               env=env,capture_output=True,text=True,timeout=30)
    assert installed.returncode == 0, installed.stderr
    app = data/'fortytchat/app'
    assert (app/'uninstall.sh').is_file() and not (app/'update.sh').exists()
    store = ConfigStore(config/'fortytchat')
    cid = session_id()
    store.preferences.friends[cid] = 'RememberedFriend42'
    store.preferences.nickname = 'KeptNick42'
    store.save()
    templates = CardTemplates(config/'fortytchat')
    templates.save({'title': 'Modèle conservé', 'text': 'On bouge ?', 'emoji': '1f44d'}, 'invite')
    unrelated = config/'autostart/unrelated.desktop'
    unrelated.write_text('keep me')
    outside = tmp_path/'other-app'
    outside.mkdir()
    (outside/'main.py').write_text('import time\ntime.sleep(30)\n')
    (app/'main.py').write_text('import time\ntime.sleep(30)\n')
    own = subprocess.Popen([sys.executable,str(app/'main.py')],env=env)
    other = subprocess.Popen([sys.executable,str(outside/'main.py')],env=env)
    try:
        removed = subprocess.run(['sh',str(ROOT/'uninstall.sh'),'--bin-dir',str(bindir)],
                                 env=env,capture_output=True,text=True,timeout=20)
        assert removed.returncode == 0, removed.stderr
        own.wait(timeout=3)
        assert other.poll() is None
        assert not (data/'fortytchat').exists() and not (bindir/'fortytchat').exists()
        assert not (config/'autostart/fr.fortytchat.FortyTchat.desktop').exists()
        assert not (data/'applications/fr.fortytchat.FortyTchat.desktop').exists()
        restored = ConfigStore(config/'fortytchat')
        assert restored.preferences.friends[cid] == 'RememberedFriend42'
        assert restored.preferences.nickname == 'KeptNick42' and not restored.preferences.autostart
        assert CardTemplates(config/'fortytchat').items[0]['title'] == 'Modèle conservé'
        assert unrelated.read_text() == 'keep me'
        purged = subprocess.run(['sh',str(ROOT/'uninstall.sh'),'--purge','--bin-dir',str(bindir)],
                                env=env,capture_output=True,text=True,timeout=20)
        assert purged.returncode == 0, purged.stderr
        assert not (config/'fortytchat').exists() and unrelated.exists()
    finally:
        for process in (own,other):
            if process.poll() is None:
                process.terminate()
            process.wait(timeout=3)
