import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

from aiohttp.test_utils import TestServer

from app.core import Store
from app.mail import DeliveryError
from app.templates import MailEngine, resolve_time, validate_template
from app.variables import LOCAL_TZ, build_variables
from support import TestClient, create_app
from test_app import rule
from test_mail import auto


def window(**changes):
    return dict(kind='window', start_weekday=4, start_clock='15:30',
                end_weekday=6, end_clock='17:00') | changes


class CheckWindowTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(self.tmp.name+'/db')
        self.current = datetime(2026,10,16,15,29,59,tzinfo=LOCAL_TZ)
        self.monitor = SimpleNamespace(running=False)
        self.sender = AsyncMock()
        self.restart()

    def restart(self):
        self.engine = MailEngine(self.store, self.monitor, self.sender, lambda:self.current)

    async def asyncTearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def success(self, **changes):
        self.store.set('snapshot', dict(semantics_version=2, last_success=self.current.isoformat(),
            last_attempt=self.current.isoformat(), stale=False, records=[], rule_people={}) | changes)

    def test_validation_and_normalization(self):
        catalog = build_variables(self.store, current=self.current)
        saved = validate_template(auto(schedule=window()), catalog)['schedule']
        self.assertEqual(saved, window(start_clock='15:30:00', end_clock='17:00:00'))
        self.assertEqual(validate_template(auto(schedule=window(end_weekday=4, end_clock='16:00')), catalog)['schedule']['end_weekday'], 4)
        for changes in [dict(start_weekday=True), dict(end_weekday='6'), dict(start_weekday=-1),
                        dict(end_weekday=7), dict(end_weekday=None), dict(start_clock='24:00'),
                        dict(end_clock='17:60'), dict(start_clock=None),
                        dict(start_weekday=6, end_weekday=4),
                        dict(end_weekday=4, end_clock='15:30'),
                        dict(end_weekday=4, end_clock='15:29:59')]:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                validate_template(auto(schedule=window(**changes)), catalog)

    def test_boundaries_timezone_and_year_rollover(self):
        schedule = window()
        start = self.current.replace(minute=30, second=0)
        for current in [self.current, start, start+timedelta(days=1),
                        start.replace(hour=16, minute=59, second=59)+timedelta(days=2)]:
            self.assertEqual(resolve_time(schedule, current), start)
            self.assertEqual(resolve_time(schedule, current.astimezone(timezone.utc)), start)
        self.assertIsNone(resolve_time(schedule, start.replace(hour=17, minute=0)+timedelta(days=2)))
        self.assertEqual(resolve_time(schedule, start+timedelta(days=3)), start+timedelta(days=7))
        january = datetime(2027,1,3,16,tzinfo=LOCAL_TZ)
        self.assertEqual(resolve_time(schedule, january), datetime(2027,1,1,15,30,tzinfo=LOCAL_TZ))
        self.assertEqual(resolve_time(schedule, january+timedelta(days=1)), datetime(2027,1,8,15,30,tzinfo=LOCAL_TZ))

    async def test_once_across_days_restart_and_next_week(self):
        item = self.engine.save(auto(schedule=window()))
        await self.engine.tick()
        self.sender.assert_not_awaited()
        self.current += timedelta(seconds=1)
        await self.engine.tick()
        first_cycle = self.engine.get(item['id'])['cycle']
        self.assertEqual(self.sender.await_count, 1)
        self.current += timedelta(days=1)
        await self.engine.tick()
        self.assertEqual(self.engine.get(item['id'])['cycle'], first_cycle)
        self.store.close()
        self.store = Store(self.tmp.name+'/db')
        self.restart()
        self.current += timedelta(days=1)
        await self.engine.tick()
        self.assertEqual(self.sender.await_count, 1)
        self.current = self.current.replace(hour=17, minute=0)
        await self.engine.tick()
        self.assertIn('时段已结束', self.engine.get(item['id'])['note'])
        self.current += timedelta(days=1)
        await self.engine.tick()
        self.assertEqual(self.sender.await_count, 1)
        self.current = self.current.replace(hour=15, minute=30)+timedelta(days=4)
        await self.engine.tick()
        await self.engine.tick()
        self.assertEqual(self.sender.await_count, 2)

    async def test_condition_can_become_true_later_but_not_after_end(self):
        self.engine.save(auto(schedule=window(), condition=dict(variable='global.listenenable', operator='gt', value='0')))
        self.current = self.current.replace(hour=16)
        await self.engine.tick()
        self.sender.assert_not_awaited()
        self.current += timedelta(days=1)
        self.store.set('rules', [rule(variable_name='listener1')])
        await self.engine.tick()
        self.assertEqual(self.sender.await_count, 1)
        # A new template created at the closing boundary must wait until next week.
        self.current = self.current.replace(hour=17, minute=0, second=0)+timedelta(days=1)
        self.engine.save(auto(schedule=window()))
        await self.engine.tick()
        self.assertEqual(self.sender.await_count, 1)
        self.current = self.current.replace(hour=16)+timedelta(days=5)
        await self.engine.tick()
        self.assertEqual(self.sender.await_count, 2)  # only the first template's condition is met

    async def test_expired_period_does_not_catch_up_and_mid_period_start_works(self):
        self.current = self.current.replace(hour=17, minute=1)+timedelta(days=2)
        self.engine.save(auto(schedule=window()))
        await self.engine.tick()
        self.current += timedelta(days=1)
        self.restart()
        await self.engine.tick()
        self.sender.assert_not_awaited()
        self.current = self.current.replace(hour=16)+timedelta(days=5)
        await self.engine.tick()
        self.assertEqual(self.sender.await_count, 1)

    async def test_requires_fresh_post_start_statistics(self):
        self.engine.save(auto(schedule=window(), condition=dict(variable='global.personcount', operator='eq', value='0')))
        self.success()
        self.current += timedelta(seconds=1)
        await self.engine.tick()
        self.sender.assert_not_awaited()
        self.success(stale=True)
        await self.engine.tick()
        self.sender.assert_not_awaited()
        self.success()
        self.monitor.running = True
        await self.engine.tick()
        self.sender.assert_not_awaited()
        self.monitor.running = False
        await self.engine.tick()
        self.assertEqual(self.sender.await_count, 1)

    async def test_failed_attempt_is_not_retried_automatically_in_same_window(self):
        self.current = self.current.replace(hour=16)
        self.sender.side_effect = DeliveryError('SMTP failed')
        item = self.engine.save(auto(schedule=window()))
        await self.engine.tick()
        self.current += timedelta(days=1)
        self.restart()
        await self.engine.tick()
        self.assertEqual(self.sender.await_count, 1)
        self.assertEqual(self.engine.get(item['id'])['status'], 'error')
        self.current += timedelta(days=6)
        self.sender.side_effect = None
        await self.engine.tick()
        self.assertEqual(self.sender.await_count, 2)


class CheckWindowAPITests(unittest.IsolatedAsyncioTestCase):
    async def test_save_load_and_reject_reversed_window(self):
        with tempfile.TemporaryDirectory() as directory:
            app = create_app(directory, 'window-test-password', start_scheduler=False)
            async with TestClient(TestServer(app)) as client:
                headers = {'X-Requested-With':'WeeklyReport'}
                await client.post('/api/login', json=dict(username='admin', password='window-test-password'), headers=headers)
                response = await client.post('/api/templates', json=auto(schedule=window()), headers=headers)
                self.assertEqual(response.status, 200)
                saved = await response.json()
                self.assertEqual(saved['schedule']['start_clock'], '15:30:00')
                self.assertEqual(saved['syntax_errors'], [])
                response = await client.put('/api/templates/'+saved['id'], json=saved | {'schedule':window(end_weekday=3)}, headers=headers)
                self.assertEqual(response.status, 400)
                self.assertIn('早于结束', (await response.json())['error'])
                current = (await (await client.get('/api/templates')).json())[0]
                self.assertEqual(current['schedule'], saved['schedule'])
