import tempfile
import unittest
from datetime import datetime, timezone
from aiohttp.test_utils import TestClient, TestServer

from app.core import Store, migrate_variables
from app.main import Monitor, create_app
from app.variables import build_variables
from test_app import rule, FakeClient


class VariableTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(self.tmp.name + '/db')

    async def asyncTearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_calendar_year_leap_and_timezone_boundaries(self):
        values = build_variables(self.store, current=datetime(2024, 12, 29, 16, tzinfo=timezone.utc))['values']
        self.assertEqual(values['global.date'], '2024-12-30')
        self.assertEqual(values['global.time'], '00:00:00')
        self.assertEqual(values['global.week.now'], '星期一')
        self.assertEqual(values['global.week.duration'], '2024-12-30 ~ 2025-01-05')
        self.assertEqual(values['global.preweek.sunday'], '2024-12-29')
        self.assertEqual(values['global.postweek.monday'], '2025-01-06')
        leap = build_variables(self.store, current=datetime(2024, 2, 29, tzinfo=timezone.utc))['values']
        self.assertEqual(leap['global.week.thursday'], '2024-02-29')
        self.assertEqual(leap['global.week.sunday'], '2024-03-03')
        self.assertEqual(len(values), 34)

    def test_migration_stable_and_persisted(self):
        self.store.set('rules', [rule(), rule(id='r2', variable_name='listener1')])
        migrate_variables(self.store)
        original = self.store.get('rules')
        self.assertEqual(original[0]['variable_name'], 'listener2')
        migrate_variables(self.store)
        self.assertEqual(original, self.store.get('rules'))
        self.store.close()
        self.store = Store(self.tmp.name + '/db')
        self.assertEqual(original, self.store.get('rules'))

    async def test_overlap_dedup_stale_disabled_and_empty(self):
        self.store.set('rules', [rule(variable_name='One'), rule(id='r2', variable_name='Two')])
        self.store.set('roster', {'source': {'sheet_id':'tab1','column':1,'start_row':2,'end_row':5}, 'names':['张三','李四'],'excluded':[]})
        client = FakeClient()
        monitor = Monitor(self.store, client)
        self.assertIsNone(build_variables(self.store)['values']['global.personcount'])
        await monitor.check()
        values = build_variables(self.store)['values']
        self.assertEqual(values['global.personcount'], 1)
        self.assertEqual(values['One.personlist'], '张三')
        self.assertEqual(values['Two.personcount'], 1)
        self.assertEqual(len(self.store.get('snapshot')['records']), 2)
        self.assertEqual(values['One.taskcol'], 'C')
        self.assertTrue(values['One.sheeturl'].endswith('?tab=tab1'))
        self.assertEqual(build_variables(self.store, True)['values']['global.healthy'], '查询中')
        client.fail = True
        await monitor.check()
        values = build_variables(self.store)['values']
        self.assertIsNone(values['Two.personcount'])
        self.assertIsNone(values['global.personlist'])
        self.assertEqual(values['global.healthy'], '异常')
        client.fail = False
        self.store.set('rules', [rule(variable_name='One', enabled=False), rule(id='r2',variable_name='Two',start_row=4,end_row=4)])
        await monitor.check()
        values = build_variables(self.store)['values']
        self.assertIsNone(values['One.personcount'])
        self.assertEqual(values['Two.personcount'], 0)
        self.assertEqual(values['Two.personlist'], '')
        self.assertEqual(values['global.listenenable'], 1)


class VariableWebTests(unittest.IsolatedAsyncioTestCase):
    async def test_auth_validation_edit_delete_and_no_secrets(self):
        with tempfile.TemporaryDirectory() as directory:
            async with TestClient(TestServer(create_app(directory, 'variable-test-password', start_scheduler=False))) as client:
                self.assertEqual((await client.get('/api/variables')).status, 401)
                self.assertEqual((await client.get('/variables', allow_redirects=False)).status, 302)
                headers = {'X-Requested-With':'WeeklyReport'}
                await client.post('/api/login', json={'password':'variable-test-password'}, headers=headers)
                self.assertEqual((await client.get('/variables')).status, 200)
                r = await client.post('/api/rules', json=rule(), headers=headers)
                first = (await r.json())[0]
                self.assertEqual(first['variable_name'], 'listener1')
                for name in ('1bad', 'bad_name', '中文', 'global', 'GLOBAL', 'LISTENER1', 'a.b'):
                    r = await client.post('/api/rules', json=rule(variable_name=name), headers=headers)
                    self.assertEqual(r.status, 400, name)
                r = await client.put('/api/rules/' + first['id'], json=rule(variable_name='ReportA'), headers=headers)
                self.assertEqual(r.status, 200)
                # Legacy clients that omit the new field must preserve the namespace.
                r = await client.put('/api/rules/' + first['id'], json=rule(), headers=headers)
                self.assertEqual((await r.json())[0]['variable_name'], 'ReportA')
                await client.put('/api/settings', json={'smtp_password':'TOPSECRET','access_token':'APITOKEN'}, headers=headers)
                response = await client.get('/api/variables')
                data = await response.json()
                self.assertEqual(len(data['rows']), 44)
                self.assertIn('ReportA.name', data['values'])
                self.assertNotIn('listener1.name', data['values'])
                self.assertNotIn('TOPSECRET', await response.text())
                self.assertNotIn('APITOKEN', await response.text())
                await client.delete('/api/rules/' + first['id'], json={}, headers=headers)
                r = await client.post('/api/rules', json=rule(), headers=headers)
                self.assertEqual((await r.json())[0]['variable_name'], 'listener2')
                data = await (await client.get('/api/variables')).json()
                self.assertNotIn('ReportA.name', data['values'])
