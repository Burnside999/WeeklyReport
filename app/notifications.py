"""Document-scoped primary notifications and durable, direct Apple Web Push outbox.

Single process, like the existing mail scheduler. No SMTP retries are introduced.
Only Apple's subscription hosts are accepted: this is not a general URL fetcher.
"""
import asyncio
import base64
import hashlib
import json
import logging
import os
import re
import secrets
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from aiohttp import web
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

LOG = logging.getLogger(__name__)
DEVICE_COOKIE = 'wr_push_device'
TTL = 3600


def b64(data):
    return base64.urlsafe_b64encode(data).decode().rstrip('=')


def decode(value, length):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]+={0,2}', value):
        raise ValueError('推送加密参数不正确')
    try:
        result = base64.b64decode(value + '=' * (-len(value) % 4), altchars=b'-_', validate=True)
    except ValueError:
        raise ValueError('推送加密参数不正确') from None
    if len(result) != length:
        raise ValueError('推送加密参数不正确')
    return result


def subscription(raw):
    if not isinstance(raw, dict):
        raise ValueError('设备订阅格式错误')
    endpoint = raw.get('endpoint')
    if not isinstance(endpoint, str) or len(endpoint) > 2048 or any(ord(c) < 33 for c in endpoint):
        raise ValueError('推送地址不正确')
    url = urlsplit(endpoint)
    host = url.hostname or ''
    if (url.scheme != 'https' or url.username or url.password or url.port not in (None, 443)
            or url.fragment or not url.path.startswith('/')
            or not re.fullmatch(r'(?:[a-z0-9-]+\.)*push\.apple\.com', host)):
        raise ValueError('本版本仅支持 Apple Web Push，请使用 iPhone 主屏幕应用开启通知')
    keys = raw.get('keys')
    if not isinstance(keys, dict):
        raise ValueError('推送加密参数不正确')
    point = decode(keys.get('p256dh'), 65)
    ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), point)
    decode(keys.get('auth'), 16)
    return {'endpoint': endpoint, 'keys': {k: keys[k] for k in ('p256dh', 'auth')}}


def device_hash(request):
    value = request.cookies.get(DEVICE_COOKIE, '')
    return hashlib.sha256(value.encode()).hexdigest() if value else ''


class Notifications:
    def __init__(self, accounts):
        self.accounts, self.db = accounts, accounts.db
        self.enabled = os.environ.get('WEB_PUSH_ENABLED', 'false').lower() == 'true'
        self.subject = os.environ.get('VAPID_SUBJECT', '').strip()
        self.key_path = Path(accounts.root.path).parent / 'vapid-private.pem'
        self.public_key = ''
        if self.enabled:
            if not re.fullmatch(r'mailto:[^\s@]+@[^\s@]+\.[^\s@]+', self.subject):
                raise RuntimeError('开启推送时必须设置 VAPID_SUBJECT=mailto:你的联系邮箱')
            if not self.key_path.exists():
                key = ec.generate_private_key(ec.SECP256R1())
                pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                        serialization.NoEncryption())
                fd = os.open(self.key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, 'wb') as handle:
                    handle.write(pem)
                    handle.flush()
                    os.fsync(handle.fileno())
            os.chmod(self.key_path, 0o600)
            key = serialization.load_pem_private_key(self.key_path.read_bytes(), password=None)
            if not isinstance(key, ec.EllipticCurvePrivateKey) or not isinstance(key.curve, ec.SECP256R1):
                raise RuntimeError('VAPID 密钥必须为 P-256 私钥')
            self.public_key = b64(key.public_key().public_bytes(serialization.Encoding.X962,
                                                            serialization.PublicFormat.UncompressedPoint))
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS primary_notifications (
                user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                document_id TEXT NOT NULL PRIMARY KEY REFERENCES documents(id) ON DELETE CASCADE,
                trigger_id TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS push_subscriptions (
                id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                user_version INTEGER NOT NULL, device_hash TEXT NOT NULL UNIQUE,
                endpoint TEXT NOT NULL UNIQUE, payload TEXT NOT NULL,
                last_test REAL NOT NULL DEFAULT 0, updated_at REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS notifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                document_id TEXT REFERENCES documents(id) ON DELETE CASCADE,
                trigger_id TEXT, attempt TEXT NOT NULL UNIQUE, title TEXT NOT NULL,
                created_at REAL NOT NULL, expires_at REAL NOT NULL, is_test INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS push_deliveries (
                notification_id INTEGER NOT NULL REFERENCES notifications(id) ON DELETE CASCADE,
                subscription_id TEXT NOT NULL REFERENCES push_subscriptions(id) ON DELETE CASCADE,
                state TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0,
                next_attempt REAL NOT NULL, error TEXT NOT NULL DEFAULT '',
                PRIMARY KEY(notification_id,subscription_id));
            CREATE INDEX IF NOT EXISTS notification_user_cursor ON notifications(user_id,id);
            CREATE INDEX IF NOT EXISTS push_due ON push_deliveries(state,next_attempt);
        ''')
        # Upgrade the original account-wide selection without losing its value.
        if any(r[1] == 'user_id' and r[5] for r in self.db.execute('PRAGMA table_info(primary_notifications)')):
            with self.db:
                self.db.execute('BEGIN IMMEDIATE')
                self.db.execute('ALTER TABLE primary_notifications RENAME TO primary_notifications_old')
                self.db.execute('CREATE TABLE primary_notifications ('
                    'user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,'
                    'document_id TEXT NOT NULL PRIMARY KEY REFERENCES documents(id) ON DELETE CASCADE,'
                    'trigger_id TEXT NOT NULL)')
                self.db.execute('INSERT INTO primary_notifications SELECT * FROM primary_notifications_old')
                self.db.execute('DROP TABLE primary_notifications_old')
        # A process may have stopped after sending but before saving acceptance.
        # Retried payloads keep the same notification id/tag (at-least-once).
        with self.db:
            self.db.execute("UPDATE push_deliveries SET state='pending' WHERE state='sending'")
        self.wake = asyncio.Event()
        self.lock = asyncio.Lock()
        self.sender = self.send

    def primary(self, uid, did):
        row = self.db.execute('SELECT document_id,trigger_id FROM primary_notifications WHERE user_id=? AND document_id=?', (uid,did)).fetchone()
        if not row:
            return None
        document = self.accounts.document(row[0], uid)
        from .accounts import DocumentStore
        item = next((x for x in DocumentStore(self.accounts, document).get('mail_templates', [])
                     if x['id'] == row[1]), None) if document else None
        if not item:
            with self.db:
                self.db.execute('DELETE FROM primary_notifications WHERE user_id=? AND document_id=?', (uid,did))
            return None
        return dict(document_id=row[0], trigger_id=row[1], document_name=document['name'], title=item['subject'])

    def choose(self, uid, raw):
        if not isinstance(raw, dict):
            raise ValueError('主推送格式错误')
        did, tid = raw.get('document_id'), raw.get('trigger_id')
        document = self.accounts.document(did, uid)
        if not document:
            raise web.HTTPNotFound(text='文档管理器不存在')
        if tid is None:
            with self.db:
                self.db.execute('DELETE FROM primary_notifications WHERE user_id=? AND document_id=?', (uid,did))
            return None
        from .accounts import DocumentStore
        if not any(x['id'] == tid for x in DocumentStore(self.accounts, document).get('mail_templates', [])):
            raise web.HTTPNotFound(text='邮件模板不存在')
        with self.db:
            self.db.execute('INSERT INTO primary_notifications VALUES (?,?,?) ON CONFLICT(document_id) '
                            'DO UPDATE SET document_id=excluded.document_id,trigger_id=excluded.trigger_id', (uid,did,tid))
        return self.primary(uid,did)

    def clear_trigger(self, did, tid):
        with self.db:
            self.db.execute('DELETE FROM primary_notifications WHERE document_id=? AND trigger_id=?', (did,tid))

    def _enqueue(self, uid, did, tid, attempt, title, *, sid=None, test=False):
        current = time.time()
        cursor = self.db.execute('INSERT OR IGNORE INTO notifications '
            '(user_id,document_id,trigger_id,attempt,title,created_at,expires_at,is_test) VALUES (?,?,?,?,?,?,?,?)',
            (uid,did,tid,attempt,title,current,current+TTL,int(test)))
        if not cursor.rowcount:
            return self.db.execute('SELECT id FROM notifications WHERE attempt=?', (attempt,)).fetchone()[0]
        nid = cursor.lastrowid
        if self.enabled:
            self.db.execute('INSERT INTO push_deliveries(notification_id,subscription_id,next_attempt) '
                'SELECT ?,s.id,? FROM push_subscriptions s JOIN users u ON u.id=s.user_id '
                'WHERE s.user_id=? AND s.user_version=u.version' + (' AND s.id=?' if sid else ''),
                (nid,current,uid) + ((sid,) if sid else ()))
        self.wake.set()
        return nid

    def claim(self, store, item, title):
        """Commit the existing SMTP claim and new outbox together, before SMTP."""
        items = [item if x['id'] == item['id'] else x for x in store.get('mail_templates', [])]
        with self.db:
            self.db.execute("UPDATE document_state SET value=? WHERE document_id=? AND key='mail_templates'",
                            (json.dumps(items,ensure_ascii=False),store.id))
            primary = self.db.execute('SELECT 1 FROM primary_notifications '
                'WHERE user_id=? AND document_id=? AND trigger_id=?', (store.owner_id,store.id,item['id'])).fetchone()
            if primary:
                self._enqueue(store.owner_id,store.id,item['id'],item['attempt'],title)

    def unbind_if_other_user(self, request, uid):
        with self.db:
            self.db.execute('DELETE FROM push_subscriptions WHERE device_hash=? AND user_id<>?',
                            (device_hash(request),uid))

    def unbind(self, request):
        hashed = device_hash(request)
        if hashed:
            with self.db:
                self.db.execute('DELETE FROM push_subscriptions WHERE device_hash=?', (hashed,))

    def send(self, info, payload, ttl):
        # Local imports keep disabled installations and unit fixtures lightweight.
        import requests
        from pywebpush import webpush
        class DirectSession(requests.Session):
            def request(self, method, url, **kwargs):
                kwargs['allow_redirects'] = False
                return super().request(method, url, **kwargs)
        with DirectSession() as session:
            session.trust_env = False
            response = webpush(subscription_info=subscription(info), data=json.dumps(payload,ensure_ascii=False),
                vapid_private_key=str(self.key_path), vapid_claims={'sub':self.subject},
                ttl=ttl, timeout=10, requests_session=session,
                headers={'Urgency':'normal','Topic':hashlib.sha256(str(payload['id']).encode()).hexdigest()[:32]})
            return response.status_code

    async def tick(self):
        if self.lock.locked():
            return
        async with self.lock:
            current = time.time()
            with self.db:
                self.db.execute('DELETE FROM push_subscriptions WHERE NOT EXISTS '
                    '(SELECT 1 FROM users u WHERE u.id=user_id AND u.version=user_version)')
                self.db.execute("UPDATE push_deliveries SET state='expired' WHERE state IN ('pending','sending') "
                    'AND notification_id IN (SELECT id FROM notifications WHERE expires_at<=?)', (current,))
                self.db.execute('DELETE FROM notifications WHERE created_at<?', (current-30*86400,))
            if not self.enabled:
                return
            rows = self.db.execute("SELECT notification_id,subscription_id FROM push_deliveries "
                                   "WHERE state='pending' AND next_attempt<=? ORDER BY next_attempt LIMIT 20", (current,)).fetchall()
            for nid,sid in rows:
                # Re-read before each network call: logout/deletion may run at awaits.
                row = self.db.execute('SELECT s.payload,n.title,n.document_id,n.expires_at,d.attempts '
                    'FROM push_deliveries d JOIN push_subscriptions s ON s.id=d.subscription_id '
                    'JOIN users u ON u.id=s.user_id AND u.version=s.user_version '
                    'JOIN notifications n ON n.id=d.notification_id '
                    "WHERE d.notification_id=? AND d.subscription_id=? AND d.state='pending'", (nid,sid)).fetchone()
                if not row:
                    continue
                info,title,did,expiry,attempts = row
                remaining = int(expiry-time.time())
                if remaining <= 0:
                    with self.db:
                        self.db.execute("UPDATE push_deliveries SET state='expired' WHERE notification_id=? AND subscription_id=?", (nid,sid))
                    continue
                payload = dict(id=nid,title='周报提醒',body=title,url='/?doc='+did if did else '/',expires_at=expiry)
                # Web Push payloads have a small encrypted size limit. Preserve full
                # subject in the notification API; shorten only the device preview.
                while len(json.dumps(payload,ensure_ascii=False).encode()) > 3500:
                    payload['body'] = payload['body'][:-20]
                if payload['body'] != title:
                    payload['body'] += '…'
                with self.db:
                    self.db.execute("UPDATE push_deliveries SET state='sending',attempts=attempts+1 "
                                    'WHERE notification_id=? AND subscription_id=?', (nid,sid))
                status = 0
                try:
                    status = await asyncio.to_thread(self.sender,json.loads(info),payload,remaining)
                except Exception as exc:
                    response = getattr(exc, 'response', None)
                    status = getattr(response, 'status_code', 0) or 0
                    # Never log exception text: libraries include private endpoints.
                attempts += 1
                with self.db:
                    if status in (404,410):
                        self.db.execute('DELETE FROM push_subscriptions WHERE id=?', (sid,))
                    else:
                        accepted = 200 <= status < 300
                        retry = (status == 0 or status == 429 or status >= 500) and attempts < 5
                        state = 'accepted' if accepted else 'pending' if retry else 'failed'
                        self.db.execute('UPDATE push_deliveries SET state=?,next_attempt=?,error=? '
                            'WHERE notification_id=? AND subscription_id=?',
                            (state,time.time()+min(900,30*2**(attempts-1)),'' if accepted else f'HTTP {status}' if status else '连接失败',nid,sid))
                if not 200 <= status < 300:
                    LOG.warning('Push delivery %s attempt %s: HTTP %s', nid,attempts,status)

    async def loop(self):
        while True:
            self.wake.clear()
            try:
                await self.tick()
            except Exception:
                LOG.error('Push queue check failed; retry next interval')
            try:
                await asyncio.wait_for(self.wake.wait(),15)
            except asyncio.TimeoutError:
                pass


def register_notifications(app):
    service = app['notifications']
    async def primary(request):
        uid = request['user']['id']
        value = service.choose(uid,await request.json()) if request.method == 'PUT' else service.primary(uid,request.query.get('document_id') or request.headers.get('X-Document-ID'))
        return web.json_response({'primary':value})

    async def config(request):
        uid = request['user']['id']
        row = service.db.execute('SELECT id FROM push_subscriptions WHERE user_id=? AND user_version=? AND device_hash=?',
                                (uid,request['user']['version'],device_hash(request))).fetchone()
        sid = row[0] if row else None
        delivery = service.db.execute('SELECT d.state,d.error FROM push_deliveries d '
            'WHERE d.subscription_id=? ORDER BY d.notification_id DESC LIMIT 1', (sid,)).fetchone() if sid else None
        response = web.json_response(dict(user_id=uid,enabled=service.enabled,public_key=service.public_key,subscription_id=sid,
            primary=service.primary(uid,request.headers.get('X-Document-ID')),delivery=dict(state=delivery[0],error=delivery[1]) if delivery else None,
            supported_provider='apple'))
        if not device_hash(request):
            response.set_cookie(DEVICE_COOKIE,secrets.token_urlsafe(32),max_age=365*86400,httponly=True,
                samesite='Strict',secure=app['browser_auth'].secure,path='/')
        return response

    async def subscribe(request):
        if not service.enabled:
            raise web.HTTPConflict(text='服务器尚未开启设备推送')
        hashed = device_hash(request)
        if not hashed:
            raise web.HTTPConflict(text='请刷新页面后重新开启通知')
        raw = subscription(await request.json())
        uid = request['user']['id']
        existing = service.db.execute('SELECT device_hash FROM push_subscriptions WHERE endpoint=?', (raw['endpoint'],)).fetchone()
        if existing and existing[0] != hashed:
            raise web.HTTPConflict(text='设备标识已变更，请关闭本设备通知后重新开启')
        existing = service.db.execute('SELECT id,user_id,payload,user_version FROM push_subscriptions WHERE device_hash=?', (hashed,)).fetchone()
        sid = existing[0] if existing else secrets.token_hex(16)
        if (not existing or existing[1] != uid) and service.db.execute('SELECT count(*) FROM push_subscriptions WHERE user_id=?', (uid,)).fetchone()[0] >= 10:
            raise ValueError('每个帐号最多开启 10 台设备')
        encoded = json.dumps(raw)
        changed = not existing or existing[1] != uid or existing[2] != encoded or existing[3] != request['user']['version']
        with service.db:
            if existing and (existing[1] != uid or existing[2] != encoded or existing[3] != request['user']['version']):
                service.db.execute('DELETE FROM push_subscriptions WHERE id=?', (sid,))
            service.db.execute('INSERT INTO push_subscriptions '
                '(id,user_id,user_version,device_hash,endpoint,payload,updated_at) VALUES (?,?,?,?,?,?,?) '
                'ON CONFLICT(id) DO UPDATE SET updated_at=excluded.updated_at',
                (sid,uid,request['user']['version'],hashed,raw['endpoint'],encoded,time.time()))
            if changed:
                service._enqueue(uid,None,None,secrets.token_hex(16),'消息推送启动成功！',sid=sid,test=True)
        return web.json_response({'id':sid})

    async def unsubscribe(request):
        with service.db:
            service.db.execute('DELETE FROM push_subscriptions WHERE id=? AND user_id=?',
                               (request.match_info['id'],request['user']['id']))
        return web.json_response({'ok':True})

    async def listing(request):
        after = request.query.get('after', '0')
        if after == 'latest':
            cursor = service.db.execute('SELECT COALESCE(MAX(id),0) FROM notifications WHERE user_id=?',
                                        (request['user']['id'],)).fetchone()[0]
            return web.json_response(dict(user_id=request['user']['id'],items=[],next_cursor=cursor))
        if not re.fullmatch(r'\d{1,18}',after):
            raise ValueError('通知游标格式错误')
        rows = service.db.execute('SELECT id,document_id,trigger_id,title,created_at,expires_at FROM notifications '
            'WHERE user_id=? AND id>? AND is_test=0 ORDER BY id LIMIT 100', (request['user']['id'],int(after))).fetchall()
        items = [dict(id=r[0],document_id=r[1],trigger_id=r[2],title=r[3],expires_at=r[5],
                      created_at=datetime.fromtimestamp(r[4],timezone.utc).isoformat()) for r in rows]
        return web.json_response(dict(user_id=request['user']['id'],items=items,next_cursor=rows[-1][0] if rows else int(after)))

    app.add_routes([web.get('/api/me/primary-trigger',primary),web.put('/api/me/primary-trigger',primary),
        web.get('/api/push/config',config),web.post('/api/push/subscriptions',subscribe),
        web.delete('/api/push/subscriptions/{id}',unsubscribe),
        web.get('/api/notifications',listing)])
