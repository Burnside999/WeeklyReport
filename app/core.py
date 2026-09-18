import json
import os
import re
import sqlite3
from datetime import datetime, timezone
from urllib.parse import urlsplit

DEFAULT_URL = 'https://docs.qq.com/sheet/DY3hoUWtYVGJOR1lz'
SECRETS = ('access_token', 'refresh_token', 'client_secret')
DEFAULTS = dict(document_url=DEFAULT_URL, file_id='', interval_seconds=300,
                timeout_seconds=20, client_id='', open_id='', access_token='',
                refresh_token='', client_secret='', token_expires_at=0)


def now():
    return datetime.now(timezone.utc).isoformat()


def column(value):
    value = str(value).strip().upper()
    if re.fullmatch(r'[A-Z]{1,3}', value):
        number = 0
        for c in value:
            number = number * 26 + ord(c) - 64
    elif value.isascii() and value.isdigit():
        number = int(value)
    else:
        raise ValueError('列必须是 1 开始的数字或 A、B、AA 等字母')
    if not 1 <= number <= 16384:
        raise ValueError('列号范围为 1–16384')
    return number


def letters(n):
    result = ''
    while n:
        n, c = divmod(n - 1, 26)
        result = chr(65 + c) + result
    return result


def doc_id(url):
    u = urlsplit(url)
    if u.scheme != 'https' or u.netloc != 'docs.qq.com' or not re.fullmatch(r'/sheet/[A-Za-z0-9_-]+/?', u.path):
        raise ValueError('请填写 https://docs.qq.com/sheet/ 开头的腾讯在线表格地址')
    return u.path.rstrip('/').split('/')[-1]


def integer(value, low, high, label):
    if isinstance(value, bool) or not re.fullmatch(r'\d+', str(value)):
        raise ValueError(f'{label}必须为整数')
    n = int(value)
    if not low <= n <= high:
        raise ValueError(f'{label}范围为 {low}–{high}')
    return n


def validate_rule(raw):
    if not isinstance(raw, dict):
        raise ValueError('规则格式错误')
    name = str(raw.get('name', '')).strip()
    sheet = str(raw.get('sheet_id', '')).strip()
    if not name or len(name) > 80:
        raise ValueError('请填写 1–80 字的监听条目名称')
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', sheet):
        raise ValueError('请选择工作表或填写正确的 Sheet ID')
    start = integer(raw.get('start_row', 2), 1, 100000, '起始行')
    end = integer(raw.get('end_row', 1000), start, 100000, '结束行')
    if end - start + 1 > 10000:
        raise ValueError('单条规则最多监听 10000 行，请拆分规则')
    enabled = raw.get('enabled', True)
    if type(enabled) is not bool:
        raise ValueError('启用状态必须为布尔值')
    return dict(name=name, sheet_id=sheet, sheet_name=str(raw.get('sheet_name', sheet))[:100],
                owner_column=column(raw.get('owner_column', 'A')),
                target_column=column(raw.get('target_column', 'B')),
                start_row=start, end_row=end, enabled=enabled)


def cell_text(cell):
    if cell is None:
        return ''
    if not isinstance(cell, dict):
        return str(cell).strip()
    value = cell.get('cellValue')
    if value is None:
        return ''
    if not isinstance(value, dict):
        raise ValueError('腾讯 API 返回了无法识别的单元格格式')
    if 'text' in value:
        return str(value['text'] if value['text'] is not None else '').strip()
    if not value:
        return ''
    # Numbers (including zero), booleans, images and locations count as filled.
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def missing_rows(rule, owners, targets):
    result = []
    for row in range(rule['start_row'], rule['end_row'] + 1):
        owner = cell_text(owners.get(row))
        if owner and not cell_text(targets.get(row)):
            result.append(dict(person=owner, row=row, sheet_id=rule['sheet_id'],
                               sheet=rule['sheet_name'], item=rule['name'],
                               column=letters(rule['target_column']), rule_id=rule['id']))
    return result


class Store:
    def __init__(self, path):
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT NOT NULL)')
        self.db.commit()
        os.chmod(path, 0o600)

    def get(self, key, default=None):
        row = self.db.execute('SELECT value FROM state WHERE key=?', (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set(self, key, value):
        with self.db:
            self.db.execute('INSERT INTO state VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                            (key, json.dumps(value, ensure_ascii=False)))

    def settings(self):
        return DEFAULTS | self.get('settings', {})

    def close(self):
        self.db.close()
