"""Local UI test fixture; this file is never copied into the production image."""
import tempfile
from datetime import datetime
from aiohttp import web
from support import create_app
from app.tencent import TencentClient, TencentError
from app.variables import LOCAL_TZ

if __name__ == '__main__':
    with tempfile.TemporaryDirectory() as directory:
        app = create_app(directory, 'browser-test-password', start_scheduler=False)
        deliveries = []
        async def fake_sheets(self):
            if 'NOTSHEET' in self.store.settings()['document_url']:
                raise TencentError('不是在线表格')
            return [{'sheetId':'tab1','title':'研发'}]
        TencentClient.sheets = fake_sheets
        async def fake_metadata(self,*args):
            return [{'sheetId':'tab1','title':'研发','rowTotal':100,'columnTotal':100}]
        async def fake_file_id(self,*args):return 'browser-file'
        matrix={(1,2):{'cellValue':{'text':'张三'}},(1,3):{'cellValue':{'text':'李四'}},
                (2,3):{'cellValue':{'text':'已填写'}},(2,1):{'cellValue':{'text':'张三'}},(3,1):{'cellValue':{'text':'李四'}}}
        async def fake_row(self,fid,sheet,row,start,end,headers):return {c:matrix.get((row,c)) for c in range(start,end+1)}
        async def fake_column(self,fid,sheet,col,start,end,headers):return {r:matrix.get((r,col)) for r in range(start,end+1)}
        TencentClient.metadata=fake_metadata
        TencentClient.file_id=fake_file_id
        TencentClient.read_row=fake_row
        TencentClient.read_column=fake_column
        async def setup(app):
            async def fake_sender(**kwargs):
                deliveries.append(kwargs)
            app['mail_engine'].sender = fake_sender
            app['mail_engine'].clock = lambda: datetime(2026, 9, 18, 10, tzinfo=LOCAL_TZ)
        async def tick(request):
            await app['mail_engine'].tick()
            return web.json_response({'ok': True})
        async def check(request):
            await app['workspaces'].items[request.query['doc']].monitor.check()
            return web.json_response({'ok':True})
        async def sent(request):
            return web.json_response(deliveries)
        app.on_startup.append(setup)
        app.add_routes([web.post('/test/check',check),web.post('/test/tick',tick), web.get('/test/deliveries',sent)])
        web.run_app(app, host='127.0.0.1', port=18081, access_log=None)

