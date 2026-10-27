import asyncio
import time
from datetime import datetime, timedelta

from app.redaction import redact_sensitive


class TelegramPrivate:
    SUBSCRIBE = '🔕 Уведомления выключены · Подписаться'
    UNSUBSCRIBE = '🔔 Уведомления включены · Отписаться'

    def __init__(self, service):
        self.service = service
        self.backoff = {}
        with service.store.connect() as connection:
            ordered = connection.execute("SELECT 1 FROM sqlite_master WHERE name='telegram_private_events'").fetchone() is not None
            connection.executescript('''
                CREATE TABLE IF NOT EXISTS telegram_private_events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,event_id TEXT NOT NULL UNIQUE);
                INSERT INTO telegram_private_events(event_id) SELECT h.id FROM press_history h WHERE h.live_notified=1
                    AND NOT EXISTS(SELECT 1 FROM telegram_private_events o WHERE o.event_id=h.id) ORDER BY h.rowid;
                CREATE TRIGGER IF NOT EXISTS private_live_insert AFTER INSERT ON press_history WHEN NEW.live_notified=1 BEGIN
                    INSERT OR IGNORE INTO telegram_private_events(event_id) VALUES(NEW.id);
                END;
                CREATE TRIGGER IF NOT EXISTS private_live_update AFTER UPDATE OF live_notified ON press_history WHEN NEW.live_notified=1 BEGIN
                    INSERT OR IGNORE INTO telegram_private_events(event_id) VALUES(NEW.id);
                END;
                CREATE TRIGGER IF NOT EXISTS private_history_delete AFTER DELETE ON press_history BEGIN
                    DELETE FROM telegram_private_events WHERE event_id=OLD.id;
                END;
                CREATE TABLE IF NOT EXISTS telegram_contacts (
                    bot_id INTEGER NOT NULL,user_id TEXT NOT NULL,area_id TEXT NOT NULL,
                    blocked INTEGER NOT NULL DEFAULT 0,PRIMARY KEY(bot_id,user_id));
                CREATE TABLE IF NOT EXISTS telegram_subscriptions (
                    bot_id INTEGER NOT NULL,user_id TEXT NOT NULL,area_id TEXT NOT NULL,
                    enabled INTEGER NOT NULL,start_rowid INTEGER NOT NULL,last_error TEXT,
                    PRIMARY KEY(bot_id,user_id));
                CREATE TABLE IF NOT EXISTS telegram_private_outbox (
                    event_id TEXT NOT NULL,bot_id INTEGER NOT NULL,user_id TEXT NOT NULL,area_id TEXT NOT NULL,
                    state TEXT NOT NULL DEFAULT 'queued',message_id INTEGER,attempts INTEGER NOT NULL DEFAULT 0,
                    next_attempt REAL NOT NULL DEFAULT 0,expires_at REAL NOT NULL,synced_revision INTEGER NOT NULL DEFAULT -1,
                    last_error TEXT,PRIMARY KEY(event_id,bot_id,user_id));
                CREATE INDEX IF NOT EXISTS idx_private_outbox_due ON telegram_private_outbox(state,next_attempt);
                CREATE INDEX IF NOT EXISTS idx_private_subscriptions_area ON telegram_subscriptions(area_id,enabled,user_id);
                CREATE INDEX IF NOT EXISTS idx_private_subscriptions_user ON telegram_subscriptions(user_id,enabled,area_id);
                CREATE INDEX IF NOT EXISTS idx_live_private_history ON press_history(pressed_at) WHERE live_notified=1;
                CREATE TRIGGER IF NOT EXISTS revoke_private_profile AFTER UPDATE OF profile_locked,blocked,allowed_areas,telegram_user_id ON employees BEGIN
                    INSERT INTO employee_audit(employee_id,actor,action,details,created_at)
                    SELECT NEW.id,'Система','private_unsubscribed',json_object('area_id',area_id,'reason','Подтверждение или доступ изменены'),
                        strftime('%Y-%m-%dT%H:%M:%S','now','localtime') FROM telegram_subscriptions
                    WHERE enabled=1 AND user_id=OLD.telegram_user_id AND (NEW.profile_locked=0 OR NEW.blocked=1
                        OR NEW.telegram_user_id IS NOT OLD.telegram_user_id OR (NEW.allowed_areas IS NOT NULL
                            AND NOT EXISTS(SELECT 1 FROM json_each(NEW.allowed_areas) WHERE value=telegram_subscriptions.area_id)));
                    UPDATE telegram_subscriptions SET enabled=0,last_error='Подтверждение или доступ изменены'
                    WHERE user_id=OLD.telegram_user_id AND (NEW.profile_locked=0 OR NEW.blocked=1
                        OR NEW.telegram_user_id IS NOT OLD.telegram_user_id OR (NEW.allowed_areas IS NOT NULL
                            AND NOT EXISTS(SELECT 1 FROM json_each(NEW.allowed_areas) WHERE value=telegram_subscriptions.area_id)));
                    UPDATE telegram_private_outbox SET state='canceled' WHERE user_id=OLD.telegram_user_id AND state='queued'
                        AND NOT EXISTS(SELECT 1 FROM telegram_subscriptions s WHERE s.bot_id=telegram_private_outbox.bot_id
                            AND s.user_id=telegram_private_outbox.user_id AND s.enabled=1);
                END;
                CREATE TRIGGER IF NOT EXISTS revoke_private_area AFTER UPDATE OF enabled ON employee_regions WHEN NEW.enabled=0 BEGIN
                    INSERT INTO employee_audit(employee_id,actor,action,details,created_at)
                    SELECT NEW.employee_id,'Система','private_unsubscribed',json_object('area_id',NEW.area_id,'reason','Доступ к локации отключён'),
                        strftime('%Y-%m-%dT%H:%M:%S','now','localtime') FROM telegram_subscriptions
                    WHERE enabled=1 AND user_id=(SELECT telegram_user_id FROM employees WHERE id=NEW.employee_id) AND area_id=NEW.area_id;
                    UPDATE telegram_subscriptions SET enabled=0,last_error='Доступ к локации отключён'
                    WHERE user_id=(SELECT telegram_user_id FROM employees WHERE id=NEW.employee_id) AND area_id=NEW.area_id;
                    UPDATE telegram_private_outbox SET state='canceled' WHERE area_id=NEW.area_id AND state='queued'
                        AND user_id=(SELECT telegram_user_id FROM employees WHERE id=NEW.employee_id);
                END;
            ''')
            if not ordered:
                connection.execute('UPDATE telegram_subscriptions SET start_rowid=(SELECT COALESCE(MAX(sequence),0) FROM telegram_private_events)')

    def register(self, setting, user):
        with self.service.store.connect() as connection:
            connection.execute('''INSERT INTO telegram_contacts(bot_id,user_id,area_id) VALUES(?,?,?)
                ON CONFLICT(bot_id,user_id) DO UPDATE SET area_id=excluded.area_id,blocked=0''',
                (setting['bot_id'],str(user['id']),setting['area_id']))

    def eligible(self, setting, user_id):
        profile = self.service.employees.by_user(user_id)
        if not profile or not profile['profile_locked']:
            raise ValueError('Для уведомлений и действий в ЛС обратитесь к администратору для подтверждения профиля')
        if (profile['blocked'] or not self.service.employees.area_enabled(profile['id'],setting['area_id'])
                or (profile['allowed_areas'] is not None and setting['area_id'] not in profile['allowed_areas'])):
            raise ValueError('Доступ к этой локации ограничен администратором')
        with self.service.store.connect() as connection:
            contact = connection.execute('SELECT * FROM telegram_contacts WHERE bot_id=? AND user_id=? AND area_id=?',
                (setting['bot_id'],str(user_id),setting['area_id'])).fetchone()
        if not contact or contact['blocked']:
            raise ValueError('Сначала откройте личный чат с ботом и отправьте /start')
        return profile

    def state(self, setting, user_id):
        with self.service.store.connect() as connection:
            row = connection.execute('''SELECT c.blocked,s.enabled,s.last_error FROM telegram_contacts c
                LEFT JOIN telegram_subscriptions s ON s.bot_id=c.bot_id AND s.user_id=c.user_id AND s.area_id=c.area_id
                WHERE c.bot_id=? AND c.user_id=? AND c.area_id=?''',
                (setting['bot_id'],str(user_id),setting['area_id'])).fetchone()
        return {'opened':bool(row),'blocked':bool(row and row['blocked']),
                'enabled':bool(row and row['enabled']),'last_error':row['last_error'] if row else None}

    def set_subscription(self, setting, user_id, enabled, actor='Telegram', reason=''):
        profile = self.service.employees.by_user(user_id)
        with self.service.store.connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            if enabled:
                self.eligible(setting,user_id)
            old = connection.execute('SELECT enabled,area_id FROM telegram_subscriptions WHERE bot_id=? AND user_id=?',
                (setting['bot_id'],str(user_id))).fetchone()
            if enabled:
                connection.execute('''INSERT INTO telegram_subscriptions(bot_id,user_id,area_id,enabled,start_rowid)
                    VALUES(?,?,?,1,(SELECT COALESCE(MAX(sequence),0) FROM telegram_private_events))
                    ON CONFLICT(bot_id,user_id) DO UPDATE SET area_id=excluded.area_id,enabled=1,
                    start_rowid=CASE WHEN telegram_subscriptions.enabled=1 AND telegram_subscriptions.area_id=excluded.area_id THEN telegram_subscriptions.start_rowid ELSE excluded.start_rowid END,last_error=NULL''',
                    (setting['bot_id'],str(user_id),setting['area_id']))
            else:
                connection.execute('UPDATE telegram_subscriptions SET enabled=0,last_error=? WHERE bot_id=? AND user_id=?',
                    (reason or None,setting['bot_id'],str(user_id)))
                connection.execute("UPDATE telegram_private_outbox SET state='canceled' WHERE bot_id=? AND user_id=? AND state='queued'",
                    (setting['bot_id'],str(user_id)))
            if profile and bool(old and old['enabled'] and old['area_id']==setting['area_id']) != bool(enabled):
                import json
                connection.execute('INSERT INTO employee_audit(employee_id,actor,action,details,created_at) VALUES(?,?,?,?,?)',
                    (profile['id'],actor,'private_subscribed' if enabled else 'private_unsubscribed',
                     json.dumps({'area_id':setting['area_id'],'reason':reason},ensure_ascii=False),datetime.now().isoformat(timespec='seconds')))

    def reconcile(self):
        with self.service.store.connect() as connection:
            rows = connection.execute('''SELECT s.* FROM telegram_subscriptions s
                LEFT JOIN employees e ON e.telegram_user_id=s.user_id
                LEFT JOIN employee_regions er ON er.employee_id=e.id AND er.area_id=s.area_id
                LEFT JOIN telegram_contacts c ON c.bot_id=s.bot_id AND c.user_id=s.user_id AND c.area_id=s.area_id
                LEFT JOIN telegram_regions r ON r.bot_id=s.bot_id AND r.area_id=s.area_id
                WHERE s.enabled=1 AND (e.id IS NULL OR e.profile_locked=0 OR e.blocked=1 OR er.employee_id IS NULL
                    OR er.enabled=0 OR c.user_id IS NULL OR c.blocked=1 OR r.bot_id IS NULL
                    OR (e.allowed_areas IS NOT NULL AND NOT EXISTS(SELECT 1 FROM json_each(e.allowed_areas) WHERE value=s.area_id)))''').fetchall()
        for row in rows:
            setting = self.service._setting(row['area_id'])
            try:
                if not setting or setting['bot_id'] != row['bot_id']:
                    raise ValueError('Бот локации изменён')
                self.eligible(setting,row['user_id'])
            except ValueError as exc:
                self.set_subscription(dict(row),row['user_id'],False,'Система',str(exc))

    def enqueue(self, event_id=None):
        with self.service.store.connect() as connection:
            connection.create_function('press_epoch',1,self.epoch)
            connection.execute('''WITH recent AS MATERIALIZED (
                    SELECT * FROM press_history WHERE live_notified=1 AND pressed_at>=?
                    AND (? IS NULL OR id=?) AND press_epoch(pressed_at)+?>?)
                INSERT OR IGNORE INTO telegram_private_outbox(event_id,bot_id,user_id,area_id,expires_at)
                SELECT h.id,s.bot_id,s.user_id,s.area_id,press_epoch(h.pressed_at)+?
                FROM telegram_subscriptions s JOIN telegram_regions r ON r.bot_id=s.bot_id AND r.area_id=s.area_id
                JOIN city_settings c ON c.area_id=s.area_id JOIN recent h ON h.area_id=s.area_id
                JOIN telegram_private_events ordering ON ordering.event_id=h.id
                WHERE s.enabled=1 AND r.enabled=1 AND c.enabled=1 AND ordering.sequence>s.start_rowid
                AND press_epoch(h.pressed_at)>=CAST(r.enabled_since AS INTEGER)
                AND NOT EXISTS(SELECT 1 FROM telegram_private_outbox o WHERE o.event_id=h.id AND o.bot_id=s.bot_id AND o.user_id=s.user_id)
                LIMIT 1000''',
                ((datetime.now()-timedelta(days=3)).isoformat(),event_id,event_id,
                 self.service.config.telegram_event_ttl_seconds,time.time(),self.service.config.telegram_event_ttl_seconds))

    @staticmethod
    def epoch(value):
        try:
            return datetime.fromisoformat(value).timestamp()
        except (ValueError,TypeError,OverflowError):
            return 0

    def delivery(self, event_id, bot_id, user_id):
        with self.service.store.connect() as connection:
            row = connection.execute('SELECT * FROM telegram_private_outbox WHERE event_id=? AND bot_id=? AND user_id=?',
                (event_id,bot_id,str(user_id))).fetchone()
        return dict(row) if row else None

    def has_copy(self, event_id, bot_id):
        with self.service.store.connect() as connection:
            return connection.execute("SELECT 1 FROM telegram_private_outbox WHERE event_id=? AND bot_id=? AND state IN ('sent','sending','uncertain') LIMIT 1",
                (event_id,bot_id)).fetchone() is not None

    def update(self, row, state, message_id=None, revision=-1, error=None, delay=0):
        with self.service.store.connect() as connection:
            connection.execute('''UPDATE telegram_private_outbox SET state=?,message_id=COALESCE(?,message_id),
                synced_revision=CASE WHEN ?>=0 THEN ? ELSE synced_revision END,last_error=?,next_attempt=?,
                attempts=CASE WHEN ?='sent' AND ?>=0 THEN 0 ELSE attempts+1 END
                WHERE event_id=? AND bot_id=? AND user_id=?''',
                (state,message_id,revision,revision,error,time.time()+delay,state,revision,row['event_id'],row['bot_id'],row['user_id']))
            if error or (state=='sent' and revision>=0):
                connection.execute('UPDATE telegram_subscriptions SET last_error=? WHERE bot_id=? AND user_id=?',
                    (error,row['bot_id'],row['user_id']))

    async def deliver(self, row):
        setting = self.service._setting(row['area_id'])
        try:
            if not setting or setting['bot_id'] != row['bot_id']:
                raise ValueError('Бот локации изменён')
            self.eligible(setting,row['user_id'])
            city = self.service._city(row['area_id'])
            if not city:
                raise ValueError('Локация удалена')
            if not setting['enabled'] or not city.enabled:
                self.update(row,row['state'],delay=10)
                return
            if row['state']=='queued':
                with self.service.store.connect() as connection:
                    current = connection.execute('''SELECT 1 FROM telegram_subscriptions s
                        JOIN telegram_private_events e ON e.event_id=? AND e.sequence>s.start_rowid
                        WHERE s.bot_id=? AND s.user_id=? AND s.area_id=? AND s.enabled=1''',
                        (row['event_id'],row['bot_id'],row['user_id'],row['area_id'])).fetchone()
                if not current:
                    self.update(row,'canceled')
                    return
            event = self.service.calls.details(row['event_id'])
            if not event:
                raise ValueError('Вызов не найден')
            payload = {'chat_id':int(row['user_id']),'text':self.service.message_text(event),'parse_mode':'HTML',
                       'reply_markup':self.service.interactions.keyboard(setting,event)}
            if row['state']=='queued':
                self.update(row,'sending')
                sent = await self.service.api.call(self.service.store.token(setting),'sendMessage',**payload)
                self.update(row,'sent',sent['message_id'],event['revision'])
            else:
                await self.service.api.call(self.service.store.token(setting),'editMessageText',message_id=row['message_id'],**payload)
                self.update(row,'sent',revision=event['revision'])
        except asyncio.CancelledError:
            if row['state']=='queued':
                self.update(row,'uncertain',error='Отправка прервана. Повтор отключён для защиты от дублей')
            raise
        except Exception as exc:
            code = getattr(exc,'code',0)
            if code==400 and 'message is not modified' in str(exc):
                self.update(row,'sent',revision=event['revision'])
                return
            error = redact_sensitive(str(exc)) if isinstance(exc,ValueError) or code else 'Не удалось доставить сообщение'
            if code==403:
                with self.service.store.connect() as connection:
                    connection.execute('UPDATE telegram_contacts SET blocked=1 WHERE bot_id=? AND user_id=?',(row['bot_id'],row['user_id']))
                self.set_subscription(dict(row),row['user_id'],False,'Система','Бот заблокирован получателем')
            retry = getattr(exc,'retryable',False) and not getattr(exc,'uncertain',False) and row['attempts']<8
            delay = max(2,getattr(exc,'retry_after',0),min(60,2**min(row['attempts'],6)))
            if code==429:
                self.backoff[row['bot_id']]=time.time()+delay
            uncertain = getattr(exc,'uncertain',False) or (row['state']=='queued' and not code and not isinstance(exc,ValueError) and not retry)
            state = row['state'] if retry else ('uncertain' if uncertain else 'failed')
            if uncertain:
                error='Доставка не подтверждена. Автоматический повтор отключён'
            self.update(row,state,error=error,delay=delay)

    async def tick(self, recover=True):
        self.reconcile()
        if recover:
            self.enqueue()
        with self.service.store.connect() as connection:
            connection.execute("UPDATE telegram_private_outbox SET state='expired' WHERE state='queued' AND expires_at<=?",(time.time(),))
            paused = [bot_id for bot_id,until in self.backoff.items() if until>time.time()]
            ready = ' AND o.bot_id NOT IN ('+','.join('?' for _ in paused)+')' if paused else ''
            rows = connection.execute('''SELECT * FROM (
                SELECT o.*,ROW_NUMBER() OVER(PARTITION BY o.bot_id,o.user_id ORDER BY o.next_attempt,o.rowid) AS priority
                FROM telegram_private_outbox o JOIN press_history h ON h.id=o.event_id
                WHERE o.next_attempt<=?'''+ready+''' AND (o.state='queued' OR (o.state='sent' AND o.synced_revision<h.revision)))
                WHERE priority=1 ORDER BY next_attempt LIMIT 4''',(time.time(),*paused)).fetchall()
        await asyncio.gather(*(self.deliver(dict(row)) for row in rows))

    async def run(self):
        recovered = 0
        with self.service.store.connect() as connection:
            interrupted = connection.execute("SELECT * FROM telegram_private_outbox WHERE state='sending'").fetchall()
        for row in interrupted:
            self.update(dict(row),'uncertain',error='Отправка прервана. Повтор отключён для защиты от дублей')
        while self.service.running:
            try:
                refresh = time.monotonic()-recovered>=10
                await self.tick(refresh)
                if refresh:
                    recovered=time.monotonic()
            except asyncio.CancelledError:
                raise
            except Exception:
                self.service.logger.warning('Private Telegram delivery check failed; retrying')
            await asyncio.sleep(1)

    def menu(self, token, profile):
        setting = next((item for item in self.service.store.settings() if str(item['bot_id'])==token.split(':')[0]),None)
        if not setting or not profile:
            return None
        try:
            self.eligible(setting,profile['telegram_user_id'])
        except ValueError:
            return None
        return self.UNSUBSCRIBE if self.state(setting,profile['telegram_user_id'])['enabled'] else self.SUBSCRIBE

    async def message(self, setting, token, user, text):
        command = text.split(maxsplit=1)[0].lower() if text else ''
        subscribe = {'/subscribe','/subscribe@'+setting['bot_username'].lower()}
        unsubscribe = {'/unsubscribe','/unsubscribe@'+setting['bot_username'].lower()}
        subscribe_buttons = {self.SUBSCRIBE,'🔔 Подписаться на уведомления'}
        unsubscribe_buttons = {self.UNSUBSCRIBE,'🔕 Отписаться от уведомлений'}
        if text not in subscribe_buttons | unsubscribe_buttons and command not in subscribe | unsubscribe:
            return False
        self.service.profile_ui.cancel(setting,user)
        for flow in self.service.flows.comments_for_user(setting['bot_id'],user['id'],user['id']):
            self.service.flows.update(flow['id'],done=True)
            await self.service.interactions.finish(token,flow,'Ввод отменён')
        enabled = text in subscribe_buttons or command in subscribe
        try:
            self.set_subscription(setting,user['id'],enabled)
            answer = 'Подписка включена. Будут приходить только новые вызовы этой локации.' if enabled else 'Подписка отключена. Ваши принятые вызовы не отменены.'
        except ValueError as exc:
            answer = str(exc)
        await self.service.profile_ui.show(token,user,answer)
        return True
