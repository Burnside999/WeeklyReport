import json
import os
import re
import sqlite3
from datetime import datetime, timezone
from urllib.parse import urlsplit

DEFAULT_URL = 'https://docs.qq.com/sheet/DY3hoUWtYVGJOR1lz'
DEFAULTS = dict(document_url=DEFAULT_URL, interval_seconds=300, auto_query_enabled=True,
                timeout_seconds=20, client_id='', open_id='', access_token='',
                smtp_host='', smtp_port=465, smtp_security='ssl', smtp_sender='',
                smtp_password='', smtp_sender_name='')


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
    cols = raw.get('target_columns', [raw.get('target_column', 'B')])
    if isinstance(cols, str):
        cols = re.split(r'[,，、\s]+', cols.strip())
    if not isinstance(cols, list) or not 1 <= len(cols) <= 20:
        raise ValueError('请选择 1–20 个需要检查的列')
    cols = sorted(set(column(c) for c in cols))
    return dict(name=name, sheet_id=sheet, sheet_name=str(raw.get('sheet_name', sheet))[:100],
                owner_column=column(raw.get('owner_column', 'A')),
                target_column=cols[0], target_columns=cols,
                start_row=start, end_row=end, enabled=enabled)


def variable_name(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9]{0,63}', value):
        raise ValueError('变量名须以字母开头，仅含英文字母和数字，长度 1–64')
    if value.lower() == 'global':
        raise ValueError('global 是保留变量名')
    return value


def allocate_variable(store, items):
    used = {r.get('variable_name', '').lower() for r in items}
    number = store.get('listener_sequence', 0) + 1
    while f'listener{number}'.lower() in used:
        number += 1
    store.set('listener_sequence', number)
    return f'listener{number}'


def migrate_variables(store):
    items = store.get('rules', [])
    changed = False
    for rule in items:
        if not rule.get('variable_name'):
            rule['variable_name'] = allocate_variable(store, items)
            changed = True
    if changed:
        store.set('rules', items)


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


def written_text(cell):
    """Only actual text (including a hyperlink's label) satisfies the new rule."""
    if not isinstance(cell, dict):
        return cell.strip() if isinstance(cell, str) else ''
    value = cell.get('cellValue') or {}
    if not isinstance(value, dict):
        raise ValueError('单元格格式异常')
    text = value.get('text', '')
    if not text and isinstance(value.get('link'), dict):
        text = value['link'].get('text', '')
    return text.strip() if isinstance(text, str) else ''


def target_columns(rule):
    return rule.get('target_columns') or [rule['target_column']]


def validate_source(raw):
    r = validate_rule(dict(name='同事名单', sheet_id=raw.get('sheet_id', ''),
        sheet_name=raw.get('sheet_name', ''), owner_column=raw.get('column', 'A'),
        start_row=raw.get('start_row', 1), end_row=raw.get('end_row', 100)))
    return {k: r[k] for k in ('sheet_id', 'sheet_name', 'start_row', 'end_row')} | {'column':r['owner_column']}


def refresh_roster(source, names, previous=None):
    names = list(dict.fromkeys(n.strip() for n in names if n.strip()))
    if not names:
        raise ValueError('名单范围没有读取到姓名，请检查 Sheet、列和起止行')
    # Preserve exclusions across refreshes and reappearance; new names are selected.
    excluded = (previous or {}).get('excluded', [])
    return dict(source=source, names=names, excluded=excluded, updated_at=now())


def task_ranges(rule, merges):
    """Owner-cell merge is a task; unmerged rows remain separate tasks."""
    col = rule['owner_column']
    relevant = sorted((m for m in merges if m[2] <= col <= m[3]), key=lambda m:m[0])
    spans, row, i = [], rule['start_row'], 0
    while row <= rule['end_row']:
        while i < len(relevant) and relevant[i][1] < row:
            i += 1
        if i < len(relevant) and relevant[i][0] <= row <= relevant[i][1]:
            start, end, left, right = relevant[i]
            if start < rule['start_row'] or end > rule['end_row']:
                raise ValueError(f'监听范围截断了责任人合并区域 {letters(left)}{start}:{letters(right)}{end}，请扩大起止行')
            spans.append((start, end, left))
            row = end + 1
        else:
            spans.append((row, row, col))
            row += 1
    return spans


def missing_tasks(rule, cells, merges, names):
    result = []
    for first, last, owner_col in task_ranges(rule, merges):
        owner = written_text(cells.get((first, owner_col)))
        people = [name for name in names if name in owner]
        if not people:
            continue
        filled = False
        for row in range(first, last + 1):
            for col in target_columns(rule):
                anchor = (row, col)
                for top, bottom, left, right in merges:
                    if top <= row <= bottom and left <= col <= right:
                        if top < first or bottom > last:
                            raise ValueError('填写列合并区域跨越多个责任人任务，请拆分合并单元格')
                        anchor = (top, left)
                        break
                if written_text(cells.get(anchor)):
                    filled = True
                    break
            if filled:
                break
        if not filled:
            for person in people:
                result.append(dict(person=person, row=first, end_row=last,
                    sheet_id=rule['sheet_id'], sheet=rule['sheet_name'], item=rule['name'],
                    column=','.join(letters(c) for c in target_columns(rule)), rule_id=rule['id']))
    return result

