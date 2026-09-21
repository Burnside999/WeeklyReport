"""Legacy single-document fixtures for existing endpoint regressions."""
from pathlib import Path
from aiohttp.test_utils import TestClient as BaseTestClient
from app.core import Store
from app.main import create_app as real_create_app
from app.accounts import Accounts, DocumentStore


def create_app(directory, password, start_scheduler=False):
    root = Store(str(Path(directory)/'weeklyreport.db'))
    if not root.get('accounts_initialized'):
        root.set('settings',root.settings() | {'client_id':'fixture-client','open_id':'fixture-open','access_token':'fixture-token'})
    root.close()
    app = real_create_app(directory,password,start_scheduler)
    did = app['accounts'].documents()[0]['id']
    app['_test_document_id'] = did
    async def aliases(app):
        runtime = app['workspaces'].items[did]
        app['store'],app['client'],app['monitor'],app['mail_engine'] = runtime.store,runtime.client,runtime.monitor,runtime.mailer
    app.on_startup.append(aliases)
    return app


class TestClient(BaseTestClient):
    def __init__(self, server, *args, **kwargs):
        headers = kwargs.pop('headers',{})
        headers = {'X-Document-ID':server.app['_test_document_id']} | headers
        super().__init__(server,*args,headers=headers,**kwargs)


def scoped_store(root):
    accounts = Accounts(root,'unused-password-123')
    return DocumentStore(accounts,accounts.documents()[0])
