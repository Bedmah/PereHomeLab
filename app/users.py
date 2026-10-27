import hashlib
import json
import secrets
import sqlite3
from datetime import datetime
from pathlib import Path
from threading import Lock


class UserStore:
    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = Lock()
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._db_path)
        connection.row_factory = sqlite3.Row
        connection.create_function('casefold',1,lambda value: str(value or '').casefold(),deterministic=True)
        return connection

    def _init_db(self) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                '''
                CREATE TABLE IF NOT EXISTS admin_users (
                    username TEXT PRIMARY KEY,
                    password_hash TEXT NOT NULL,
                    role TEXT NOT NULL DEFAULT 'admin',
                    is_active INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    last_login_at TEXT
                )
                '''
            )
            columns = {row['name'] for row in connection.execute('PRAGMA table_info(admin_users)')}
            if 'allowed_areas' not in columns:
                connection.execute('ALTER TABLE admin_users ADD COLUMN allowed_areas TEXT')
            connection.execute('''CREATE TABLE IF NOT EXISTS admin_actions (
                id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT NOT NULL,
                action TEXT NOT NULL, target TEXT NOT NULL, created_at TEXT NOT NULL)''')
            connection.execute(
                '''
                CREATE TABLE IF NOT EXISTS admin_login_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT NOT NULL,
                    success INTEGER NOT NULL,
                    role TEXT,
                    remote_addr TEXT,
                    user_agent TEXT,
                    message TEXT,
                    created_at TEXT NOT NULL
                )
                '''
            )

    @staticmethod
    def _now() -> str:
        return datetime.now().isoformat(timespec='seconds')

    @staticmethod
    def hash_password(password: str) -> str:
        salt = secrets.token_hex(16)
        digest = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt.encode('utf-8'), 200_000)
        return f'pbkdf2_sha256$200000${salt}${digest.hex()}'

    @staticmethod
    def verify_password(password: str, password_hash: str) -> bool:
        try:
            algorithm, rounds, salt, expected = password_hash.split('$', 3)
            if algorithm != 'pbkdf2_sha256':
                return False
            digest = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt.encode('utf-8'), int(rounds))
            return secrets.compare_digest(digest.hex(), expected)
        except (ValueError, TypeError):
            return False

    def ensure_owner(self, username: str, password: str) -> None:
        clean_username = username.strip()
        if not clean_username or not password:
            return
        now = self._now()
        with self._lock, self._connect() as connection:
            existing = connection.execute(
                'SELECT username FROM admin_users WHERE username = ?',
                (clean_username,),
            ).fetchone()
            if existing:
                connection.execute(
                    '''
                    UPDATE admin_users
                    SET role = 'owner',
                        is_active = 1,
                        password_hash = ?,
                        updated_at = ?
                    WHERE username = ?
                    ''',
                    (self.hash_password(password), now, clean_username),
                )
                return
            connection.execute(
                '''
                INSERT INTO admin_users (username, password_hash, role, is_active, created_at, updated_at)
                VALUES (?, ?, 'owner', 1, ?, ?)
                ''',
                (clean_username, self.hash_password(password), now, now),
            )

    def authenticate(self, username: str, password: str) -> dict | None:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                '''
                SELECT username, password_hash, role, is_active, allowed_areas
                FROM admin_users
                WHERE username = ?
                ''',
                (username,),
            ).fetchone()
            if not row or not row['is_active']:
                return None
            if not self.verify_password(password, row['password_hash']):
                return None
            now = self._now()
            connection.execute(
                'UPDATE admin_users SET last_login_at = ?, updated_at = ? WHERE username = ?',
                (now, now, username),
            )
            return {'username': row['username'], 'role': row['role'],
                    'allowed_areas': json.loads(row['allowed_areas'] or '[]') if row['role'] == 'location' else None}

    def list_users(self) -> list[dict]:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                '''
                SELECT username, role, allowed_areas, is_active, created_at, updated_at, last_login_at
                FROM admin_users
                ORDER BY role DESC, username COLLATE NOCASE
                '''
            ).fetchall()
        return [self.public_user(row) for row in rows]

    @staticmethod
    def public_user(row) -> dict:
        return dict(row) | {'is_active': bool(row['is_active']),
            'allowed_areas': json.loads(row['allowed_areas'] or '[]') if row['role'] == 'location' else None}

    def create_user(self, username: str, password: str, role: str = 'admin', allowed_areas: list[str] | None = None) -> dict:
        clean_username = username.strip()
        if not clean_username:
            raise ValueError('Имя пользователя не заполнено')
        if len(clean_username) > 100 or any(char in clean_username for char in '/:') or any(ord(char) < 32 for char in clean_username):
            raise ValueError('Некорректный логин')
        if role not in {'admin', 'location'}:
            raise ValueError('Некорректная роль')
        if len(password) < 6:
            raise ValueError('Пароль должен быть не короче 6 символов')
        now = self._now()
        with self._lock, self._connect() as connection:
            connection.execute(
                '''
                INSERT INTO admin_users (username, password_hash, role, allowed_areas, is_active, created_at, updated_at)
                VALUES (?, ?, ?, ?, 1, ?, ?)
                ''',
                (clean_username, self.hash_password(password), role, json.dumps(allowed_areas or []) if role == 'location' else None, now, now),
            )
        return self.get_user(clean_username)

    def get_user(self, username: str) -> dict:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                '''
                SELECT username, role, allowed_areas, is_active, created_at, updated_at, last_login_at
                FROM admin_users
                WHERE username = ?
                ''',
                (username,),
            ).fetchone()
        if not row:
            raise KeyError(username)
        return self.public_user(row)

    def credential(self, username: str) -> str | None:
        with self._connect() as connection:
            row = connection.execute('SELECT password_hash FROM admin_users WHERE username=?', (username,)).fetchone()
        return row['password_hash'] if row else None

    def set_access(self, username: str, role: str, allowed_areas: list[str]) -> dict:
        if role not in {'admin', 'location'}:
            raise ValueError('Некорректная роль')
        with self._lock, self._connect() as connection:
            cursor = connection.execute('''UPDATE admin_users SET role=?, allowed_areas=?, updated_at=?
                WHERE username=? AND role != 'owner' ''',
                (role, json.dumps(list(dict.fromkeys(allowed_areas))) if role == 'location' else None, self._now(), username))
            if not cursor.rowcount:
                raise KeyError(username)
        return self.get_user(username)

    def add_action(self, username: str, action: str, target: str) -> None:
        with self._lock, self._connect() as connection:
            connection.execute('INSERT INTO admin_actions(username,action,target,created_at) VALUES(?,?,?,?)',
                (username, action, target, self._now()))

    def actions(self, limit: int = 120) -> list[dict]:
        with self._lock, self._connect() as connection:
            return [dict(row) for row in connection.execute(
                'SELECT username,action,target,created_at FROM admin_actions ORDER BY id DESC LIMIT ?', (limit,))]

    def set_password(self, username: str, password: str) -> dict:
        if len(password) < 6:
            raise ValueError('Пароль должен быть не короче 6 символов')
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                '''
                UPDATE admin_users
                SET password_hash = ?, updated_at = ?
                WHERE username = ?
                ''',
                (self.hash_password(password), self._now(), username),
            )
            if cursor.rowcount == 0:
                raise KeyError(username)
            if connection.execute("SELECT 1 FROM sqlite_master WHERE name='admin_sessions'").fetchone():
                connection.execute('DELETE FROM admin_sessions WHERE username=?', (username,))
        return self.get_user(username)

    def set_active(self, username: str, is_active: bool) -> dict:
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                '''
                UPDATE admin_users
                SET is_active = ?, updated_at = ?
                WHERE username = ? AND role != 'owner'
                ''',
                (1 if is_active else 0, self._now(), username),
            )
            if cursor.rowcount == 0:
                raise KeyError(username)
            if not is_active and connection.execute("SELECT 1 FROM sqlite_master WHERE name='admin_sessions'").fetchone():
                connection.execute('DELETE FROM admin_sessions WHERE username=?', (username,))
        return self.get_user(username)

    def delete_user(self, username: str) -> None:
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM admin_users WHERE username = ? AND role != 'owner'",
                (username,),
            )
            if cursor.rowcount == 0:
                raise KeyError(username)
            if connection.execute("SELECT 1 FROM sqlite_master WHERE name='admin_sessions'").fetchone():
                connection.execute('DELETE FROM admin_sessions WHERE username=?', (username,))

    def add_login_log(
        self,
        username: str,
        success: bool,
        role: str | None,
        remote_addr: str | None,
        user_agent: str | None,
        message: str,
    ) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                '''
                INSERT INTO admin_login_log (username, success, role, remote_addr, user_agent, message, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ''',
                (username, 1 if success else 0, role, remote_addr, user_agent, message, self._now()),
            )

    def login_logs(self, limit: int = 200) -> list[dict]:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                '''
                SELECT username, success, role, remote_addr, user_agent, message, created_at
                FROM admin_login_log
                ORDER BY id DESC
                LIMIT ?
                ''',
                (limit,),
            ).fetchall()
        return [dict(row) | {'success': bool(row['success'])} for row in rows]
