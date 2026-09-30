"""Application composition and process entry point."""
import asyncio
import logging
import os
from pathlib import Path

import aiohttp
from aiohttp import web
from .accounts import Accounts
from .auth import BrowserAuth
from .core import Store, integer
from .monitor import Monitor
from .notifications import Notifications, register_notifications
from .web.assets import register_assets
from .web.security import security_middleware
from .web.sessions import register_sessions
from .web.workspace import register_workspace
from .workspaces import Workspaces, register_management


def create_app(data_dir=None, password=None, start_scheduler=True):
    password = password if password is not None else os.environ.get('ADMIN_PASSWORD', '')
    if len(password) < 12 or password == 'change-this-password':
        raise RuntimeError('ADMIN_PASSWORD 必须设置为至少 12 字符的自定义密码')
    store = Store(str(Path(data_dir or os.environ.get('DATA_DIR', './data')) / 'weeklyreport.db'))
    accounts = Accounts(store, password)
    sessions = {}
    cookie_secure = os.environ.get('COOKIE_SECURE', 'false').lower() == 'true'
    session_hours = integer(os.environ.get('SESSION_HOURS', '24'), 1, 168, '会话时长')
    app = web.Application(middlewares=[security_middleware(accounts, sessions)], client_max_size=65536)
    app['store'], app['accounts'] = store, accounts
    app['browser_auth'] = BrowserAuth(accounts, cookie_secure)
    app['notifications'] = Notifications(accounts)

    async def lifecycle(app):
        async with aiohttp.ClientSession() as session:
            hub = app['workspaces'] = Workspaces(accounts,session,Monitor,start_scheduler,app['notifications'])
            for document in accounts.documents():
                hub.add(document)
            push_task = asyncio.create_task(app['notifications'].loop()) if start_scheduler else None
            try:
                yield
            finally:
                await hub.close()
                if push_task:
                    push_task.cancel()
                    await asyncio.gather(push_task,return_exceptions=True)
        store.close()
    app.cleanup_ctx.append(lifecycle)

    register_assets(app)
    register_sessions(app, sessions, cookie_secure, session_hours)
    register_workspace(app)
    register_management(app)
    register_notifications(app)
    return app


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    os.umask(0o077)
    web.run_app(create_app(), host='0.0.0.0', port=int(os.environ.get('PORT', 8080)), access_log=None)
