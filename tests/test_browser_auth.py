import tempfile
import unittest
from aiohttp.test_utils import TestClient, TestServer
from app.main import create_app
from auth_support import encrypt

HEADERS={'X-Requested-With':'WeeklyReport'}
PASSWORD='remember-test-password'


class BrowserAuthTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        await self.open()

    async def open(self):
        self.app=create_app(self.tmp.name,PASSWORD,start_scheduler=False)
        self.client=TestClient(TestServer(self.app),headers=HEADERS)
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()
        self.tmp.cleanup()

    async def envelope(self):
        challenge=await (await self.client.get('/api/auth/challenge')).json()
        return encrypt(challenge,PASSWORD)

    async def login(self, **options):
        return await self.client.post('/api/login',json=dict(username='admin',encrypted_password=await self.envelope(),**options))

    async def test_encryption_replay_tampering_and_plaintext_rejection(self):
        body=dict(username='admin',encrypted_password=await self.envelope())
        self.assertEqual((await self.client.post('/api/login',json=body)).status,200)
        self.assertEqual((await self.client.post('/api/login',json=body)).status,400)
        self.assertEqual((await self.client.post('/api/login',json=dict(username='admin',password=PASSWORD))).status,400)
        body['encrypted_password']=await self.envelope()
        body['encrypted_password']['ciphertext']='AAAA'
        self.assertEqual((await self.client.post('/api/login',json=body)).status,400)
        body['encrypted_password']=await self.envelope()
        self.app['browser_auth'].challenges[body['encrypted_password']['nonce']]=0
        self.assertEqual((await self.client.post('/api/login',json=body)).status,400)
        self.assertEqual((await self.client.post('/api/admin/users',json=dict(username='alice',password=PASSWORD,role='user'))).status,400)

    async def test_remember_manual_and_logout_disables_auto(self):
        response=await self.login(automatic=True)
        self.assertEqual(response.status,200)
        token=response.cookies['wr_remember'].value
        self.assertTrue(response.cookies['wr_remember']['httponly'])
        self.assertEqual(response.cookies['wr_remember']['samesite'],'Strict')
        row=self.app['accounts'].db.execute('SELECT token FROM remembered_devices').fetchone()
        self.assertNotEqual(row[0],token)
        options=await (await self.client.get('/api/auth/options')).json()
        self.assertEqual(options,dict(username='admin',remember=True,automatic=True))
        await self.client.post('/api/logout',json={})
        options=await (await self.client.get('/api/auth/options')).json()
        self.assertFalse(options['automatic']);self.assertTrue(options['remember'])
        self.assertEqual((await self.client.get('/api/me')).status,401)
        self.assertEqual((await self.client.post('/api/auth/resume',json=dict(username='admin',auto=True,remember=True))).status,401)
        self.assertEqual((await self.client.post('/api/auth/resume',json=dict(username='admin',auto=False,remember=True))).status,200)
        self.client.session.cookie_jar.clear()
        self.assertFalse((await (await self.client.get('/api/auth/options',headers={'Cookie':'wr_remember='+token})).json())['remember'])

    async def test_remember_survives_restart_and_reset_revokes(self):
        response=await self.login(remember=True,automatic=True)
        token=response.cookies['wr_remember'].value
        await self.client.close()
        await self.open()
        response=await self.client.post('/api/auth/resume',json=dict(username='admin',auto=True,remember=True,automatic=True),headers={'Cookie':'wr_remember='+token})
        self.assertEqual(response.status,200)
        uid=self.app['accounts'].user(name='admin')['id']
        response=await self.client.put('/api/admin/users/'+uid,json={'encrypted_password':await self.envelope()})
        self.assertEqual(response.status,200)
        self.assertFalse((await (await self.client.get('/api/auth/options')).json())['remember'])
        self.assertEqual((await self.client.post('/api/auth/resume',json=dict(username='admin',remember=True))).status,401)

    async def test_forget_expiry_and_wrong_username(self):
        await self.login(remember=True)
        self.assertEqual((await self.client.post('/api/auth/resume',json=dict(username='other',remember=True))).status,401)
        await self.client.post('/api/auth/forget',json={})
        self.assertFalse((await (await self.client.get('/api/auth/options')).json())['remember'])
        await self.login(remember=True)
        with self.app['accounts'].db:
            self.app['accounts'].db.execute('UPDATE remembered_devices SET expires=0')
        self.assertEqual((await self.client.post('/api/auth/resume',json=dict(username='admin',remember=True))).status,401)

    async def test_logout_without_live_session_still_disables_auto(self):
        response=await self.login(remember=True,automatic=True)
        token=response.cookies['wr_remember'].value
        self.client.session.cookie_jar.clear()
        response=await self.client.post('/api/logout',json={},headers={'Cookie':'wr_remember='+token})
        self.assertEqual(response.status,200)
        options=await (await self.client.get('/api/auth/options')).json()
        self.assertTrue(options['remember']);self.assertFalse(options['automatic'])

    async def test_normal_login_has_no_persistent_cookie(self):
        response=await self.login()
        self.assertEqual(response.cookies['wr_session']['max-age'],'')
        self.assertFalse((await (await self.client.get('/api/auth/options')).json())['remember'])
