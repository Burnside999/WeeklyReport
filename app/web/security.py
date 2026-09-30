"""Session authorization, document isolation and response security headers."""
import time
from urllib.parse import quote
from aiohttp import web
from ..mail import DeliveryError
from ..templates import TemplateConflict
from ..tencent import TencentError
from .assets import PUBLIC_ASSET_PATHS


ACCOUNT_API_PATHS = frozenset({
    '/api/login', '/api/logout', '/api/me', '/api/version', '/api/notifications',
})
ACCOUNT_API_PREFIXES = ('/api/admin/', '/api/documents', '/api/auth/', '/api/me/', '/api/push/')


def document_scoped(path):
    return (path.startswith('/api/') and path not in ACCOUNT_API_PATHS
            and not path.startswith(ACCOUNT_API_PREFIXES))


def security_middleware(accounts, sessions):
    @web.middleware
    async def security(request, handler):
        try:
            if request.path.startswith('/api/') and request.method != 'GET':
                if request.headers.get('X-Requested-With') != 'WeeklyReport':
                    raise web.HTTPForbidden(text='请求校验失败')
                if request.content_type != 'application/json':
                    raise web.HTTPUnsupportedMediaType()
            token = request.cookies.get('wr_session', '')
            session = sessions.get(token)
            user = accounts.user(uid=session['uid']) if session and session['expires'] > time.time() else None
            authenticated = bool(user and user['version'] == session['version'])
            if authenticated:
                request['user'], request['session'] = user, session
            public = request.path in PUBLIC_ASSET_PATHS or request.path in (
                '/api/login', '/api/logout', '/api/auth/challenge', '/api/auth/options',
                '/api/auth/resume', '/api/auth/forget')
            if not public and not authenticated:
                if request.path.startswith('/api/'):
                    response = web.json_response({'error': '请先登录'}, status=401)
                else:
                    target = str(request.rel_url) if request.path in ('/', '/help') else '/'
                    raise web.HTTPFound('/login?next=' + quote(target, safe=''))
            else:
                if authenticated:
                    if request.path == '/admin' and user['role'] not in ('admin','superadmin'):
                        raise web.HTTPForbidden(text='无权访问管理界面')
                    scoped = document_scoped(request.path)
                    if scoped:
                        did = request.headers.get('X-Document-ID','')
                        if not did:
                            raise web.HTTPConflict(text='请选择文档管理器')
                        document = accounts.document(did,user['id'])
                        if not document:
                            raise web.HTTPNotFound(text='文档管理器不存在')
                        request['workspace'] = request.app['workspaces'].items[did]
                        request['document'] = document
                if authenticated and request.path.startswith('/api/') and request.path not in ('/api/login','/api/logout') and request.method != 'GET':
                    if request.app['workspaces'].mutations.locked():
                        raise web.HTTPConflict(text='操作正在进行，请稍后重试')
                    async with request.app['workspaces'].mutations:
                        current_user = accounts.user(uid=user['id'])
                        if not current_user or current_user['version'] != session['version']:
                            raise web.HTTPUnauthorized(text='请重新登录')
                        if scoped and not accounts.document(did,user['id']):
                            raise web.HTTPNotFound(text='文档管理器不存在')
                        response = await handler(request)
                else:
                    response = await handler(request)
        except (ValueError, TypeError, KeyError) as exc:
            response = web.json_response({'error': str(exc)[:200]}, status=400)
        except TemplateConflict as exc:
            response = web.json_response({'error': str(exc)} | exc.details, status=409)
        except DeliveryError as exc:
            response = web.json_response({'error': str(exc)}, status=502)
        except TencentError as exc:
            response = web.json_response({'error': str(exc)}, status=502)
        except web.HTTPException as exc:
            response = web.Response(status=exc.status, text=exc.text, headers=exc.headers)
        response.headers.update({'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
            'X-Frame-Options': 'DENY', 'Referrer-Policy': 'no-referrer',
            'Content-Security-Policy': "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"})
        return response

    return security
