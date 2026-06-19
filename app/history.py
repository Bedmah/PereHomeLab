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
            connection.execute(
                '''
                CREATE INDEX IF NOT EXISTS idx_press_history_pressed_at
                ON press_history (pressed_at DESC)
                '''
            )
            connection.execute(
                '''
                CREATE UNIQUE INDEX IF NOT EXISTS idx_press_history_device_time
                ON press_history (device_id, pressed_at)
                '''
            )

    def add_press(self, event_id: str, device_id: str, device_name: str, pressed_at: str, pressed_at_display: str) -> bool:
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                '''
                INSERT OR IGNORE INTO press_history
                    (id, device_id, device_name, pressed_at, pressed_at_display)
                VALUES (?, ?, ?, ?, ?)
                ''',
                (event_id, device_id, device_name, pressed_at, pressed_at_display),
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

    def page(self, page: int, page_size: int) -> dict:
        offset = (page - 1) * page_size
        with self._lock, self._connect() as connection:
            total = connection.execute('SELECT COUNT(*) FROM press_history').fetchone()[0]
            rows = connection.execute(
                '''
                SELECT id, device_id, device_name, pressed_at, pressed_at_display, acknowledged_at, acknowledged_at_display
                FROM press_history
                ORDER BY pressed_at DESC
                LIMIT ? OFFSET ?
                ''',
                (page_size, offset),
            ).fetchall()

        total_pages = max(1, (total + page_size - 1) // page_size)
        return {
            'items': [dict(row) for row in rows],
            'page': page,
            'page_size': page_size,
            'total': total,
            'total_pages': total_pages,
        }
