import asyncio
import tempfile
import unittest
from unittest.mock import AsyncMock, patch
from aiohttp.test_utils import TestClient, TestServer

from app.accounts import Accounts, DocumentStore, verify_password
from app.core import Store
from app.main import create_app
from app.tencent import TencentClient, TencentError

PASSWORD = 'account-test-password'
HEADERS = {'X-Requested-With':'WeeklyReport'}
CREDS = dict(client_id='client',open_id='openid',access_token='token')


class MigrationTests(unittest.TestCase):
    def test_atomic_idempotent_legacy_migration_preserves_all_document_data(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Store(directory+'/db')
            root.set('settings',root.settings() | CREDS | {'smtp_password':'smtp-secret','refresh_token':'obsolete'})
            for key,value in {'rules':[{'id':'rule'}],'roster':{'names':['张三(备注)'],'excluded':['李四']},
                              'mail_templates':[{'id':'mail'}],'snapshot':{'records':[{'person':'张三'}]},
                              'merge_layout':{'sheets':{}},'listener_sequence':12,'export_attempts':[100]}.items():
                root.set(key,value)
            accounts=Accounts(root,PASSWORD)
            admin=accounts.user(name='admin');document=accounts.documents(admin['id'])[0]
            scoped=DocumentStore(accounts,document)
            self.assertEqual(admin['role'],'superadmin')
            self.assertTrue(verify_password(PASSWORD,admin['password']))
            self.assertNotIn(PASSWORD,admin['password'])
            self.assertEqual(document['name'],'文档1')
            self.assertEqual(scoped.get('listener_sequence'),12)
            self.assertEqual(scoped.get('mail_templates'),[{'id':'mail'}])
            self.assertEqual(scoped.get('roster')['names'],['张三(备注)'])
            self.assertEqual(scoped.get('export_attempts'),[100])
            self.assertEqual(scoped.settings()['smtp_password'],'smtp-secret')
            self.assertEqual(scoped.settings()['access_token'],'token')
            self.assertNotIn('refresh_token',scoped.settings())
            self.assertIsNone(root.get('settings'))
            Accounts(root,'a-different-password')
            self.assertEqual(accounts.documents(),[document])
            self.assertTrue(verify_password(PASSWORD,accounts.user(name='admin')['password']))
            root.close()


class MultiUserTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.app=create_app(self.tmp.name,PASSWORD,start_scheduler=False)
        self.server=TestServer(self.app)
        self.clients=[]
        self.client=await self.login('admin')
        self.metadata=patch.object(TencentClient,'sheets',AsyncMock(return_value=[{'sheetId':'s','title':'Sheet'}]))
        self.sheets=self.metadata.start()

    async def asyncTearDown(self):
        self.metadata.stop()
        for client in self.clients:
            await client.close()
        self.tmp.cleanup()

    async def login(self,name,password=PASSWORD):
        client=TestClient(self.server);await client.start_server();self.clients.append(client)
        response=await client.post('/api/login',json=dict(username=name,password=password),headers=HEADERS)
        self.assertEqual(response.status,200,await response.text())
        return client

    async def user(self,name,role='user'):
        response=await self.client.post('/api/admin/users',json=dict(username=name,password=PASSWORD,role=role),headers=HEADERS)
        self.assertEqual(response.status,200,await response.text())
        return await response.json()

    async def document(self,client=None,name='文档1',**extra):
        response=await (client or self.client).post('/api/documents',json=dict(name=name,url='https://docs.qq.com/sheet/DTEST',**CREDS)|extra,headers=HEADERS)
        self.assertEqual(response.status,201,await response.text())
        return await response.json()

    def headers(self,d):
        return HEADERS | {'X-Document-ID':d['id']}

    async def test_fresh_user_empty_and_explicit_document_scope_required(self):
        me=await (await self.client.get('/api/me')).json()
        self.assertEqual(me['documents'],[])
        self.assertEqual(me['next_name'],'文档1')
        self.assertFalse(me['credentials_configured'])
        self.assertEqual((await self.client.get('/api/settings')).status,409)
        await self.user('alice');alice=await self.login('alice')
        self.assertEqual((await (await alice.get('/api/me')).json())['documents'],[])
        self.assertEqual((await alice.get('/api/admin/users')).status,403)
        self.assertEqual((await alice.get('/admin')).status,403)
        self.assertEqual((await alice.get('/api/admin/smtp')).status,403)
        no_creds=await alice.post('/api/documents',json={'name':'文档1','url':'https://docs.qq.com/sheet/DTEST'},headers=HEADERS)
        self.assertEqual(no_creds.status,400)

    async def test_document_validation_unique_names_and_no_partial_writes(self):
        self.sheets.side_effect=TencentError('not spreadsheet')
        response=await self.client.post('/api/documents',json=dict(name='bad',url='https://docs.qq.com/sheet/FAKE',**CREDS),headers=HEADERS)
        self.assertEqual(response.status,502)
        self.assertEqual(self.app['accounts'].documents(),[])
        self.assertFalse((await (await self.client.get('/api/me')).json())['credentials_configured'])
        self.sheets.side_effect=None
        for url in ['https://docs.qq.com/doc/NOTSHEET','https://evil.example/sheet/FAKE']:
            response=await self.client.post('/api/documents',json=dict(name='bad',url=url,**CREDS),headers=HEADERS)
            self.assertEqual(response.status,400)
        first=await self.document(name='Report')
        response=await self.client.post('/api/documents',json=dict(name='report',url=first['url'],**CREDS),headers=HEADERS)
        self.assertEqual(response.status,400)
        second=await self.document(name='Report2')
        response=await self.client.put('/api/documents/'+second['id'],json={'name':'REPORT'},headers=HEADERS)
        self.assertEqual(response.status,400)
        response=await self.client.put('/api/settings',json={'document_url':'https://docs.qq.com/sheet/OTHER'},headers=self.headers(first))
        self.assertEqual(response.status,400)
        self.assertEqual(self.app['accounts'].document(first['id'])['url'],first['url'])

    async def test_scope_isolation_credentials_inheritance_and_global_smtp(self):
        first=await self.document(name='文档1');second=await self.document(name='文档2')
        await self.user('alice');alice=await self.login('alice');third=await self.document(alice)
        runtime=self.app['workspaces'].items[first['id']]
        runtime.store.set('rules',[{'id':'private-rule'}])
        runtime.store.set('mail_templates',[{'id':'private-mail'}])
        runtime.store.set('roster',{'names':['private-person']})
        for d in [second,third]:
            scoped=self.app['workspaces'].items[d['id']].store
            self.assertEqual(scoped.get('rules',[]),[])
            self.assertEqual(scoped.get('mail_templates',[]),[])
            self.assertIsNone(scoped.get('roster'))
        for path in ['settings','rules','variables','templates','status','roster']:
            response=await alice.get('/api/'+path,headers=self.headers(first))
            self.assertEqual(response.status,404,path)
            self.assertNotIn('private',await response.text())
        self.assertEqual((await alice.post('/api/templates/private-mail/send',json={},headers=self.headers(first))).status,404)
        self.assertEqual((await alice.delete('/api/documents/'+first['id'],headers=HEADERS,json={})).status,404)
        response=await self.client.put('/api/settings',json={'access_token':'new-token'},headers=self.headers(first))
        self.assertEqual(response.status,200)
        for d in [first,second]:
            self.assertEqual(self.app['workspaces'].items[d['id']].store.settings()['access_token'],'new-token')
        self.assertEqual(self.app['workspaces'].items[third['id']].store.settings()['access_token'],'token')
        self.assertEqual((await alice.put('/api/settings',json={'smtp_password':'evil'},headers=self.headers(third))).status,403)
        self.assertEqual((await self.client.put('/api/admin/smtp',json={'smtp_password':'shared-smtp'},headers=HEADERS)).status,200)
        for d in [first,second,third]:
            self.assertEqual(self.app['workspaces'].items[d['id']].store.settings()['smtp_password'],'shared-smtp')
        settings=await (await alice.get('/api/settings',headers=self.headers(third))).json()
        self.assertFalse(any(k.startswith('smtp_') for k in settings))
        self.assertNotIn('shared-smtp',str(await (await alice.get('/api/me')).json()))

    async def test_roles_password_reset_revocation_and_last_superadmin(self):
        manager=await self.user('manager','admin');normal=await self.user('alice')
        mgr=await self.login('manager');alice=await self.login('alice')
        admin=(await (await self.client.get('/api/me')).json())['user']
        for uid,body in [(normal['id'],{'role':'admin'}),(admin['id'],{'password':'reset-password-123'})]:
            self.assertEqual((await mgr.put('/api/admin/users/'+uid,json=body,headers=HEADERS)).status,403)
        self.assertEqual((await mgr.post('/api/admin/users',json=dict(username='promoted',password=PASSWORD,role='superadmin'),headers=HEADERS)).status,403)
        self.assertEqual((await mgr.post('/api/admin/users',json=dict(username='created',password=PASSWORD,role='user'),headers=HEADERS)).status,200)
        self.assertEqual((await mgr.put('/api/admin/smtp',json={'smtp_sender_name':'Admin'},headers=HEADERS)).status,200)
        response=await self.client.put('/api/admin/users/'+admin['id'],json={'role':'user'},headers=HEADERS)
        self.assertEqual(response.status,400)
        self.assertEqual((await mgr.put('/api/admin/users/'+normal['id'],json={'password':'new-password-123'},headers=HEADERS)).status,200)
        self.assertEqual((await alice.get('/api/me')).status,401)
        await self.login('alice','new-password-123')
        self.assertEqual((await self.client.put('/api/admin/users/'+manager['id'],json={'role':'user'},headers=HEADERS)).status,200)
        self.assertEqual((await mgr.get('/api/admin/smtp')).status,401)
        demoted=await self.login('manager')
        self.assertEqual((await demoted.get('/api/admin/users')).status,403)

    async def test_document_delete_cascades_but_preserves_other_document(self):
        first=await self.document();second=await self.document(name='文档2')
        for d in [first,second]:
            self.app['workspaces'].items[d['id']].store.set('mail_templates',[{'id':d['id']}])
        response=await self.client.delete('/api/documents/'+first['id'],json={},headers=HEADERS)
        self.assertEqual(response.status,200)
        self.assertNotIn(first['id'],self.app['workspaces'].items)
        self.assertEqual(self.app['accounts'].db.execute('SELECT COUNT(*) FROM document_state WHERE document_id=?',(first['id'],)).fetchone()[0],0)
        self.assertEqual(self.app['workspaces'].items[second['id']].store.get('mail_templates'),[{'id':second['id']}])
        self.assertEqual((await self.client.get('/api/status',headers=self.headers(first))).status,404)

    async def test_username_password_auth_no_legacy_bypass(self):
        client=TestClient(self.server);await client.start_server();self.clients.append(client)
        for raw in [{'password':PASSWORD},{'username':"admin' OR 1=1--",'password':PASSWORD},
                    {'username':'admin','password':PASSWORD+'\x00'}]:
            self.assertEqual((await client.post('/api/login',json=raw,headers=HEADERS)).status,401)
        self.assertEqual((await client.get('/api/me')).status,401)

    async def test_independent_mail_contexts_and_tasks(self):
        first=await self.document();second=await self.document(name='文档2')
        r1=self.app['workspaces'].items[first['id']];r2=self.app['workspaces'].items[second['id']]
        self.assertIsNot(r1.monitor,r2.monitor);self.assertIsNot(r1.mailer,r2.mailer)
        r1.mailer.sender=AsyncMock();r2.mailer.sender=AsyncMock()
        template=dict(recipients=['a@example.com'],subject='{{global.url}}',body='{{global.listencount}}',mode='manual')
        mail=r1.mailer.save(template)
        response=await self.client.post('/api/templates/'+mail['id']+'/send',json={'revision':mail['revision']},headers=self.headers(second))
        self.assertEqual(response.status,400)
        r1.mailer.sender.assert_not_awaited();r2.mailer.sender.assert_not_awaited()
