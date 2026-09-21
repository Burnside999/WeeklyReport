"""Account ownership, document state and atomic legacy migration."""
import hashlib
import hmac
import json
import re
import secrets
import sqlite3

from .core import DEFAULTS, doc_id, now

CREDENTIAL_KEYS = ('client_id', 'open_id', 'access_token')
SMTP_KEYS = tuple(k for k in DEFAULTS if k.startswith('smtp_'))
ROLES = ('user', 'admin', 'superadmin')


def password_hash(password):
    if not isinstance(password, str) or not 12 <= len(password) <= 1024:
        raise ValueError('密码须为 12–1024 字符')
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(hashlib.sha256(password.encode()).digest(), salt=salt, n=16384, r=8, p=1)
    return salt.hex() + ':' + digest.hex()


def verify_password(password, encoded):
    salt, expected = encoded.split(':')
    digest = hashlib.scrypt(hashlib.sha256(password.encode()).digest(), salt=bytes.fromhex(salt), n=16384, r=8, p=1)
    return hmac.compare_digest(digest.hex(), expected)


def username(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{2,63}', value):
        raise ValueError('帐号须为 3–64 位字母、数字、点、下划线或短横线')
    return value


def document_name(value):
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > 80:
        raise ValueError('文档管理器名称须为 1–80 字')
    return value.strip()


def credentials(raw, previous, required=True):
    result = {k: previous.get(k, '') for k in CREDENTIAL_KEYS}
    for key in CREDENTIAL_KEYS:
        if key in raw:
            if not isinstance(raw[key], str) or len(raw[key]) > 4096 or any(c in raw[key] for c in '\r\n\x00'):
                raise ValueError('腾讯文档凭据格式错误')
            result[key] = raw[key].strip()
    if required and not all(result.values()):
        raise ValueError('请填写 Client ID、Open ID 和 Access Token')
    return result


class Accounts:
    def __init__(self, root, bootstrap_password):
        self.root, self.db = root, root.db
        self.db.execute('PRAGMA foreign_keys=ON')
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY, username TEXT NOT NULL COLLATE NOCASE UNIQUE,
                password TEXT NOT NULL, role TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1);
            CREATE TABLE IF NOT EXISTS documents (
                id TEXT PRIMARY KEY, owner_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                name TEXT NOT NULL COLLATE NOCASE, url TEXT NOT NULL,
                UNIQUE(owner_id, name));
            CREATE TABLE IF NOT EXISTS document_state (
                document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
                key TEXT NOT NULL, value TEXT NOT NULL, PRIMARY KEY(document_id, key));
        ''')
        if root.get('accounts_initialized'):
            return
        legacy = {k: json.loads(v) for k, v in self.db.execute('SELECT key,value FROM state')}
        settings = DEFAULTS | legacy.get('settings', {})
        uid = secrets.token_hex(16)
        encoded = password_hash(bootstrap_password)
        with self.db:
            self.db.execute('INSERT INTO users(id,username,password,role) VALUES (?,?,?,?)', (uid,'admin',encoded,'superadmin'))
            self._state('smtp', {k:settings[k] for k in SMTP_KEYS})
            self._state('credentials:'+uid, {k:settings[k] for k in CREDENTIAL_KEYS})
            if legacy:
                did = secrets.token_hex(16)
                self.db.execute('INSERT INTO documents VALUES (?,?,?,?)', (did,uid,'文档1',settings['document_url']))
                scoped = {k:v for k,v in settings.items() if k not in SMTP_KEYS + CREDENTIAL_KEYS and k not in ('refresh_token','client_secret','token_expires_at','file_id','smtp_recipient','smtp_recipient_name')}
                legacy['settings'] = scoped
                for key, value in legacy.items():
                    if key == 'export_attempts':
                        self._state('export_attempts:'+uid, value)
                    else:
                        self.db.execute('INSERT INTO document_state VALUES (?,?,?)', (did,key,json.dumps(value,ensure_ascii=False)))
            # Remove unscoped data and credentials only after all copies succeed.
            self.db.execute("DELETE FROM state WHERE key NOT IN ('smtp',?,?)", ('credentials:'+uid,'export_attempts:'+uid))
            self._state('accounts_initialized', True)

    def _state(self, key, value):
        self.db.execute('INSERT INTO state VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value', (key,json.dumps(value,ensure_ascii=False)))

    def user(self, uid=None, name=None):
        row = self.db.execute('SELECT id,username,password,role,version FROM users WHERE '+('id=?' if uid else 'username=?'), (uid or name,)).fetchone()
        return dict(zip(('id','username','password','role','version'),row)) if row else None

    @staticmethod
    def public(user):
        return {k:user[k] for k in ('id','username','role')}

    def users(self):
        return [dict(zip(('id','username','role'),r)) for r in self.db.execute('SELECT id,username,role FROM users ORDER BY username')]

    def documents(self, uid=None):
        rows = self.db.execute('SELECT id,owner_id,name,url FROM documents'+(' WHERE owner_id=?' if uid else '')+' ORDER BY rowid', (uid,) if uid else ())
        return [dict(zip(('id','owner_id','name','url'),r)) for r in rows]

    def document(self, did, uid=None):
        return next((d for d in self.documents(uid) if d['id'] == did), None)

    def next_name(self, uid):
        names = {d['name'].casefold() for d in self.documents(uid)}
        n = 1
        while '文档'+str(n) in names:
            n += 1
        return '文档'+str(n)

    def add_document(self, uid, name, url, creds):
        name = document_name(name)
        doc_id(url)
        did = secrets.token_hex(16)
        try:
            with self.db:
                self.db.execute('INSERT INTO documents VALUES (?,?,?,?)', (did,uid,name,url))
                self._state('credentials:'+uid, creds)
        except sqlite3.IntegrityError:
            raise ValueError('文档管理器名称重复') from None
        return self.document(did,uid)

    def rename_document(self, did, name):
        try:
            with self.db:
                self.db.execute('UPDATE documents SET name=? WHERE id=?', (document_name(name),did))
        except sqlite3.IntegrityError:
            raise ValueError('文档管理器名称重复') from None

    def save_user(self, actor, raw, uid=None, encoded=None):
        target = self.user(uid=uid) if uid else None
        if uid and not target:
            raise ValueError('用户不存在')
        role = raw.get('role', target['role'] if target else 'user')
        if role not in ROLES:
            raise ValueError('权限不正确')
        if actor['role'] != 'superadmin':
            if actor['role'] != 'admin' or role != 'user' or (target and target['role'] != 'user'):
                raise PermissionError('无权修改此用户')
        if target and target['role'] == 'superadmin' and role != 'superadmin':
            if self.db.execute("SELECT COUNT(*) FROM users WHERE role='superadmin'").fetchone()[0] <= 1:
                raise ValueError('至少保留一个超级管理员')
        name = username(raw.get('username', target['username'] if target else ''))
        if not target and not encoded:
            raise ValueError('请设置密码')
        uid = uid or secrets.token_hex(16)
        try:
            with self.db:
                if target:
                    self.db.execute('UPDATE users SET username=?,password=?,role=?,version=version+1 WHERE id=?', (name,encoded or target['password'],role,uid))
                else:
                    self.db.execute('INSERT INTO users(id,username,password,role) VALUES (?,?,?,?)', (uid,name,encoded,role))
        except sqlite3.IntegrityError:
            raise ValueError('帐号已存在') from None
        return self.public(self.user(uid=uid))


class DocumentStore:
    def __init__(self, accounts, document):
        self.accounts, self.id, self.owner_id = accounts, document['id'], document['owner_id']

    def get(self, key, default=None):
        if key == 'export_attempts':
            return self.accounts.root.get('export_attempts:'+self.owner_id, default)
        row = self.accounts.db.execute('SELECT value FROM document_state WHERE document_id=? AND key=?', (self.id,key)).fetchone()
        return json.loads(row[0]) if row else default

    def set(self, key, value):
        if key == 'export_attempts':
            return self.accounts.root.set('export_attempts:'+self.owner_id,value)
        if key == 'settings':
            value = {k:v for k,v in value.items() if k not in CREDENTIAL_KEYS + SMTP_KEYS and k != 'document_url'}
        with self.accounts.db:
            self.accounts.db.execute('INSERT INTO document_state VALUES (?,?,?) ON CONFLICT(document_id,key) DO UPDATE SET value=excluded.value', (self.id,key,json.dumps(value,ensure_ascii=False)))

    def settings(self):
        document = self.accounts.document(self.id,self.owner_id)
        if not document:
            raise ValueError('文档管理器已删除')
        return (DEFAULTS | self.get('settings', {}) | {'document_url':document['url']}
                | self.accounts.root.get('credentials:'+self.owner_id,{}) | self.accounts.root.get('smtp',{}))
