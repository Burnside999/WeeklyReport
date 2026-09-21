"""Document runtimes and account/document management endpoints."""
import asyncio
from types import SimpleNamespace
from aiohttp import web
from .accounts import (Accounts, DocumentStore, CREDENTIAL_KEYS, SMTP_KEYS,
                       credentials, document_name, password_hash)
from .core import DEFAULTS, doc_id, integer, migrate_variables, migrate_roster
from .mail import email_address
from .templates import MailEngine
from .tencent import TencentClient
import re


def invalidate(store, message):
    snapshot = store.get('snapshot', {})
    snapshot.update(stale=True, errors=[dict(rule='配置更新',message=message)])
    store.set('snapshot',snapshot)


class Workspaces:
    def __init__(self, accounts, session, monitor_class, scheduled):
        self.accounts, self.session, self.monitor_class = accounts, session, monitor_class
        self.scheduled, self.items, self.user_locks = scheduled, {}, {}
        self.mutations = asyncio.Lock()

    def add(self, document):
        store = DocumentStore(self.accounts,document)
        migrate_variables(store)
        migrate_roster(store)
        client = TencentClient(store,self.session)
        client.lock = self.user_locks.setdefault(document['owner_id'],asyncio.Lock())
        monitor = self.monitor_class(store,client)
        mailer = MailEngine(store,monitor)
        runtime = SimpleNamespace(store=store,client=client,monitor=monitor,mailer=mailer,tasks=[])
        self.items[document['id']] = runtime
        if self.scheduled:
            runtime.tasks = [asyncio.create_task(monitor.loop()),asyncio.create_task(mailer.loop())]
        return runtime

    def owned(self, uid):
        return [v for v in self.items.values() if v.store.owner_id == uid]

    def busy(self, uid):
        for r in self.owned(uid):
            if r.monitor.running or r.client.lock.locked():
                raise web.HTTPConflict(text='正在读取腾讯文档，请稍后操作')
            r.mailer.ensure_idle()

    async def stop(self, did):
        runtime = self.items.pop(did)
        runtime.mailer.stopping = True
        for task in runtime.tasks:
            task.cancel()
        await asyncio.gather(*runtime.tasks,return_exceptions=True)

    async def close(self):
        # Let SMTP attempts finish before closing their stores; monitor requests
        # can be cancelled safely because document records still exist.
        for runtime in self.items.values():
            runtime.mailer.stopping = True
            runtime.mailer.wake.set()
        for runtime in self.items.values():
            if runtime.tasks:
                await runtime.tasks[1]
        for did in list(self.items):
            await self.stop(did)


def register_management(app):
    accounts = app['accounts']

    def admin(request):
        if request['user']['role'] not in ('admin','superadmin'):
            raise web.HTTPForbidden(text='无权访问管理界面')

    async def me(request):
        user = request['user']
        creds = accounts.root.get('credentials:'+user['id'],{})
        return web.json_response(dict(user=accounts.public(user), documents=accounts.documents(user['id']),
            credentials_configured=all(creds.get(k) for k in CREDENTIAL_KEYS), next_name=accounts.next_name(user['id'])))

    async def documents(request):
        uid = request['user']['id']
        hub = app['workspaces']
        if request.method == 'POST':
            raw = await request.json()
            if not isinstance(raw,dict):
                raise ValueError('文档管理器格式错误')
            name = document_name(raw.get('name'))
            url = raw.get('url')
            if not isinstance(url,str) or len(url)>4096:
                raise ValueError('文档地址不正确')
            url = url.strip()
            doc_id(url)
            if len(accounts.documents(uid))>=50:
                raise ValueError('最多创建 50 个文档管理器')
            old = accounts.root.get('credentials:'+uid,{})
            creds = credentials(raw,old)
            hub.busy(uid)
            # Spreadsheet metadata is required; a /sheet/ URL alone is not proof.
            candidate = SimpleNamespace(settings=lambda: DEFAULTS | creds | {'document_url':url})
            client = TencentClient(candidate,hub.session)
            async with hub.user_locks.setdefault(uid,asyncio.Lock()):
                sheets = await client.sheets()
                if not sheets:
                    raise ValueError('该文档不是有效的在线表格')
            # Recheck account/session after network awaits, before persisting.
            current = accounts.user(uid=uid)
            if not current or current['version'] != request['user']['version']:
                raise web.HTTPUnauthorized(text='请重新登录')
            document = accounts.add_document(uid,name,url,creds)
            if creds != old:
                for r in hub.owned(uid):
                    invalidate(r.store,'凭据已更新，等待重新查询')
                    r.monitor.wake.set()
            hub.add(document)
            return web.json_response(document,status=201)
        did = request.match_info['id']
        document = accounts.document(did,uid)
        if not document:
            raise web.HTTPNotFound()
        if request.method == 'DELETE':
            hub.busy(uid)
            await hub.stop(did)
            with accounts.db:
                accounts.db.execute('DELETE FROM documents WHERE id=? AND owner_id=?',(did,uid))
            return web.json_response({'ok':True})
        raw = await request.json()
        if not isinstance(raw,dict) or set(raw)-{'name'}:
            raise ValueError('只能修改文档管理器名称')
        accounts.rename_document(did,raw.get('name'))
        return web.json_response(accounts.document(did,uid))

    async def users(request):
        admin(request)
        if request.method == 'GET':
            rows = accounts.users()
            if request['user']['role'] == 'admin':
                rows = [u for u in rows if u['role']=='user']
            return web.json_response(rows)
        hub = app['workspaces']
        raw = await request.json()
        if not isinstance(raw,dict):
            raise ValueError('用户格式错误')
        uid = request.match_info.get('id')
        actor = accounts.user(uid=request['user']['id'])
        if not actor or actor['version'] != request['user']['version']:
            raise web.HTTPUnauthorized(text='请重新登录')
        if request.method == 'DELETE':
            target = accounts.user(uid=uid)
            if not target:
                raise web.HTTPNotFound()
            if uid == actor['id']:
                raise ValueError('不能删除当前帐号')
            if actor['role'] != 'superadmin' and target['role'] != 'user':
                raise web.HTTPForbidden(text='无权删除此用户')
            hub.busy(uid)
            for d in accounts.documents(uid):
                await hub.stop(d['id'])
            with accounts.db:
                accounts.db.execute('DELETE FROM users WHERE id=?',(uid,))
                accounts.db.execute('DELETE FROM state WHERE key IN (?,?)',('credentials:'+uid,'export_attempts:'+uid))
            return web.json_response({'ok':True})
        if 'password' in raw and not isinstance(raw['password'],str):
            raise ValueError('密码格式错误')
        # Authorize before performing costly password hashing.
        target = accounts.user(uid=uid) if uid else None
        if actor['role'] != 'superadmin' and (raw.get('role','user')!='user' or (target and target['role']!='user')):
            raise web.HTTPForbidden(text='无权修改此用户')
        encoded = await asyncio.to_thread(password_hash,raw['password']) if raw.get('password') else None
        try:
            user = accounts.save_user(actor,raw,uid,encoded)
        except PermissionError as exc:
            raise web.HTTPForbidden(text=str(exc)) from None
        # A self-edit keeps only this session, all other sessions are revoked.
        if uid == actor['id']:
            request['session']['version'] = accounts.user(uid=uid)['version']
        return web.json_response(user)

    async def smtp(request):
        admin(request)
        if request.method == 'GET':
            return web.json_response({k:accounts.root.get('smtp',{}).get(k,DEFAULTS[k]) for k in SMTP_KEYS})
        for r in app['workspaces'].items.values():
            r.mailer.ensure_idle()
        raw = await request.json()
        if not isinstance(raw,dict) or set(raw)-set(SMTP_KEYS):
            raise ValueError('SMTP 设置格式错误')
        value = {k:accounts.root.get('smtp',{}).get(k,DEFAULTS[k]) for k in SMTP_KEYS}
        for k,v in raw.items():
            if k!='smtp_port':
                if not isinstance(v,str) or len(v)>4096 or any(c in v for c in '\r\n\x00'):
                    raise ValueError('SMTP 设置格式错误')
                value[k] = v.strip()
        value['smtp_port'] = integer(raw.get('smtp_port',value['smtp_port']),1,65535,'发送端口')
        if value['smtp_security'] not in ('ssl','starttls'):
            raise ValueError('SMTP 加密方式必须为 SSL 或 STARTTLS')
        if value['smtp_sender']:
            email_address(value['smtp_sender'])
        if value['smtp_host'] and not re.fullmatch(r'[a-zA-Z0-9.-]+',value['smtp_host']):
            raise ValueError('SMTP 服务器请填写主机名')
        accounts.root.set('smtp',value)
        return web.json_response({'ok':True})

    app.add_routes([web.get('/api/me',me),web.post('/api/documents',documents),
        web.put('/api/documents/{id}',documents),web.delete('/api/documents/{id}',documents),
        web.get('/api/admin/users',users),web.post('/api/admin/users',users),
        web.put('/api/admin/users/{id}',users),web.delete('/api/admin/users/{id}',users),
        web.get('/api/admin/smtp',smtp),web.put('/api/admin/smtp',smtp)])
