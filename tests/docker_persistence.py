"""Explicit integration check using temporary volumes and an isolated local image."""

import argparse
import json
import os
from contextlib import ExitStack
from pathlib import Path
import subprocess
import tempfile


def docker(*args, script=None):
    return subprocess.run(['docker', *args], input=script, text=True, capture_output=True, check=True).stdout.strip()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', default='perehomelab:0.2.0-test')
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='perehomelab-docker-check-') as directory, ExitStack() as cleanup:
        root = Path(directory)
        for name in ('data', 'logs', 'sound', 'downloads'):
            (root / name).mkdir()
        (root / 'downloads/Test.exe').write_bytes(b'isolated-test-download')
        def ownership(uid, gid):
            docker('run', '--rm', '--network', 'none', '--user', '0', '--entrypoint', 'chown',
                   '-v', str(root/'data')+':/app/data', '-v', str(root/'logs')+':/app/logs',
                   '-v', str(root/'sound')+':/app/sound', args.image, '-R', '--no-dereference',
                   f'{uid}:{gid}', '/app/data', '/app/logs', '/app/sound')
        cleanup.callback(ownership,os.getuid(),os.getgid())
        ownership(10001,10001)
        previous_id = None
        for phase in (1, 2):
            options = ['run', '-d', '--network', 'none', '--read-only', '--tmpfs', '/tmp']
            for name, value in {'HA_URL':'http://127.0.0.1:9', 'HA_TOKEN':'isolated-test',
                    'ADMIN_USERNAME':'testadmin', 'ADMIN_PASSWORD':'isolated-password',
                    'TELEGRAM_ENABLED':'false', 'HISTORY_DB_PATH':'/app/data/press_history.db',
                    'APP_DOWNLOAD_DIR':'/download-app', 'APP_DOWNLOAD_FILENAME':'Test.exe'}.items():
                options.extend(['-e', name + '=' + value])
            for name, target in [('data','/app/data'), ('logs','/app/logs'), ('sound','/app/sound'), ('downloads','/download-app:ro')]:
                options.extend(['-v', str(root / name) + ':' + target])
            container = docker(*options, args.image)
            try:
                script = '''
import base64, json, time, urllib.request, urllib.error
import os
from pathlib import Path

assert os.getuid()==10001 and os.getgid()==10001
assert not os.access('/app/app/main.py',os.W_OK)
def request(path, data=None, method='GET', headers=None):
    extra={'Authorization':'Basic '+base64.b64encode(b'testadmin:isolated-password').decode()}
    extra.update(headers or {})
    req=urllib.request.Request('http://127.0.0.1:8000'+path, data=data, method=method, headers=extra)
    with urllib.request.urlopen(req,timeout=5) as response: return response.read()
for _ in range(100):
    try:
        request('/healthz')
        break
    except Exception: time.sleep(.1)
else: raise AssertionError('Container did not start')
status=json.loads(request('/api/status'))
assert status.get('last_error') or status.get('watched_count') == 0
batteries=json.loads(request('/api/batteries?limit=5'))
assert isinstance(batteries.get('items'),list)
assert request('/monitor')
try:
    request('/api/admin/users',headers={'Authorization':'Basic '+base64.b64encode(b'bad:bad').decode()})
except urllib.error.HTTPError as error:
    assert error.code==401
else: raise AssertionError('Admin endpoint accepted invalid credentials')
'''
                if phase == 1:
                    script += '''
item=json.loads(request('/api/admin/employees',json.dumps({'login':'docker.test','full_name':'Test Employee'}).encode(),'POST',{'Content-Type':'application/json'}))['item']
import sqlite3
with sqlite3.connect('/app/data/press_history.db') as connection:
    connection.execute('INSERT INTO telegram_contacts(bot_id,user_id,area_id) VALUES(?,?,?)',(123,'777','offline'))
    connection.execute('INSERT INTO telegram_subscriptions(bot_id,user_id,area_id,enabled,start_rowid) VALUES(?,?,?,?,?)',(123,'777','offline',1,0))
    connection.execute('INSERT INTO telegram_private_outbox(event_id,bot_id,user_id,area_id,state,expires_at) VALUES(?,?,?,?,?,?)',('offline-test',123,'777','offline','uncertain',0))
request('/api/admin/sounds/persistence.wav', b'RIFF-test', 'POST')
try:
    request('/api/admin/sounds/too-big.wav',b'', 'POST', {'Content-Length':str(21*1024*1024)})
except urllib.error.HTTPError as error:
    assert error.code==413
else: raise AssertionError('Oversized upload was accepted')
assert not Path('/app/sound/too-big.wav').exists()
print(json.dumps({'id':item['id']}))
'''
                else:
                    script += '''
items=json.loads(request('/api/admin/employees?q=docker.test'))['items']
assert len(items)==1 and items[0]['id']==EXPECTED
assert request('/sound/persistence.wav')==b'RIFF-test'
assert request('/download-app')==b'isolated-test-download'
assert Path('/app/logs/app.log').stat().st_size>0
import sqlite3
with sqlite3.connect('/app/data/press_history.db') as connection:
    assert connection.execute('SELECT enabled FROM telegram_subscriptions WHERE bot_id=123 AND user_id=?',('777',)).fetchone()[0]==1
    assert connection.execute('SELECT state FROM telegram_private_outbox WHERE event_id=?',('offline-test',)).fetchone()[0]=='uncertain'
print('Database, sound, logs and executable survived container recreation')
'''.replace('EXPECTED', repr(previous_id))
                output = docker('exec', '-i', container, 'python', '-B', '-', script=script)
                if phase == 1:
                    previous_id = json.loads(output)['id']
                else:
                    print(output)
            finally:
                docker('stop', '-t', '15', container)
                docker('rm', container)
        print('Docker persistence and upload limit checks passed; real data was not mounted.')


if __name__ == '__main__':
    main()
