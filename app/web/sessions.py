"""Encrypted login, remembered accounts and session lifecycle."""
import asyncio
import secrets
import time
from aiohttp import web
from ..accounts import password_hash, verify_password


def register_sessions(app, sessions, cookie_secure, session_hours):
    accounts = app['accounts']
    browser_auth = app['browser_auth']
    attempts = {}
    login_slots = asyncio.Semaphore(2)
    dummy_hash = password_hash(secrets.token_urlsafe(32))

    async def login(request):
        current = time.time()
        for key in list(attempts):
            if attempts[key][1] < current - 900:
                del attempts[key]
        ip = request.remote or 'unknown'  # Never trust arbitrary forwarded IP headers.
        count, since = attempts.get(ip, (0, current))
        if count >= 10:
            return web.json_response({'error': '尝试次数过多，请 15 分钟后再试'}, status=429)
        # Reserve before reading the body: awaiting JSON must not allow concurrent
        # requests to overwrite the same attempt counter.
        attempts[ip] = (count + 1, since)
        raw = await request.json()
        if not isinstance(raw, dict):
            raise ValueError('密码格式错误')
        if 'password' in raw:
            raise ValueError('不接受明文密码')
        supplied, name = browser_auth.decrypt(raw.get('encrypted_password')), raw.get('username', '')
        if not isinstance(supplied, str) or len(supplied) > 1024 or not isinstance(name,str) or len(name)>64:
            raise ValueError('帐号或密码格式错误')
        if login_slots.locked():
            return web.json_response({'error': '登录请求繁忙，请稍后重试'}, status=429)
        user = accounts.user(name=name)
        async with login_slots:
            valid = await asyncio.to_thread(verify_password,supplied,user['password'] if user else dummy_hash)
        current_user = accounts.user(uid=user['id']) if user else None
        if not valid or not current_user or current_user['version'] != user['version']:
            return web.json_response({'error': '帐号或密码不正确'}, status=401)
        attempts.pop(ip, None)
        remember, automatic = preferences(raw)
        response = start_session(request,user)
        if remember:
            browser_auth.remember(request,response,user,automatic)
        else:
            browser_auth.forget(request,response)
        return response

    def preferences(raw):
        remember, automatic = raw.get('remember',False), raw.get('automatic',False)
        if not isinstance(remember,bool) or not isinstance(automatic,bool):
            raise ValueError('登录选项格式错误')
        return remember or automatic, automatic

    def start_session(request,user):
        current = time.time()
        for key in list(sessions):
            if sessions[key]['expires'] <= current:
                del sessions[key]
        if len(sessions) >= 1000:
            sessions.pop(next(iter(sessions)))
        sessions.pop(request.cookies.get('wr_session', ''), None)
        app['notifications'].unbind_if_other_user(request,user['id'])
        token = secrets.token_urlsafe(32)
        sessions[token] = dict(uid=user['id'],version=user['version'],expires=current + session_hours * 3600)
        response = web.json_response({'ok': True})
        response.set_cookie('wr_session', token, httponly=True, samesite='Strict', secure=cookie_secure, path='/')
        return response

    async def auth_challenge(request):
        return web.json_response(browser_auth.challenge())

    async def auth_options(request):
        saved = browser_auth.remembered(request)
        return web.json_response(dict(username=saved['user']['username'] if saved else '',remember=bool(saved),automatic=bool(saved and saved['automatic'])))

    async def auth_forget(request):
        response = web.json_response({'ok':True})
        browser_auth.forget(request,response)
        return response

    async def auth_resume(request):
        raw = await request.json()
        if not isinstance(raw,dict) or not isinstance(raw.get('auto',False),bool):
            raise ValueError('登录选项格式错误')
        saved = browser_auth.remembered(request)
        if not saved or raw.get('username') != saved['user']['username'] or (raw.get('auto') and not saved['automatic']):
            return web.json_response({'error':'登录记忆已失效，请输入密码'},status=401)
        remember, automatic = preferences(raw)
        response = start_session(request,saved['user'])
        if remember:
            browser_auth.remember(request,response,saved['user'],automatic)
        else:
            browser_auth.forget(request,response)
        return response

    async def logout(request):
        app['notifications'].unbind(request)
        sessions.pop(request.cookies.get('wr_session', ''), None)
        response = web.json_response({'ok': True})
        response.del_cookie('wr_session', path='/')
        saved = browser_auth.remembered(request)
        if saved:
            browser_auth.remember(request,response,saved['user'],False)
        else:
            browser_auth.forget(request,response)
        return response

    app.add_routes([
        web.post('/api/login', login),
        web.post('/api/logout', logout),
        web.get('/api/auth/challenge', auth_challenge),
        web.get('/api/auth/options', auth_options),
        web.post('/api/auth/resume', auth_resume),
        web.post('/api/auth/forget', auth_forget),
    ])
