"""Local UI test fixture; this file is never copied into the production image."""
import tempfile
from datetime import datetime
from aiohttp import web
from app.main import create_app
from app.variables import LOCAL_TZ

if __name__ == '__main__':
    with tempfile.TemporaryDirectory() as directory:
        app = create_app(directory, 'browser-test-password', start_scheduler=False)
        deliveries = []
        async def setup(app):
            async def fake_sender(**kwargs):
                deliveries.append(kwargs)
            app['mail_engine'].sender = fake_sender
            app['mail_engine'].clock = lambda: datetime(2026, 9, 18, 10, tzinfo=LOCAL_TZ)
        async def tick(request):
            await app['mail_engine'].tick()
            return web.json_response({'ok': True})
        async def sent(request):
            return web.json_response(deliveries)
        app.on_startup.append(setup)
        app.add_routes([web.post('/test/tick',tick), web.get('/test/deliveries',sent)])
        web.run_app(app, host='127.0.0.1', port=18081, access_log=None)
