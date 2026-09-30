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
        raise ValueError('请选择工作表')
    distribution = validate_distribution(raw)
    horizontal = distribution == 'row'
    axis = '行' if horizontal else '列'
    span = '列' if horizontal else '行'
    if horizontal:
        start = column(raw.get('start_column', 'B'))
        end = column(raw.get('end_column', 'AL'))
        if end < start:
            raise ValueError('结束列不能早于起始列')
    else:
        start = integer(raw.get('start_row', 2), 1, 100000, '起始行')
        end = integer(raw.get('end_row', 1000), start, 100000, '结束行')
    if end - start + 1 > 10000:
        raise ValueError(f'单条规则最多监听 10000 {span}，请拆分规则')
    enabled = raw.get('enabled', True)
    if type(enabled) is not bool:
        raise ValueError('启用状态必须为布尔值')
    targets = raw.get('target_rows', [2]) if horizontal else raw.get('target_columns', [raw.get('target_column', 'B')])
    if isinstance(targets, str):
        targets = re.split(r'[,，、\s]+', targets.strip())
    if not isinstance(targets, list) or not 1 <= len(targets) <= 20:
        raise ValueError(f'请选择 1–20 个需要检查的{axis}')
    parse_axis = (lambda v: integer(v, 1, 100000, '行号')) if horizontal else column
    targets = sorted(set(parse_axis(v) for v in targets))
    base = dict(name=name, sheet_id=sheet, sheet_name=str(raw.get('sheet_name', ''))[:100],
                distribution=distribution, enabled=enabled)
    if horizontal:
        return base | dict(owner_row=parse_axis(raw.get('owner_row', 1)), target_rows=targets,
                           start_column=start, end_column=end)
    return base | dict(owner_column=column(raw.get('owner_column', 'A')),
                       target_column=targets[0], target_columns=targets, start_row=start, end_row=end)


def validate_distribution(raw):
    value = raw.get('distribution', 'column')
    if value not in ('column', 'row'):
        raise ValueError('请选择行分布或列分布')
    return value


def is_row(config):
    return config.get('distribution', 'column') == 'row'


def rule_axes(rule):
    """Normalize geometry only; persisted row configurations keep real axis names."""
    if not is_row(rule):
        return rule
    return rule | dict(owner_column=rule['owner_row'], target_columns=rule['target_rows'],
                       start_row=rule['start_column'], end_row=rule['end_column'])


def source_axes(source):
    return ((source['row'], source['start_column'], source['end_column']) if is_row(source)
            else (source['column'], source['start_row'], source['end_row']))


def oriented_merges(config, merges):
    return [[left,right,top,bottom] for top,bottom,left,right in merges] if is_row(config) else merges


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
        self.path = path
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
    distribution = validate_distribution(raw)
    if distribution == 'row':
        r = validate_rule(dict(name='同事名单', sheet_id=raw.get('sheet_id', ''),
            sheet_name=raw.get('sheet_name', ''), distribution=distribution, owner_row=raw.get('row', 1),
            start_column=raw.get('start_column', 'B'), end_column=raw.get('end_column', 'AL')))
        return {k:r[k] for k in ('sheet_id','sheet_name','distribution','start_column','end_column')} | {'row':r['owner_row']}
    r = validate_rule(dict(name='同事名单', sheet_id=raw.get('sheet_id', ''),
        sheet_name=raw.get('sheet_name', ''), owner_column=raw.get('column', 'A'),
        start_row=raw.get('start_row', 1), end_row=raw.get('end_row', 100)))
    return {k:r[k] for k in ('sheet_id','sheet_name','distribution','start_row','end_row')} | {'column':r['owner_column']}


def colleague_name(value):
    """Keep the source name intact apart from surrounding whitespace."""
    return value.strip()


def colleague_names(values):
    return list(dict.fromkeys(name for value in values if (name := colleague_name(value))))


def migrate_roster(store):
    roster = store.get('roster')
    if not roster:
        return
    cleaned = roster | {'names': colleague_names(roster['names']),
                        'excluded': colleague_names(roster.get('excluded', []))}
    if cleaned != roster:
        store.set('roster', cleaned)
        snapshot = store.get('snapshot', {})
        snapshot.update(stale=True, errors=[dict(rule='名单更新', message='姓名已更新，等待重新查询')])
        store.set('snapshot', snapshot)


def refresh_roster(source, names, previous=None):
    names = colleague_names(names)
    if not names:
        raise ValueError('名单范围没有读取到姓名，请检查工作表、姓名所在行和起止列' if is_row(source) else '名单范围没有读取到姓名，请检查工作表、姓名所在列和起止行')
    # Preserve exclusions across refreshes and reappearance; new names are selected.
    excluded = colleague_names((previous or {}).get('excluded', []))
    return dict(source=source, names=names, excluded=excluded, updated_at=now())


def task_ranges(rule, merges):
    """Owner-cell merge is a task along the selected distribution axis."""
    horizontal = is_row(rule)
    merges = oriented_merges(rule,merges)
    rule = rule_axes(rule)
    col = rule['owner_column']
    relevant = sorted((m for m in merges if m[2] <= col <= m[3]), key=lambda m:m[0])
    spans, row, i = [], rule['start_row'], 0
    while row <= rule['end_row']:
        while i < len(relevant) and relevant[i][1] < row:
            i += 1
        if i < len(relevant) and relevant[i][0] <= row <= relevant[i][1]:
            start, end, left, right = relevant[i]
            if start < rule['start_row'] or end > rule['end_row']:
                area = f'{letters(start)}{left}:{letters(end)}{right}' if horizontal else f'{letters(left)}{start}:{letters(right)}{end}'
                raise ValueError(f'监听范围截断了责任人合并区域 {area}，请扩大起止'+('列' if horizontal else '行'))
            spans.append((start, end, left))
            row = end + 1
        else:
            spans.append((row, row, col))
            row += 1
    return spans


def missing_tasks(rule, cells, merges, names):
    spans = task_ranges(rule,merges)
    horizontal = is_row(rule)
    if horizontal:
        cells = {(col,row):value for (row,col),value in cells.items()}
    merges = oriented_merges(rule,merges)
    rule = rule_axes(rule)
    result = []
    for first, last, owner_col in spans:
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
                            raise ValueError(('填写行' if horizontal else '填写列')+'合并区域跨越多个责任人任务，请拆分合并单元格')
                        anchor = (top, left)
                        break
                if written_text(cells.get(anchor)):
                    filled = True
                    break
            if filled:
                break
        if not filled:
            for person in people:
                location = (dict(distribution='row', row=rule['target_rows'][0], rows=','.join(map(str,rule['target_rows'])),
                                 column=letters(first), end_column=letters(last)) if horizontal else
                            dict(distribution='column', row=first, end_row=last, column=','.join(letters(c) for c in target_columns(rule))))
                result.append(dict(person=person, sheet_id=rule['sheet_id'], sheet=rule['sheet_name'],
                                   item=rule['name'], rule_id=rule['id']) | location)
    return result


def task_identity(record):
    if record.get('distribution') == 'row':
        rows = tuple(int(r) for r in record['rows'].split(','))
        cols = tuple(range(column(record['column']),column(record['end_column'])+1))
    else:
        rows = tuple(range(record['row'],record['end_row']+1))
        cols = tuple(column(c) for c in record['column'].split(','))
    return record['person'],record['sheet_id'],rows,cols
