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
from .core import (Store, SECRETS, API_SECRETS, doc_id, integer, now, validate_rule,
                   written_text, target_columns, task_ranges, missing_tasks,
                   validate_source, refresh_roster)
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
                            raise TencentError('请先在监听管理中选择同事名单数据源')
                        source = roster['source']
                        source_meta = metadata.get(source['sheet_id'])
                        if not source_meta:
                            raise TencentError('名单 Sheet 已不存在，请重新选择数据源')
                        names_cells = await self.client.read_column(fid, source['sheet_id'], source['column'], source['start_row'], source['end_row'], headers)
                        roster = refresh_roster(source | {'sheet_name':source_meta['title']},
                            [written_text(names_cells.get(r)) for r in range(source['start_row'],source['end_row']+1)], roster)
                        self.store.set('roster',roster)
                        names = [n for n in roster['names'] if n not in roster['excluded']]
                        layout = await self.client.layout(fid,headers,metadata) if names else {'sheets':{}}
                        cache = {}
                        for rule in rules if names else []:
                            try:
                                meta = metadata.get(rule['sheet_id'])
                                if meta is None:
                                    raise TencentError('工作表不存在，请重新选择 Sheet')
                                rule = rule | {'sheet_name':meta['title']}
                                total = meta.get('rowTotal')
                                if isinstance(total,int) and total>0:
                                    if rule['start_row']>total:
                                        raise TencentError('起始行超出工作表范围')
                                    rule = rule | {'end_row':min(rule['end_row'],total)}
                                merges = layout['sheets'][rule['sheet_id']]
                                spans = task_ranges(rule,merges)
                                cols = set(target_columns(rule)) | {rule['owner_column']} | {x[2] for x in spans}
                                # Read anchors of horizontally merged target cells too.
                                for top,bottom,left,right in merges:
                                    if top<=rule['end_row'] and bottom>=rule['start_row'] and any(left<=c<=right for c in target_columns(rule)):
                                        cols.add(left)
                                cells = {}
                                for col in sorted(cols):
                                    key = (rule['sheet_id'], col, rule['start_row'], rule['end_row'])
                                    if key not in cache:
                                        cache[key] = await self.client.read_column(fid,*key,headers)
                                    cells.update({(row,col):value for row,value in cache[key].items()})
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
                        key = (r['person'], r['sheet_id'], r['row'], r['end_row'], r['column'])
                        if key not in seen:
                            seen.add(key)
                            unique.append(r)
                    snapshot.update(records=unique, people_count=len({r['person'] for r in unique}),
                                    last_success=snapshot['finished_at'])
                self.store.set('snapshot', snapshot)
                self.running = False
                self.next_check = time.time() + self.store.settings()['interval_seconds']
        return True

    async def loop(self):
        while True:
            self.wake.clear()
            await self.check()
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
    sessions, attempts = {}, {}
    salt = secrets.token_bytes(16)
    password_hash = hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1)
    cookie_secure = os.environ.get('COOKIE_SECURE', 'false').lower() == 'true'
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
            authenticated = sessions.get(token, 0) > time.time()
            public = request.path in ('/login', '/api/login', '/healthz', '/static/style.css', '/static/login.js')
            if not public and not authenticated:
                if request.path.startswith('/api/'):
                    return web.json_response({'error': '请先输入访问密码'}, status=401)
                raise web.HTTPFound('/login')
            response = await handler(request)
        except (ValueError, TypeError, KeyError) as exc:
            response = web.json_response({'error': str(exc)[:200]}, status=400)
        except TencentError as exc:
            response = web.json_response({'error': str(exc)}, status=502)
        except web.HTTPException as exc:
            response = web.Response(status=exc.status, text=exc.text, headers=exc.headers)
        response.headers.update({'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
            'X-Frame-Options': 'DENY', 'Referrer-Policy': 'no-referrer',
            'Content-Security-Policy': "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"})
        return response

    app = web.Application(middlewares=[security], client_max_size=65536)
    app['store'] = store

    async def lifecycle(app):
        async with aiohttp.ClientSession() as session:
            client = TencentClient(store, session)
            monitor = Monitor(store, client)
            app['client'], app['monitor'] = client, monitor
            task = asyncio.create_task(monitor.loop()) if start_scheduler else None
            yield
            if task:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
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
        raw = await request.json()
        supplied = raw.get('password', '')
        if not isinstance(supplied, str) or len(supplied) > 1024:
            raise ValueError('密码格式错误')
        attempts[ip] = (count + 1, since)
        candidate = await asyncio.to_thread(hashlib.scrypt, supplied.encode(), salt=salt, n=16384, r=8, p=1)
        if not hmac.compare_digest(candidate, password_hash):
            return web.json_response({'error': '访问密码不正确'}, status=401)
        attempts.pop(ip, None)
        for key in list(sessions):
            if sessions[key] <= current:
                del sessions[key]
        if len(sessions) >= 1000:
            sessions.pop(next(iter(sessions)))
        token = secrets.token_urlsafe(32)
        sessions[token] = current + session_hours * 3600
        response = web.json_response({'ok': True})
        response.set_cookie('wr_session', token, httponly=True, samesite='Strict', secure=cookie_secure,
                            max_age=session_hours * 3600, path='/')
        return response

    async def logout(request):
        sessions.pop(request.cookies.get('wr_session', ''), None)
        response = web.json_response({'ok': True})
        response.del_cookie('wr_session', path='/')
        return response

    async def page(request):
        name = 'login.html' if request.path == '/login' else 'index.html'
        return web.Response(text=(ROOT / name).read_text(), content_type='text/html')

    async def static(request):
        name = request.match_info['name']
        if name not in ('app.js', 'login.js', 'style.css'):
            raise web.HTTPNotFound()
        return web.Response(body=(ROOT / name).read_bytes(),
                            content_type='text/css' if name.endswith('.css') else 'application/javascript')

    async def status(request):
        m = app['monitor']
        return web.json_response(store.get('snapshot', {}) | dict(running=m.running,
            next_check=m.next_check, interval_seconds=store.settings()['interval_seconds'],
            configured=bool(store.get('rules', [])),
            roster_configured=bool(store.get('roster')),
            layout_updated_at=store.get('merge_layout',{}).get('updated_at')))

    async def check(request):
        if app['monitor'].running:
            return web.json_response({'ok': True, 'running': True}, status=202)
        app['monitor'].wake.set()
        return web.json_response({'ok': True}, status=202)

    def busy():
        if app['monitor'].running or app['client'].lock.locked():
            raise web.HTTPConflict(text='正在读取腾讯文档，请检查完成后再保存')

    async def rules(request):
        items = store.get('rules', [])
        if request.method == 'GET':
            return web.json_response(items)
        busy()
        raw = await request.json()
        rid = request.match_info.get('id')
        if rid and not any(x['id'] == rid for x in items):
            raise web.HTTPNotFound()
        if request.method == 'DELETE':
            items = [x for x in items if x['id'] != rid]
        else:
            value = validate_rule(raw) | {'id': rid or secrets.token_hex(8)}
            if not rid and len(items) >= 50:
                raise ValueError('最多添加 50 条监听规则')
            items = [value if x['id'] == rid else x for x in items] if rid else items + [value]
        store.set('rules', items)
        invalidate('监听规则已修改，等待重新查询')
        app['monitor'].wake.set()
        return web.json_response(items)

    def invalidate(message):
        snapshot = store.get('snapshot', {})
        snapshot.update(stale=True, errors=[dict(rule='配置更新', message=message)])
        store.set('snapshot', snapshot)

    async def settings(request):
        s = store.settings()
        if request.method == 'GET':
            return web.json_response({k: v for k, v in s.items() if k not in SECRETS} |
                                     {k + '_configured': bool(s[k]) for k in SECRETS})
        busy()
        raw = await request.json()
        if not isinstance(raw, dict):
            raise ValueError('设置格式错误')
        for k in ('document_url', 'file_id', 'client_id', 'open_id',
                  'smtp_host', 'smtp_sender', 'smtp_sender_name', 'smtp_recipient', 'smtp_recipient_name', 'smtp_security', *SECRETS):
            if k in raw:
                if not isinstance(raw[k], str) or len(raw[k]) > 4096 or '\n' in raw[k] or '\r' in raw[k]:
                    raise ValueError('设置项格式错误')
                # Empty secret inputs mean keep; clear_secrets explicitly removes them.
                if k not in SECRETS or raw[k].strip():
                    s[k] = raw[k].strip()
        doc_id(s['document_url'])
        if s['file_id'] and not re.fullmatch(r'[A-Za-z0-9_$-]{1,200}', s['file_id']):
            raise ValueError('File ID 格式错误')
        s['interval_seconds'] = integer(raw.get('interval_seconds', s['interval_seconds']), 60, 86400, '检查间隔（秒）')
        s['timeout_seconds'] = integer(raw.get('timeout_seconds', s['timeout_seconds']), 5, 120, '请求超时（秒）')
        s['smtp_port'] = integer(raw.get('smtp_port',s['smtp_port']),1,65535,'发送端口')
        if s['smtp_security'] not in ('ssl','starttls'):
            raise ValueError('SMTP 加密方式必须为 SSL 或 STARTTLS')
        for key in ('smtp_sender','smtp_recipient'):
            if s[key] and not re.fullmatch(r'[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+',s[key]):
                raise ValueError('邮箱地址格式错误')
        if s['smtp_host'] and not re.fullmatch(r'[a-zA-Z0-9.-]+',s['smtp_host']):
            raise ValueError('SMTP 服务器请填写主机名，不含协议和端口')
        if raw.get('clear_smtp_password') is True:
            s['smtp_password'] = ''
        if raw.get('clear_secrets') is True:
            for k in API_SECRETS:
                s[k] = ''
        if any(raw.get(k) for k in (*API_SECRETS, 'client_id', 'open_id')):
            s['token_expires_at'] = 0
        if (s['document_url'],s['file_id']) != (store.settings()['document_url'],store.settings()['file_id']):
            store.set('roster',None)
            store.set('merge_layout',{})
        store.set('settings', s)
        invalidate('设置已修改，等待重新查询')
        app['monitor'].wake.set()
        return web.json_response({'ok': True})

    async def sheets(request):
        return web.json_response(await app['client'].sheets())

    async def roster(request):
        current = store.get('roster')
        if request.method == 'GET':
            return web.json_response(current or {})
        busy()
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
            names, source = await app['client'].roster(source)
            current = refresh_roster(source,names,current)
        store.set('roster',current)
        invalidate('名单或统计人员已更新，等待重新查询')
        app['monitor'].wake.set()
        return web.json_response(current)

    async def refresh_layout(request):
        busy()
        async with app['client'].lock:
            client = app['client']
            headers = await client.headers()
            fid = await client.file_id(headers)
            metadata = {m['sheetId']:m for m in await client.metadata(fid,headers)}
            result = await client.layout(fid,headers,metadata,force=True)
        invalidate('合并结构已刷新，等待重新查询')
        app['monitor'].wake.set()
        return web.json_response({'updated_at':result['updated_at']})

    async def health(request):
        return web.json_response({'ok': True})

    app.add_routes([web.get('/healthz', health), web.get('/login', page), web.get('/', page),
        web.get('/manage', page), web.get('/static/{name}', static), web.post('/api/login', login),
        web.post('/api/logout', logout), web.get('/api/status', status), web.post('/api/check', check),
        web.get('/api/rules', rules), web.post('/api/rules', rules),
        web.put('/api/rules/{id}', rules), web.delete('/api/rules/{id}', rules),
        web.get('/api/roster',roster), web.put('/api/roster',roster),
        web.put('/api/roster/selection',roster), web.post('/api/layout/refresh',refresh_layout),
        web.get('/api/settings', settings), web.put('/api/settings', settings), web.get('/api/sheets', sheets)])
    return app


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    os.umask(0o077)
    web.run_app(create_app(), host='0.0.0.0', port=int(os.environ.get('PORT', 8080)), access_log=None)
