import hashlib
import secrets
import time


class AdminSessions:
    cookie_name = 'perehomelab_admin'
    lifetime = 12 * 60 * 60

    def __init__(self, users):
        self.users = users
        with users._connect() as connection:
            connection.execute('''CREATE TABLE IF NOT EXISTS admin_sessions (
                token_hash TEXT PRIMARY KEY, username TEXT NOT NULL, credential TEXT NOT NULL,
                csrf TEXT NOT NULL, expires_at REAL NOT NULL)''')

    @staticmethod
    def digest(value):
        return hashlib.sha256(value.encode()).hexdigest()

    def create(self, username, credential):
        token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        with self.users._connect() as connection:
            connection.execute('DELETE FROM admin_sessions WHERE expires_at <= ?', (time.time(),))
            connection.execute('INSERT INTO admin_sessions VALUES(?,?,?,?,?)',
                (self.digest(token), username, credential, csrf, time.time() + self.lifetime))
        return token, csrf

    def resolve(self, token, credential_for):
        if not token or len(token) > 100:
            return None
        with self.users._connect() as connection:
            row = connection.execute('SELECT * FROM admin_sessions WHERE token_hash=? AND expires_at>?',
                (self.digest(token),time.time())).fetchone()
        if not row:
            return None
        try:
            user = self.users.get_user(row['username'])
        except KeyError:
            return None
        credential = credential_for(user)
        if not user['is_active'] or not credential or not secrets.compare_digest(credential, row['credential']):
            return None
        return user, row['csrf']

    def delete(self, token):
        with self.users._connect() as connection:
            connection.execute('DELETE FROM admin_sessions WHERE token_hash=?', (self.digest(token or ''),))

    def revoke(self, username):
        with self.users._connect() as connection:
            connection.execute('DELETE FROM admin_sessions WHERE username=?', (username,))

    def throttled(self, username, remote_addr):
        cutoff = time.strftime('%Y-%m-%dT%H:%M:%S', time.localtime(time.time() - 300))
        with self.users._connect() as connection:
            count = connection.execute('''SELECT COUNT(*) FROM admin_login_log WHERE success=0 AND created_at>=?
                AND (username=? OR remote_addr=?)''', (cutoff,username,remote_addr)).fetchone()[0]
        return count >= 10
