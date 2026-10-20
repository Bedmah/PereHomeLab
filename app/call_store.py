from datetime import datetime
from uuid import uuid4

from app.dates import display_date


class StaleCall(ValueError):
    pass


class CallStore:
    def __init__(self, history, employees):
        self.history = history
        self.employees = employees
        with history._connect() as connection:
            for name, definition in [('revision','INTEGER NOT NULL DEFAULT 0'),('silenced_at','TEXT'),('last_released_at','TEXT')]:
                history._ensure_column(connection,'press_history',name,definition)
            connection.execute("UPDATE press_history SET silenced_at=acknowledged_at WHERE silenced_at IS NULL AND acknowledged_at IS NOT NULL AND COALESCE(acknowledged_source,'') != 'telegram'")
            connection.executescript('''
                CREATE TABLE IF NOT EXISTS call_actions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,event_id TEXT NOT NULL,action TEXT NOT NULL,
                    actor_user_id TEXT,actor_name TEXT NOT NULL,body TEXT NOT NULL,created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_call_actions_event ON call_actions(event_id,id);
                CREATE TABLE IF NOT EXISTS call_comments (
                    id TEXT PRIMARY KEY,event_id TEXT NOT NULL,author_user_id TEXT NOT NULL,
                    author_name TEXT NOT NULL,body TEXT NOT NULL,created_at TEXT NOT NULL,dedupe_key TEXT UNIQUE
                );
                CREATE INDEX IF NOT EXISTS idx_call_comments_event ON call_comments(event_id,created_at);
                CREATE TABLE IF NOT EXISTS call_receipts (id TEXT PRIMARY KEY, created_at TEXT NOT NULL);
            ''')

    @staticmethod
    def assigned(row: dict) -> bool:
        return bool(row.get('acknowledged_user_id') and row.get('acknowledged_source') and row.get('acknowledged_at'))

    def decorate(self, row: dict, profiles: dict | None = None) -> dict:
        item = dict(row)
        assigned = self.assigned(item)
        profile = (profiles.get(item['acknowledged_user_id']) if profiles is not None
                   else self.employees.by_user(item['acknowledged_user_id'])) if assigned and item.get('acknowledged_source') == 'telegram' else None
        item['assignee_name'] = profile['display_name'] if profile else item.get('acknowledged_by') if assigned else None
        login = profile.get('login') if profile else None
        username = profile.get('telegram_username') if profile else None
        telegram = ('@' + username.lstrip('@')) if username else (profile.get('telegram_name') if profile else None)
        if assigned and item.get('acknowledged_source') == 'telegram':
            telegram = telegram or f'Telegram ID {item["acknowledged_user_id"]}'
        item['assignee_login'] = login or None
        item['assignee_telegram'] = telegram
        item['assignee_short'] = (login or telegram or item['assignee_name']) if assigned else None
        if assigned and item.get('acknowledged_source') == 'telegram':
            item['assignee_details'] = (profile['display_name'] + ' · Telegram: ' + telegram
                                        if login else 'Telegram: ' + telegram)
        else:
            item['assignee_details'] = item['assignee_name']
        item['call_state'] = 'accepted' if assigned else 'waiting'
        source = item.get('acknowledged_source') if assigned else None
        item['assignee_source'] = source
        item['assignee_source_label'] = {'telegram': 'Telegram', 'web': 'Веб-интерфейс'}.get(source, source)
        item['sound_silenced'] = assigned or bool(item.get('silenced_at'))
        item['revision'] = int(item.get('revision') or 0)
        for field in ('pressed_at','acknowledged_at','silenced_at','last_released_at'):
            item[field+'_display'] = display_date(item.get(field))
        return item

    def get(self, event_id: str) -> dict | None:
        row = self.history.get_press(event_id)
        return self.decorate(row) if row else None

    def decorate_many(self, rows: list[dict]) -> list[dict]:
        profiles = self.employees.by_users(row.get('acknowledged_user_id') for row in rows if row.get('acknowledged_source') == 'telegram')
        return [self.decorate(row, profiles) for row in rows]

    def get_many(self, event_ids: list[str]) -> list[dict]:
        return self.decorate_many(self.history.get_presses(event_ids))

    def change(self, event_id: str, action: str, user_id: str, name: str, expected: int | None = None,
               receipt: str | None = None, allow_reassign: bool = True) -> dict | None:
        if action not in {'claim', 'takeover', 'release', 'assign'}:
            raise ValueError('Неизвестное действие')
        now = datetime.now().isoformat(timespec='seconds')
        with self.history._lock, self.history._connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            if receipt:
                if not connection.execute('INSERT OR IGNORE INTO call_receipts VALUES(?,?)', (receipt, now)).rowcount:
                    return None
            raw = connection.execute('SELECT * FROM press_history WHERE id=?',(event_id,)).fetchone()
            if not raw:
                raise ValueError('Вызов не найден')
            row = dict(raw)
            if expected is not None and row['revision'] != expected:
                raise StaleCall('Вызов уже изменился. Обновите сообщение и повторите действие')
            assigned = self.assigned(row)
            if assigned and action in {'assign', 'release', 'takeover'}:
                if not allow_reassign and row['acknowledged_user_id'] != str(user_id):
                    raise ValueError('В этом регионе менять ответственного может только принявший вызов')
            if action == 'assign':
                if assigned and row['acknowledged_user_id'] == str(user_id):
                    return None
                action = 'takeover' if assigned else 'claim'
            if action == 'claim' and assigned:
                return None
            if action == 'takeover' and not assigned:
                raise StaleCall('Вызов уже освобождён. Нажмите «Я иду»')
            if action == 'takeover' and row['acknowledged_user_id'] == str(user_id):
                raise ValueError('Вы уже приняли этот вызов')
            if action == 'release':
                if not assigned:
                    raise StaleCall('Вызов уже освобождён')
                connection.execute('''UPDATE press_history SET acknowledged_at=NULL,acknowledged_at_display=NULL,
                    acknowledged_by=NULL,acknowledged_user_id=NULL,acknowledged_source=NULL,
                    silenced_at=?,last_released_at=?,revision=revision+1 WHERE id=?''',(now,now,event_id))
            else:
                connection.execute('''UPDATE press_history SET acknowledged_at=?,acknowledged_at_display=?,
                    acknowledged_by=?,acknowledged_user_id=?,acknowledged_source='telegram',
                    silenced_at=?,revision=revision+1 WHERE id=?''',(now,display_date(now),name,str(user_id),now,event_id))
            previous = row.get('acknowledged_by') if assigned else None
            body = f'Предыдущий ответственный: {previous}' if previous else ''
            connection.execute('INSERT INTO call_actions(event_id,action,actor_user_id,actor_name,body,created_at) VALUES(?,?,?,?,?,?)',
                (event_id,action,str(user_id),name,body,now))
        return self.get(event_id)

    def silence(self, event_ids: list[str]) -> list[dict]:
        now = datetime.now().isoformat(timespec='seconds')
        items = []
        with self.history._lock, self.history._connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            for event_id in dict.fromkeys(event_ids):
                cursor = connection.execute('UPDATE press_history SET silenced_at=?,revision=revision+1 WHERE id=? AND silenced_at IS NULL',(now,event_id))
                if cursor.rowcount:
                    connection.execute('INSERT INTO call_actions(event_id,action,actor_name,body,created_at) VALUES(?,?,?,?,?)',
                        (event_id,'silence','Веб-монитор','Звуковое оповещение отключено',now))
                    items.append(dict(connection.execute('SELECT * FROM press_history WHERE id=?',(event_id,)).fetchone()))
        return [self.decorate(row) for row in items]

    def comment(self, event_id: str, user_id: str, name: str, body: str, dedupe_key: str) -> dict:
        body = body.strip()
        if not 1 <= len(body) <= 2000:
            raise ValueError('Комментарий должен содержать от 1 до 2000 символов')
        now = datetime.now().isoformat(timespec='seconds')
        with self.history._lock, self.history._connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            if not connection.execute('SELECT 1 FROM press_history WHERE id=?',(event_id,)).fetchone():
                raise ValueError('Вызов не найден')
            cursor = connection.execute('INSERT OR IGNORE INTO call_comments VALUES(?,?,?,?,?,?,?)',
                (uuid4().hex,event_id,str(user_id),name,body,now,dedupe_key))
            if cursor.rowcount:
                connection.execute('UPDATE press_history SET revision=revision+1 WHERE id=?',(event_id,))
                connection.execute('INSERT INTO call_actions(event_id,action,actor_user_id,actor_name,body,created_at) VALUES(?,?,?,?,?,?)',
                    (event_id,'comment',str(user_id),name,body,now))
        return self.get(event_id)

    def details(self, event_id: str, include_actions: bool = True) -> dict | None:
        item = self.get(event_id)
        if not item:
            return None
        with self.history._connect() as connection:
            item['comments'] = [dict(row) for row in connection.execute('SELECT * FROM call_comments WHERE event_id=? ORDER BY created_at,id',(event_id,))]
            if include_actions:
                item['actions'] = [dict(row) for row in connection.execute('SELECT * FROM call_actions WHERE event_id=? ORDER BY id',(event_id,))]
        for entry in item['comments'] + item.get('actions', []):
            entry['created_at_display'] = display_date(entry['created_at'])
        return item

    def refresh_profile(self, user_ids: list[str]) -> list[dict]:
        ids = [str(user_id) for user_id in user_ids if user_id]
        if not ids:
            return []
        placeholders = ','.join('?' for _ in ids)
        with self.history._lock, self.history._connect() as connection:
            rows = connection.execute(f'SELECT id FROM press_history WHERE acknowledged_user_id IN ({placeholders})',ids).fetchall()
            connection.execute(f'UPDATE press_history SET revision=revision+1 WHERE acknowledged_user_id IN ({placeholders})',ids)
        return [self.get(row['id']) for row in rows]
