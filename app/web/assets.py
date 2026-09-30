"""Explicit public/private asset registry and versioned HTML rendering."""
from html import escape
from pathlib import Path
from aiohttp import web
from ..release import APPLICATION, MANIFEST

ROOT = Path(__file__).resolve().parents[1] / 'static'
PUBLIC_FILES = {
    'style.css', 'version.js', 'login.js', 'crypto.js', 'pwa-register.js',
    'icon-192.png', 'icon-512.png', 'apple-touch-icon.png',
}
PRIVATE_FILES = {
    'app.js', 'mail.js', 'workspace.js', 'admin.js', 'time.js', 'help.js',
    'tour.js', 'tour-steps.js', 'tips.js', 'tooltip.js', 'help.css', 'tour.css',
    'apple-safari.png', 'apple-share.png', 'pwa.js',
}
PAGE_PATHS = ('/', '/admin', '/mail', '/manage', '/settings', '/variables', '/help', '/login')
PWA_FILES = {
    '/sw.js': ('sw.js', 'application/javascript'),
    '/manifest.webmanifest': ('manifest.webmanifest', 'application/manifest+json'),
}
PUBLIC_ASSET_PATHS = {
    '/login', '/offline', '/healthz', '/api/version', *PWA_FILES,
    *('/static/' + name for name in PUBLIC_FILES),
}
CONTENT_TYPES = {'.png': 'image/png', '.css': 'text/css', '.js': 'application/javascript'}


def render_page(name):
    text = (ROOT / name).read_text(encoding='utf-8')
    if name == 'index.html':
        text = text.replace('<!-- USER_HELP -->', (ROOT / 'help.html').read_text(encoding='utf-8'))
    text = text.replace('{{APP_VERSION}}', escape(APPLICATION['version'], quote=True))
    return text


def register_assets(app):
    async def page(request):
        name = {'/login': 'login.html', '/offline': 'offline.html'}.get(request.path, 'index.html')
        return web.Response(text=render_page(name), content_type='text/html')

    async def static(request):
        name = request.match_info['name']
        if name not in PUBLIC_FILES | PRIVATE_FILES:
            raise web.HTTPNotFound()
        return web.Response(body=(ROOT / name).read_bytes(), content_type=CONTENT_TYPES[Path(name).suffix])

    async def pwa_asset(request):
        name, content_type = PWA_FILES[request.path]
        text = (ROOT / name).read_text(encoding='utf-8').replace('{{APP_VERSION}}', APPLICATION['version'])
        return web.Response(text=text, content_type=content_type)

    async def version(request):
        # Native client versions must come from the installed shell, never this endpoint.
        return web.json_response({**APPLICATION, 'client_protocol': MANIFEST['client_protocol']})

    async def health(request):
        return web.json_response({'ok': True})

    app.add_routes([
        *(web.get(path, page) for path in (*PAGE_PATHS, '/offline')),
        *(web.get(path, pwa_asset) for path in PWA_FILES),
        web.get('/static/{name}', static), web.get('/api/version', version),
        web.get('/healthz', health),
    ])
