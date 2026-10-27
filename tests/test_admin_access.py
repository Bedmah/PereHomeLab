import asyncio
import base64
import importlib
import json
import logging
import sqlite3
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import AsyncMock, patch
from urllib.parse import quote

from app.admin_access import require_area
from app.cities import CityStore
from app.employees import EmployeeStore
from app.users import UserStore
from tests.test_core import make_config


class AdminAccessTest(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name)
        config = replace(make_config(cls.root), telegram_enabled=False)
        cls.previous_main = sys.modules.pop('app.main', None)
        with patch('app.config.load_config', return_value=config), patch('app.logging_config.configure_logging', return_value=logging.getLogger('admin-test')):
            cls.main = importlib.import_module('app.main')
        cls.main.cities.sync_from_ha_areas([{'area_id': 'spb', 'name': 'SPB'}, {'area_id': 'msk', 'name': 'MSK'}])
        cls.main.users.create_user('general', 'test-password', 'admin')
        cls.main.users.create_user('regional', 'test-password', 'location', ['spb'])

    @classmethod
    def tearDownClass(cls):
        sys.modules.pop('app.main', None)
        if cls.previous_main:
            sys.modules['app.main'] = cls.previous_main
        cls.temp.cleanup()

    async def request(self, method, path, user='regional', data=None, content=None, headers=None):
        body = content if content is not None else json.dumps(data or {}).encode()
        request_headers = [(b'content-type', b'application/json'), (b'content-length', str(len(body)).encode())]
        if user is not None:
            request_headers.append((b'authorization',b'Basic '+base64.b64encode(f'{user}:test-password'.encode())))
        request_headers.extend((key.lower().encode(),value.encode()) for key,value in (headers or {}).items())
        path, _, query = path.partition('?')
        scope = {'type': 'http', 'asgi': {'version': '3.0'}, 'http_version': '1.1', 'method': method,
                 'scheme': 'http', 'path': path, 'raw_path': path.encode(), 'query_string': quote(query,safe='=&%').encode(),
                 'headers': request_headers, 'client': (self._testMethodName, 1), 'server': ('test', 80), 'root_path': ''}
        sent = []
        received = False
        async def receive():
            nonlocal received
            if not received:
                received = True
                return {'type': 'http.request', 'body': body}
            await asyncio.Event().wait()
        async def send(message):
            sent.append(message)
        await self.main.app(scope, receive, send)
        status = next(item['status'] for item in sent if item['type'] == 'http.response.start')
        self.last_headers = {key.decode():value.decode() for item in sent if item['type']=='http.response.start' for key,value in item['headers']}
        output = b''.join(item.get('body', b'') for item in sent if item['type'] == 'http.response.body')
        return status, json.loads(output) if output.startswith((b'{',b'[')) else output.decode()

    async def login(self, username='regional'):
        status, payload = await self.request('POST','/api/admin/auth/login',None,{'username':username,'password':'test-password'})
        self.assertEqual(status,200)
        cookie = self.last_headers['set-cookie'].split(';')[0]
        self.assertIn('HttpOnly',self.last_headers['set-cookie'])
        self.assertIn('SameSite=strict',self.last_headers['set-cookie'])
        return {'Cookie':cookie,'X-CSRF-Token':payload['csrf_token']}

    async def test_session_login_csrf_and_logout(self):
        status, _ = await self.request('GET','/admin',None)
        self.assertEqual(status,303)
        self.assertEqual(self.last_headers['location'],'/admin/login')
        self.assertNotIn('www-authenticate',self.last_headers)
        headers = await self.login()
        status, payload = await self.request('GET','/api/admin/me',None,headers=headers)
        self.assertEqual(status,200)
        self.assertEqual(payload['user']['role'],'location')
        status, _ = await self.request('POST','/api/admin/auth/logout',None,headers={'Cookie':headers['Cookie']})
        self.assertEqual(status,403)
        status, _ = await self.request('POST','/api/admin/auth/logout',None,headers=headers)
        self.assertEqual(status,200)
        status, _ = await self.request('GET','/api/admin/me',None,headers=headers)
        self.assertEqual(status,401)

    async def test_session_roles_are_fresh_and_password_reset_revokes(self):
        self.main.users.create_user('session-changing','test-password','admin')
        headers = await self.login('session-changing')
        self.main.users.set_access('session-changing','location',['msk'])
        status, payload = await self.request('GET','/api/admin/me',None,headers=headers)
        self.assertEqual(payload['user']['allowed_areas'],['msk'])
        status, _ = await self.request('GET','/api/admin/users',None,headers=headers)
        self.assertEqual(status,403)
        self.main.users.set_password('session-changing','new-password')
        status, _ = await self.request('GET','/api/admin/me',None,headers=headers)
        self.assertEqual(status,401)

    async def test_session_expiration_and_blocking(self):
        headers = await self.login()
        token = headers['Cookie'].split('=',1)[1]
        with self.main.users._connect() as connection:
            row = connection.execute('SELECT token_hash FROM admin_sessions WHERE token_hash=?',
                (self.main.sessions.digest(token),)).fetchone()
            self.assertNotEqual(row['token_hash'],token)
            connection.execute('UPDATE admin_sessions SET expires_at=0 WHERE token_hash=?',(row['token_hash'],))
        status, _ = await self.request('GET','/api/admin/me',None,headers=headers)
        self.assertEqual(status,401)

    async def test_owner_session_survives_restart_but_not_env_password_change(self):
        from app.admin_sessions import AdminSessions
        headers = await self.login('admin')
        token = headers['Cookie'].split('=',1)[1]
        self.main.users.ensure_owner('admin','test-password')
        reopened = AdminSessions(self.main.users)
        self.assertIsNotNone(reopened.resolve(token,self.main.session_credential))
        with patch.object(self.main,'config',replace(self.main.config,admin_password='changed-owner-password')):
            self.assertIsNone(reopened.resolve(token,self.main.session_credential))
        headers = await self.login()
        self.main.users.set_active('regional',False)
        self.main.users.set_active('regional',True)
        status, _ = await self.request('GET','/api/admin/me',None,headers=headers)
        self.assertEqual(status,401)

    async def test_login_rate_limit_and_foreign_origin(self):
        status, _ = await self.request('POST','/api/admin/auth/login',None,{'username':'regional','password':'test-password'},
            headers={'Origin':'https://foreign.test'})
        self.assertEqual(status,403)
        for attempt in range(10):
            status, _ = await self.request('POST','/api/admin/auth/login',None,{'username':'unknown-login','password':'wrong-password'})
            self.assertEqual(status,401)
        status, _ = await self.request('POST','/api/admin/auth/login',None,{'username':'regional','password':'test-password'})
        self.assertEqual(status,429)

    async def test_audit_scope_filters_and_secret_redaction(self):
        from app.admin_audit import audit_change
        for scope,actor in [(['spb'],'shared-editor'),(['msk'],'foreign-editor'),([],'global-editor'),(['spb','msk'],'shared-editor')]:
            entry = {}
            audit_change(entry,'location','Журнал-тест: Звук изменён',before={'sound_repeat_seconds':25,'token':'private-old'},
                after={'sound_repeat_seconds':30,'token':'private-new'},area_ids=scope)
            self.main.audit.save(actor,entry['_changes'])
        status, payload = await self.request('GET','/api/admin/audit?q=ЖУРНАЛ-ТЕСТ')
        self.assertEqual(status,200)
        self.assertEqual(payload['total'],2)
        self.assertTrue(all(item['area_ids']==['spb'] for item in payload['items']))
        self.assertNotIn('foreign-editor',payload['actors'])
        self.assertNotIn('private-',json.dumps(payload))
        status, payload = await self.request('GET','/api/admin/audit?q=ЖУРНАЛ-ТЕСТ','general')
        self.assertEqual(payload['total'],4)
        status, payload = await self.request('GET','/api/admin/audit?q=ЖУРНАЛ-ТЕСТ&category=telegram','admin')
        self.assertEqual(payload['total'],0)
        status, _ = await self.request('GET','/api/admin/audit?area_id=msk')
        self.assertEqual(status,403)
        status, _ = await self.request('GET','/api/admin/audit?date_from=not-a-date')
        self.assertEqual(status,400)

    async def test_basic_auth_uses_same_login_rate_limit(self):
        headers = {'Authorization':'Basic '+base64.b64encode(b'basic-unknown:wrong-password').decode()}
        for attempt in range(10):
            status, _ = await self.request('GET','/api/admin/me',None,headers=headers)
            self.assertEqual(status,401)
        status, _ = await self.request('GET','/api/admin/me',None,headers=headers)
        self.assertEqual(status,429)
        self.assertEqual(self.last_headers['retry-after'],'300')
        status, _ = await self.request('POST','/api/admin/auth/login',None,{'username':'regional','password':'test-password'})
        self.assertEqual(status,429)

    async def test_location_slugs_cannot_shadow_service_routes(self):
        from app.cities import RESERVED_AREA_SLUGS
        area = next(city.to_dict() for city in self.main.cities.list(include_disabled=True) if city.area_id=='spb')
        for slug in RESERVED_AREA_SLUGS:
            status, _ = await self.request('POST','/api/admin/areas','general',{**area,'slug':slug})
            self.assertEqual(status,409,slug)
        store = CityStore(self.root/'reserved-slugs.db')
        store.sync_from_ha_areas([{'area_id':slug,'name':slug} for slug in RESERVED_AREA_SLUGS])
        self.assertTrue(all(city.slug not in RESERVED_AREA_SLUGS for city in store.list(include_disabled=True)))

    async def test_audit_real_setting_diff_and_pagination(self):
        area = next(city.to_dict() for city in self.main.cities.list(include_disabled=True) if city.area_id=='spb')
        with patch.object(self.main.watcher,'reload_entities',new=AsyncMock()):
            status, _ = await self.request('POST','/api/admin/areas',data={**area,'ack_button_text':'Unique Audit Value'})
        self.assertEqual(status,200)
        status, payload = await self.request('GET','/api/admin/audit?q=Unique%20Audit%20Value')
        self.assertEqual(payload['total'],1)
        self.assertEqual(payload['items'][0]['changes'][0]['field'],'ack_button_text')
        entry = {'category':'history','summary':'Pagination marker','target':'','changes':[],'area_ids':['spb'],'details':''}
        self.main.audit.save('page-editor',[entry]*35)
        status, first = await self.request('GET','/api/admin/audit?q=Pagination%20marker')
        status, second = await self.request('GET','/api/admin/audit?q=Pagination%20marker&page=2')
        self.assertEqual((len(first['items']),len(second['items'])),(30,5))

    async def test_shared_support_settings_validation_permissions_and_persistence(self):
        status, _ = await self.request('GET','/api/admin/settings')
        self.assertEqual(status,403)
        status, _ = await self.request('PUT','/api/admin/settings',data={'support_url':'https://support.test'})
        self.assertEqual(status,403)
        for invalid in ['javascript:alert(1)','data:text/html,test','/relative','https://user:password@support.test','https://support.test:invalid']:
            status, _ = await self.request('PUT','/api/admin/settings','general',{'support_url':invalid})
            self.assertEqual(status,400)
        status, _ = await self.request('PUT','/api/admin/settings','general',{'support_url':'https://support.test/help'})
        self.assertEqual(status,200)
        for path in ['/api/areas','/api/client-config','/api/admin/settings']:
            status, payload = await self.request('GET',path,'admin')
            self.assertEqual(payload['support_url'],'https://support.test/help')
        from app.history import HistoryStore
        self.assertEqual(HistoryStore(self.main.config.history_db_path).get_runtime_state('support_url'),'https://support.test/help')
        status, payload = await self.request('GET','/api/admin/audit?category=settings')
        self.assertTrue(payload['items'])
        status, _ = await self.request('PUT','/api/admin/settings','admin',{'support_url':''})
        self.assertEqual(status,200)

    async def test_area_lists_are_filtered_and_own_settings_save(self):
        with patch.object(self.main.watcher, 'refresh_areas_from_ha', new=AsyncMock()):
            status, payload = await self.request('GET', '/api/admin/areas')
        self.assertEqual(status, 200)
        self.assertEqual([item['area_id'] for item in payload['items']], ['spb'])
        area = payload['items'][0]
        with patch.object(self.main.watcher, 'reload_entities', new=AsyncMock()):
            status, _ = await self.request('POST', '/api/admin/areas', data={**area, 'ack_button_text': 'Test'})
        self.assertEqual(status, 200)
        status, _ = await self.request('POST', '/api/admin/areas', data={**area, 'display_name': 'Renamed'})
        self.assertEqual(status, 403)

    async def test_foreign_area_routes_are_denied_before_side_effects(self):
        for method, path, data in [
            ('POST', '/api/admin/areas', {'area_id':'msk', 'display_name':'MSK'}),
            ('POST', '/api/admin/areas/msk/sync-history', {}),
            ('POST', '/api/admin/telegram/msk', {'enabled':False}),
            ('DELETE', '/api/admin/telegram/msk', {}),
            ('POST', '/api/admin/telegram/msk/begin-setup', {}),
            ('POST', '/api/admin/telegram/msk/confirm-setup', {'setup_id':'test'}),
            ('POST', '/api/admin/telegram/msk/test', {}),
            ('PUT', '/api/admin/employees/test/areas/msk', {'enabled':False}),
            ('GET', '/api/admin/employees?area_id=msk', {}),
        ]:
            with self.subTest(path=path):
                status, _ = await self.request(method, path, data=data)
                self.assertEqual(status, 403)

    async def test_regional_cannot_access_global_resources(self):
        for method, path, data in [
            ('GET','/api/admin/users',{}), ('GET','/api/admin/logs',{}), ('GET','/api/admin/login-logs',{}),
            ('GET','/api/admin/employees',{}), ('GET','/api/admin/employees/test/audit',{}),
            ('POST','/api/admin/employees',{'login':'test.user','full_name':'Test User'}),
            ('DELETE','/api/admin/sounds/test.mp3',{}),
            ('POST','/api/admin/users',{'username':'illegal','password':'secret123'}),
        ]:
            with self.subTest(path=path):
                status, _ = await self.request(method, path, data=data)
                self.assertEqual(status, 403)

    async def test_general_can_manage_regional_but_not_promote_or_edit_general(self):
        status, payload = await self.request('GET', '/api/admin/users', 'general')
        self.assertEqual(status, 200)
        self.assertTrue(all(item['role'] == 'location' for item in payload['items']))
        status, _ = await self.request('POST', '/api/admin/users', 'general',
            {'username':'created','password':'test-password','role':'location','allowed_areas':['msk']})
        self.assertEqual(status, 200)
        for path, method, data in [
            ('/api/admin/users/created/access','PUT',{'role':'admin'}),
            ('/api/admin/users/general/password','POST',{'password':'changed-password'}),
            ('/api/admin/users/admin/password','POST',{'password':'changed-password'}),
            ('/api/admin/users/general','DELETE',{}),
        ]:
            status, _ = await self.request(method, path, 'general', data)
            self.assertEqual(status, 403)
        status, _ = await self.request('PUT','/api/admin/users/created/access','general',
            {'role':'location','allowed_areas':['spb','msk']})
        self.assertEqual(status, 200)
        status, _ = await self.request('DELETE','/api/admin/users/created','general')
        self.assertEqual(status, 200)

    async def test_owner_roles_and_immediate_revocation(self):
        status, _ = await self.request('POST','/api/admin/users','admin',
            {'username':'changing','password':'test-password','role':'admin'})
        self.assertEqual(status, 200)
        status, _ = await self.request('PUT','/api/admin/users/changing/access','admin',
            {'role':'location','allowed_areas':['msk']})
        self.assertEqual(status, 200)
        status, payload = await self.request('GET','/api/admin/me','changing')
        self.assertEqual(payload['user']['allowed_areas'], ['msk'])
        status, _ = await self.request('GET','/api/admin/users','changing')
        self.assertEqual(status, 403)
        await self.request('POST','/api/admin/users/changing/active','admin',{'is_active':False})
        status, _ = await self.request('GET','/api/admin/me','changing')
        self.assertEqual(status, 401)
        await self.request('DELETE','/api/admin/users/changing','admin')

    async def test_invalid_assignments_and_env_owner_are_protected(self):
        for role, areas in [('owner', []), ('location', []), ('location', ['missing'])]:
            status, _ = await self.request('POST','/api/admin/users','admin',
                {'username':'invalid','password':'test-password','role':role,'allowed_areas':areas})
            self.assertEqual(status, 400)
        for method, suffix, data in [('DELETE','',{}),('POST','/active',{'is_active':False}),
                                     ('POST','/password',{'password':'new-password'}),('PUT','/access',{'role':'admin'})]:
            status, _ = await self.request(method,'/api/admin/users/admin'+suffix,'admin',data)
            self.assertEqual(status, 403)

    async def test_telegram_list_is_scoped(self):
        status, payload = await self.request('GET','/api/admin/telegram')
        self.assertEqual(status, 200)
        self.assertEqual([item['area_id'] for item in payload['items']], ['spb'])
        self.assertTrue(all('token' not in item for item in payload['items']))

    async def test_sound_upload_is_unique_and_used_sound_is_protected(self):
        (self.root / 'sound').mkdir(exist_ok=True)
        with patch.object(self.main, 'PROJECT_ROOT', self.root):
            status, _ = await self.request('POST','/api/admin/sounds/new.mp3',content=b'test-sound')
            self.assertEqual(status, 200)
            status, _ = await self.request('POST','/api/admin/sounds/new.mp3',content=b'overwrite')
            self.assertEqual(status, 409)
            self.assertEqual((self.root/'sound/new.mp3').read_bytes(), b'test-sound')
            self.main.cities.upsert('msk','MSK',sound_path='sound/new.mp3')
            status, _ = await self.request('DELETE','/api/admin/sounds/new.mp3','general')
            self.assertEqual(status, 409)

    async def test_employee_permissions_do_not_change_other_regions_or_profile(self):
        item = self.main.telegram.employees.observe({'id':123,'first_name':'Test'},'spb')
        self.main.telegram.employees.observe({'id':123,'first_name':'Test'},'msk')
        status, _ = await self.request('PUT',f'/api/admin/employees/{item["id"]}/areas/spb',data={'enabled':False})
        self.assertEqual(status, 200)
        self.assertFalse(self.main.telegram.employees.area_enabled(item['id'],'spb'))
        self.assertFalse(EmployeeStore(self.main.config.history_db_path).area_enabled(item['id'],'spb'))
        self.assertTrue(self.main.telegram.employees.area_enabled(item['id'],'msk'))
        self.assertEqual(self.main.telegram.employees.get(item['id'])['blocked'], False)
        status, payload = await self.request('GET','/api/admin/employees?area_id=spb')
        self.assertEqual(status, 200)
        self.assertFalse(payload['items'][0]['area_enabled'])
        with self.assertRaises(ValueError):
            self.main.telegram.interactions.identity({'area_id':'spb'},{'id':123})
        self.assertTrue(self.main.telegram.interactions.identity({'area_id':'msk'},{'id':123}))

    async def test_audit_does_not_store_request_secrets(self):
        self.main.users.create_user('password-test','test-password','location',['spb'])
        status, _ = await self.request('POST','/api/admin/users/password-test/password','admin',{'password':'Never-log-this'})
        self.assertEqual(status, 200)
        actions = self.main.audit.page({'role':'owner'},query='password-test')['items']
        self.assertTrue(any(item['summary']=='Пароль администратора изменён' for item in actions))
        self.assertNotIn('Never-log-this', json.dumps(actions))

    async def test_new_ha_location_refresh_and_settings_are_preserved(self):
        client = self.main.ha_client
        registry = {'areas':[{'area_id':'spb','name':'SPB'}, {'area_id':'msk','name':'MSK'},
                             {'area_id':'new-town','name':'New Town'}], 'devices':[],
                    'entities':[{'entity_id':'binary_sensor.new_button_safety','area_id':'new-town'}]}
        states = [{'entity_id':'binary_sensor.new_button_safety', 'state':'off',
                   'attributes':{'device_class':'safety','friendly_name':'New Button'}}]
        with patch.object(client,'get_registry',new=AsyncMock(return_value=registry)), \
             patch.object(client,'get_states',new=AsyncMock(return_value=states)), \
             patch.object(self.main.watcher,'_entities',{}):
            status, payload = await self.request('GET','/api/admin/areas','general')
            self.assertEqual(status,200)
            area = next(item for item in payload['items'] if item['area_id']=='new-town')
            self.assertEqual(area['button_count'],1)
            self.assertEqual(area['active_button_count'],1)
            with patch.object(self.main.watcher,'reload_entities',new=AsyncMock()):
                status, _ = await self.request('POST','/api/admin/areas','general',
                    {**area,'display_name':'New Location','slug':'new-location','ack_button_text':'Done','history_sync_hours':48})
            self.assertEqual(status,200)
            status, _ = await self.request('GET','/new-location','general')
            self.assertEqual(status,200)
            status, payload = await self.request('GET','/api/admin/areas','general')
            saved = next(item for item in payload['items'] if item['area_id']=='new-town')
            self.assertEqual(saved['display_name'],'New Location')
            self.assertEqual(saved['history_sync_hours'],48)
            status, payload = await self.request('GET','/api/admin/areas')
            self.assertNotIn('new-town',[item['area_id'] for item in payload['items']])
            before = self.main.users.get_user('regional')['allowed_areas']
            self.assertEqual(before,['spb'])


class AdminMigrationTest(unittest.TestCase):
    def test_old_audit_migrates_once_with_scope_and_new_entries_do_not_duplicate(self):
        from app.admin_audit import AdminAudit
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'history.db'
            users = UserStore(path)
            CityStore(path).sync_from_ha_areas([{'area_id':'spb','name':'SPB'}])
            users.add_action('old-editor','POST','/api/admin/areas · spb')
            audit = AdminAudit(users)
            self.assertEqual(audit.page({'role':'location','allowed_areas':['spb']})['total'],1)
            audit.save('editor',[{'category':'location','summary':'Changed','target':'SPB',
                'changes':[],'details':'','area_ids':['spb']}])
            self.assertEqual(AdminAudit(users).page({'role':'owner'})['total'],2)

    def test_discovered_locations_get_distinct_stable_links(self):
        with tempfile.TemporaryDirectory() as directory:
            store = CityStore(Path(directory)/'cities.db')
            areas = [{'area_id':'city-a','name':'MSK'}, {'area_id':'city-b','name':'Москва'},
                     {'area_id':'city-c','name':'MSK 1'}]
            store.sync_from_ha_areas(areas)
            first = {city.area_id:city.slug for city in store.list(include_disabled=True)}
            self.assertEqual(len(set(first.values())),3)
            store.sync_from_ha_areas(list(reversed(areas)))
            self.assertEqual(first,{city.area_id:city.slug for city in store.list(include_disabled=True)})

    def test_existing_admin_preserved_and_scoped_access_persists(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'old.db'
            with sqlite3.connect(path) as connection:
                connection.execute('''CREATE TABLE admin_users(username TEXT PRIMARY KEY,password_hash TEXT,
                    role TEXT,is_active INTEGER,created_at TEXT,updated_at TEXT,last_login_at TEXT)''')
                connection.execute('INSERT INTO admin_users VALUES(?,?,?,?,?,?,?)',
                    ('existing',UserStore.hash_password('test-password'),'admin',1,'old','old',None))
            store = UserStore(path)
            self.assertEqual(store.authenticate('existing','test-password')['role'],'admin')
            store.set_access('existing','location',['spb'])
            self.assertEqual(UserStore(path).authenticate('existing','test-password')['allowed_areas'],['spb'])
            with self.assertRaises(Exception):
                require_area(store.authenticate('existing','test-password'),'msk')
