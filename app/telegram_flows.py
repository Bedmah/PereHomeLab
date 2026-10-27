import json
import time
from uuid import uuid4


class TelegramFlows:
    def __init__(self, store):
        self.store = store
        with store.connect() as connection:
            connection.executescript('''CREATE TABLE IF NOT EXISTS telegram_flows (
                id TEXT PRIMARY KEY,bot_id INTEGER NOT NULL,user_id TEXT NOT NULL,
                kind TEXT NOT NULL,event_id TEXT,revision INTEGER,chat_id INTEGER NOT NULL,
                thread_id INTEGER,prompt_id INTEGER,step TEXT NOT NULL,data TEXT NOT NULL,
                expires_at REAL NOT NULL,done INTEGER NOT NULL DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS idx_tg_flows_user ON telegram_flows(bot_id,user_id,kind,done);
            CREATE TABLE IF NOT EXISTS telegram_cleanup (
                bot_id INTEGER NOT NULL,chat_id INTEGER NOT NULL,message_id INTEGER NOT NULL,
                next_attempt REAL NOT NULL,expires_at REAL NOT NULL,attempts INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY(bot_id,chat_id,message_id)
            );
            ''')

    def delete_later(self, bot_id, chat_id, message_id, delay=0):
        if message_id is None:
            return
        with self.store.connect() as connection:
            connection.execute('INSERT OR IGNORE INTO telegram_cleanup(bot_id,chat_id,message_id,next_attempt,expires_at) VALUES(?,?,?,?,?)',
                (bot_id,chat_id,message_id,time.time()+delay,time.time()+86400))

    def cleanup_due(self):
        with self.store.connect() as connection:
            expired = connection.execute("SELECT * FROM telegram_flows WHERE kind IN ('comment','confirm') AND done=0 AND expires_at<=?",(time.time(),)).fetchall()
            for row in expired:
                if row['prompt_id'] is not None:
                    connection.execute('INSERT OR IGNORE INTO telegram_cleanup(bot_id,chat_id,message_id,next_attempt,expires_at) VALUES(?,?,?,?,?)',
                        (row['bot_id'],row['chat_id'],row['prompt_id'],time.time(),time.time()+86400))
                connection.execute("UPDATE telegram_flows SET done=1,step='retired' WHERE id=?",(row['id'],))
            connection.execute('DELETE FROM telegram_cleanup WHERE expires_at<=?',(time.time(),))
            return [dict(row) for row in connection.execute('SELECT * FROM telegram_cleanup WHERE next_attempt<=? ORDER BY next_attempt LIMIT 5',(time.time(),))]

    def cleanup_result(self, row, delay=None):
        key=(row['bot_id'],row['chat_id'],row['message_id'])
        with self.store.connect() as connection:
            if delay is None:
                connection.execute('DELETE FROM telegram_cleanup WHERE bot_id=? AND chat_id=? AND message_id=?',key)
            else:
                connection.execute('UPDATE telegram_cleanup SET attempts=attempts+1,next_attempt=? WHERE bot_id=? AND chat_id=? AND message_id=?',(time.time()+delay,*key))

    def retire_confirmations(self):
        with self.store.connect() as connection:
            for row in connection.execute("SELECT * FROM telegram_flows WHERE (kind='confirm' OR (kind='comment' AND done=1)) AND step!='retired'").fetchall():
                if row['prompt_id'] is not None:
                    connection.execute('INSERT OR IGNORE INTO telegram_cleanup(bot_id,chat_id,message_id,next_attempt,expires_at) VALUES(?,?,?,?,?)',
                        (row['bot_id'],row['chat_id'],row['prompt_id'],time.time(),time.time()+86400))
                connection.execute("UPDATE telegram_flows SET done=1,step='retired' WHERE id=?",(row['id'],))

    @staticmethod
    def decode(row):
        if not row:
            return None
        result = dict(row)
        result['data'] = json.loads(result['data'])
        return result

    def get(self, flow_id: str):
        with self.store.connect() as connection:
            return self.decode(connection.execute('SELECT * FROM telegram_flows WHERE id=? AND done=0 AND expires_at>?',(flow_id,time.time())).fetchone())

    def pending(self, bot_id, user_id, kind, event_id=None):
        with self.store.connect() as connection:
            return self.decode(connection.execute('''SELECT * FROM telegram_flows WHERE bot_id=? AND user_id=?
                AND kind=? AND event_id IS ? AND done=0 AND expires_at>? ORDER BY rowid DESC LIMIT 1''',
                (bot_id,str(user_id),kind,event_id,time.time())).fetchone())

    def for_reply(self, bot_id, chat_id, prompt_id):
        with self.store.connect() as connection:
            return self.decode(connection.execute('''SELECT * FROM telegram_flows WHERE bot_id=? AND chat_id=?
                AND prompt_id=? AND kind='comment' AND done=0 AND expires_at>?''',(bot_id,chat_id,prompt_id,time.time())).fetchone())

    def comments_for_user(self, bot_id, user_id, chat_id):
        with self.store.connect() as connection:
            return [self.decode(row) for row in connection.execute("SELECT * FROM telegram_flows WHERE bot_id=? AND user_id=? AND chat_id=? AND kind='comment' AND done=0 AND expires_at>?",
                (bot_id,str(user_id),chat_id,time.time()))]

    def for_original_reply(self, bot_id, user_id, chat_id, message_id):
        with self.store.connect() as connection:
            if chat_id==int(user_id):
                return self.decode(connection.execute('''SELECT f.* FROM telegram_flows f
                    JOIN telegram_private_outbox o ON o.event_id=f.event_id AND o.bot_id=f.bot_id AND o.user_id=f.user_id
                    WHERE f.bot_id=? AND f.user_id=? AND f.chat_id=? AND o.message_id=?
                        AND f.kind='comment' AND f.done=0 AND f.expires_at>? ORDER BY f.rowid DESC LIMIT 1''',
                    (bot_id,str(user_id),chat_id,message_id,time.time())).fetchone())
            return self.decode(connection.execute('''SELECT f.* FROM telegram_flows f JOIN telegram_outbox o ON o.event_id=f.event_id
                WHERE f.bot_id=? AND f.user_id=? AND f.chat_id=? AND o.message_id=? AND o.bot_id=f.bot_id AND o.chat_id=f.chat_id
                AND f.kind='comment' AND f.done=0 AND f.expires_at>? ORDER BY f.rowid DESC LIMIT 1''',
                (bot_id,str(user_id),chat_id,message_id,time.time())).fetchone())

    def create(self, setting, user_id, kind, chat_id, event_id=None, revision=None, thread_id=None, step='pending', data=None):
        flow_id = uuid4().hex
        with self.store.connect() as connection:
            connection.execute('DELETE FROM telegram_flows WHERE expires_at<?',(time.time()-86400,))
            connection.execute('INSERT INTO telegram_flows(id,bot_id,user_id,kind,event_id,revision,chat_id,thread_id,step,data,expires_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                (flow_id,setting['bot_id'],str(user_id),kind,event_id,revision,chat_id,thread_id,step,json.dumps(data or {},ensure_ascii=False),time.time()+900))
        return self.get(flow_id)

    def update(self, flow_id, *, step=None, data=None, prompt_id=None, done=False):
        with self.store.connect() as connection:
            connection.execute('''UPDATE telegram_flows SET step=COALESCE(?,step),data=COALESCE(?,data),
                prompt_id=COALESCE(?,prompt_id),done=? WHERE id=?''',
                (step,json.dumps(data,ensure_ascii=False) if data is not None else None,prompt_id,int(done),flow_id))
