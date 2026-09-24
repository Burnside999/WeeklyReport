import asyncio
import contextlib
import hashlib
import hmac
import logging
import os
import re
import secrets
import time
from pathlib import Path

import aiohttp
from aiohttp import web
from .core import (Store, doc_id, integer, now, validate_rule,
                   written_text, target_columns, task_ranges, missing_tasks, task_identity, is_row, rule_axes, source_axes, oriented_merges,
                   validate_source, refresh_roster, variable_name, allocate_variable, migrate_variables, migrate_roster)
from .variables import build_variables
from .templates import MailEngine, TemplateConflict, check_syntax
from .mail import DeliveryError
from .auth import BrowserAuth
from .notifications import Notifications, register_notifications
from .accounts import Accounts, CREDENTIAL_KEYS, SMTP_KEYS, credentials, password_hash, verify_password
from .workspaces import Workspaces, register_management, invalidate
from .tencent import TencentClient, TencentError

LOG = logging.getLogger(__name__)
ROOT = Path(__file__).parent / 'static'


class Monitor:
    def __init__(self, store, client):
        self.store, self.client = store, client
        self.lock = asyncio.Lock()
        self.wake = asyncio.Event()
        self.running = False
        self.next_check = None
        self.manual_requested = False

    async def check(self):
        if self.lock.locked():
            return False
        async with self.lock:
            self.running = True
            started = now()
            rules = [r for r in self.store.get('rules', []) if r['enabled']]
            results, errors = [], []
            try:
                if rules:
                    async with self.client.lock:
                        headers = await self.client.headers()
                        fid = await self.client.file_id(headers)
                        metadata = {x['sheetId']: x for x in await self.client.metadata(fid, headers)}
                        roster = self.store.get('roster')
                        if not roster or not roster.get('source'):
                            raise TencentError('请先在设置中选择同事名单数据源')
                        source = roster['source']
                        source_meta = metadata.get(source['sheet_id'])
                        if not source_meta:
                            raise TencentError('名单工作表已不存在，请重新选择数据源')
                        source_axis, source_start, source_end = source_axes(source)
                        reader = self.client.read_row if is_row(source) else self.client.read_column
                        names_cells = await reader(fid,source['sheet_id'],source_axis,source_start,source_end,headers)
                        roster = refresh_roster(source | {'sheet_name':source_meta['title']},
                            [written_text(names_cells.get(r)) for r in range(source_start,source_end+1)], roster)
                        self.store.set('roster',roster)
                        names = [n for n in roster['names'] if n not in roster['excluded']]
                        layout = await self.client.layout(fid,headers,metadata) if names else {'sheets':{}}
                        cache = {}
                        for rule in rules if names else []:
                            try:
                                meta = metadata.get(rule['sheet_id'])
                                if meta is None:
                                    raise TencentError('工作表不存在，请重新选择工作表')
                                rule = rule | {'sheet_name':meta['title']}
                                horizontal = is_row(rule)
                                axes = rule_axes(rule)
                                total = meta.get('columnTotal' if horizontal else 'rowTotal')
                                if isinstance(total,int) and total>0:
                                    if axes['start_row']>total:
                                        raise TencentError('起始列超出工作表范围' if horizontal else '起始行超出工作表范围')
                                    rule = rule | {('end_column' if horizontal else 'end_row'):min(axes['end_row'],total)}
                                    axes = rule_axes(rule)
                                merges = layout['sheets'][rule['sheet_id']]
                                spans = task_ranges(rule,merges)
                                tracks = set(target_columns(axes)) | {axes['owner_column']} | {x[2] for x in spans}
                                # Include merged-cell anchors on the opposite axis too.
                                for top,bottom,left,right in oriented_merges(rule,merges):
                                    if top<=axes['end_row'] and bottom>=axes['start_row'] and any(left<=c<=right for c in target_columns(axes)):
                                        tracks.add(left)
                                cells = {}
                                reader = self.client.read_row if horizontal else self.client.read_column
                                for track in sorted(tracks):
                                    key = (horizontal,rule['sheet_id'],track,axes['start_row'],axes['end_row'])
                                    if key not in cache:
                                        cache[key] = await reader(fid,*key[1:],headers)
                                    cells.update({((track,index) if horizontal else (index,track)):value for index,value in cache[key].items()})
                                results.extend(missing_tasks(rule,cells,merges,names))
                            except (TencentError, ValueError) as exc:
                                errors.append(dict(rule=rule['name'], message=str(exc)))
            except (TencentError, ValueError) as exc:
                errors.append(dict(rule='文档连接', message=str(exc)))
            except Exception:
                LOG.exception('Unexpected monitor failure')
                errors.append(dict(rule='检查任务', message='检查发生内部错误，请查看服务器日志'))
            finally:
                previous = self.store.get('snapshot', {})
                if previous.get('semantics_version') != 2:
                    previous = {}
                snapshot = dict(semantics_version=2, last_attempt=started, finished_at=now(), errors=errors,
                                last_success=previous.get('last_success'),
                                records=previous.get('records', []),
                                people_count=previous.get('people_count'),
                                rule_count=len(rules), stale=bool(errors),
                                document_url=(previous.get('document_url') if errors else None)
                                or self.store.settings()['document_url'])
                # Publish atomically only after ALL enabled rules succeeded.
                if not errors:
                    seen = set()
                    unique = []
                    for r in results:
                        key = task_identity(r)
                        if key not in seen:
                            seen.add(key)
                            unique.append(r)
                    snapshot.update(rule_people={rule['id']: sorted({r['person'] for r in results if r['rule_id'] == rule['id']}) for rule in rules},
                                    records=unique, people_count=len({r['person'] for r in unique}),
                                    last_success=snapshot['finished_at'])
                self.store.set('snapshot', snapshot)
                self.running = False
                self.next_check = (time.time() + self.store.settings()['interval_seconds']
                                   if self.store.settings()['auto_query_enabled'] else None)
        return True

    async def loop(self):
        while True:
            self.wake.clear()
            manual = self.manual_requested
            self.manual_requested = False
            if manual or self.store.settings()['auto_query_enabled']:
                await self.check()
            if not self.store.settings()['auto_query_enabled']:
                self.next_check = None
                await self.wake.wait()
                continue
            delay = max(0, (self.next_check or time.time()) - time.time())
            try:
                await asyncio.wait_for(self.wake.wait(), delay)
            except asyncio.TimeoutError:
                pass


def create_app(data_dir=None, password=None, start_scheduler=True):
    password = password if password is not None else os.environ.get('ADMIN_PASSWORD', '')
    if len(password) < 12 or password == 'change-this-password':
        raise RuntimeError('ADMIN_PASSWORD 必须设置为至少 12 字符的自定义密码')
    store = Store(str(Path(data_dir or os.environ.get('DATA_DIR', './data')) / 'weeklyreport.db'))
    accounts = Accounts(store,password)
    sessions, attempts = {}, {}
    login_slots = asyncio.Semaphore(2)
    dummy_hash = password_hash(secrets.token_urlsafe(32))
    cookie_secure = os.environ.get('COOKIE_SECURE', 'false').lower() == 'true'
    browser_auth = BrowserAuth(accounts,cookie_secure)
    session_hours = integer(os.environ.get('SESSION_HOURS', '24'), 1, 168, '会话时长')

    @web.middleware
    async def security(request, handler):
        try:
            if request.path.startswith('/api/') and request.method != 'GET':
                if request.headers.get('X-Requested-With') != 'WeeklyReport':
                    raise web.HTTPForbidden(text='请求校验失败')
                if request.content_type != 'application/json':
                    raise web.HTTPUnsupportedMediaType()
            token = request.cookies.get('wr_session', '')
            session = sessions.get(token)
            user = accounts.user(uid=session['uid']) if session and session['expires'] > time.time() else None
            authenticated = bool(user and user['version'] == session['version'])
            if authenticated:
                request['user'], request['session'] = user, session
            public = request.path in ('/sw.js', '/manifest.webmanifest', '/offline', '/static/icon-192.png', '/static/icon-512.png', '/static/apple-touch-icon.png', '/static/pwa-register.js', '/login', '/api/login', '/api/logout', '/healthz', '/static/style.css', '/static/login.js', '/static/crypto.js', '/api/auth/challenge', '/api/auth/options', '/api/auth/resume', '/api/auth/forget')
            if not public and not authenticated:
                if request.path.startswith('/api/'):
                    response = web.json_response({'error': '请先登录'}, status=401)
                else:
                    from urllib.parse import quote
                    target = str(request.rel_url) if request.path == '/' else '/'
                    raise web.HTTPFound('/login?next=' + quote(target, safe=''))
            else:
                if authenticated:
                    if request.path == '/admin' and user['role'] not in ('admin','superadmin'):
                        raise web.HTTPForbidden(text='无权访问管理界面')
                    scoped = request.path.startswith('/api/') and request.path not in ('/api/login','/api/logout','/api/me') and not request.path.startswith(('/api/admin/','/api/documents','/api/auth/','/api/me/','/api/push/')) and request.path != '/api/notifications'
                    if scoped:
                        did = request.headers.get('X-Document-ID','')
                        if not did:
                            raise web.HTTPConflict(text='请选择文档管理器')
                        document = accounts.document(did,user['id'])
                        if not document:
                            raise web.HTTPNotFound(text='文档管理器不存在')
                        request['workspace'] = app['workspaces'].items[did]
                        request['document'] = document
                if authenticated and request.path.startswith('/api/') and request.path not in ('/api/login','/api/logout') and request.method != 'GET':
                    if app['workspaces'].mutations.locked():
                        raise web.HTTPConflict(text='操作正在进行，请稍后重试')
                    async with app['workspaces'].mutations:
                        current_user = accounts.user(uid=user['id'])
                        if not current_user or current_user['version'] != session['version']:
                            raise web.HTTPUnauthorized(text='请重新登录')
                        if scoped and not accounts.document(did,user['id']):
                            raise web.HTTPNotFound(text='文档管理器不存在')
                        response = await handler(request)
                else:
                    response = await handler(request)
        except (ValueError, TypeError, KeyError) as exc:
            response = web.json_response({'error': str(exc)[:200]}, status=400)
        except TemplateConflict as exc:
            response = web.json_response({'error': str(exc)} | exc.details, status=409)
        except DeliveryError as exc:
            response = web.json_response({'error': str(exc)}, status=502)
        except TencentError as exc:
            response = web.json_response({'error': str(exc)}, status=502)
        except web.HTTPException as exc:
            response = web.Response(status=exc.status, text=exc.text, headers=exc.headers)
        response.headers.update({'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
            'X-Frame-Options': 'DENY', 'Referrer-Policy': 'no-referrer',
            'Content-Security-Policy': "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"})
        return response

    app = web.Application(middlewares=[security], client_max_size=65536)
    app['store'], app['accounts'] = store, accounts
    app['browser_auth'] = browser_auth
    app['notifications'] = Notifications(accounts)

    async def lifecycle(app):
        async with aiohttp.ClientSession() as session:
            hub = app['workspaces'] = Workspaces(accounts,session,Monitor,start_scheduler,app['notifications'])
            for document in accounts.documents():
                hub.add(document)
            push_task = asyncio.create_task(app['notifications'].loop()) if start_scheduler else None
            try:
                yield
            finally:
                await hub.close()
                if push_task:
                    push_task.cancel()
                    await asyncio.gather(push_task,return_exceptions=True)
        store.close()
    app.cleanup_ctx.append(lifecycle)

    async def login(request):
        current = time.time()
        for key in list(attempts):
            if attempts[key][1] < current - 900:
                del attempts[key]
        ip = request.remote or 'unknown'  # Never trust arbitrary forwarded IP headers.
        count, since = attempts.get(ip, (0, current))
        if count >= 10:
            return web.json_response({'error': '尝试次数过多，请 15 分钟后再试'}, status=429)
        # Reserve before reading the body: awaiting JSON must not allow concurrent
        # requests to overwrite the same attempt counter.
        attempts[ip] = (count + 1, since)
        raw = await request.json()
        if not isinstance(raw, dict):
            raise ValueError('密码格式错误')
        if 'password' in raw:
            raise ValueError('不接受明文密码')
        supplied, name = browser_auth.decrypt(raw.get('encrypted_password')), raw.get('username', '')
        if not isinstance(supplied, str) or len(supplied) > 1024 or not isinstance(name,str) or len(name)>64:
            raise ValueError('帐号或密码格式错误')
        if login_slots.locked():
            return web.json_response({'error': '登录请求繁忙，请稍后重试'}, status=429)
        user = accounts.user(name=name)
        async with login_slots:
            valid = await asyncio.to_thread(verify_password,supplied,user['password'] if user else dummy_hash)
        current_user = accounts.user(uid=user['id']) if user else None
        if not valid or not current_user or current_user['version'] != user['version']:
            return web.json_response({'error': '帐号或密码不正确'}, status=401)
        attempts.pop(ip, None)
        remember, automatic = preferences(raw)
        response = start_session(request,user)
        if remember:
            browser_auth.remember(request,response,user,automatic)
        else:
            browser_auth.forget(request,response)
        return response

    def preferences(raw):
        remember, automatic = raw.get('remember',False), raw.get('automatic',False)
        if not isinstance(remember,bool) or not isinstance(automatic,bool):
            raise ValueError('登录选项格式错误')
        return remember or automatic, automatic

    def start_session(request,user):
        current = time.time()
        for key in list(sessions):
            if sessions[key]['expires'] <= current:
                del sessions[key]
        if len(sessions) >= 1000:
            sessions.pop(next(iter(sessions)))
        sessions.pop(request.cookies.get('wr_session', ''), None)
        app['notifications'].unbind_if_other_user(request,user['id'])
        token = secrets.token_urlsafe(32)
        sessions[token] = dict(uid=user['id'],version=user['version'],expires=current + session_hours * 3600)
        response = web.json_response({'ok': True})
        response.set_cookie('wr_session', token, httponly=True, samesite='Strict', secure=cookie_secure, path='/')
        return response

    async def auth_challenge(request):
        return web.json_response(browser_auth.challenge())

    async def auth_options(request):
        saved = browser_auth.remembered(request)
        return web.json_response(dict(username=saved['user']['username'] if saved else '',remember=bool(saved),automatic=bool(saved and saved['automatic'])))

    async def auth_forget(request):
        response = web.json_response({'ok':True})
        browser_auth.forget(request,response)
        return response

    async def auth_resume(request):
        raw = await request.json()
        if not isinstance(raw,dict) or not isinstance(raw.get('auto',False),bool):
            raise ValueError('登录选项格式错误')
        saved = browser_auth.remembered(request)
        if not saved or raw.get('username') != saved['user']['username'] or (raw.get('auto') and not saved['automatic']):
            return web.json_response({'error':'登录记忆已失效，请输入密码'},status=401)
        remember, automatic = preferences(raw)
        response = start_session(request,saved['user'])
        if remember:
            browser_auth.remember(request,response,saved['user'],automatic)
        else:
            browser_auth.forget(request,response)
        return response

    async def logout(request):
        app['notifications'].unbind(request)
        sessions.pop(request.cookies.get('wr_session', ''), None)
        response = web.json_response({'ok': True})
        response.del_cookie('wr_session', path='/')
        saved = browser_auth.remembered(request)
        if saved:
            browser_auth.remember(request,response,saved['user'],False)
        else:
            browser_auth.forget(request,response)
        return response

    async def page(request):
        name = 'login.html' if request.path == '/login' else 'index.html'
        return web.Response(text=(ROOT / name).read_text(), content_type='text/html')

    async def static(request):
        name = request.match_info['name']
        if name not in ('app.js', 'mail.js', 'login.js', 'crypto.js', 'workspace.js', 'admin.js', 'style.css', 'pwa.js', 'pwa-register.js', 'icon-192.png', 'icon-512.png', 'apple-touch-icon.png'):
            raise web.HTTPNotFound()
        return web.Response(body=(ROOT / name).read_bytes(),
                            content_type='image/png' if name.endswith('.png') else 'text/css' if name.endswith('.css') else 'application/javascript')

    async def status(request):
        runtime = request['workspace']
        store = runtime.store
        m = runtime.monitor
        return web.json_response(store.get('snapshot', {}) | dict(running=m.running,
            next_check=m.next_check if store.settings()['auto_query_enabled'] else None,
            auto_query_enabled=store.settings()['auto_query_enabled'], interval_seconds=store.settings()['interval_seconds'],
            configured=bool(store.get('rules', [])),
            roster_configured=bool(store.get('roster')),
            layout_updated_at=store.get('merge_layout',{}).get('updated_at')))

    async def variables(request):
        runtime = request['workspace']
        store = runtime.store
        return web.json_response(build_variables(store, runtime.monitor.running))

    async def check(request):
        runtime = request['workspace']
        store = runtime.store
        if runtime.monitor.running:
            return web.json_response({'ok': True, 'running': True}, status=202)
        runtime.monitor.manual_requested = True
        runtime.monitor.wake.set()
        return web.json_response({'ok': True}, status=202)

    async def automatic_query(request):
        runtime = request['workspace']
        store = runtime.store
        raw = await request.json()
        if not isinstance(raw, dict) or type(raw.get('enabled')) is not bool:
            raise ValueError('自动查询开关必须为布尔值')
        s = store.settings()
        if s['auto_query_enabled'] != raw['enabled']:
            s['auto_query_enabled'] = raw['enabled']
            store.set('settings', s)
            runtime.monitor.next_check = None
            runtime.monitor.wake.set()
        return web.json_response({'auto_query_enabled': s['auto_query_enabled']})

    def busy(runtime):
        if runtime.monitor.running or runtime.client.lock.locked():
            raise web.HTTPConflict(text='正在读取腾讯文档，请检查完成后再保存')

    async def rules(request):
        runtime = request['workspace']
        store = runtime.store
        items = store.get('rules', [])
        if request.method == 'GET':
            return web.json_response(items)
        busy(runtime)
        raw = await request.json()
        rid = request.match_info.get('id')
        if rid and not any(x['id'] == rid for x in items):
            raise web.HTTPNotFound()
        if request.method == 'DELETE':
            items = [x for x in items if x['id'] != rid]
        else:
            value = validate_rule(raw) | {'id': rid or secrets.token_hex(8)}
            previous = next((x for x in items if x['id'] == rid), {})
            proposed = raw.get('variable_name', previous.get('variable_name'))
            if proposed is None or proposed == '':
                proposed = previous.get('variable_name') or allocate_variable(store, items)
            value['variable_name'] = variable_name(proposed)
            if any(x['id'] != rid and x['variable_name'].lower() == proposed.lower() for x in items):
                raise ValueError('变量名已被其他规则使用（不区分大小写）')
            if not rid and len(items) >= 50:
                raise ValueError('最多添加 50 条监听规则')
            items = [value if x['id'] == rid else x for x in items] if rid else items + [value]
        store.set('rules', items)
        invalidate(store, '监听规则已修改，等待重新查询')
        runtime.monitor.wake.set()
        return web.json_response(items)

    async def settings(request):
        runtime = request['workspace']
        store = runtime.store
        s = store.settings()
        if request.method == 'GET':
            return web.json_response({k:v for k,v in s.items() if k not in SMTP_KEYS} | {'document_name':request['document']['name']})
        busy(runtime)
        runtime.mailer.ensure_idle()
        raw = await request.json()
        if not isinstance(raw,dict):
            raise ValueError('设置格式错误')
        if any(k in raw for k in SMTP_KEYS):
            raise web.HTTPForbidden(text='请在管理界面修改 SMTP')
        if 'document_url' in raw and raw['document_url'] != s['document_url']:
            raise ValueError('文档地址不可修改')
        s['interval_seconds'] = integer(raw.get('interval_seconds',s['interval_seconds']),60,86400,'检查间隔（秒）')
        s['timeout_seconds'] = integer(raw.get('timeout_seconds',s['timeout_seconds']),5,120,'请求超时（秒）')
        if any(k in raw for k in CREDENTIAL_KEYS):
            app['workspaces'].busy(request['user']['id'])
            creds = credentials(raw,s,required=False)
        else:
            creds = None
        if 'document_name' in raw:
            accounts.rename_document(store.id,raw['document_name'])
        store.set('settings',s)
        if creds is not None:
            accounts.root.set('credentials:'+store.owner_id,creds)
            for other in app['workspaces'].owned(store.owner_id):
                invalidate(other.store,'凭据已更新，等待重新查询')
                other.monitor.wake.set()
        invalidate(store,'设置已修改，等待重新查询')
        runtime.monitor.wake.set()
        return web.json_response({'ok':True})
    async def sheets(request):
        runtime = request['workspace']
        store = runtime.store
        return web.json_response(await runtime.client.sheets())

    async def roster(request):
        runtime = request['workspace']
        store = runtime.store
        current = store.get('roster')
        if request.method == 'GET':
            return web.json_response(current or {})
        busy(runtime)
        raw = await request.json()
        if not isinstance(raw,dict):
            raise ValueError('名单设置格式错误')
        if request.path.endswith('/selection'):
            if not current:
                raise ValueError('请先读取名单数据源')
            selected = raw.get('selected')
            if not isinstance(selected,list) or any(not isinstance(n,str) or n not in current['names'] for n in selected):
                raise ValueError('选择的姓名必须来自当前名单')
            absent = [n for n in current['excluded'] if n not in current['names']]
            current['excluded'] = absent + [n for n in current['names'] if n not in selected]
        else:
            source = validate_source(raw)
            names, source = await runtime.client.roster(source)
            current = refresh_roster(source,names,current)
        store.set('roster',current)
        invalidate(store, '名单或统计人员已更新，等待重新查询')
        runtime.monitor.wake.set()
        return web.json_response(current)

    async def refresh_layout(request):
        runtime = request['workspace']
        store = runtime.store
        busy(runtime)
        async with runtime.client.lock:
            client = runtime.client
            headers = await client.headers()
            fid = await client.file_id(headers)
            metadata = {m['sheetId']:m for m in await client.metadata(fid,headers)}
            result = await client.layout(fid,headers,metadata,force=True)
        invalidate(store, '合并结构已刷新，等待重新查询')
        runtime.monitor.wake.set()
        return web.json_response({'updated_at':result['updated_at']})

    async def template_validation(request):
        runtime = request['workspace']
        store = runtime.store
        raw = await request.json()
        if not isinstance(raw, dict):
            raise ValueError('请求格式错误')
        return web.json_response(check_syntax(raw, runtime.mailer.catalog()))

    async def templates(request):
        runtime = request['workspace']
        store = runtime.store
        engine = runtime.mailer
        if request.method == 'GET':
            return web.json_response(engine.listed())
        raw = await request.json()
        if not isinstance(raw, dict):
            raise ValueError('请求格式错误')
        identifier = request.match_info.get('id')
        action = request.match_info.get('action')
        if action == 'send':
            result = await engine.manual(identifier, raw)
        elif action == 'reset':
            result = engine.reset(identifier, raw.get('revision'))
        elif action:
            raise web.HTTPNotFound()
        elif request.method == 'DELETE':
            engine.delete(identifier, raw.get('revision'))
            result = {'ok': True}
        else:
            result = engine.save(raw, identifier)
        return web.json_response(result)

    async def pwa_asset(request):
        name, content_type = {'/sw.js':('sw.js','application/javascript'),
            '/manifest.webmanifest':('manifest.webmanifest','application/manifest+json'),
            '/offline':('offline.html','text/html')}[request.path]
        return web.Response(body=(ROOT/name).read_bytes(),content_type=content_type)

    async def health(request):
        return web.json_response({'ok': True})

    app.add_routes([web.get('/sw.js',pwa_asset),web.get('/manifest.webmanifest',pwa_asset),web.get('/offline',pwa_asset),web.get('/healthz', health), web.get('/login', page), web.get('/', page),
        web.get('/admin', page), web.get('/mail', page), web.get('/manage', page), web.get('/settings', page), web.get('/variables', page), web.get('/static/{name}', static), web.post('/api/login', login),
        web.get('/api/auth/challenge',auth_challenge), web.get('/api/auth/options',auth_options),
        web.post('/api/auth/resume',auth_resume), web.post('/api/auth/forget',auth_forget),
        web.post('/api/logout', logout), web.get('/api/status', status), web.post('/api/check', check), web.put('/api/auto-query', automatic_query),
        web.post('/api/templates/validate', template_validation),
        web.get('/api/templates', templates), web.post('/api/templates', templates),
        web.put('/api/templates/{id}', templates), web.delete('/api/templates/{id}', templates),
        web.post('/api/templates/{id}/{action}', templates),
        web.get('/api/variables', variables), web.get('/api/rules', rules), web.post('/api/rules', rules),
        web.put('/api/rules/{id}', rules), web.delete('/api/rules/{id}', rules),
        web.get('/api/roster',roster), web.put('/api/roster',roster),
        web.put('/api/roster/selection',roster), web.post('/api/layout/refresh',refresh_layout),
        web.get('/api/settings', settings), web.put('/api/settings', settings), web.get('/api/sheets', sheets)])
    register_management(app)
    register_notifications(app)
    return app


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    os.umask(0o077)
    web.run_app(create_app(), host='0.0.0.0', port=int(os.environ.get('PORT', 8080)), access_log=None)
