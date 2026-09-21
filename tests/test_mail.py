import asyncio
import smtplib
import tempfile
import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from aiohttp.test_utils import TestServer
from support import TestClient, create_app, scoped_store
from app.core import Store
from app.mail import SMTPConfig, SMTPMailer, DeliveryError
from app.templates import MailEngine, TemplateConflict, validate_template, render, resolve_time, typed_value
from app.variables import LOCAL_TZ, build_variables


def template(**changes):
    return dict(recipients=['a@example.com'], subject='报告 {{global.date}}',
                body='内容 {{global.listencount}}', mode='manual') | changes


def auto(**changes):
    return template(mode='auto', schedule=dict(kind='variable',value='global.week.friday',clock='09:00'),
                    condition=dict(variable='global.listencount',operator='eq',value='0')) | changes


class EngineTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.store=Store(self.tmp.name+'/db')
        self.current=datetime(2026,9,18,8,59,tzinfo=LOCAL_TZ)
        self.monitor=SimpleNamespace(running=False)
        self.sender=AsyncMock()
        self.engine=MailEngine(self.store,self.monitor,self.sender,lambda:self.current)
    async def asyncTearDown(self):self.store.close();self.tmp.cleanup()
    def catalog(self):return build_variables(self.store,current=self.current)
    def success(self, **extra):
        self.store.set('snapshot',dict(semantics_version=2,last_success=self.current.isoformat(),last_attempt=self.current.isoformat(),stale=False,records=[],rule_people={})|extra)

    def test_recipients_tokens_and_header_validation(self):
        for changes in [dict(recipients=[]),dict(recipients=['x@example.com']*2),dict(recipients=['bad']),
                        dict(recipients=[f'a{i}@example.com' for i in range(6)]),dict(subject='x\nBcc: x'),
                        dict(subject='{{global.nope}}'),dict(body='{{global.date'),dict(body='{{__import__("os")}}')]:
            with self.assertRaises(ValueError):validate_template(template(**changes),self.catalog())
        self.assertEqual(render('{{ global.date }} {{global.listencount}}',self.catalog()),'2026-09-18 0')
        with self.assertRaises(ValueError):render('{{global.personcount}}',self.catalog())
        self.assertEqual(len(validate_template(template(recipients=[f'a{i}@example.com' for i in range(5)]),self.catalog())['recipients']),5)

    def test_time_and_condition_types(self):
        for name in ['global.personlist','global.url','global.week.duration']:
            with self.assertRaises(ValueError):validate_template(auto(condition=dict(variable=name,operator='eq',value='0')),self.catalog())
        for name, value in [('global.date','2026-09-18'), ('global.time','09:00'),
                            ('global.lastquery','2026-09-18T09:00:00+08:00')]:
            with self.assertRaises(ValueError):
                validate_template(auto(condition=dict(variable=name,operator='eq',value=value)),self.catalog())
        for value in ['global.time','global.personcount','notFound']:
            with self.assertRaises(ValueError):validate_template(auto(schedule=dict(kind='variable',value=value)),self.catalog())
        with self.assertRaises(ValueError):validate_template(auto(schedule=dict(kind='fixed',value='2026-02-30T09:00')),self.catalog())
        rule=validate_template(auto(schedule=dict(kind='variable',value='{{global.week.friday}}',clock='09:30')),self.catalog())
        self.assertEqual(resolve_time(rule['schedule'],self.catalog()),self.current.replace(hour=9,minute=30))
        self.assertEqual(typed_value('datetime','2026-09-18T01:00Z'),self.current.replace(hour=9,minute=0))
        self.assertFalse(typed_value('boolean','false'))
        with self.assertRaises(ValueError):typed_value('boolean','0')
        with self.assertRaises(ValueError):typed_value('integer','0.5')

    async def test_once_per_cycle_week_rollover_and_restart(self):
        item=self.engine.save(auto());await self.engine.tick();self.sender.assert_not_awaited()
        self.current=self.current.replace(hour=9,minute=0)
        await self.engine.tick();await self.engine.tick();self.assertEqual(self.sender.await_count,1)
        self.store.close();self.store=Store(self.tmp.name+'/db')
        self.engine=MailEngine(self.store,self.monitor,self.sender,lambda:self.current)
        await self.engine.tick();self.assertEqual(self.sender.await_count,1)
        self.current+=timedelta(days=3);await self.engine.tick()
        self.assertEqual(self.engine.get(item['id'])['status'],'waiting')
        self.current+=timedelta(days=4);await self.engine.tick();self.assertEqual(self.sender.await_count,2)

    async def test_edit_reset_noop_and_optimistic_revision(self):
        self.current=self.current.replace(hour=10)
        item=self.engine.save(auto());await self.engine.tick()
        saved=self.engine.save(auto(revision=item['revision']),item['id'])
        self.assertEqual(saved['status'],'sent');await self.engine.tick();self.assertEqual(self.sender.await_count,1)
        saved=self.engine.save(auto(body='不同正文',revision=item['revision']),item['id'])
        self.assertEqual(saved['status'],'waiting');await self.engine.tick();self.assertEqual(self.sender.await_count,2)
        with self.assertRaises(TemplateConflict):self.engine.reset(item['id'],item['revision'])
        self.engine.reset(item['id'],saved['revision']);await self.engine.tick();self.assertEqual(self.sender.await_count,3)

    async def test_manual_cooldown_confirmation_and_new_attempt_token(self):
        item=self.engine.save(template());raw=dict(revision=item['revision'])
        result=await self.engine.manual(item['id'],raw)
        self.assertEqual(result['status'],'sent')
        with self.assertRaises(TemplateConflict) as exc:await self.engine.manual(item['id'],raw)
        confirmed=raw|{'confirm_attempt':exc.exception.details['confirm_attempt']}
        await self.engine.manual(item['id'],confirmed);self.assertEqual(self.sender.await_count,2)
        with self.assertRaises(TemplateConflict):await self.engine.manual(item['id'],confirmed)
        self.current+=timedelta(minutes=5)
        await self.engine.manual(item['id'],raw);self.assertEqual(self.sender.await_count,3)

    async def test_concurrent_send_and_mutation_are_rejected(self):
        gate=asyncio.Event();started=asyncio.Event()
        async def sending(**kwargs):started.set();await gate.wait()
        self.engine.sender=sending;item=self.engine.save(template());raw=dict(revision=item['revision'])
        task=asyncio.create_task(self.engine.manual(item['id'],raw));await started.wait()
        with self.assertRaises(TemplateConflict):await self.engine.manual(item['id'],raw)
        with self.assertRaises(TemplateConflict):self.engine.delete(item['id'],item['revision'])
        with self.assertRaises(TemplateConflict):self.engine.save(template())
        self.assertEqual(self.engine.items()[0]['status'],'sending')
        gate.set();await task

    async def test_failure_and_interrupted_delivery_require_explicit_reset(self):
        self.current=self.current.replace(hour=10)
        self.sender.side_effect=DeliveryError('结果不确定')
        item=self.engine.save(auto());await self.engine.tick();await self.engine.tick()
        self.assertEqual(self.sender.await_count,1);self.assertEqual(self.engine.get(item['id'])['status'],'error')
        data=self.engine.get(item['id']);data['status']='sending';self.engine.write(data)
        self.engine=MailEngine(self.store,self.monitor,self.sender,lambda:self.current)
        await self.engine.tick();self.assertEqual(self.sender.await_count,1)
        self.assertIn('中断',self.engine.get(item['id'])['error'])
        self.sender.side_effect=None;self.engine.reset(item['id'],item['revision']);await self.engine.tick()
        self.assertEqual(self.engine.get(item['id'])['status'],'sent')

    async def test_unknown_stale_and_before_threshold_statistics_do_not_send(self):
        item=self.engine.save(auto(condition=dict(variable='global.personcount',operator='eq',value='0')))
        self.success();self.current=self.current.replace(hour=9,minute=0)
        await self.engine.tick();self.sender.assert_not_awaited()
        self.success(stale=True);await self.engine.tick();self.sender.assert_not_awaited()
        self.success();self.monitor.running=True;await self.engine.tick();self.sender.assert_not_awaited()
        self.monitor.running=False;await self.engine.tick();self.assertEqual(self.sender.await_count,1)
        self.engine.reset(item['id'],item['revision']);self.current+=timedelta(minutes=7)
        await self.engine.tick();self.assertEqual(self.sender.await_count,1)
        self.success();await self.engine.tick();self.assertEqual(self.sender.await_count,2)

    async def test_conditions_false_then_true_and_missing_reference(self):
        self.current=self.current.replace(hour=10)
        self.engine.save(auto(condition=dict(variable='global.listenenable',operator='gt',value='0')))
        await self.engine.tick();self.sender.assert_not_awaited()
        self.store.set('rules',[dict(id='r',variable_name='listener1',enabled=True,name='x',sheet_name='x',sheet_id='x',owner_column=1,target_column=2,start_row=2,end_row=5)])
        await self.engine.tick();self.assertEqual(self.sender.await_count,1)
        item=self.engine.save(auto(body='{{listener1.name}}'))
        self.store.set('rules',[]);await self.engine.tick()
        self.assertEqual(self.sender.await_count,1);self.assertIn('不存在',self.engine.get(item['id'])['note'])

    async def test_background_loop_wakes_and_sends_without_browser(self):
        self.current=self.current.replace(hour=10)
        self.engine.save(auto());task=asyncio.create_task(self.engine.loop())
        try:
            for _ in range(10):
                await asyncio.sleep(.01)
                if self.sender.await_count:break
            self.assertEqual(self.sender.await_count,1)
        finally:self.engine.stopping=True;self.engine.wake.set();await task


class SMTPTests(unittest.IsolatedAsyncioTestCase):
    def config(self,security='ssl'):return SMTPConfig('smtp.example.com',465,security,'from@example.com','secret','发送人')
    async def test_tls_and_envelope_and_utf8(self):
        for security in ['ssl','starttls']:
            with patch('smtplib.SMTP_SSL') as ssl,patch('smtplib.SMTP') as plain:
                smtp=(ssl if security=='ssl' else plain).return_value;smtp.send_message.return_value={}
                await SMTPMailer(self.config(security)).send(subject='中文标题',text='第一行\n第二行',recipients=['a@example.com','b@example.com'])
                smtp.login.assert_called_once_with('from@example.com','secret')
                message=smtp.send_message.call_args.args[0]
                self.assertEqual(str(message['Subject']),'中文标题')
                self.assertEqual(smtp.send_message.call_args.kwargs['to_addrs'],['a@example.com','b@example.com'])
                self.assertIn('第二行',message.get_content())
                if security=='starttls':smtp.starttls.assert_called_once()
    async def test_partial_refusal_and_quit_failure(self):
        with patch('smtplib.SMTP_SSL') as ssl:
            smtp=ssl.return_value;smtp.send_message.return_value={'b@example.com':(550,b'private')}
            with self.assertRaisesRegex(DeliveryError,'部分'):await SMTPMailer(self.config()).send(subject='x',text='y',recipients=['a@example.com','b@example.com'])
            smtp.send_message.return_value={};smtp.quit.side_effect=smtplib.SMTPServerDisconnected('quit failed')
            await SMTPMailer(self.config()).send(subject='x',text='y',recipients=['a@example.com'])
            smtp.close.assert_called_once()
    async def test_auth_error_does_not_expose_secrets(self):
        with patch('smtplib.SMTP_SSL') as ssl:
            ssl.return_value.login.side_effect=smtplib.SMTPAuthenticationError(535,b'secret-private')
            with self.assertRaises(DeliveryError) as exc:await SMTPMailer(self.config()).send(subject='x',text='y',recipients=['a@example.com'])
            self.assertNotIn('secret',str(exc.exception))


class MailWebTests(unittest.IsolatedAsyncioTestCase):
    async def test_crud_auth_manual_send_cooldown_and_empty_start(self):
        with tempfile.TemporaryDirectory() as directory:
            app=create_app(directory,'mail-test-password',start_scheduler=False)
            async with TestClient(TestServer(app)) as client:
                self.assertEqual((await client.get('/mail',allow_redirects=False)).status,302)
                self.assertEqual((await client.get('/api/templates')).status,401)
                headers={'X-Requested-With':'WeeklyReport'}
                await client.post('/api/login',json={'username':'admin','password':'mail-test-password'},headers=headers)
                self.assertEqual(await (await client.get('/api/templates')).json(),[])
                r=await client.post('/api/templates',json=template());self.assertEqual(r.status,403)
                r=await client.post('/api/templates',json=template(),headers=headers);item=await r.json()
                app['mail_engine'].sender=AsyncMock()
                send='/api/templates/'+item['id']+'/send';raw={'revision':item['revision']}
                r=await client.post(send,json=raw,headers=headers);self.assertEqual(r.status,200)
                r=await client.post(send,json=raw,headers=headers);self.assertEqual(r.status,409);confirmation=await r.json()
                self.assertTrue(confirmation['confirmation_required'])
                r=await client.post(send,json=raw|{'confirm_attempt':confirmation['confirm_attempt']},headers=headers);self.assertEqual(r.status,200)
                r=await client.delete('/api/templates/'+item['id'],json=raw,headers=headers);self.assertEqual(r.status,200)
                self.assertEqual(await (await client.get('/api/templates')).json(),[])

