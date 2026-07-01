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
        if not event_ids:
            return 0

        with self._lock, self._connect() as connection:
            cursor = connection.executemany(
                '''
                UPDATE press_history
                SET acknowledged_at = COALESCE(acknowledged_at, ?),
                    acknowledged_at_display = COALESCE(acknowledged_at_display, ?)
                WHERE id = ?
                ''',
                [(acknowledged_at, display, event_id) for event_id in event_ids],
            )
        return cursor.rowcount

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

    def page(self, page: int, page_size: int, area_id: str | None = None) -> dict:
        offset = (page - 1) * page_size
        where = ''
        params: list = []
        if area_id:
            where = 'WHERE area_id = ?'
            params.append(area_id)

        with self._lock, self._connect() as connection:
            total = connection.execute(f'SELECT COUNT(*) FROM press_history {where}', params).fetchone()[0]
            rows = connection.execute(
                f'''
                SELECT id, device_id, device_name, pressed_at, pressed_at_display,
                       acknowledged_at, acknowledged_at_display, area_id, area_name
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
