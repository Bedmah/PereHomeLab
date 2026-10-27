import sqlite3
import time
from datetime import datetime, timedelta
from pathlib import Path
from uuid import uuid4

from cryptography.fernet import Fernet, InvalidToken


class TelegramStore:
    def __init__(self, db_path: Path, key: str | None) -> None:
        self.db_path = db_path
        self.cipher = None
        self.key_error = None
        if key:
            try:
                self.cipher = Fernet(key.encode('ascii'))
            except (ValueError, UnicodeError):
                self.key_error = 'Некорректный TELEGRAM_ENCRYPTION_KEY'
        else:
            self.key_error = 'Добавьте TELEGRAM_ENCRYPTION_KEY в secret.env и перезапустите приложение'
        with self.connect() as connection:
            connection.executescript('''
                CREATE TABLE IF NOT EXISTS telegram_regions (
                    area_id TEXT PRIMARY KEY, enabled INTEGER NOT NULL DEFAULT 0,
                    token_encrypted TEXT NOT NULL, bot_id INTEGER NOT NULL UNIQUE,
                    bot_username TEXT NOT NULL, chat_id INTEGER, thread_id INTEGER,
                    chat_title TEXT, enabled_since REAL NOT NULL,
                    last_error TEXT, updated_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS telegram_offsets (
                    bot_id INTEGER PRIMARY KEY, next_offset INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS telegram_setups (
                    id TEXT PRIMARY KEY, area_id TEXT NOT NULL, bot_id INTEGER NOT NULL,
                    chat_id INTEGER NOT NULL, thread_id INTEGER, chat_title TEXT NOT NULL,
                    created_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS telegram_outbox (
                    event_id TEXT PRIMARY KEY, area_id TEXT NOT NULL, bot_id INTEGER NOT NULL,
                    chat_id INTEGER NOT NULL, thread_id INTEGER,
                    state TEXT NOT NULL DEFAULT 'queued', message_id INTEGER,
                    attempts INTEGER NOT NULL DEFAULT 0, next_attempt REAL NOT NULL DEFAULT 0,
                    expires_at REAL NOT NULL, edit_synced INTEGER NOT NULL DEFAULT 0,
                    last_error TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_telegram_outbox_due
                    ON telegram_outbox(state, next_attempt);
            ''')
            columns = {row['name'] for row in connection.execute('PRAGMA table_info(telegram_regions)')}
            if 'setup_until' not in columns:
                connection.execute('ALTER TABLE telegram_regions ADD COLUMN setup_until REAL NOT NULL DEFAULT 0')
            if 'last_setup_id' not in columns:
                connection.execute('ALTER TABLE telegram_regions ADD COLUMN last_setup_id TEXT')
            for name, default in [('require_profile', 0), ('allow_reassign_any', 1)]:
                if name not in columns:
                    connection.execute(f'ALTER TABLE telegram_regions ADD COLUMN {name} INTEGER NOT NULL DEFAULT {default}')
            outbox_columns = {row['name'] for row in connection.execute('PRAGMA table_info(telegram_outbox)')}
            if 'synced_revision' not in outbox_columns:
                connection.execute('ALTER TABLE telegram_outbox ADD COLUMN synced_revision INTEGER NOT NULL DEFAULT -1')

    def set_policy(self, area_id, require_profile=None, allow_reassign_any=None):
        with self.connect() as connection:
            connection.execute('UPDATE telegram_regions SET require_profile=COALESCE(?,require_profile), allow_reassign_any=COALESCE(?,allow_reassign_any) WHERE area_id=?',
                (require_profile, allow_reassign_any, area_id))

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=5)
        connection.row_factory = sqlite3.Row
        return connection

    def settings(self, area_id: str | None = None) -> list[dict]:
        with self.connect() as connection:
            if area_id is None:
                rows = connection.execute('SELECT * FROM telegram_regions').fetchall()
            else:
                rows = connection.execute('SELECT * FROM telegram_regions WHERE area_id = ?', (area_id,)).fetchall()
        return [dict(row) for row in rows]

    def token(self, settings: dict) -> str:
        if self.cipher is None:
            raise ValueError(self.key_error)
        try:
            return self.cipher.decrypt(settings['token_encrypted'].encode('ascii')).decode('ascii')
        except (InvalidToken, UnicodeError, ValueError):
            raise ValueError('Токен не расшифрован. Восстановите прежний TELEGRAM_ENCRYPTION_KEY или введите токен заново') from None

    def save(self, area_id: str, enabled: bool, token: str | None, bot: dict | None) -> None:
        current = next(iter(self.settings(area_id)), None)
        if token:
            if self.cipher is None:
                raise ValueError(self.key_error)
            encrypted = self.cipher.encrypt(token.encode('ascii')).decode('ascii')
        elif current:
            self.token(current)
            encrypted = current['token_encrypted']
        else:
            raise ValueError('Сначала укажите токен бота')
        now = time.time()
        bot_id = int(bot['id']) if bot else current['bot_id']
        username = str(bot['username']) if bot else current['bot_username']
        changed_bot = current is not None and current['bot_id'] != bot_id
        enabled_since = now if not current or not current['enabled'] or changed_bot else current['enabled_since']
        setup_until = now + 900 if not current or changed_bot or (token and current['chat_id'] is None) else current['setup_until']
        try:
            with self.connect() as connection:
                connection.execute('''
                    INSERT INTO telegram_regions
                        (area_id, enabled, token_encrypted, bot_id, bot_username, enabled_since, updated_at, setup_until)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(area_id) DO UPDATE SET enabled = excluded.enabled,
                        token_encrypted = excluded.token_encrypted, bot_id = excluded.bot_id,
                        bot_username = excluded.bot_username, enabled_since = excluded.enabled_since,
                        updated_at = excluded.updated_at, setup_until = excluded.setup_until, last_error = NULL
                ''', (area_id, int(enabled), encrypted, bot_id, username, enabled_since, now, setup_until))
                if changed_bot:
                    connection.execute('UPDATE telegram_regions SET chat_id = NULL, thread_id = NULL, chat_title = NULL, last_setup_id = NULL WHERE area_id = ?', (area_id,))
                    connection.execute('DELETE FROM telegram_setups WHERE area_id = ?', (area_id,))
                if changed_bot or not enabled:
                    connection.execute("UPDATE telegram_outbox SET state = 'canceled' WHERE area_id = ? AND state = 'queued'", (area_id,))
        except sqlite3.IntegrityError:
            raise ValueError('Этот бот уже настроен для другой локации. Используйте отдельного бота') from None

    def delete(self, area_id: str) -> None:
        with self.connect() as connection:
            connection.execute('DELETE FROM telegram_regions WHERE area_id = ?', (area_id,))
            connection.execute('DELETE FROM telegram_setups WHERE area_id = ?', (area_id,))
            connection.execute("UPDATE telegram_outbox SET state = 'canceled' WHERE area_id = ? AND state = 'queued'", (area_id,))

    def set_error(self, area_id: str, error: str | None) -> None:
        with self.connect() as connection:
            connection.execute('UPDATE telegram_regions SET last_error = ? WHERE area_id = ?', (error, area_id))

    def offset(self, bot_id: int) -> int:
        with self.connect() as connection:
            row = connection.execute('SELECT next_offset FROM telegram_offsets WHERE bot_id = ?', (bot_id,)).fetchone()
        return row['next_offset'] if row else 0

    def save_offset(self, bot_id: int, offset: int) -> None:
        with self.connect() as connection:
            connection.execute('''INSERT INTO telegram_offsets VALUES (?, ?)
                ON CONFLICT(bot_id) DO UPDATE SET next_offset = max(next_offset, excluded.next_offset)''', (bot_id, offset))

    def add_setup(self, settings: dict, chat: dict, thread_id: int | None) -> str:
        setup_id = uuid4().hex
        with self.connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            current_settings = connection.execute('SELECT bot_id, setup_until FROM telegram_regions WHERE area_id = ?', (settings['area_id'],)).fetchone()
            if not current_settings or current_settings['bot_id'] != settings['bot_id'] or current_settings['setup_until'] <= time.time():
                raise ValueError('Окно привязки закрыто. Начните привязку в админке')
            connection.execute('DELETE FROM telegram_setups WHERE created_at < ?', (time.time() - 900,))
            current = connection.execute('''SELECT id FROM telegram_setups
                WHERE area_id = ? AND bot_id = ? AND chat_id = ? AND thread_id IS ?''',
                (settings['area_id'], settings['bot_id'], int(chat['id']), thread_id)).fetchone()
            if current:
                return current['id']
            count = connection.execute('SELECT count(*) FROM telegram_setups WHERE area_id = ?', (settings['area_id'],)).fetchone()[0]
            if count >= 5:
                raise ValueError('Слишком много предложений привязки. Начните привязку заново в админке')
            connection.execute('INSERT INTO telegram_setups VALUES (?, ?, ?, ?, ?, ?, ?)',
                               (setup_id, settings['area_id'], settings['bot_id'], int(chat['id']), thread_id, str(chat.get('title') or 'Группа'), time.time()))
        return setup_id

    def begin_setup(self, area_id: str) -> None:
        with self.connect() as connection:
            cursor = connection.execute('UPDATE telegram_regions SET setup_until = ? WHERE area_id = ?', (time.time() + 900, area_id))
            if not cursor.rowcount:
                raise ValueError('Сначала сохраните токен бота')
            connection.execute('DELETE FROM telegram_setups WHERE area_id = ?', (area_id,))

    def setups(self, area_id: str) -> list[dict]:
        with self.connect() as connection:
            rows = connection.execute('SELECT * FROM telegram_setups WHERE area_id = ? AND created_at >= ? ORDER BY created_at DESC', (area_id, time.time() - 900)).fetchall()
        return [dict(row) for row in rows]

    def confirm_setup(self, area_id: str, setup_id: str) -> None:
        with self.connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            current = connection.execute('SELECT * FROM telegram_regions WHERE area_id = ?', (area_id,)).fetchone()
            if current and current['last_setup_id'] == setup_id:
                return
            setup = connection.execute('''SELECT s.* FROM telegram_setups s JOIN telegram_regions r
                ON r.area_id = s.area_id AND r.bot_id = s.bot_id
                WHERE s.id = ? AND s.area_id = ? AND s.created_at >= ? AND r.setup_until > ?''',
                (setup_id, area_id, time.time() - 900, time.time())).fetchone()
            if not setup:
                raise ValueError('Привязка устарела. Отправьте /setup боту в нужной теме ещё раз')
            changed = current['chat_id'] != setup['chat_id'] or current['thread_id'] != setup['thread_id']
            connection.execute('''UPDATE telegram_regions SET chat_id = ?, thread_id = ?, chat_title = ?, setup_until = 0,
                last_setup_id = ?, updated_at = ?, enabled_since = ?, last_error = NULL WHERE area_id = ?''',
                (setup['chat_id'], setup['thread_id'], setup['chat_title'], setup_id,
                 time.time() if changed else current['updated_at'],
                 time.time() if changed else current['enabled_since'], area_id))
            connection.execute('DELETE FROM telegram_setups WHERE area_id = ?', (area_id,))
            if changed:
                connection.execute("UPDATE telegram_outbox SET state = 'canceled' WHERE area_id = ? AND state = 'queued'", (area_id,))

    def enqueue(self, event: dict, ttl: float) -> None:
        settings = next(iter(self.settings(event.get('area_id') or '')), None)
        if not settings or not settings['enabled'] or settings['chat_id'] is None:
            return
        pressed = datetime.fromisoformat(event['pressed_at']).timestamp()
        if pressed + ttl <= time.time() or pressed < int(settings['enabled_since']):
            return
        with self.connect() as connection:
            connection.execute('''INSERT OR IGNORE INTO telegram_outbox
                (event_id, area_id, bot_id, chat_id, thread_id, expires_at)
                VALUES (?, ?, ?, ?, ?, ?)''', (event['id'], settings['area_id'], settings['bot_id'], settings['chat_id'], settings['thread_id'], pressed + ttl))

    def recover_live(self, ttl: float) -> None:
        cutoff = (datetime.now() - timedelta(seconds=ttl)).isoformat(timespec='seconds')
        with self.connect() as connection:
            rows = connection.execute('''SELECT h.* FROM press_history h
                LEFT JOIN telegram_outbox o ON o.event_id = h.id
                WHERE h.live_notified = 1 AND NOT (h.acknowledged_at IS NOT NULL AND COALESCE(h.acknowledged_source,'')!='' AND COALESCE(h.acknowledged_user_id,'')!='')
                AND h.pressed_at >= ? AND o.event_id IS NULL LIMIT 1000''', (cutoff,)).fetchall()
        for row in rows:
            self.enqueue(dict(row), ttl)

    def recover_sends(self) -> None:
        with self.connect() as connection:
            connection.execute("UPDATE telegram_outbox SET state = 'uncertain', last_error = 'Отправка прервана перезапуском; повтор отключён для защиты от дублей' WHERE state = 'sending'")

    def due(self, exclude_areas: set[str] | None = None) -> dict | None:
        exclude_areas = exclude_areas or set()
        exclusions = ' AND o.area_id NOT IN (' + ','.join('?' for _ in exclude_areas) + ')' if exclude_areas else ''
        with self.connect() as connection:
            connection.execute("UPDATE telegram_outbox SET state = 'expired' WHERE state = 'queued' AND expires_at <= ?", (time.time(),))
            private_copy = ''
            if connection.execute("SELECT 1 FROM sqlite_master WHERE name='telegram_private_outbox'").fetchone():
                private_copy = " AND NOT EXISTS(SELECT 1 FROM telegram_private_outbox p WHERE p.event_id=telegram_outbox.event_id AND p.bot_id=telegram_outbox.bot_id AND p.state IN ('sent','sending','uncertain'))"
            connection.execute("UPDATE telegram_outbox SET state = 'canceled' WHERE state = 'queued' AND event_id IN (SELECT id FROM press_history WHERE acknowledged_at IS NOT NULL AND COALESCE(acknowledged_source,'')!='' AND COALESCE(acknowledged_user_id,'')!='')"+private_copy)
            row = connection.execute('''SELECT o.*, h.device_name, h.area_name, h.pressed_at_display
                FROM telegram_outbox o JOIN press_history h ON h.id = o.event_id
                WHERE o.state = 'queued' AND o.next_attempt <= ?''' + exclusions + ' ORDER BY h.pressed_at LIMIT 1',
                (time.time(), *exclude_areas)).fetchone()
        return dict(row) if row else None

    def update_delivery(self, event_id: str, state: str, message_id: int | None = None, error: str | None = None, delay: float = 0) -> None:
        with self.connect() as connection:
            connection.execute('''UPDATE telegram_outbox SET state = ?, message_id = COALESCE(?, message_id),
                attempts = attempts + ?, last_error = ?, next_attempt = ? WHERE event_id = ?''',
                (state, message_id, int(state == 'sending'), error, time.time() + delay, event_id))

    def delivery(self, event_id: str) -> dict | None:
        with self.connect() as connection:
            row = connection.execute('SELECT * FROM telegram_outbox WHERE event_id = ?', (event_id,)).fetchone()
        return dict(row) if row else None

    def pending_edits(self) -> list[dict]:
        with self.connect() as connection:
            rows = connection.execute('''SELECT o.*, h.device_name, h.area_name, h.pressed_at_display,
                    h.acknowledged_by, h.acknowledged_source, h.acknowledged_at_display, h.revision
                FROM telegram_outbox o JOIN press_history h ON h.id = o.event_id
                WHERE o.state = 'sent' AND (o.edit_synced = 0 OR o.synced_revision < h.revision)
                  AND o.next_attempt <= ? LIMIT 20''', (time.time(),)).fetchall()
        return [dict(row) for row in rows]

    def finish_edit(self, event_id: str, error: str | None = None, delay: float | None = None, revision: int | None = None) -> None:
        with self.connect() as connection:
            if revision is None:
                row = connection.execute('SELECT revision FROM press_history WHERE id=?',(event_id,)).fetchone()
                revision = row['revision'] if row else 0
            connection.execute('UPDATE telegram_outbox SET edit_synced = ?, synced_revision=CASE WHEN ? THEN ? ELSE synced_revision END, last_error = ?, next_attempt = ? WHERE event_id = ?',
                               (int(delay is None), int(delay is None), revision, error, time.time() + (delay or 0), event_id))

    def delivery_stats(self, area_id: str) -> dict:
        with self.connect() as connection:
            rows = connection.execute('SELECT state, count(*) AS total FROM telegram_outbox WHERE area_id = ? GROUP BY state', (area_id,)).fetchall()
        return {row['state']: row['total'] for row in rows}
