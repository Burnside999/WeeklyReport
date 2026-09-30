import json
import tempfile
import unittest
from pathlib import Path
from aiohttp.test_utils import TestClient, TestServer
from app.main import create_app
from app.release import APPLICATION, MANIFEST, load_manifest
from app.web.assets import PUBLIC_FILES, PRIVATE_FILES, ROOT


class ReleaseTests(unittest.TestCase):
    def test_manifest_matches_installer_metadata(self):
        root = Path(__file__).resolve().parents[1]
        version = MANIFEST['clients']['windows']['version']
        for name in ('package.json', 'package-lock.json'):
            data = json.loads((root / 'desktop' / name).read_text())
            self.assertEqual(data['version'], version)
            if name == 'package-lock.json':
                self.assertEqual(data['packages']['']['version'], version)

    def test_manifest_rejects_invalid_version(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'release.json'
            data = json.loads(json.dumps(MANIFEST))
            data['application']['version'] = 'latest<script>'
            path.write_text(json.dumps(data))
            with self.assertRaises(ValueError):
                load_manifest(path)

    def test_explicit_asset_registry_has_no_missing_or_overlapping_files(self):
        self.assertFalse(PUBLIC_FILES & PRIVATE_FILES)
        for name in PUBLIC_FILES | PRIVATE_FILES:
            self.assertTrue((ROOT / name).is_file(), name)


class PublicVersionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.client = TestClient(TestServer(create_app(self.directory.name, 'release-test-password', False)))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()
        self.directory.cleanup()

    async def test_version_needs_no_account_or_document_and_contains_no_config(self):
        response = await self.client.get('/api/version')
        self.assertEqual(response.status, 200)
        self.assertEqual(await response.json(), {**APPLICATION, 'client_protocol':1})
        self.assertEqual(response.headers['Cache-Control'], 'no-store')
        self.assertEqual((await self.client.get('/api/settings')).status, 401)

    async def test_login_and_offline_have_rendered_version_and_public_script(self):
        for path in ('/login', '/offline'):
            response = await self.client.get(path)
            html = await response.text()
            self.assertIn('weeklyreport v' + APPLICATION['version'], html)
            self.assertNotIn('{{APP_VERSION}}', html)
        worker = await (await self.client.get('/sw.js')).text()
        self.assertIn('weeklyreport-public-' + APPLICATION['version'], worker)
        self.assertNotIn('{{APP_VERSION}}', worker)
        self.assertEqual((await self.client.get('/static/version.js')).status, 200)
        self.assertNotEqual((await self.client.get('/static/release.json', allow_redirects=False)).status, 200)
        self.assertNotEqual((await self.client.get('/static/tour-steps.js', allow_redirects=False)).status, 200)
