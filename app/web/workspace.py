"""Document-scoped APIs. Authorization is enforced by web.security."""
import secrets
from aiohttp import web
from ..accounts import CREDENTIAL_KEYS, SMTP_KEYS, credentials
from ..core import (integer, validate_rule, variable_name, allocate_variable,
                    validate_source, refresh_roster)
from ..templates import check_syntax
from ..variables import build_variables
from ..workspaces import invalidate


def register_workspace(app):
    accounts = app['accounts']

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
        raw = await request.json()
        if not isinstance(raw, dict):
            raise ValueError('请求格式错误')
        return web.json_response(check_syntax(raw, runtime.mailer.catalog()))

    async def templates(request):
        runtime = request['workspace']
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

    app.add_routes([
        web.get('/api/status', status),
        web.post('/api/check', check),
        web.put('/api/auto-query', automatic_query),
        web.post('/api/templates/validate', template_validation),
        web.get('/api/templates', templates),
        web.post('/api/templates', templates),
        web.put('/api/templates/{id}', templates),
        web.delete('/api/templates/{id}', templates),
        web.post('/api/templates/{id}/{action}', templates),
        web.get('/api/variables', variables),
        web.get('/api/rules', rules),
        web.post('/api/rules', rules),
        web.put('/api/rules/{id}', rules),
        web.delete('/api/rules/{id}', rules),
        web.get('/api/roster', roster),
        web.put('/api/roster', roster),
        web.put('/api/roster/selection', roster),
        web.post('/api/layout/refresh', refresh_layout),
        web.get('/api/settings', settings),
        web.put('/api/settings', settings),
        web.get('/api/sheets', sheets),
    ])
