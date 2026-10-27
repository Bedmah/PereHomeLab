import json
import re
import sqlite3
from datetime import datetime
from pathlib import Path
from uuid import uuid4
from app.dates import display_date


class EmployeeStore:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        with self.connect() as connection:
            connection.executescript('''
                CREATE TABLE IF NOT EXISTS employees (
                    id TEXT PRIMARY KEY, telegram_user_id TEXT UNIQUE,
                    login TEXT NOT NULL UNIQUE COLLATE NOCASE, full_name TEXT NOT NULL,
                    blocked INTEGER NOT NULL DEFAULT 0, allowed_areas TEXT,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS employee_audit (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, employee_id TEXT NOT NULL,
                    actor TEXT NOT NULL, action TEXT NOT NULL, details TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS employee_regions (
                    employee_id TEXT NOT NULL, area_id TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1,
                    PRIMARY KEY(employee_id, area_id)
                );
            ''')
            columns = {row['name'] for row in connection.execute('PRAGMA table_info(employees)')}
            for name, definition in [('profile_complete', 'INTEGER NOT NULL DEFAULT 1'),
                                     ('profile_locked', 'INTEGER NOT NULL DEFAULT 0'),
                                     ('telegram_name', 'TEXT'), ('telegram_username', 'TEXT'),
                                     ('last_seen_at', 'TEXT'), ('last_seen_area_id', 'TEXT')]:
                if name not in columns:
                    connection.execute(f'ALTER TABLE employees ADD COLUMN {name} {definition}')
            connection.execute('''INSERT OR IGNORE INTO employee_regions(employee_id,area_id)
                SELECT id,last_seen_area_id FROM employees WHERE last_seen_area_id IS NOT NULL''')
            if connection.execute("SELECT 1 FROM sqlite_master WHERE name='press_history'").fetchone():
                connection.execute('''INSERT OR IGNORE INTO employee_regions(employee_id,area_id)
                    SELECT DISTINCT employees.id,press_history.area_id FROM employees JOIN press_history
                    ON employees.telegram_user_id=press_history.acknowledged_user_id
                    WHERE press_history.acknowledged_source='telegram' AND press_history.area_id IS NOT NULL''')
                for table, user_column in [('call_actions', 'actor_user_id'), ('call_comments', 'author_user_id')]:
                    if connection.execute('SELECT 1 FROM sqlite_master WHERE name=?', (table,)).fetchone():
                        connection.execute(f'''INSERT OR IGNORE INTO employee_regions(employee_id,area_id)
                            SELECT DISTINCT employees.id,press_history.area_id FROM employees
                            JOIN {table} ON employees.telegram_user_id={table}.{user_column}
                            JOIN press_history ON press_history.id={table}.event_id
                            WHERE press_history.area_id IS NOT NULL''')

    def connect(self):
        connection = sqlite3.connect(self.db_path, timeout=5)
        connection.row_factory = sqlite3.Row
        return connection

    @staticmethod
    def validate(login: str, full_name: str) -> tuple[str, str]:
        login = login.strip().lower()
        full_name = ' '.join(full_name.split())
        if not re.fullmatch(r'[a-z][a-z0-9._-]{1,63}(?:@[a-z0-9.-]+\.[a-z]{2,})?', login) or len(login) > 100:
            raise ValueError('Логин: латинские буквы, цифры, точка, дефис или подчёркивание. Например i.petrov')
        if not 5 <= len(full_name) <= 150 or len(full_name.split()) < 2 or not all(char.isalpha() or char in " -'’" for char in full_name):
            raise ValueError('Укажите имя и фамилию буквами, например Иван Петров')
        return login, full_name

    @staticmethod
    def public(row) -> dict | None:
        if row is None:
            return None
        result = dict(row)
        result['blocked'] = bool(result['blocked'])
        result['allowed_areas'] = json.loads(result['allowed_areas']) if result['allowed_areas'] is not None else None
        result['profile_complete'] = bool(result.get('profile_complete', 1))
        result['profile_locked'] = bool(result.get('profile_locked', 0))
        if not result['profile_complete']:
            result['login'] = ''
        result['display_name'] = result['full_name'] + (' · ' + result['login'] if result['login'] else '')
        result['last_seen_at_display'] = display_date(result.get('last_seen_at'))
        return result

    @staticmethod
    def clean_user_id(user_id):
        value = str(user_id).strip() if user_id is not None else ''
        if value and (not value.isascii() or not value.isdigit() or not 0 < int(value) < 10**20):
            raise ValueError('Некорректный Telegram ID')
        return str(int(value)) if value else None

    def observe(self, user: dict, area_id: str | None = None, observed_at: str | None = None) -> dict | None:
        if not user.get('id') or user.get('is_bot'):
            return None
        user_id = self.clean_user_id(user['id'])
        name = ' '.join(str(user.get(key) or '') for key in ('first_name', 'last_name')).strip()[:150]
        username = str(user.get('username') or '').strip()[:64]
        name = name or username or f'Telegram {user_id}'
        now = observed_at or datetime.now().isoformat(timespec='seconds')
        with self.connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            current = connection.execute('SELECT id FROM employees WHERE telegram_user_id=?',(user_id,)).fetchone()
            if current:
                employee_id = current['id']
            else:
                employee_id = uuid4().hex
                connection.execute('''INSERT INTO employees(id,telegram_user_id,login,full_name,profile_complete,created_at,updated_at)
                    VALUES(?,?,?,?,0,?,?)''',(employee_id,user_id,'@telegram:'+employee_id,name,now,now))
                connection.execute('INSERT INTO employee_audit(employee_id,actor,action,details,created_at) VALUES(?,?,?,?,?)',
                    (employee_id,'Telegram','discovered',json.dumps({'telegram_user_id':user_id},ensure_ascii=False),now))
            connection.execute('UPDATE employees SET telegram_name=?,telegram_username=?,last_seen_at=?,last_seen_area_id=? WHERE id=?',
                (name,username,now,area_id,employee_id))
            if area_id:
                connection.execute('INSERT OR IGNORE INTO employee_regions(employee_id,area_id) VALUES(?,?)', (employee_id,area_id))
        return self.get(employee_id)

    def import_interactions(self, history):
        if history.get_runtime_state('employee_discovery_backfill_v1'):
            return
        with self.connect() as connection:
            rows = connection.execute('''SELECT actor_user_id AS user_id,actor_name AS name,created_at AS seen_at FROM call_actions WHERE actor_user_id IS NOT NULL
                UNION ALL SELECT author_user_id,author_name,created_at FROM call_comments
                UNION ALL SELECT acknowledged_user_id,acknowledged_by,acknowledged_at FROM press_history
                    WHERE acknowledged_source='telegram' AND acknowledged_user_id IS NOT NULL
                ORDER BY seen_at''').fetchall()
            flows = connection.execute('SELECT DISTINCT user_id FROM telegram_flows').fetchall()
        users = {str(row['user_id']): dict(row) for row in rows}
        for row in flows:
            users.setdefault(str(row['user_id']), {'user_id':row['user_id'],'name':'','seen_at':None})
        for row in users.values():
            if self.by_user(row['user_id']):
                continue
            try:
                self.observe({'id':row['user_id'],'first_name':row['name'] or ''}, observed_at=row['seen_at'])
            except ValueError:
                continue
        history.set_runtime_state('employee_discovery_backfill_v1','1',datetime.now().isoformat(timespec='seconds'))

    def create(self, login: str, full_name: str, telegram_user_id: str | None, actor: str) -> dict:
        login, full_name = self.validate(login, full_name)
        user_id = self.clean_user_id(telegram_user_id)
        employee_id = uuid4().hex
        now = datetime.now().isoformat(timespec='microseconds')
        try:
            with self.connect() as connection:
                connection.execute('''INSERT INTO employees(id,telegram_user_id,login,full_name,created_at,updated_at)
                    VALUES(?,?,?,?,?,?)''',(employee_id,user_id,login,full_name,now,now))
                connection.execute('INSERT INTO employee_audit(employee_id,actor,action,details,created_at) VALUES(?,?,?,?,?)',
                    (employee_id,actor,'admin_created',json.dumps({'login':login,'full_name':full_name,'telegram_user_id':user_id},ensure_ascii=False),now))
        except sqlite3.IntegrityError:
            raise ValueError('Логин или Telegram ID уже есть в списке. Отредактируйте существующую карточку сотрудника') from None
        return self.get(employee_id)

    def get(self, employee_id: str) -> dict | None:
        with self.connect() as connection:
            return self.public(connection.execute('SELECT * FROM employees WHERE id = ?', (employee_id,)).fetchone())

    def by_user(self, user_id: str | int) -> dict | None:
        with self.connect() as connection:
            return self.public(connection.execute('SELECT * FROM employees WHERE telegram_user_id = ?', (str(user_id),)).fetchone())

    def by_users(self, user_ids) -> dict:
        ids = list(dict.fromkeys(str(user_id) for user_id in user_ids if user_id))
        items = {}
        if not ids:
            return items
        with self.connect() as connection:
            for offset in range(0, len(ids), 500):
                batch = ids[offset:offset + 500]
                placeholders = ','.join('?' for _ in batch)
                rows = connection.execute(f'SELECT * FROM employees WHERE telegram_user_id IN ({placeholders})', batch).fetchall()
                items.update({row['telegram_user_id']: self.public(row) for row in rows})
        return items

    def save(self, user_id: str, login: str, full_name: str, actor: str = 'Telegram') -> dict:
        login, full_name = self.validate(login, full_name)
        now = datetime.now().isoformat(timespec='microseconds')
        try:
            with self.connect() as connection:
                connection.execute('BEGIN IMMEDIATE')
                current = connection.execute('SELECT * FROM employees WHERE telegram_user_id = ?', (str(user_id),)).fetchone()
                if current and current['blocked']:
                    raise ValueError('Профиль заблокирован. Обратитесь к администратору')
                if current and current['profile_locked']:
                    raise ValueError('Изменение данных запрещено администратором')
                employee_id = current['id'] if current else uuid4().hex
                connection.execute('''INSERT INTO employees (id,telegram_user_id,login,full_name,created_at,updated_at)
                    VALUES (?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET login=excluded.login,
                    full_name=excluded.full_name,profile_complete=1,updated_at=excluded.updated_at''',
                    (employee_id, str(user_id), login, full_name, now, now))
                connection.execute('INSERT INTO employee_audit(employee_id,actor,action,details,created_at) VALUES (?,?,?,?,?)',
                    (employee_id, actor, 'profile_saved', json.dumps({'login':login,'full_name':full_name},ensure_ascii=False), now))
        except sqlite3.IntegrityError:
            raise ValueError('Этот логин уже привязан к другому сотруднику. Обратитесь к администратору') from None
        return self.get(employee_id)

    def update(self, employee_id: str, login: str, full_name: str, blocked: bool,
               allowed_areas: list[str] | None, telegram_user_id: str | None, actor: str,
               profile_locked: bool | None = None) -> dict:
        user_id = self.clean_user_id(telegram_user_id)
        now = datetime.now().isoformat(timespec='microseconds')
        try:
            with self.connect() as connection:
                connection.execute('BEGIN IMMEDIATE')
                old = connection.execute('SELECT * FROM employees WHERE id = ?', (employee_id,)).fetchone()
                if not old:
                    raise KeyError(employee_id)
                complete = bool(login.strip() or old['profile_complete'])
                if complete:
                    login, full_name = self.validate(login, full_name)
                else:
                    login = old['login']
                    full_name = ' '.join(full_name.split())
                    if not 1 <= len(full_name) <= 150:
                        raise ValueError('Укажите имя длиной от 1 до 150 символов')
                connection.execute('''UPDATE employees SET login=?,full_name=?,blocked=?,allowed_areas=?,
                    telegram_user_id=?,profile_complete=?,profile_locked=?,updated_at=? WHERE id=?''',
                    (login, full_name, int(blocked), json.dumps(allowed_areas) if allowed_areas is not None else None,
                     user_id, int(complete), int(profile_locked) if profile_locked is not None else old['profile_locked'], now, employee_id))
                details = {'old': dict(old), 'new': {'login':login,'full_name':full_name,'blocked':blocked,
                    'allowed_areas':allowed_areas,'telegram_user_id':user_id,'profile_locked':profile_locked}}
                connection.execute('INSERT INTO employee_audit(employee_id,actor,action,details,created_at) VALUES (?,?,?,?,?)',
                    (employee_id, actor, 'admin_updated', json.dumps(details,ensure_ascii=False), now))
        except sqlite3.IntegrityError:
            raise ValueError('Логин или Telegram ID уже используется другим сотрудником') from None
        return self.get(employee_id)

    def clear_self(self, user_id: str, employee_id: str, expected_updated_at: str) -> dict:
        now = datetime.now().isoformat(timespec='microseconds')
        with self.connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            current = connection.execute('SELECT * FROM employees WHERE telegram_user_id=?',(str(user_id),)).fetchone()
            if not current or current['id'] != employee_id or current['updated_at'] != expected_updated_at:
                raise ValueError('Профиль изменился. Откройте «Мои данные» и повторите действие')
            if current['blocked'] or current['profile_locked']:
                raise ValueError('Изменение и удаление данных запрещено администратором')
            name = current['telegram_name'] or f'Telegram {user_id}'
            connection.execute('UPDATE employees SET login=?,full_name=?,profile_complete=0,updated_at=? WHERE id=?',
                ('@telegram:'+uuid4().hex,name,now,employee_id))
            connection.execute('INSERT INTO employee_audit(employee_id,actor,action,details,created_at) VALUES(?,?,?,?,?)',
                (employee_id,'Telegram','self_cleared',json.dumps({'previous_login':current['login'],'previous_name':current['full_name']},ensure_ascii=False),now))
        return self.get(employee_id)

    def area_enabled(self, employee_id: str, area_id: str) -> bool:
        with self.connect() as connection:
            row = connection.execute('SELECT enabled FROM employee_regions WHERE employee_id=? AND area_id=?',
                (employee_id,area_id)).fetchone()
        return not row or bool(row['enabled'])

    def areas_for(self, employee_id: str) -> list[str]:
        with self.connect() as connection:
            return [row['area_id'] for row in connection.execute('SELECT area_id FROM employee_regions WHERE employee_id=?',(employee_id,))]

    def set_area_access(self, employee_id: str, area_id: str, enabled: bool, actor: str) -> None:
        with self.connect() as connection:
            cursor = connection.execute('UPDATE employee_regions SET enabled=? WHERE employee_id=? AND area_id=?',
                (int(enabled),employee_id,area_id))
            if not cursor.rowcount:
                raise KeyError(employee_id)
            connection.execute('INSERT INTO employee_audit(employee_id,actor,action,details,created_at) VALUES(?,?,?,?,?)',
                (employee_id,actor,'area_access',json.dumps({'area_id':area_id,'enabled':enabled}),datetime.now().isoformat(timespec='seconds')))

    def page(self, query: str = '', page: int = 1, page_size: int = 20, area_id: str | None = None) -> dict:
        with self.connect() as connection:
            if area_id:
                rows = connection.execute('''SELECT employees.*,employee_regions.enabled AS area_enabled
                    FROM employees JOIN employee_regions ON employees.id=employee_regions.employee_id
                    WHERE employee_regions.area_id=? ORDER BY full_name COLLATE NOCASE''', (area_id,)).fetchall()
            else:
                rows = connection.execute('SELECT * FROM employees ORDER BY full_name COLLATE NOCASE').fetchall()
        items = [self.public(row) for row in rows]
        term = query.strip().casefold()
        if term.startswith('@'):
            term = term[1:]
        if term:
            items = [item for item in items if term in (' '.join(str(item.get(key) or '') for key in
                ('full_name','login','telegram_user_id','telegram_name','telegram_username'))).casefold()]
        total = len(items)
        return {'items':items[(page-1)*page_size:page*page_size], 'page':page,'total':total,
                'total_pages':max(1,(total+page_size-1)//page_size)}

    def audit(self, employee_id: str) -> list[dict]:
        with self.connect() as connection:
            items = [dict(row) for row in connection.execute('SELECT * FROM employee_audit WHERE employee_id=? ORDER BY id DESC LIMIT 100',(employee_id,))]
        for item in items:
            item['created_at_display'] = display_date(item['created_at'])
        return items
