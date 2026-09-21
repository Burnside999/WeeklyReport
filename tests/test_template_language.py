import tempfile
import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

from aiohttp.test_utils import TestServer
from support import TestClient, create_app, scoped_store
from app.core import Store
from app.template_language import Program, TemplateSyntaxError
from app.templates import MailEngine, render, check_syntax
from app.variables import LOCAL_TZ, build_variables
from test_app import rule
from test_mail import template, auto


class LanguageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(self.tmp.name + '/db')
        self.rules = [rule(variable_name='listener1', name='研发'),
                      rule(id='r2', variable_name='listener2', name='行政', enabled=False)]
        self.store.set('rules', self.rules)
        self.store.set('snapshot', dict(semantics_version=2, last_success=datetime.now(LOCAL_TZ).isoformat(),
            stale=False, records=[{'person':'张三'}, {'person':'李四'}], rule_people={'r1':['张三','李四']}))
        # Keep the fixture independent of the rule helper's identifier.
        snapshot = self.store.get('snapshot')
        snapshot['rule_people'][self.rules[0]['id']] = ['张三', '李四']
        self.store.set('snapshot', snapshot)
        self.catalog = build_variables(self.store)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_dynamic_explicit_filtered_and_nested_lists(self):
        self.assertEqual(render('{% for l in global.alllistener %}{{l.name}};{% endfor %}', self.catalog), '研发;行政;')
        self.assertEqual(render('{% for l in [listener2, listener1] %}{{l.name}};{% endfor %}', self.catalog), '行政;研发;')
        text = '{% for l in global.alllistener with l.enable == true and (l.personcount >= 2 or l.name == "别的") %}{{l.name}}:{% for p in l.people %}{{p}};{% endfor %}{% endfor %}'
        self.assertEqual(render(text, self.catalog), '研发:张三;李四;')
        self.assertEqual(render('{% for p in global.people %}{{p}};{% endfor %}', self.catalog), '张三;李四;')
        self.assertEqual(render('{% for l in global.alllistener with l.name == "with" %}x{% endfor %}', self.catalog), '')

    def test_generic_object_and_scalar_lists(self):
        catalog = dict(values={'global.teams':[{'name':'A','members':[{'name':'B'}]}]},
                       schema={'global':{'teams':[{'name':'string','members':[{'name':'string'}]}]}}, rows=[])
        self.assertEqual(render('{% for team in global.teams %}{{team.name}}{% for member in team.members %}{{member.name}}{% endfor %}{% endfor %}', catalog), 'AB')

    def test_dynamic_list_tracks_add_delete_display_and_variable_rename(self):
        text = '{% for l in global.alllistener %}{{l.name}};{% endfor %}'
        self.rules[0].update(name='技术', variable_name='NewName')
        self.store.set('rules', self.rules[:1])
        self.assertEqual(render(text, build_variables(self.store)), '技术;')
        with self.assertRaisesRegex(TemplateSyntaxError, 'listener1'):
            render('{% for l in [listener1, listener2] %}{{l.name}}{% endfor %}', build_variables(self.store))
        self.store.set('rules', [])
        self.assertEqual(render(text, build_variables(self.store)), '')

    def test_empty_lists_still_validate_fields_and_scope(self):
        self.store.set('rules', [])
        catalog = build_variables(self.store)
        for text in ['{% for l in global.alllistener %}{{l.wrong}}{% endfor %}',
                     '{% for l in global.alllistener with l.enabel == true %}x{% endfor %}',
                     '{% for l in global.alllistener %}x{% endfor %}{{l.name}}',
                     '{% for l in global.alllistener %}{% for p in l.people %}{{p.wrong}}{% endfor %}{% endfor %}']:
            with self.subTest(text=text), self.assertRaises(TemplateSyntaxError):
                render(text, catalog)

    def test_optional_missing_field_skips_entire_predicate(self):
        catalog = dict(values={'global.items':[{}, {'enable':None}, {'enable':False}, {'enable':True}]},
                       schema={'global':{'items':[{'enable':'boolean'}]}}, rows=[])
        self.assertEqual(render('{% for l in global.items with l.enable != true %}x{% endfor %}', catalog), 'x')
        self.assertEqual(render('{% for l in global.items with true or l.enable == true %}x{% endfor %}', catalog), 'xx')
        with self.assertRaisesRegex(TemplateSyntaxError, 'enabel'):
            render('{% for l in global.items with l.enabel != true %}x{% endfor %}', catalog)

    def test_invalid_syntax_types_and_no_code_execution(self):
        invalid = [
            '{{global["date"]}}', '{{listener1.__class__}}', '{{__import__("os")}}',
            '{% for l in global.date %}x{% endfor %}',
            '{% for l in global.alllistener with l.enable == 1 %}x{% endfor %}',
            '{% for l in global.alllistener with l.personcount == "2" %}x{% endfor %}',
            '{% for l in global.alllistener with l.name > "A" %}x{% endfor %}',
            '{% for l in global.alllistener with l.enable and 1 %}x{% endfor %}',
            '{% for l in [listener1, missing] %}x{% endfor %}',
            '{% for l in [listener1, global.date] %}x{% endfor %}',
            '{% for l in [] %}x{% endfor %}',
            '{% for global in global.alllistener %}x{% endfor %}',
            '{% for l in global.alllistener %}{% for l in global.alllistener %}x{% endfor %}{% endfor %}',
            '{% for l in global.alllistener %}', '{% endfor %}', '{% if true %}',
            '{% for l in global.alllistener with l.enable() %}x{% endfor %}',
            '{{global.date', '{% for l in global.alllistener', '}}', '%}',
            '{{global.alllistener}}', '{{listener1}}',
        ]
        for text in invalid:
            with self.subTest(text=text), self.assertRaises(TemplateSyntaxError):
                render(text, self.catalog)

    def test_depth_iteration_and_output_limits(self):
        deep = ''.join('{% for '+c+' in global.alllistener %}' for c in 'abcd') + 'x' + '{% endfor %}'*4
        with self.assertRaisesRegex(TemplateSyntaxError, '3 层'):
            render(deep, self.catalog)
        catalog = dict(values={'global.items':['x']*101}, schema={'global':{'items':['string']}}, rows=[])
        with self.assertRaisesRegex(ValueError, '展开次数'):
            render('{% for a in global.items %}{% for b in global.items %}x{% endfor %}{% endfor %}', catalog)
        with self.assertRaisesRegex(ValueError, '20 万'):
            render('{% for a in global.items %}' + 'x'*2000 + '{% endfor %}', catalog)

    def test_error_positions_and_source_not_reinterpreted(self):
        result = check_syntax(dict(subject='正常', body='第一行\n{{listener1.nope}}'), self.catalog)
        error = result['syntax_errors'][0]
        self.assertEqual((error['field'], error['line'], error['column']), ('body',2,1))
        self.catalog['values']['listener1.name'] = '{{global.secret}}{% endfor %}<img src=x>'
        self.assertEqual(render('{{listener1.name}}', self.catalog), '{{global.secret}}{% endfor %}<img src=x>')
        self.assertTrue(Program('{% for l in global.alllistener %}{{l.name}}{% endfor %}', self.catalog).tokens)


class LoopEngineTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(self.tmp.name + '/db')
        self.current = datetime(2026,9,18,10,tzinfo=LOCAL_TZ)
        self.sender = AsyncMock()
        self.engine = MailEngine(self.store, SimpleNamespace(running=False), self.sender, lambda:self.current)

    async def asyncTearDown(self):
        self.store.close()
        self.tmp.cleanup()

    async def test_invalid_saved_blocked_manual_auto_and_recovers(self):
        bad = self.engine.save(template(body='{% for l in global.alllistener %}{{l.nope}}{% endfor %}'))
        self.assertTrue(bad['syntax_errors'])
        with self.assertRaisesRegex(ValueError, '语法错误'):
            await self.engine.manual(bad['id'], {'revision':bad['revision']})
        invalid_auto = self.engine.save(auto(body='{{global.nope}}'))
        valid_auto = self.engine.save(auto())
        await self.engine.tick()
        self.assertEqual(self.sender.await_count, 1)
        self.assertIsNone(self.engine.get(invalid_auto['id'])['last_trigger'])
        self.assertEqual(self.engine.get(valid_auto['id'])['status'], 'sent')
        saved = self.engine.save(template(body='{% for p in [global.date] %}{{p}}{% endfor %}', revision=bad['revision']), bad['id'])
        self.assertFalse(saved['syntax_errors'])
        await self.engine.manual(saved['id'], {'revision':saved['revision']})
        self.assertEqual(self.sender.await_count, 2)

    async def test_explicit_list_invalidated_before_manual_send_and_list(self):
        self.store.set('rules', [rule(variable_name='listener1')])
        saved = self.engine.save(template(body='{% for l in [listener1] %}{{l.name}}{% endfor %}'))
        self.store.set('rules', [])
        self.assertTrue(self.engine.listed()[0]['syntax_errors'])
        with self.assertRaisesRegex(ValueError, '不存在'):
            await self.engine.manual(saved['id'], {'revision':saved['revision']})
        self.sender.assert_not_awaited()
        self.store.set('rules', [rule(variable_name='listener1')])
        self.assertFalse(self.engine.listed()[0]['syntax_errors'])

    async def test_loop_statistics_do_not_bypass_stale_or_schedule_freshness(self):
        self.store.set('rules', [rule(variable_name='listener1')])
        snapshot = dict(semantics_version=2, stale=False, last_success=self.current.isoformat(),
                        records=[{'person':'张三'}], rule_people={rule()['id']:['张三']})
        self.store.set('snapshot', snapshot)
        body = '{% for l in global.alllistener with l.enable == true %}{% for p in l.people %}{{p}}{% endfor %}{% endfor %}'
        item = self.engine.save(template(body=body))
        self.current += timedelta(minutes=10)
        with self.assertRaisesRegex(ValueError, '列表当前值未知'):
            await self.engine.manual(item['id'], {'revision':item['revision']})
        self.sender.assert_not_awaited()
        self.current -= timedelta(minutes=10)
        snapshot['last_success'] = self.current.replace(hour=8,minute=59).isoformat()
        self.store.set('snapshot', snapshot)
        automatic = self.engine.save(auto(body=body, condition=dict(variable='global.listencount',operator='eq',value='1')))
        await self.engine.tick()
        self.sender.assert_not_awaited()
        self.assertIn('成功表格查询', self.engine.get(automatic['id'])['note'])
        snapshot['last_success'] = self.current.isoformat()
        self.store.set('snapshot', snapshot)
        await self.engine.tick()
        self.assertEqual(self.sender.await_count,1)
        self.assertEqual(self.sender.call_args.kwargs['text'],'张三')

    async def test_invalid_schedule_reference_is_marked_and_blocked(self):
        self.store.set('rules',[rule(variable_name='listener1')])
        item = self.engine.save(auto(condition=dict(variable='listener1.enable',operator='eq',value='true')))
        self.store.set('rules',[])
        self.assertTrue(self.engine.listed()[0]['syntax_errors'])
        await self.engine.tick()
        self.sender.assert_not_awaited()


class LoopWebTests(unittest.IsolatedAsyncioTestCase):
    async def test_validation_auth_save_invalid_list_and_send_gate(self):
        with tempfile.TemporaryDirectory() as directory:
            app = create_app(directory, 'loop-test-password', start_scheduler=False)
            async with TestClient(TestServer(app)) as client:
                self.assertEqual((await client.post('/api/templates/validate',json={},headers={'X-Requested-With':'WeeklyReport'})).status,401)
                headers = {'X-Requested-With':'WeeklyReport'}
                await client.post('/api/login',json={'username':'admin','password':'loop-test-password'},headers=headers)
                raw = template(body='{% for l in global.alllistener %}{{l.wrong}}{% endfor %}')
                self.assertEqual((await client.post('/api/templates/validate',json=raw)).status,403)
                result = await (await client.post('/api/templates/validate',json=raw,headers=headers)).json()
                self.assertTrue(result['syntax_errors'])
                saved = await (await client.post('/api/templates',json=raw,headers=headers)).json()
                self.assertTrue(saved['syntax_errors'])
                rows = await (await client.get('/api/templates')).json()
                self.assertTrue(rows[0]['syntax_errors'])
                app['mail_engine'].sender = AsyncMock()
                response = await client.post('/api/templates/'+saved['id']+'/send',json={'revision':saved['revision']},headers=headers)
                self.assertEqual(response.status,400)
                app['mail_engine'].sender.assert_not_awaited()
