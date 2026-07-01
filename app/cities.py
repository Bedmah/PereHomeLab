import sqlite3
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
import re


@dataclass(frozen=True)
class CityInfo:
    area_id: str
    ha_name: str
    display_name: str
    slug: str | None
    enabled: bool
    color: str
    sound_path: str | None = None
    sound_repeat_mode: str = 'seconds'
    sound_repeat_seconds: float | None = None
    sound_repeat_count: int | None = None
    history_sync_hours: float | None = None
    ack_button_text: str | None = None
    button_count: int = 0
    active_button_count: int = 0

    def to_dict(self) -> dict:
        return {
            'area_id': self.area_id,
            'slug': self.slug or slugify(self.display_name, self.area_id),
            'ha_name': self.ha_name,
            'display_name': self.display_name,
            'enabled': self.enabled,
            'color': self.color,
            'sound_path': self.sound_path,
            'sound_repeat_mode': self.sound_repeat_mode,
            'sound_repeat_seconds': self.sound_repeat_seconds,
            'sound_repeat_count': self.sound_repeat_count,
            'history_sync_hours': self.history_sync_hours,
            'ack_button_text': self.ack_button_text,
            'button_count': self.button_count,
            'active_button_count': self.active_button_count,
        }


def slugify(value: str, fallback: str) -> str:
    aliases = {
        'москва': 'msk',
        'питер': 'spb',
        'санкт-петербург': 'spb',
        'санкт петербург': 'spb',
        'екатеринбург': 'ekb',
    }
    normalized = value.strip().lower()
    if normalized in aliases:
        return aliases[normalized]

    clean = re.sub(r'[^a-zA-Z0-9]+', '-', normalized).strip('-').lower()
    if clean:
        return clean
    return re.sub(r'[^a-zA-Z0-9]+', '-', fallback.strip().lower()).strip('-') or fallback.strip().lower()


class CityStore:
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
                CREATE TABLE IF NOT EXISTS city_settings (
                    area_id TEXT PRIMARY KEY,
                    ha_name TEXT NOT NULL,
                    display_name TEXT NOT NULL,
                    slug TEXT,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    color TEXT NOT NULL DEFAULT '#60a5fa',
                    sound_path TEXT,
                    sound_repeat_mode TEXT NOT NULL DEFAULT 'seconds',
                    sound_repeat_seconds REAL,
                    sound_repeat_count INTEGER,
                    history_sync_hours REAL,
                    ack_button_text TEXT
                )
                '''
            )
            self._ensure_column(connection, 'city_settings', 'slug', 'TEXT')
            self._ensure_column(connection, 'city_settings', 'sound_path', 'TEXT')
            self._ensure_column(connection, 'city_settings', 'sound_repeat_mode', "TEXT NOT NULL DEFAULT 'seconds'")
            self._ensure_column(connection, 'city_settings', 'sound_repeat_seconds', 'REAL')
            self._ensure_column(connection, 'city_settings', 'sound_repeat_count', 'INTEGER')
            self._ensure_column(connection, 'city_settings', 'history_sync_hours', 'REAL')
            self._ensure_column(connection, 'city_settings', 'ack_button_text', 'TEXT')
            rows = connection.execute(
                'SELECT area_id, display_name FROM city_settings WHERE slug IS NULL OR slug = ""'
            ).fetchall()
            for row in rows:
                connection.execute(
                    'UPDATE city_settings SET slug = ? WHERE area_id = ?',
                    (slugify(row['display_name'], row['area_id']), row['area_id']),
                )

    @staticmethod
    def _ensure_column(connection: sqlite3.Connection, table: str, column: str, definition: str) -> None:
        columns = {row['name'] for row in connection.execute(f'PRAGMA table_info({table})').fetchall()}
        if column not in columns:
            connection.execute(f'ALTER TABLE {table} ADD COLUMN {column} {definition}')

    def sync_from_ha_areas(self, areas: list[dict], button_counts: dict[str, int] | None = None) -> None:
        with self._lock, self._connect() as connection:
            for area in areas:
                area_id = str(area.get('area_id') or '')
                if not area_id:
                    continue
                ha_name = str(area.get('name') or area_id)
                connection.execute(
                    '''
                    INSERT INTO city_settings (area_id, ha_name, display_name, slug)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(area_id) DO UPDATE SET ha_name = excluded.ha_name
                    ''',
                    (area_id, ha_name, ha_name, slugify(ha_name, area_id)),
                )

    def list(
        self,
        button_counts: dict[str, int] | None = None,
        include_disabled: bool = False,
        active_button_counts: dict[str, int] | None = None,
    ) -> list[CityInfo]:
        button_counts = button_counts or {}
        active_button_counts = active_button_counts or {}
        query = 'SELECT area_id, ha_name, display_name, slug, enabled, color, sound_path, sound_repeat_mode, sound_repeat_seconds, sound_repeat_count, history_sync_hours, ack_button_text FROM city_settings'
        params: tuple = ()
        if not include_disabled:
            query += ' WHERE enabled = 1'
        query += ' ORDER BY display_name COLLATE NOCASE'

        with self._lock, self._connect() as connection:
            rows = connection.execute(query, params).fetchall()

        return [
            CityInfo(
                area_id=row['area_id'],
                ha_name=row['ha_name'],
                display_name=row['display_name'],
                slug=row['slug'],
                enabled=bool(row['enabled']),
                color=row['color'],
                sound_path=row['sound_path'],
                sound_repeat_mode=row['sound_repeat_mode'] or 'seconds',
                sound_repeat_seconds=row['sound_repeat_seconds'],
                sound_repeat_count=row['sound_repeat_count'],
                history_sync_hours=row['history_sync_hours'],
                ack_button_text=row['ack_button_text'],
                button_count=button_counts.get(row['area_id'], 0),
                active_button_count=active_button_counts.get(row['area_id'], 0),
            )
            for row in rows
        ]

    def names_by_area_id(self) -> dict[str, str]:
        with self._lock, self._connect() as connection:
            rows = connection.execute('SELECT area_id, display_name FROM city_settings WHERE enabled = 1').fetchall()
        return {row['area_id']: row['display_name'] for row in rows}

    def clear_sound_path(self, sound_path: str) -> int:
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                'UPDATE city_settings SET sound_path = NULL WHERE sound_path = ?',
                (sound_path,),
            )
            return cursor.rowcount

    def upsert(
        self,
        area_id: str,
        display_name: str,
        enabled: bool = True,
        color: str = '#60a5fa',
        slug: str | None = None,
        sound_path: str | None = None,
        sound_repeat_mode: str = 'seconds',
        sound_repeat_seconds: float | None = None,
        sound_repeat_count: int | None = None,
        history_sync_hours: float | None = None,
        ack_button_text: str | None = None,
    ) -> CityInfo:
        clean_area_id = area_id.strip()
        clean_display_name = display_name.strip() or clean_area_id
        clean_slug = slugify(slug or clean_display_name, clean_area_id)
        clean_color = color.strip() or '#60a5fa'
        clean_sound_path = sound_path.strip() if sound_path and sound_path.strip() else None
        clean_repeat_mode = sound_repeat_mode if sound_repeat_mode in {'seconds', 'count'} else 'seconds'
        clean_repeat_seconds = max(0.1, float(sound_repeat_seconds)) if sound_repeat_seconds else None
        clean_repeat_count = max(1, int(sound_repeat_count)) if sound_repeat_count else None
        clean_history_sync_hours = max(0, float(history_sync_hours)) if history_sync_hours is not None else None
        clean_ack_button_text = ack_button_text.strip() if ack_button_text and ack_button_text.strip() else None
        with self._lock, self._connect() as connection:
            current = connection.execute(
                'SELECT ha_name FROM city_settings WHERE area_id = ?',
                (clean_area_id,),
            ).fetchone()
            ha_name = current['ha_name'] if current else clean_display_name
            connection.execute(
                '''
                INSERT INTO city_settings (
                    area_id, ha_name, display_name, slug, enabled, color, sound_path,
                    sound_repeat_mode, sound_repeat_seconds, sound_repeat_count, history_sync_hours, ack_button_text
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(area_id) DO UPDATE SET
                    display_name = excluded.display_name,
                    slug = excluded.slug,
                    enabled = excluded.enabled,
                    color = excluded.color,
                    sound_path = excluded.sound_path,
                    sound_repeat_mode = excluded.sound_repeat_mode,
                    sound_repeat_seconds = excluded.sound_repeat_seconds,
                    sound_repeat_count = excluded.sound_repeat_count,
                    history_sync_hours = excluded.history_sync_hours,
                    ack_button_text = excluded.ack_button_text
                ''',
                (
                    clean_area_id, ha_name, clean_display_name, clean_slug, 1 if enabled else 0, clean_color, clean_sound_path,
                    clean_repeat_mode, clean_repeat_seconds, clean_repeat_count, clean_history_sync_hours, clean_ack_button_text,
                ),
            )
        return CityInfo(
            clean_area_id, ha_name, clean_display_name, clean_slug, enabled, clean_color, clean_sound_path,
            clean_repeat_mode, clean_repeat_seconds, clean_repeat_count, clean_history_sync_hours, clean_ack_button_text,
        )
