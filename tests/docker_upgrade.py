"""Offline root-to-nonroot upgrade and Fernet compatibility with a real older image."""
import argparse
import json
import os
from contextlib import ExitStack
from pathlib import Path
import subprocess
import tempfile


def docker(*args, script=None):
    return subprocess.run(['docker',*args],input=script,text=True,capture_output=True,check=True).stdout.strip()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image',default='perehomelab:0.2.0-test')
    parser.add_argument('--legacy-image',default='bedmah/perehomelab:0.1.3')
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='perehomelab-upgrade-') as directory, ExitStack() as cleanup:
        root = Path(directory)
        mounts = []
        for name in ('data','logs','sound'):
            (root/name).mkdir()
            mounts.extend(['-v',str(root/name)+':/app/'+name])
        def ownership(uid,gid):
            docker('run','--rm','--network','none','--user','0','--entrypoint','chown',*mounts,
                   args.image,'-R','--no-dereference',f'{uid}:{gid}','/app/data','/app/logs','/app/sound')
        cleanup.callback(ownership,os.getuid(),os.getgid())
        seed = '''
from pathlib import Path
from datetime import datetime
from cryptography.fernet import Fernet
from app.history import HistoryStore
from app.employees import EmployeeStore
from app.call_store import CallStore
from app.cities import CityStore
from app.telegram_store import TelegramStore
path=Path('/app/data/press_history.db')
history=HistoryStore(path); employees=EmployeeStore(path); calls=CallStore(history,employees)
now=datetime.now().isoformat(timespec='seconds')
history.add_press('old-event','old-button','Test Room',now,'Test time','spb','SPB')
employee=employees.create('legacy.user','Test Person','123','test')
with history._connect() as connection:
 connection.execute('UPDATE employees SET profile_locked=1,blocked=1 WHERE id=?',(employee['id'],))
 connection.execute('INSERT INTO call_comments VALUES(?,?,?,?,?,?,?)',('old-comment','old-event','123','Test Person','Preserved comment',now,'old-key'))
CityStore(path).upsert('spb','SPB',ack_button_text='Acknowledge',sound_path='sound/legacy.wav')
key=Fernet.generate_key().decode()
Path('/app/data/legacy-key').write_text(key)
Path('/app/data/legacy-key').chmod(0o600)
TelegramStore(path,key).save('spb',True,'123456789:'+('x'*32),{'id':123456789,'username':'offline_test_bot'})
Path('/app/sound/legacy.wav').write_bytes(b'RIFF-test')
print('Legacy root-owned fixture created')
'''
        print(docker('run','--rm','--network','none','--user','0','--entrypoint','python',*mounts,
                     '-i',args.legacy_image,'-',script=seed))
        ownership(10001,10001)
        container = docker('run','-d','--network','none','--read-only','--tmpfs','/tmp',
                           '-e','HA_URL=http://127.0.0.1:9','-e','HA_TOKEN=isolated-token',
                           '-e','ADMIN_USERNAME=upgrade-admin','-e','ADMIN_PASSWORD=isolated-password',
                           '-e','TELEGRAM_ENABLED=false','-e','HISTORY_DB_PATH=/app/data/press_history.db',
                           *mounts,args.image)
        try:
            verify = '''
import json,os,time,urllib.request,sqlite3
from pathlib import Path
for attempt in range(100):
 try:
  with urllib.request.urlopen('http://127.0.0.1:8000/healthz',timeout=1) as response: data=json.load(response)
  assert data['version']=='0.2.0'
  break
 except OSError: time.sleep(.1)
else: raise AssertionError('New container failed to start')
assert os.getuid()==10001 and os.getgid()==10001
from app.telegram_store import TelegramStore
from app.employees import EmployeeStore
from app.cities import CityStore
path=Path('/app/data/press_history.db')
store=TelegramStore(path,Path('/app/data/legacy-key').read_text())
assert store.token(store.settings('spb')[0])=='123456789:'+('x'*32)
employee=EmployeeStore(path).by_user('123')
assert employee['login']=='legacy.user' and employee['blocked'] and employee['profile_locked']
assert CityStore(path).list()[0].ack_button_text=='Acknowledge'
with sqlite3.connect(path) as connection:
 assert connection.execute('PRAGMA quick_check').fetchone()[0]=='ok'
 assert connection.execute('SELECT body FROM call_comments WHERE id=?',('old-comment',)).fetchone()[0]=='Preserved comment'
 assert connection.execute('SELECT id FROM press_history WHERE id=?',('old-event',)).fetchone()
assert Path('/app/sound/legacy.wav').read_bytes()==b'RIFF-test'
assert not os.access('/app/app/main.py',os.W_OK)
print('History, employee restrictions, location settings, sound and legacy encrypted token preserved under nonroot')
'''
            print(docker('exec','-i',container,'python','-',script=verify))
        finally:
            docker('stop','--timeout','15',container)
            docker('rm',container)


if __name__ == '__main__':
    main()
