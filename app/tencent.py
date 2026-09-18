"""Tencent Docs official Open API V3; no undocumented browser endpoints."""
import asyncio
import time
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

    async def request(self, path, params=None, headers=None):
        delay = .65 - (time.monotonic() - self.last_request)
        if delay > 0:
            await asyncio.sleep(delay)
        self.last_request = time.monotonic()
        timeout = self.store.settings()['timeout_seconds']
        try:
            async with self.session.get('https://docs.qq.com' + path, params=params,
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
            raise TencentError('请先在管理 → 高级设置中配置腾讯文档 Client ID 和 Open ID')
        if s['refresh_token'] and s['client_secret'] and (not s['access_token'] or s['token_expires_at'] < time.time() + 120):
            data = await self.request('/oauth/v2/token', dict(client_id=s['client_id'],
                client_secret=s['client_secret'], grant_type='refresh_token', refresh_token=s['refresh_token']))
            if not data.get('access_token'):
                raise TencentError('令牌续期失败，请重新授权并更新 Refresh Token')
            s.update(access_token=data['access_token'],
                     token_expires_at=time.time() + int(data.get('expires_in', 3600)))
            if data.get('refresh_token'):
                s['refresh_token'] = data['refresh_token']
            if data.get('user_id'):
                s['open_id'] = data['user_id']
            self.store.set('settings', s)
        if not s['access_token']:
            raise TencentError('请填写 Access Token，或配置 Refresh Token 与 Client Secret 自动续期')
        return {'Access-Token': s['access_token'], 'Client-Id': s['client_id'], 'Open-Id': s['open_id']}

    async def file_id(self, headers):
        s = self.store.settings()
        if s['file_id']:
            return s['file_id']
        data = await self.request('/openapi/drive/v2/util/converter',
                                  dict(type=2, value=doc_id(s['document_url'])), headers)
        if not isinstance(data.get('fileID'), str) or not data['fileID']:
            raise TencentError('无法转换文档 ID；检查 drive 元数据只读权限或手动填写 File ID')
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
