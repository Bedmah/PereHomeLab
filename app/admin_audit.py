import json
import re
from datetime import datetime
from urllib.parse import unquote


FIELDS = {
    'support_url':'Ссылка поддержки',
    'display_name':'Название', 'slug':'Ссылка', 'enabled':'Включено', 'color':'Цвет',
    'sound_path':'Звук', 'sound_repeat_mode':'Режим повтора', 'sound_repeat_seconds':'Проигрывание, секунд',
    'sound_repeat_count':'Повторов', 'history_sync_hours':'История, часов', 'ack_button_text':'Кнопка подтверждения',
    'require_profile':'Требовать профиль', 'allow_reassign_any':'Переназначение сотрудниками',
    'chat_title':'Группа', 'chat_id':'ID группы', 'thread_id':'Тема', 'bot_username':'Бот',
    'login':'Корпоративный логин', 'full_name':'ФИО', 'blocked':'Блокировка', 'profile_locked':'Запрет изменения профиля',
    'telegram_user_id':'Telegram ID', 'allowed_areas':'Локации', 'role':'Роль', 'is_active':'Активен',
}


def audit_change(admin, category, summary, target='', before=None, after=None, area_ids=None, details=None):
    changes = [{'field':key,'label':label,'before':(before or {}).get(key),'after':(after or {}).get(key)}
        for key,label in FIELDS.items() if key in (before or {}) or key in (after or {})
        if (before or {}).get(key) != (after or {}).get(key)]
    if before is not None and after is not None and not changes and not details:
        return
    admin.setdefault('_changes', []).append({'category':category,'summary':summary,'target':target,
        'area_ids':area_ids or [], 'changes':changes, 'details':details or ''})


class AdminAudit:
    def __init__(self, users):
        self.users = users
        with users._connect() as connection:
            connection.executescript('''CREATE TABLE IF NOT EXISTS admin_changes (
                id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT NOT NULL, category TEXT NOT NULL,
                summary TEXT NOT NULL, target TEXT NOT NULL, changes TEXT NOT NULL, details TEXT NOT NULL,
                area_ids TEXT NOT NULL, created_at TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS idx_admin_changes_date ON admin_changes(created_at,id);''')
            connection.execute('CREATE TABLE IF NOT EXISTS admin_audit_migrations(name TEXT PRIMARY KEY)')
            if not connection.execute("SELECT 1 FROM admin_audit_migrations WHERE name='legacy'").fetchone():
                connection.execute('''INSERT INTO admin_changes(id,username,category,summary,target,changes,details,area_ids,created_at)
                    SELECT -id,username,'legacy','Действие до обновления журнала',target,'[]',action,'[]',created_at
                    FROM admin_actions WHERE -id NOT IN (SELECT id FROM admin_changes)''')
                connection.execute("INSERT INTO admin_audit_migrations VALUES('legacy')")
            if connection.execute("SELECT 1 FROM sqlite_master WHERE name='city_settings'").fetchone():
                known = {row['area_id'] for row in connection.execute('SELECT area_id FROM city_settings')}
                for row in connection.execute("SELECT id,target FROM admin_changes WHERE category='legacy' AND area_ids='[]'"):
                    suffix = row['target'].split(' · ')[-1]
                    match = re.search(r'/(?:telegram|areas)/([^/ ·]+)',row['target'])
                    area_id = suffix if suffix in known else unquote(match[1]) if match else ''
                    if area_id in known:
                        connection.execute('UPDATE admin_changes SET area_ids=? WHERE id=?',(json.dumps([area_id]),row['id']))

    def save(self, username, changes):
        with self.users._connect() as connection:
            for entry in changes:
                connection.execute('''INSERT INTO admin_changes(username,category,summary,target,changes,details,area_ids,created_at)
                    VALUES(?,?,?,?,?,?,?,?)''', (username,entry['category'],entry['summary'],entry['target'],
                    json.dumps(entry['changes'],ensure_ascii=False),entry['details'],json.dumps(entry['area_ids']),
                    datetime.now().isoformat(timespec='seconds')))

    def page(self, admin, page=1, page_size=30, area_id='', category='', actor='', query='', date_from='', date_to=''):
        where, params = [], []
        if admin['role'] == 'location':
            allowed = admin.get('allowed_areas') or []
            if not allowed:
                where.append('0')
            else:
                where.append('EXISTS (SELECT 1 FROM json_each(admin_changes.area_ids) WHERE value IN (' + ','.join('?' for _ in allowed) + '))')
                params.extend(allowed)
        scope_clause = ' WHERE ' + ' AND '.join(where) if where else ''
        scope_params = list(params)
        if area_id:
            where.append('EXISTS (SELECT 1 FROM json_each(admin_changes.area_ids) WHERE value=?)'); params.append(area_id)
        for field,value in [('category',category),('username',actor)]:
            if value:
                where.append(field+'=?'); params.append(value)
        if query:
            where.append("instr(casefold(username||' '||summary||' '||target||' '||changes),casefold(?))>0"); params.append(query)
        if date_from:
            where.append('created_at>=?'); params.append(date_from)
        if date_to:
            where.append('created_at<?'); params.append(date_to+'T24')
        clause = ' WHERE ' + ' AND '.join(where) if where else ''
        with self.users._connect() as connection:
            actors = [row['username'] for row in connection.execute('SELECT DISTINCT username FROM admin_changes'+scope_clause+' ORDER BY username',scope_params)]
            total = connection.execute('SELECT COUNT(*) FROM admin_changes'+clause,params).fetchone()[0]
            rows = connection.execute('SELECT * FROM admin_changes'+clause+' ORDER BY created_at DESC,id DESC LIMIT ? OFFSET ?',
                [*params,page_size,(page-1)*page_size]).fetchall()
        items = []
        for row in rows:
            item = dict(row)
            item['changes'] = json.loads(item['changes'])
            item['area_ids'] = json.loads(item['area_ids'])
            if admin['role'] == 'location':
                item['area_ids'] = [area for area in item['area_ids'] if area in admin.get('allowed_areas', [])]
                for change in item['changes']:
                    if change['field'] == 'allowed_areas':
                        for side in ('before','after'):
                            if isinstance(change[side],list):
                                change[side] = [area for area in change[side] if area in admin.get('allowed_areas',[])]
            items.append(item)
        return {'items':items,'total':total,'page':page,'actors':actors,'total_pages':max(1,(total+page_size-1)//page_size)}
