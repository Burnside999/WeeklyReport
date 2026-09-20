"""Typed, read-only variable catalog shared by the UI and future mail templates."""
from datetime import datetime, timedelta, timezone
from urllib.parse import quote, urlsplit, urlunsplit

from .core import letters, target_columns

LOCAL_TZ = timezone(timedelta(hours=8), 'Asia/Shanghai')
DAYS = ('monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday')
DAY_LABELS = ('一', '二', '三', '四', '五', '六', '日')


def build_variables(store, running=False, current=None):
    current = (current or datetime.now(LOCAL_TZ)).astimezone(LOCAL_TZ)
    rules = store.get('rules', [])
    snapshot = store.get('snapshot', {})
    valid = bool(snapshot.get('last_success')) and not snapshot.get('stale') and snapshot.get('semantics_version') == 2
    rows = []

    def add(name, kind, description, value):
        rows.append(dict(name=name, type=kind, description=description, value=value))

    add('global.time', 'time', '当前时间（北京时间）', current.strftime('%H:%M:%S'))
    add('global.date', 'date', '当前日期（北京时间）', current.date().isoformat())
    add('global.week.now', 'string', '当前星期几', '星期' + DAY_LABELS[current.weekday()])
    monday = current.date() - timedelta(days=current.weekday())
    for prefix, offset, label in [('week', 0, '本周'), ('preweek', -7, '上周'), ('postweek', 7, '下周')]:
        start = monday + timedelta(days=offset)
        for index, day in enumerate(DAYS):
            add(f'global.{prefix}.{day}', 'date', f'{label}周{DAY_LABELS[index]}日期', (start + timedelta(days=index)).isoformat())
        add(f'global.{prefix}.duration', 'string', f'{label}日期区间（周一至周日，含首尾）', f'{start.isoformat()} ~ {(start + timedelta(days=6)).isoformat()}')
    add('global.listencount', 'integer', '当前监听规则总数', len(rules))
    add('global.listenenable', 'integer', '当前启用的监听规则数', sum(r['enabled'] for r in rules))
    url = store.settings()['document_url']
    add('global.url', 'url', '当前腾讯表格地址', url)
    people = sorted({r['person'] for r in snapshot.get('records', [])}) if valid else None
    add('global.personcount', 'integer', '未交人数（去重；无有效结果时 null）', len(people) if people is not None else None)
    add('global.personlist', 'string', '未交姓名（去重、英文逗号分隔）', ','.join(people) if people is not None else None)
    health = 2 if running else 0 if snapshot.get('stale') else 1 if valid else None
    add('global.healthy', 'integer', '系统健康：1 正常，0 异常，2 查询中，null 未查询', health)
    last = snapshot.get('last_attempt')
    add('global.lastquery', 'datetime', '上次查询开始时间（北京时间，含失败查询）', datetime.fromisoformat(last).astimezone(LOCAL_TZ).isoformat(timespec='seconds') if last else None)
    parts = urlsplit(url)
    for rule in rules:
        prefix = rule['variable_name']
        sheet_url = urlunsplit((parts.scheme, parts.netloc, parts.path, 'tab=' + quote(rule['sheet_id'], safe=''), ''))
        values = [('name', 'string', '监听规则名称', rule['name']),
                  ('sheetname', 'string', '监听工作表名称', rule['sheet_name']),
                  ('sheeturl', 'url', '监听工作表链接', sheet_url),
                  ('personcol', 'string', '责任人列（字母列号）', letters(rule['owner_column'])),
                  ('taskcol', 'string', '监听内容列（字母列号，逗号分隔）', ','.join(letters(c) for c in target_columns(rule))),
                  ('startrow', 'integer', '起始行', rule['start_row']),
                  ('endrow', 'integer', '结束行', rule['end_row']),
                  ('enable', 'boolean', '是否启用', rule['enabled'])]
        # Per-rule data must precede dashboard deduplication: overlapping rules
        # can share a task, but both must expose its missing people.
        names = snapshot.get('rule_people', {}).get(rule['id']) if valid and rule['enabled'] else None
        for key, kind, label, value in values:
            add(f'{prefix}.{key}', kind, label, value)
        add(f'{prefix}.personcount', 'integer', '该规则未交人数（停用或无有效结果时 null）', len(names) if names is not None else None)
        add(f'{prefix}.personlist', 'string', '该规则未交姓名（去重、逗号分隔）', ','.join(names) if names is not None else None)
    return dict(generated_at=current.isoformat(timespec='seconds'), timezone='Asia/Shanghai',
                query_running=running, results_available=valid,
                rows=rows, values={r['name']: r['value'] for r in rows})

