import asyncio
import json
import tempfile
import unittest
from unittest.mock import patch, AsyncMock
from aiohttp.test_utils import TestClient, TestServer
from app.core import Store, written_text, column, doc_id, letters, missing_tasks, validate_rule
from app.main import Monitor, create_app
from app.tencent import TencentClient, TencentError


def cell(value):
    return {'cellValue': {'text': value}}


def rule(**kwargs):
    return dict(id='r1', name='本周工作', sheet_id='tab1', sheet_name='研发',
                owner_column=1, target_column=3, start_row=2, end_row=5, enabled=True) | kwargs


class CoreTests(unittest.TestCase):
    def test_columns(self):
        for n, s in [(1,'A'),(26,'Z'),(27,'AA'),(16384,'XFD')]:
            self.assertEqual(column(s.lower()), n)
            self.assertEqual(letters(n), s)
        for bad in ('0','-1','XFE','A1',None):
            with self.assertRaises(ValueError): column(bad)

    def test_blank_semantics_and_row_alignment(self):
        owners={2:cell(' 张三 '),3:cell('李四'),4:cell(''),5:cell('王五')}
        targets={2:cell(' \n\t'),3:{'cellValue':{'number':0}},4:cell(''),5:cell('已完成')}
        cells={(r,1):v for r,v in owners.items()} | {(r,3):v for r,v in targets.items()}
        rows=missing_tasks(rule(),cells,[],['张三','李四','王五'])
        self.assertEqual([(r['person'],r['row']) for r in rows],[('张三',2),('李四',3)])
        self.assertFalse(written_text({'cellValue':{'boolean':False}}))
        self.assertTrue(written_text(cell('0')))
        self.assertFalse(written_text(None))

    def test_rule_validation(self):
        self.assertEqual(validate_rule(rule(owner_column='AA'))['owner_column'],27)
        for changes in [dict(start_row=9,end_row=3),dict(end_row=10003),dict(sheet_id='../x'),dict(enabled='false')]:
            with self.assertRaises(ValueError): validate_rule(rule(**changes))

    def test_url_is_restricted(self):
        self.assertEqual(doc_id('https://docs.qq.com/sheet/DABC?tab=test'),'DABC')
        for url in ['http://127.0.0.1/sheet/DABC','https://docs.qq.com.evil/sheet/DABC','https://evil@docs.qq.com/sheet/DABC']:
            with self.assertRaises(ValueError): doc_id(url)


class FakeClient:
    def __init__(self): self.lock=asyncio.Lock(); self.fail=False
    async def layout(self,*args,**kwargs): return {'sheets':{'tab1':[]}}
    async def headers(self): return {}
    async def file_id(self,h): return 'file'
    async def metadata(self,f,h): return [dict(sheetId='tab1',title='研发',rowTotal=5)]
    async def read_column(self,f,s,c,start,end,h):
        if self.fail: raise TencentError('测试超时')
        return {2:cell('张三'),3:cell('张三'),4:cell('李四')} if c==1 else {4:cell('完成')}


class MonitorTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.store=Store(self.tmp.name+'/db')
        self.store.set('rules',[rule()]);self.store.set('roster',{'source':{'sheet_id':'tab1','column':1,'start_row':2,'end_row':5},'names':['张三','李四'],'excluded':[]});self.client=FakeClient();self.m=Monitor(self.store,self.client)
    async def asyncTearDown(self): self.store.close();self.tmp.cleanup()
    async def test_dedup_and_stale_on_failure(self):
        await self.m.check();good=self.store.get('snapshot')
        self.assertEqual(good['people_count'],1);self.assertEqual(len(good['records']),2)
        self.client.fail=True;await self.m.check();bad=self.store.get('snapshot')
        self.assertTrue(bad['stale']);self.assertEqual(bad['records'],good['records'])
        self.assertEqual(bad['last_success'],good['last_success'])
    async def test_first_failure_is_unknown_not_zero(self):
        self.client.fail=True;await self.m.check();s=self.store.get('snapshot')
        self.assertIsNone(s['people_count']);self.assertIsNone(s['last_success'])
    async def test_duplicate_rules_and_missing_sheet(self):
        self.store.set('rules',[rule(),rule(id='r2')]);await self.m.check()
        self.assertEqual(len(self.store.get('snapshot')['records']),2)
        self.store.set('rules',[rule(sheet_id='deleted')]);await self.m.check()
        self.assertTrue(self.store.get('snapshot')['stale'])
    async def test_disabled_rules_and_restart(self):
        self.store.set('rules',[rule(enabled=False)]);await self.m.check()
        self.assertEqual(self.store.get('snapshot')['rule_count'],0)
        self.store.close();self.store=Store(self.tmp.name+'/db')
        self.assertEqual(self.store.get('rules')[0]['enabled'],False)
    async def test_overlapping_check_is_rejected(self):
        async with self.m.lock:
            self.assertFalse(await self.m.check())
    async def test_scheduler_runs_without_browser(self):
        s=self.store.settings();s['interval_seconds']=.02;self.store.set('settings',s)
        task=asyncio.create_task(self.m.loop())
        try:
            await asyncio.sleep(.08)
            self.assertIsNotNone(self.store.get('snapshot')['last_success'])
        finally:
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):await task

    async def test_paused_scheduler_manual_query_and_resume(self):
        settings = self.store.settings() | {'auto_query_enabled': False, 'interval_seconds': .02}
        self.store.set('settings', settings)
        original = self.m.check
        self.m.check = AsyncMock(wraps=original)
        task = asyncio.create_task(self.m.loop())
        try:
            await asyncio.sleep(.05)
            self.m.check.assert_not_awaited()
            self.m.wake.set()  # Rule/configuration changes cannot bypass pause.
            await asyncio.sleep(.05)
            self.m.check.assert_not_awaited()
            self.m.manual_requested = True
            self.m.wake.set()
            await asyncio.sleep(.05)
            self.assertEqual(self.m.check.await_count, 1)
            self.assertIsNone(self.m.next_check)
            self.store.set('settings', settings | {'auto_query_enabled': True})
            self.m.wake.set()
            await asyncio.sleep(.08)
            self.assertGreater(self.m.check.await_count, 1)
            self.store.set('settings', settings)
            self.m.wake.set()
            await asyncio.sleep(.02)
            count = self.m.check.await_count
            await asyncio.sleep(.06)
            self.assertEqual(self.m.check.await_count, count)
            self.assertIsNone(self.m.next_check)
        finally:
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task


class AdapterTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.store=Store(self.tmp.name+'/db');self.client=TencentClient(self.store,None)
    async def asyncTearDown(self):self.store.close();self.tmp.cleanup()
    async def test_official_grid_offsets_and_pagination(self):
        paths=[]
        async def request(path,params,headers):
            paths.append(path)
            first=2 if len(paths)==1 else 1002
            return {'gridData':{'startRow':first-1,'startColumn':2,'rows':[{'values':[cell('x')]}]}}
        self.client.request=request
        cells=await self.client.read_column('300$ABC','tab1',3,2,1005,{})
        self.assertEqual(set(cells),{2,1002})
        self.assertIn('C2%3AC1001',paths[0]);self.assertIn('C1002%3AC1005',paths[1])
    async def test_malformed_grid_never_means_all_filled(self):
        async def request(*args): return {'unknown': []}
        self.client.request=request
        with self.assertRaises(TencentError):await self.client.read_column('f','s',1,2,3,{})
    async def test_personal_token_used_without_refresh(self):
        self.store.set('settings', self.store.settings() | {'client_id':'id','open_id':'user','access_token':'token'})
        self.client.request = AsyncMock()
        self.assertEqual((await self.client.headers())['Access-Token'], 'token')
        self.client.request.assert_not_awaited()


class WebTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.app=create_app(self.tmp.name,'test-password-12345',start_scheduler=False)
        self.web=TestClient(TestServer(self.app));await self.web.start_server()
        self.headers={'X-Requested-With':'WeeklyReport'}
    async def asyncTearDown(self):await self.web.close();self.tmp.cleanup()
    async def login(self):
        return await self.web.post('/api/login',json={'password':'test-password-12345'},headers=self.headers)
    async def test_auth_gate_csrf_logout(self):
        self.assertEqual((await self.web.get('/api/status')).status,401)
        self.assertEqual((await self.web.get('/manage',allow_redirects=False)).status,302)
        self.assertEqual((await self.web.post('/api/login',json={'password':'test-password-12345'})).status,403)
        response=await self.login();self.assertEqual(response.status,200)
        self.assertTrue(response.cookies['wr_session']['httponly'])
        self.assertEqual(response.cookies['wr_session']['samesite'],'Strict')
        self.assertEqual((await self.web.get('/api/status')).status,200)
        await self.web.post('/api/logout',json={},headers=self.headers)
        self.assertEqual((await self.web.get('/api/status')).status,401)
    async def test_auto_query_api_persists_and_manual_is_independent(self):
        endpoint = '/api/auto-query'
        self.assertEqual((await self.web.put(endpoint,json={'enabled':False},headers=self.headers)).status,401)
        await self.login()
        self.assertEqual((await self.web.put(endpoint,json={'enabled':False})).status,403)
        for value in ['false', 0, None]:
            self.assertEqual((await self.web.put(endpoint,json={'enabled':value},headers=self.headers)).status,400)
        self.assertTrue((await (await self.web.get('/api/status')).json())['auto_query_enabled'])
        await self.web.put(endpoint,json={'enabled':False},headers=self.headers)
        state = await (await self.web.get('/api/status')).json()
        self.assertFalse(state['auto_query_enabled'])
        self.assertIsNone(state['next_check'])
        persisted = Store(self.tmp.name+'/weeklyreport.db')
        try:
            self.assertFalse(persisted.settings()['auto_query_enabled'])
        finally:
            persisted.close()
        await self.web.put('/api/settings',json={'interval_seconds':600},headers=self.headers)
        self.assertFalse(self.app['store'].settings()['auto_query_enabled'])
        self.assertFalse(self.app['monitor'].manual_requested)
        self.assertEqual((await self.web.post('/api/check',json={},headers=self.headers)).status,202)
        self.assertTrue(self.app['monitor'].manual_requested)
        await self.web.put(endpoint,json={'enabled':True},headers=self.headers)
        self.assertTrue(self.app['store'].settings()['auto_query_enabled'])

    async def test_rule_crud_and_authenticated_credential_editing(self):
        await self.login()
        response=await self.web.post('/api/rules',json=rule(),headers=self.headers)
        data=await response.json();self.assertEqual(response.status,200);rid=data[0]['id']
        response=await self.web.put('/api/rules/'+rid,json=rule(target_column='D'),headers=self.headers)
        self.assertEqual((await response.json())[0]['target_column'],4)
        response=await self.web.put('/api/settings',json={'access_token':'VERY_SECRET'},headers=self.headers)
        self.assertEqual(response.status,200)
        response=await self.web.get('/api/settings');text=await response.text()
        self.assertEqual((await response.json())['access_token'],'VERY_SECRET')
        self.assertEqual(response.headers['Cache-Control'],'no-store')
        await self.web.put('/api/settings',json={'access_token':''},headers=self.headers)
        self.assertEqual(self.app['store'].settings()['access_token'],'')
        response=await self.web.delete('/api/rules/'+rid,json={},headers=self.headers)
        self.assertEqual(await response.json(),[])
    async def test_rate_limit_and_health(self):
        for _ in range(10):
            response=await self.web.post('/api/login',json={'password':'bad'},headers=self.headers)
            self.assertEqual(response.status,401)
        self.assertEqual((await self.login()).status,429)
        self.assertEqual((await self.web.get('/healthz')).status,200)
    async def test_reject_invalid_rule_and_busy_save(self):
        await self.login()
        self.assertEqual((await self.web.post('/api/rules',json=rule(start_row=0),headers=self.headers)).status,400)
        self.app['monitor'].running=True
        self.assertEqual((await self.web.put('/api/settings',json={},headers=self.headers)).status,409)
    async def test_reject_default_password(self):
        with self.assertRaises(RuntimeError):create_app(self.tmp.name,'change-this-password')

    async def test_injection_and_malformed_login_cannot_authenticate(self):
        for password in ["' OR 1=1 --", "admin' UNION SELECT 1--", 'test-password-12345\x00']:
            response = await self.web.post('/api/login', json={'password':password}, headers=self.headers)
            self.assertEqual(response.status, 401)
            self.assertNotIn('wr_session', response.cookies)
        for raw in [[], None, {'password': {'$ne': None}}, {'password': 'x'*1025}]:
            response = await self.web.post('/api/login', data=json.dumps(raw),
                headers=self.headers | {'Content-Type':'application/json'})
            self.assertEqual(response.status, 400)
        self.assertEqual((await self.web.get('/api/settings')).status, 401)

    async def test_settings_require_valid_session_and_logout_revokes_it(self):
        self.app['store'].set('settings', self.app['store'].settings() |
            {'access_token':'private-token', 'smtp_password':'private-password'})
        for path in ['/api/settings', '/api/variables', '/api/../api/settings']:
            response = await self.web.get(path, headers={'Cookie':'wr_session=forged'})
            self.assertEqual(response.status, 401)
            self.assertNotIn('private-', await response.text())
            self.assertEqual(response.headers['Cache-Control'], 'no-store')
        first = (await self.login()).cookies['wr_session'].value
        second = (await self.login()).cookies['wr_session'].value
        self.assertNotEqual(first, second)
        self.web.session.cookie_jar.clear()
        self.assertEqual((await self.web.get('/api/settings', headers={'Cookie':f'wr_session={first}'})).status, 401)
        self.assertEqual((await self.web.get('/api/settings', headers={'Cookie':f'wr_session={second}'})).status, 200)
        await self.web.post('/api/logout', json={}, headers=self.headers | {'Cookie':f'wr_session={second}'})
        self.assertEqual((await self.web.get('/api/settings', headers={'Cookie':f'wr_session={second}'})).status, 401)

    async def test_concurrent_login_cannot_overwrite_rate_limit(self):
        from aiohttp import web
        original = web.Request.json
        async def delayed_json(request, *args, **kwargs):
            await asyncio.sleep(0.02)
            return await original(request, *args, **kwargs)
        with patch.object(web.Request, 'json', delayed_json):
            for _ in range(5):
                responses = await asyncio.gather(*[
                    self.web.post('/api/login', json={'password':'wrong'}, headers=self.headers)
                    for _ in range(2)])
                self.assertTrue(all(r.status == 401 for r in responses))
            response = await self.web.post('/api/login', json={'password':'test-password-12345'},
                headers=self.headers | {'X-Forwarded-For':'203.0.113.42', 'X-Real-IP':'203.0.113.42'})
            self.assertEqual(response.status, 429)

    async def test_removed_settings_and_plaintext_persistence(self):
        await self.login()
        removed = dict(refresh_token='old', client_secret='old', file_id='old', token_expires_at=1)
        response = await self.web.put('/api/settings', json=removed |
            {'access_token':'persisted-token', 'smtp_password':'persisted-password'}, headers=self.headers)
        self.assertEqual(response.status, 200)
        persisted = Store(self.tmp.name+'/weeklyreport.db')
        try:
            self.assertEqual(persisted.settings()['access_token'], 'persisted-token')
            self.assertEqual(persisted.settings()['smtp_password'], 'persisted-password')
            self.assertTrue(set(removed).isdisjoint(persisted.settings()))
        finally:
            persisted.close()
        self.assertEqual((await self.web.put('/api/settings', json={'access_token':'bypass'})).status, 403)
        self.assertEqual(self.app['store'].settings()['access_token'], 'persisted-token')


if __name__ == '__main__': unittest.main()
