"""Push tests use generated local keys and fake transports; never send real push/mail."""
import asyncio
import json
import os
import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from aiohttp.test_utils import TestServer
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from support import TestClient, create_app
from app.accounts import DocumentStore, password_hash
from app.mail import DeliveryError
from app.notifications import Notifications, b64, subscription
from app.variables import LOCAL_TZ

HEADERS = {'X-Requested-With':'WeeklyReport'}


def sub(suffix='device'):
    point = ec.generate_private_key(ec.SECP256R1()).public_key().public_bytes(
        serialization.Encoding.X962,serialization.PublicFormat.UncompressedPoint)
    return {'endpoint':'https://web.push.apple.com/'+suffix,'keys':{'p256dh':b64(point),'auth':b64(os.urandom(16))}}


def template(subject='周报 {{global.date}}', **extra):
    return dict(recipients=['test@example.com'],subject=subject,body='正文',mode='manual') | extra


class PushTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.env=patch.dict(os.environ,{'WEB_PUSH_ENABLED':'true','VAPID_SUBJECT':'mailto:test@example.com'})
        self.env.start()
        self.app=create_app(self.tmp.name,'test-password-123',False)
        self.client=TestClient(TestServer(self.app));await self.client.start_server()
        await self.client.post('/api/login',json={'username':'admin','password':'test-password-123'},headers=HEADERS)
        self.service=self.app['notifications'];self.db=self.service.db
        self.runtime=self.app['workspaces'].items[self.app['_test_document_id']]
        self.engine=self.runtime.mailer;self.engine.sender=AsyncMock()
        self.uid=self.runtime.store.owner_id;self.did=self.runtime.store.id
        self.service.sender=Mock(return_value=201)

    async def asyncTearDown(self):
        await self.client.close();self.env.stop();self.tmp.cleanup()

    async def register(self, info=None, keep_welcome=False):
        response=await self.client.get('/api/push/config');self.assertEqual(response.status,200)
        response=await self.client.post('/api/push/subscriptions',json=info or sub(),headers=HEADERS)
        self.assertEqual(response.status,200,await response.text())
        if not keep_welcome:
            self.db.execute('DELETE FROM notifications WHERE is_test=1');self.db.commit()
        return (await response.json())['id']

    def choose(self,item,did=None,uid=None):
        return self.service.choose(uid or self.uid,{'document_id':did or self.did,'trigger_id':item['id']})

    async def fire(self,item):
        return await self.engine.manual(item['id'],{'revision':item['revision']})

    async def test_primary_unique_across_documents_and_delete(self):
        a=self.engine.save(template());self.choose(a)
        doc=self.app['accounts'].add_document(self.uid,'另一文档','https://docs.qq.com/sheet/Other',{})
        runtime=self.app['workspaces'].add(doc)
        b=runtime.mailer.save(template('另一标题'));self.choose(b,doc['id'])
        self.assertEqual(self.db.execute('SELECT count(*) FROM primary_notifications').fetchone()[0],2)
        await self.fire(a)
        self.assertEqual(self.db.execute('SELECT count(*) FROM notifications').fetchone()[0],1)
        runtime.mailer.delete(b['id'],b['revision']);self.assertIsNone(self.service.primary(self.uid,doc['id']))
        self.assertEqual(self.service.primary(self.uid,self.did)['trigger_id'],a['id'])
        self.choose(a);self.service.choose(self.uid,{'document_id':self.did,'trigger_id':None})
        self.assertIsNone(self.service.primary(self.uid,self.did))

    async def test_mail_preserved_rendered_title_and_one_notification_per_attempt(self):
        await self.register()
        ordinary=self.engine.save(template());await self.fire(ordinary)
        self.assertEqual(self.db.execute('SELECT count(*) FROM notifications').fetchone()[0],0)
        primary=self.engine.save(template());self.choose(primary);await self.fire(primary)
        self.assertEqual(self.engine.sender.await_count,2)
        claimed=self.engine.items()[-1]
        subject=self.engine.sender.call_args.kwargs['subject']
        self.service.claim(self.runtime.store,claimed,subject)
        records=await (await self.client.get('/api/notifications?after=0')).json()
        self.assertEqual(len(records['items']),1);self.assertEqual(records['items'][0]['title'],subject)
        self.assertNotIn('{{',subject)
        await self.service.tick();await self.service.tick()
        self.service.sender.assert_called_once()
        self.assertEqual(self.service.sender.call_args.args[1]['body'],subject)
        self.assertEqual(self.db.execute('SELECT state FROM push_deliveries').fetchone()[0],'accepted')
        next_page=await (await self.client.get('/api/notifications?after='+str(records['next_cursor']))).json()
        self.assertEqual(next_page['items'],[])

    async def test_mail_failure_does_not_cancel_push_and_push_failure_does_not_change_mail(self):
        await self.register();item=self.engine.save(template());self.choose(item)
        self.engine.sender=AsyncMock(side_effect=DeliveryError('SMTP failed'))
        with self.assertRaises(DeliveryError):await self.fire(item)
        await self.service.tick();self.service.sender.assert_called_once()
        self.assertEqual(self.engine.get(item['id'])['status'],'error')
        item=self.engine.save(template());self.choose(item);self.engine.sender=AsyncMock()
        await self.fire(item);self.service.sender=Mock(return_value=403);await self.service.tick()
        self.assertEqual(self.engine.get(item['id'])['status'],'sent')
        self.assertEqual(self.db.execute('SELECT state FROM push_deliveries ORDER BY notification_id DESC LIMIT 1').fetchone()[0],'failed')

    async def test_weekly_and_fixed_auto_use_same_push_hook(self):
        await self.register()
        current=datetime(2026,9,25,10,tzinfo=LOCAL_TZ);self.engine.clock=lambda:current
        for schedule in [{'kind':'weekly','weekdays':[4],'clock':'09:00'}, {'kind':'fixed','value':'2026-09-25T09:00'}]:
            item=self.engine.save(template(mode='auto',schedule=schedule,
                condition={'variable':'global.listencount','operator':'eq','value':'0'}))
            self.choose(item);await self.engine.tick();await self.engine.tick()
        self.assertEqual(self.db.execute('SELECT count(*) FROM notifications').fetchone()[0],2)
        self.assertEqual(self.engine.sender.await_count,2)

    async def test_retry_restart_key_persistence_and_expiry(self):
        await self.register();item=self.engine.save(template());self.choose(item);await self.fire(item)
        self.service.sender=Mock(return_value=503);await self.service.tick()
        self.assertEqual(self.db.execute('SELECT state,attempts FROM push_deliveries').fetchone(),('pending',1))
        self.db.execute("UPDATE push_deliveries SET state='sending',next_attempt=0");self.db.commit()
        key=self.service.public_key;restarted=Notifications(self.app['accounts'])
        self.assertEqual(restarted.public_key,key)
        restarted.sender=Mock(return_value=201);await restarted.tick();restarted.sender.assert_called_once()
        self.assertEqual(restarted.sender.call_args.args[1]['id'],self.service.sender.call_args.args[1]['id'])
        self.assertEqual(Path(self.tmp.name,'vapid-private.pem').stat().st_mode & 0o777,0o600)
        self.db.execute("UPDATE push_deliveries SET state='pending',next_attempt=0")
        self.db.execute('UPDATE notifications SET expires_at=0');self.db.commit()
        restarted.sender.reset_mock();await restarted.tick();restarted.sender.assert_not_called()
        self.assertEqual(self.db.execute('SELECT state FROM push_deliveries').fetchone()[0],'expired')

    async def test_invalid_endpoint_keys_and_no_redirect_transport(self):
        for endpoint in ['http://web.push.apple.com/a','https://127.0.0.1/a','https://push.apple.com.evil.test/a',
                         'https://push.apple.com@evil.test/a','https://web.push.apple.com:8443/a','https://fcm.googleapis.com/a']:
            response=await self.client.post('/api/push/subscriptions',json=sub() | {'endpoint':endpoint},headers=HEADERS)
            self.assertEqual(response.status,409) # no device cookie yet
            with self.assertRaises(ValueError):subscription(sub() | {'endpoint':endpoint})
        await self.client.get('/api/push/config')
        response=await self.client.post('/api/push/subscriptions',json=sub() | {'keys':{'p256dh':'bad','auth':'bad'}},headers=HEADERS)
        self.assertEqual(response.status,400)
        # Real encryption/VAPID signing, mocked HTTP only, no real notification.
        import requests
        response=Mock(status_code=201)
        with patch.object(requests.Session,'request',return_value=response) as transport:
            self.assertEqual(self.service.send(sub(),{'id':1,'body':'测试'},30),201)
            args=transport.call_args
            self.assertFalse(args.kwargs['allow_redirects']);self.assertEqual(args.kwargs['timeout'],10)
            self.assertIn('vapid',args.kwargs['headers']['Authorization'].lower())
            self.assertIsInstance(args.kwargs['data'],bytes)

    async def test_enable_welcome_once_logout_and_expired_session_unbind(self):
        info=sub()
        await self.register(info,keep_welcome=True)
        await self.register(info,keep_welcome=True)
        self.assertEqual(self.db.execute('SELECT title FROM notifications').fetchall(),[('消息推送启动成功！',)])
        response=await self.client.post('/api/push/test',json={},headers=HEADERS)
        self.assertEqual(response.status,404)
        self.engine.sender.assert_not_awaited()
        self.assertEqual((await (await self.client.get('/api/notifications')).json())['items'],[])
        # Even logout after session expiry can revoke this browser's subscription.
        self.client.session.cookie_jar.update_cookies({'wr_session':'expired'})
        response=await self.client.post('/api/logout',json={},headers=HEADERS);self.assertEqual(response.status,200)
        self.assertEqual(self.db.execute('SELECT count(*) FROM push_subscriptions').fetchone()[0],0)
        await self.service.tick();self.service.sender.assert_not_called()

    async def test_isolation_and_device_rebinding_after_account_switch(self):
        info=sub();await self.register(info)
        item=self.engine.save(template());self.choose(item);await self.fire(item)
        actor=self.app['accounts'].user(uid=self.uid)
        other=self.app['accounts'].save_user(actor,{'username':'other','role':'user'},encoded=password_hash('other-password-123'))
        await self.client.post('/api/login',json={'username':'other','password':'other-password-123'},headers=HEADERS)
        self.assertEqual(self.db.execute('SELECT count(*) FROM push_subscriptions').fetchone()[0],0)
        self.assertEqual((await (await self.client.get('/api/notifications')).json())['items'],[])
        response=await self.client.put('/api/me/primary-trigger',json={'document_id':self.did,'trigger_id':item['id']},headers=HEADERS)
        self.assertEqual(response.status,404)
        response=await self.client.post('/api/push/subscriptions',json=info,headers=HEADERS);self.assertEqual(response.status,200)
        self.assertEqual(self.db.execute('SELECT user_id FROM push_subscriptions').fetchone()[0],other['id'])
        await self.service.tick();self.service.sender.assert_called_once()
        self.assertEqual(self.service.sender.call_args.args[1]['body'],'消息推送启动成功！')

    async def test_invalid_subscription_and_account_version_revocation(self):
        await self.register();item=self.engine.save(template());self.choose(item);await self.fire(item)
        self.service.sender=Mock(return_value=410);await self.service.tick()
        self.assertEqual(self.db.execute('SELECT count(*) FROM push_subscriptions').fetchone()[0],0)
        await self.register(sub('second'));new=self.engine.save(template());self.choose(new);await self.fire(new)
        self.db.execute('UPDATE users SET version=version+1 WHERE id=?',(self.uid,));self.db.commit()
        self.service.sender.reset_mock();await self.service.tick();self.service.sender.assert_not_called()
        self.assertEqual(self.db.execute('SELECT count(*) FROM push_subscriptions').fetchone()[0],0)

    async def test_public_assets_auth_csrf_and_document_cascade(self):
        item=self.engine.save(template());self.choose(item);await self.fire(item)
        response=await self.client.put('/api/me/primary-trigger',json={});self.assertEqual(response.status,403)
        for path,kind in [('/sw.js','javascript'),('/manifest.webmanifest','manifest'),('/static/icon-192.png','image/png')]:
            response=await self.client.get(path);self.assertEqual(response.status,200);self.assertIn(kind,response.content_type)
            self.assertEqual(response.headers['Cache-Control'],'no-store')
        self.db.execute('DELETE FROM documents WHERE id=?',(self.did,));self.db.commit()
        self.assertIsNone(self.service.primary(self.uid,self.did))
        self.assertEqual(self.db.execute('SELECT count(*) FROM notifications').fetchone()[0],0)
        await self.client.post('/api/logout',json={},headers=HEADERS)
        self.assertEqual((await self.client.get('/api/notifications')).status,401)
        self.assertEqual((await self.client.get('/sw.js')).status,200)
        response=await self.client.get('/?doc=example',allow_redirects=False)
        self.assertIn('next=',response.headers['Location'])

    async def test_disabled_push_still_records_primary_and_sends_email(self):
        self.service.enabled=False
        item=self.engine.save(template());self.choose(item);await self.fire(item)
        self.engine.sender.assert_awaited_once()
        self.assertEqual(self.db.execute('SELECT count(*) FROM notifications').fetchone()[0],1)
        self.assertEqual(self.db.execute('SELECT count(*) FROM push_deliveries').fetchone()[0],0)
        response=await self.client.post('/api/push/test',json={},headers=HEADERS);self.assertEqual(response.status,404)

    async def test_outbox_failure_does_not_block_email(self):
        item=self.engine.save(template());self.choose(item)
        with patch.object(self.service,'_enqueue',side_effect=RuntimeError('simulated')):
            await self.fire(item)
        self.assertEqual(self.engine.get(item['id'])['status'],'sent')
        self.engine.sender.assert_awaited_once()
        self.assertEqual(self.db.execute('SELECT count(*) FROM notifications').fetchone()[0],0)

    async def test_two_devices_only_current_test_and_selective_unsubscribe(self):
        first=await self.register(sub('first'))
        # A second independent cookie jar on the same running server.
        from aiohttp import ClientSession, CookieJar
        from auth_support import encrypt
        async with ClientSession(cookie_jar=CookieJar(unsafe=True)) as phone:
            base=str(self.client.make_url('/')).rstrip('/')
            challenge=await (await phone.get(base+'/api/auth/challenge')).json()
            response=await phone.post(base+'/api/login',json={'username':'admin','encrypted_password':encrypt(challenge,'test-password-123')},headers=HEADERS)
            self.assertEqual(response.status,200)
            await phone.get(base+'/api/push/config')
            response=await phone.post(base+'/api/push/subscriptions',json=sub('second'),headers=HEADERS)
            second=(await response.json())['id']
            await self.service.tick();self.service.sender.assert_called_once()
            self.assertTrue(self.service.sender.call_args.args[0]['endpoint'].endswith('/second'))
            self.assertEqual(self.service.sender.call_args.args[1]['body'],'消息推送启动成功！')
            self.service.sender.reset_mock()
            item=self.engine.save(template());self.choose(item);await self.fire(item)
            await self.service.tick();self.assertEqual(self.service.sender.call_count,2)
            await self.client.delete('/api/push/subscriptions/'+first,json={},headers=HEADERS)
            self.assertEqual(self.db.execute('SELECT id FROM push_subscriptions').fetchall(),[(second,)])

    async def test_full_restart_preserves_pending_outbox_without_replaying_mail(self):
        await self.register();item=self.engine.save(template());self.choose(item);await self.fire(item)
        key=self.service.public_key
        await self.client.close()
        self.app=create_app(self.tmp.name,'test-password-123',False)
        self.client=TestClient(TestServer(self.app));await self.client.start_server()
        service=self.app['notifications'];service.sender=Mock(return_value=201)
        self.assertEqual(service.public_key,key)
        self.assertEqual(service.primary(self.uid,self.did)['trigger_id'],item['id'])
        await service.tick();service.sender.assert_called_once()
        engine=self.app['workspaces'].items[self.did].mailer
        self.assertEqual(engine.get(item['id'])['status'],'sent')

    async def test_migration_keeps_old_choice_and_allows_second_document(self):
        item=self.engine.save(template());self.choose(item)
        with self.db:
            self.db.execute('ALTER TABLE primary_notifications RENAME TO saved_selection')
            self.db.execute('CREATE TABLE primary_notifications (user_id TEXT PRIMARY KEY,document_id TEXT NOT NULL,trigger_id TEXT NOT NULL)')
            self.db.execute('INSERT INTO primary_notifications SELECT * FROM saved_selection')
            self.db.execute('DROP TABLE saved_selection')
        migrated=Notifications(self.app['accounts'])
        self.assertEqual(migrated.primary(self.uid,self.did)['trigger_id'],item['id'])
        doc=self.app['accounts'].add_document(self.uid,'另一文档','https://docs.qq.com/sheet/Other',{})
        runtime=self.app['workspaces'].add(doc)
        other=runtime.mailer.save(template('另一标题'))
        migrated.choose(self.uid,{'document_id':doc['id'],'trigger_id':other['id']})
        migrated.choose(self.uid,{'document_id':doc['id'],'trigger_id':None})
        self.assertEqual(migrated.primary(self.uid,self.did)['trigger_id'],item['id'])

    async def test_latest_cursor_does_not_replay_history(self):
        item=self.engine.save(template());self.choose(item);await self.fire(item)
        result=await (await self.client.get('/api/notifications?after=latest')).json()
        self.assertEqual(result['items'],[]);self.assertGreater(result['next_cursor'],0)
        next_page=await (await self.client.get('/api/notifications?after='+str(result['next_cursor']))).json()
        self.assertEqual(next_page['items'],[])
