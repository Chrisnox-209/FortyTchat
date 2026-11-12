#!/usr/bin/env python3
"""Write an explicit, private diagnostic report; never read chat/profile secrets."""
import argparse
from datetime import datetime
from importlib import metadata
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time

SOURCE=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(SOURCE))
from src import __version__

KNOWN_PROCESSES={'gnome-shell','gnome-screensav','cinnamon-screen','mate-screensave',
                 'xfce4-screensav','light-locker','xscreensaver','i3lock','i3lock-color',
                 'xsecurelock','slock','swaylock','gtklock','xss-lock'}


def known_processes():
    """Only recognized names for this UID, never arguments or process environments."""
    result=set();deadline=time.monotonic()+.2
    try:
        with os.scandir('/proc') as entries:
            for entry in entries:
                if time.monotonic()>deadline:break
                if not entry.name.isdigit():continue
                try:
                    if entry.stat().st_uid!=os.getuid():continue
                    with open(Path(entry.path)/'comm') as stream:name=stream.read(64).strip()
                    if name in KNOWN_PROCESSES:result.add(name)
                except OSError:pass
    except OSError:pass
    return sorted(result)


def read_version(path):
    try:
        with path.open() as stream:text=stream.read(8192)
        found=re.search(r"__version__\s*=\s*['\"]([0-9]+\.[0-9]+\.[0-9]+)['\"]",text)
        return found[1] if found else 'inconnue'
    except (OSError,UnicodeError):return 'indisponible'


def context():
    data=os.environ.get('XDG_DATA_HOME','')
    data_home=Path(data) if data and Path(data).is_absolute() else Path.home()/'.local/share'
    try:qt_version=metadata.version('PySide6-Essentials')
    except metadata.PackageNotFoundError:qt_version='indisponible'
    os_name='indisponible'
    try:
        for line in Path('/etc/os-release').read_text().splitlines():
            if line.startswith('PRETTY_NAME='):os_name=line.split('=',1)[1].strip('"');break
    except OSError:pass
    return {'date':datetime.now().astimezone().isoformat(timespec='seconds'),
            'fortytchat_script':__version__,
            'fortytchat_installe_sur_disque':read_version(data_home/'fortytchat/app/src/__init__.py'),
            'python':platform.python_version(),'pyside6':qt_version,'systeme':os_name,
            'noyau':platform.release(),'architecture':platform.machine(),
            'bureau':{key:os.environ.get(key,'') for key in ('XDG_CURRENT_DESKTOP','XDG_SESSION_TYPE','XDG_SESSION_ID','DISPLAY','WAYLAND_DISPLAY')},
            'bus_session_configure':bool(os.environ.get('DBUS_SESSION_BUS_ADDRESS')),
            'outils_disponibles':{name:bool(shutil.which(name)) for name in ('gdbus','loginctl','gnome-screensaver-command','i3lock','xsecurelock','swaylock','light-locker')},
            'processus_connus_meme_utilisateur':known_processes(),
            'note':'Les processus et outils repérés ne prouvent pas à eux seuls un verrouillage. La version installée est lue sur disque, pas dans un processus déjà lancé.'}


def main():
    parser=argparse.ArgumentParser(description='Rapport FortyTchat de verrouillage, sans droits administrateur.')
    parser.add_argument('--duration',type=int,default=60,help='Durée de 5 à 120 secondes (défaut 60)')
    parser.add_argument('--output',type=Path,help='Fichier nouveau à créer (défaut : rapport unique dans ton dossier personnel)')
    args=parser.parse_args()
    if not 5<=args.duration<=120:parser.error('--duration : 5 à 120 secondes')
    try:
        if args.output:
            path=args.output.expanduser().absolute()
            fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|getattr(os,'O_NOFOLLOW',0),0o600)
        else:
            stamp=datetime.now().strftime('%Y%m%d-%H%M%S')
            fd,name=tempfile.mkstemp(prefix='FortyTchat-diagnostic-'+stamp+'-',suffix='.txt',dir=Path.home());path=Path(name)
    except OSError as error:
        print('Impossible de créer le rapport (aucun fichier existant n’est écrasé) : '+str(error),file=sys.stderr);return 1
    print('Rapport : '+str(path),flush=True)
    print(f'Observation pendant {args.duration} secondes : verrouille le poste avec le cadenas habituel, attends quelques secondes, puis déverrouille-le.',flush=True)
    print('Sans droits administrateur. Aucun message, mot de passe, clé ou historique du tchat collecté.',flush=True)
    child=None;code=1;started=time.monotonic()
    process_changes=[{'secondes':0,'noms':known_processes()}]
    with os.fdopen(fd,'w',encoding='utf-8') as report:
        report.write('FortyTchat — diagnostic de session, lecture seule\n')
        report.write(json.dumps(context(),ensure_ascii=False,indent=2)+'\n\n')
        report.flush()
        try:
            child=subprocess.Popen([sys.executable,str(SOURCE/'main.py'),'--diagnose-lock',str(args.duration)],stdout=report,stderr=subprocess.STDOUT)
            deadline=time.monotonic()+args.duration+15
            while child.poll() is None:
                remaining=deadline-time.monotonic()
                if remaining<=0:raise subprocess.TimeoutExpired(child.args,args.duration+15)
                try:child.wait(timeout=min(1,remaining))
                except subprocess.TimeoutExpired:
                    names=known_processes()
                    if names!=process_changes[-1]['noms']:
                        process_changes.append({'secondes':round(time.monotonic()-started,1),'noms':names})
            code=child.returncode
        except KeyboardInterrupt:
            code=130
        except (OSError,subprocess.TimeoutExpired) as error:
            report.write('\nDiagnostic interrompu : '+str(error)+'\n')
        finally:
            if child and child.poll() is None:
                child.terminate()
                try:child.wait(timeout=3)
                except subprocess.TimeoutExpired:child.kill();child.wait(timeout=3)
        report.write('\nCode de fin : '+str(code)+'\n')
        report.write('Changements des processus connus observés pendant le test : '+json.dumps(process_changes,ensure_ascii=False)+'\n')
        report.write('Processus connus en fin de test (même utilisateur) : '+json.dumps(known_processes(),ensure_ascii=False)+'\n')
    print('Terminé. Tu peux retourner ce fichier texte : '+str(path),flush=True)
    return code if code>=0 else 1


if __name__=='__main__':sys.exit(main())
