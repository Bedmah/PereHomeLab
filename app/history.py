import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from threading import Lock


class HistoryStore:
    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = Lock()
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._db_path)
        connection.row_factory = sqlite3.Row
        connection.create_function('casefold', 1, lambda value: str(value or '').casefold(), deterministic=True)
        return connection

    def _init_db(self) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                '''
                CREATE TABLE IF NOT EXISTS press_history (
                    id TEXT PRIMARY KEY,
                    device_id TEXT NOT NULL,
                    device_name TEXT NOT NULL,
                    pressed_at TEXT NOT NULL,
                    pressed_at_display TEXT NOT NULL,
                    acknowledged_at TEXT,
                    acknowledged_at_display TEXT
                )
                '''
            )
            self._ensure_column(connection, 'press_history', 'area_id', 'TEXT')
            self._ensure_column(connection, 'press_history', 'area_name', 'TEXT')
            self._ensure_column(connection, 'press_history', 'acknowledged_by', 'TEXT')
            self._ensure_column(connection, 'press_history', 'acknowledged_source', 'TEXT')
            self._ensure_column(connection, 'press_history', 'acknowledged_user_id', 'TEXT')
            self._ensure_column(connection, 'press_history', 'live_notified', 'INTEGER NOT NULL DEFAULT 0')
            self._ensure_column(connection, 'press_history', 'revision', 'INTEGER NOT NULL DEFAULT 0')
            self._ensure_column(connection, 'press_history', 'silenced_at', 'TEXT')
            self._ensure_column(connection, 'press_history', 'last_released_at', 'TEXT')
            connection.execute(
                '''
                CREATE INDEX IF NOT EXISTS idx_press_history_pressed_at
                ON press_history (pressed_at DESC)
                '''
            )
            connection.execute(
                '''
                CREATE INDEX IF NOT EXISTS idx_press_history_area_pressed_at
                ON press_history (area_id, pressed_at DESC)
                '''
            )
            connection.execute(
                '''
                CREATE UNIQUE INDEX IF NOT EXISTS idx_press_history_device_time
                ON press_history (device_id, pressed_at)
                '''
            )
            connection.execute(
                '''
                CREATE TABLE IF NOT EXISTS runtime_state (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                '''
            )

    @staticmethod
    def _ensure_column(connection: sqlite3.Connection, table: str, column: str, definition: str) -> None:
        columns = {row['name'] for row in connection.execute(f'PRAGMA table_info({table})').fetchall()}
        if column not in columns:
            connection.execute(f'ALTER TABLE {table} ADD COLUMN {column} {definition}')

    def add_press(
        self,
        event_id: str,
        device_id: str,
        device_name: str,
        pressed_at: str,
        pressed_at_display: str,
        area_id: str | None = None,
        area_name: str | None = None,
    ) -> bool:
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                '''
                INSERT OR IGNORE INTO press_history
                    (id, device_id, device_name, pressed_at, pressed_at_display, area_id, area_name)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ''',
                (event_id, device_id, device_name, pressed_at, pressed_at_display, area_id, area_name),
            )
            return cursor.rowcount > 0

    def has_nearby_press(self, device_id: str, pressed_at: datetime, window_seconds: float) -> bool:
        if window_seconds <= 0:
            return False

        start = pressed_at - timedelta(seconds=window_seconds)
        end = pressed_at + timedelta(seconds=window_seconds)
        with self._lock, self._connect() as connection:
            row = connection.execute(
                '''
                SELECT 1
                FROM press_history
                WHERE device_id = ?
                  AND pressed_at BETWEEN ? AND ?
                LIMIT 1
                ''',
                (device_id, start.isoformat(timespec='seconds'), end.isoformat(timespec='seconds')),
            ).fetchone()
        return row is not None

    def acknowledge(self, event_ids: list[str], acknowledged_at: str, display: str) -> int:
        return len(self.claim(event_ids, acknowledged_at, display, 'Веб-монитор', 'web'))

    def claim(
        self, event_ids: list[str], acknowledged_at: str, display: str,
        actor: str, source: str, user_id: str | None = None,
    ) -> list[dict]:
        if not event_ids:
            return []

        items = []
        with self._lock, self._connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            for event_id in dict.fromkeys(event_ids):
                cursor = connection.execute(
                    '''UPDATE press_history SET acknowledged_at = ?, acknowledged_at_display = ?,
                       acknowledged_by = ?, acknowledged_source = ?, acknowledged_user_id = ?
                       WHERE id = ? AND acknowledged_at IS NULL''',
                    (acknowledged_at, display, actor, source, user_id, event_id),
                )
                if cursor.rowcount:
                    items.append(dict(connection.execute('SELECT * FROM press_history WHERE id = ?', (event_id,)).fetchone()))
        return items

    def get_press(self, event_id: str) -> dict | None:
        with self._lock, self._connect() as connection:
            row = connection.execute('SELECT * FROM press_history WHERE id = ?', (event_id,)).fetchone()
        return dict(row) if row else None

    def get_presses(self, event_ids: list[str]) -> list[dict]:
        ids = list(dict.fromkeys(event_ids))
        if not ids:
            return []
        items = {}
        with self._lock, self._connect() as connection:
            for offset in range(0, len(ids), 500):
                batch = ids[offset:offset + 500]
                placeholders = ','.join('?' for _ in batch)
                rows = connection.execute(f'SELECT * FROM press_history WHERE id IN ({placeholders})', batch).fetchall()
                items.update({row['id']: dict(row) for row in rows})
        return [items[event_id] for event_id in ids if event_id in items]

    def nearby_press(self, device_id: str, pressed_at: datetime, seconds: float) -> dict | None:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                '''SELECT * FROM press_history WHERE device_id = ? AND pressed_at BETWEEN ? AND ?
                   ORDER BY pressed_at LIMIT 1''',
                (device_id, (pressed_at - timedelta(seconds=seconds)).isoformat(timespec='seconds'),
                 (pressed_at + timedelta(seconds=seconds)).isoformat(timespec='seconds')),
            ).fetchone()
        return dict(row) if row else None

    def mark_live_notified(self, event_id: str) -> bool:
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                '''UPDATE press_history SET live_notified = 1
                   WHERE id = ? AND live_notified = 0 AND acknowledged_at IS NULL''', (event_id,),
            )
        return cursor.rowcount > 0

    def backfill_areas(self, entity_areas: dict[str, tuple[str | None, str | None]]) -> int:
        if not entity_areas:
            return 0

        updates = [
            (area_id, area_name, device_id)
            for device_id, (area_id, area_name) in entity_areas.items()
            if area_id
        ]
        if not updates:
            return 0

        with self._lock, self._connect() as connection:
            cursor = connection.executemany(
                '''
                UPDATE press_history
                SET area_id = COALESCE(area_id, ?),
                    area_name = COALESCE(area_name, ?)
                WHERE device_id = ?
                  AND area_id IS NULL
                ''',
                updates,
            )
            return cursor.rowcount

    def page(self, page: int, page_size: int, area_id: str | None = None,
             query: str = '', state: str = '', date_from: str = '', date_to: str = '') -> dict:
        offset = (page - 1) * page_size
        clauses = []
        params: list = []
        if area_id:
            clauses.append('area_id = ?')
            params.append(area_id)
        assigned = "acknowledged_at IS NOT NULL AND COALESCE(acknowledged_source,'')!='' AND COALESCE(acknowledged_user_id,'')!=''"
        if state == 'accepted':
            clauses.append('(' + assigned + ')')
        elif state == 'waiting':
            clauses.append('NOT (' + assigned + ')')
        if date_from:
            clauses.append('pressed_at >= ?')
            params.append(date_from)
        if date_to:
            clauses.append('pressed_at < ?')
            params.append((datetime.fromisoformat(date_to) + timedelta(days=1)).isoformat(timespec='seconds'))
        if query.strip():
            pattern = '%' + query.strip().casefold().replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%'
            clauses.append("""(casefold(device_name || ' ' || COALESCE(area_name,'') || ' ' || COALESCE(acknowledged_by,'')) LIKE ? ESCAPE '\\'
                OR EXISTS(SELECT 1 FROM call_comments c WHERE c.event_id=press_history.id AND casefold(c.body || ' ' || c.author_name) LIKE ? ESCAPE '\\')
                OR EXISTS(SELECT 1 FROM employees e WHERE e.telegram_user_id=press_history.acknowledged_user_id AND casefold(e.full_name || ' ' || e.login) LIKE ? ESCAPE '\\'))""")
            params.extend([pattern] * 3)
        where = 'WHERE ' + ' AND '.join(clauses) if clauses else ''

        with self._lock, self._connect() as connection:
            total = connection.execute(f'SELECT COUNT(*) FROM press_history {where}', params).fetchone()[0]
            rows = connection.execute(
                f'''
                SELECT id, device_id, device_name, pressed_at, pressed_at_display,
                       acknowledged_at, acknowledged_at_display, area_id, area_name,
                       acknowledged_by, acknowledged_source, acknowledged_user_id,
                       revision, silenced_at, last_released_at
                FROM press_history
                {where}
                ORDER BY pressed_at DESC
                LIMIT ? OFFSET ?
                ''',
                (*params, page_size, offset),
            ).fetchall()

        total_pages = max(1, (total + page_size - 1) // page_size)
        return {
            'items': [dict(row) for row in rows],
            'page': page,
            'page_size': page_size,
            'total': total,
            'total_pages': total_pages,
            'area_id': area_id,
        }

    def latest_press(self) -> dict | None:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                '''
                SELECT id, device_id, device_name, pressed_at, pressed_at_display,
                       acknowledged_at, acknowledged_at_display, area_id, area_name
                FROM press_history
                ORDER BY pressed_at DESC
                LIMIT 1
                '''
            ).fetchone()
        return dict(row) if row else None

    def get_runtime_state(self, key: str) -> str | None:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                'SELECT value FROM runtime_state WHERE key = ?',
                (key,),
            ).fetchone()
        return str(row['value']) if row else None

    def set_runtime_state(self, key: str, value: str, updated_at: str) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                '''
                INSERT INTO runtime_state (key, value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value = excluded.value,
                    updated_at = excluded.updated_at
                ''',
                (key, value, updated_at),
            )
