"""Tencent Docs official Open API V3; no undocumented browser endpoints."""
import asyncio
import time
import ipaddress
from urllib.parse import urlsplit
from .merge_layout import parse_layout
from .core import now
from urllib.parse import quote
import aiohttp
from .core import doc_id, letters


class TencentError(Exception):
    pass


class TencentClient:
    def __init__(self, store, session):
        self.store, self.session = store, session
        self.lock = asyncio.Lock()
        self.last_request = 0.0

    async def request(self, path, params=None, headers=None, method="GET") :
        delay = .65 - (time.monotonic() - self.last_request)
        if delay > 0:
            await asyncio.sleep(delay)
        self.last_request = time.monotonic()
        timeout = self.store.settings()['timeout_seconds']
        try:
            async with self.session.request(method, 'https://docs.qq.com' + path, params=params,
                                        headers=headers, allow_redirects=False,
                                        timeout=aiohttp.ClientTimeout(total=timeout)) as response:
                if response.status != 200:
                    raise TencentError(f'腾讯 API HTTP {response.status}；请检查授权、权限或稍后重试')
                body = await response.json(content_type=None)
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
            raise TencentError('腾讯 API 网络超时或返回内容异常，请稍后重试') from None
        if not isinstance(body, dict):
            raise TencentError('腾讯 API 响应格式异常')
        code = body.get('ret', body.get('code', 0))
        if code not in (0, '0', None):
            raise TencentError(f'腾讯 API 错误码 {str(code)[:20]}；请检查令牌、文档权限与 API 授权范围')
        if 'error' in body:
            raise TencentError('腾讯授权已失效，请更新授权令牌')
        data = body.get('data', body)
        if not isinstance(data, dict):
            raise TencentError('腾讯 API 数据格式异常')
        return data

    async def headers(self):
        s = self.store.settings()
        if not s['client_id'] or not s['open_id']:
            raise TencentError('请先在设置 → 高级设置中配置腾讯文档 Client ID 和 Open ID')
        if not s['access_token']:
            raise TencentError('请在设置中填写 Access Token')
        return {'Access-Token': s['access_token'], 'Client-Id': s['client_id'], 'Open-Id': s['open_id']}

    async def file_id(self, headers):
        s = self.store.settings()
        data = await self.request('/openapi/drive/v2/util/converter',
                                  dict(type=2, value=doc_id(s['document_url'])), headers)
        if not isinstance(data.get('fileID'), str) or not data['fileID']:
            raise TencentError('无法转换文档 ID；检查 drive 元数据只读权限')
        return data['fileID']

    async def sheets(self):
        async with self.lock:
            headers = await self.headers()
            fid = await self.file_id(headers)
            return await self.metadata(fid, headers)

    async def metadata(self, fid, headers):
        data = await self.request('/openapi/spreadsheet/v3/files/' + quote(fid, safe=''), None, headers)
        props = data.get('properties')
        if not isinstance(props, list) or any(not isinstance(x, dict) or not x.get('sheetId') for x in props):
            raise TencentError('工作表元数据格式异常，未生成检查结果')
        return props

    async def read_column(self, fid, sheet, col, start, end, headers):
        cells = {}
        for first in range(start, end + 1, 1000):
            last = min(first + 999, end)
            area = f'{letters(col)}{first}:{letters(col)}{last}'
            path = '/openapi/spreadsheet/v3/files/' + '/'.join(quote(x, safe='') for x in (fid, sheet, area))
            data = await self.request(path, None, headers)
            grid = data.get('gridData')
            if not isinstance(grid, dict) or not isinstance(grid.get('rows', []), list):
                raise TencentError('单元格数据格式异常，未生成检查结果')
            sr, sc = grid.get('startRow', first - 1), grid.get('startColumn', col - 1)
            if not isinstance(sr, int) or not isinstance(sc, int) or sc != col - 1 or not first - 1 <= sr <= last:
                raise TencentError('单元格范围偏移异常，未生成检查结果')
            for offset, row in enumerate(grid.get('rows', [])):
                if not isinstance(row, dict) or not isinstance(row.get('values', []), list):
                    raise TencentError('单元格行格式异常，未生成检查结果')
                index = sr + offset + 1
                if not first <= index <= last:
                    raise TencentError('返回行超出请求范围')
                values = row.get('values', [])
                cells[index] = values[0] if values else None
        return cells

    async def roster(self, source):
        async with self.lock:
            headers = await self.headers()
            fid = await self.file_id(headers)
            sheets = {s['sheetId']:s for s in await self.metadata(fid, headers)}
            meta = sheets.get(source['sheet_id'])
            if not meta:
                raise TencentError('名单工作表不存在，请重新选择')
            cells = await self.read_column(fid, source['sheet_id'], source['column'], source['start_row'], source['end_row'], headers)
            from .core import written_text
            names = [written_text(cells.get(r)) for r in range(source['start_row'],source['end_row']+1)]
            return names, source | {'sheet_name':meta['title']}

    async def layout(self, fid, headers, metadata, force=False):
        stamp = time.time()
        signature = sorted((m['sheetId'],m['title']) for m in metadata.values())
        signature = [list(x) for x in signature]
        cached = self.store.get('merge_layout', {})
        if not force and cached.get('file_id') == fid and cached.get('signature') == signature and stamp-cached.get('timestamp',0)<21600:
            return cached
        # Tencent allows only 9 export operations/user/day. Reserve one for other clients.
        attempts = [x for x in self.store.get('export_attempts',[]) if x > stamp-86400]
        if len(attempts)>=8:
            raise TencentError('合并结构导出额度已达本应用 24 小时上限（8 次），请稍后重试')
        if not force and attempts and stamp-attempts[-1]<3600:
            raise TencentError('合并结构上次同步失败，自动重试间隔为 1 小时；请检查导出权限或手动刷新')
        self.store.set('export_attempts',attempts+[stamp])
        base = '/openapi/drive/v2/files/'+quote(fid,safe='')
        data = await self.request(base+'/async-export', headers=headers | {'Content-Type':'application/x-www-form-urlencoded'}, method='POST')
        operation = data.get('operationID')
        if not operation:
            raise TencentError('官方导出未返回任务 ID，请检查 scope.drive.exportable 权限')
        deadline = time.monotonic()+150
        while time.monotonic()<deadline:
            data = await self.request(base+'/export-progress',{'operationID':operation},headers)
            if data.get('progress') == 100 and data.get('url'):
                break
            await asyncio.sleep(2)
        else:
            raise TencentError('合并结构导出超时，请稍后手动刷新')
        try:
            payload = await download_export(data['url'], self.store.settings()['timeout_seconds'])
            by_title = await asyncio.to_thread(parse_layout,payload)
        except (ValueError, aiohttp.ClientError, asyncio.TimeoutError):
            raise TencentError('无法下载或解析 XLSX 合并结构，请检查导出权限并重试') from None
        sheets = {}
        for sid, meta in metadata.items():
            if meta['title'] not in by_title:
                raise TencentError('导出工作表与当前 Sheet 列表不一致，请重新同步')
            sheets[sid] = by_title[meta['title']]
        cached = dict(file_id=fid, signature=signature, timestamp=time.time(), updated_at=now(), sheets=sheets)
        self.store.set('merge_layout',cached)
        return cached


class PublicResolver(aiohttp.resolver.DefaultResolver):
    async def resolve(self, host, port=0, family=0):
        results = await super().resolve(host, port, family)
        if any(not ipaddress.ip_address(r['host']).is_global for r in results):
            raise ValueError('下载地址必须使用公网地址')
        return results


async def download_export(url, timeout):
    # Dedicated session: never forward Tencent OAuth headers/cookies to download storage.
    async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(resolver=PublicResolver()),
                                     cookie_jar=aiohttp.DummyCookieJar(),
                                     timeout=aiohttp.ClientTimeout(total=timeout)) as session:
        from urllib.parse import urljoin
        for _ in range(4):
            u = urlsplit(url)
            if u.scheme!='https' or not u.hostname or u.username or u.password or u.port not in (None,443):
                raise ValueError('下载链接不安全')
            try:
                ip = ipaddress.ip_address(u.hostname)
            except ValueError:
                ip = None
            if ip is not None and not ip.is_global:
                raise ValueError('下载地址必须使用公网地址')
            async with session.get(url,allow_redirects=False) as response:
                if response.status in (301,302,303,307,308):
                    url=urljoin(url,response.headers.get('Location',''))
                    continue
                if response.status!=200:
                    raise ValueError('导出下载失败')
                content=bytearray()
                async for chunk in response.content.iter_chunked(65536):
                    content.extend(chunk)
                    if len(content)>20*1024*1024:
                        raise ValueError('导出超过 20 MiB')
                return bytes(content)
        raise ValueError('导出重定向过多')

