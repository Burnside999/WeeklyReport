"""Typed, read-only variable catalog shared by the UI and future mail templates."""
from datetime import datetime, timedelta, timezone
from urllib.parse import quote, urlsplit, urlunsplit

from .core import letters, target_columns, is_row
from .template_language import nest

LOCAL_TZ = timezone(timedelta(hours=8), 'Asia/Shanghai')
DAYS = ('monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday')
DAY_LABELS = ('一', '二', '三', '四', '五', '六', '日')


LISTENER_SCHEMA = {
    'name': 'string', 'sheetname': 'string', 'sheeturl': 'url',
    'personcol': 'string', 'taskcol': 'string', 'startrow': 'integer',
    'endrow': 'integer', 'distribution': 'string', 'personrow': 'integer',
    'taskrow': 'string', 'startcol': 'string', 'endcol': 'string', 'enable': 'boolean', 'personcount': 'integer',
    'personlist': 'string', 'people': ['string'],
}


def build_variables(store, running=False, current=None):
    current = (current or datetime.now(LOCAL_TZ)).astimezone(LOCAL_TZ)
    rules = store.get('rules', [])
    snapshot = store.get('snapshot', {})
    valid = bool(snapshot.get('last_success')) and not snapshot.get('stale') and snapshot.get('semantics_version') == 2
    rows = []

    def add(name, kind, description, value):
        rows.append(dict(name=name, type=kind, description=description, value=value))

    add('global.time', 'time', '当前时间', current.strftime('%H:%M:%S'))
    add('global.date', 'date', '当前日期', current.date().isoformat())
    add('global.week.now', 'string', '当前星期几', '星期' + DAY_LABELS[current.weekday()])
    monday = current.date() - timedelta(days=current.weekday())
    for prefix, offset, label in [('week', 0, '本周'), ('preweek', -7, '上周'), ('postweek', 7, '下周')]:
        start = monday + timedelta(days=offset)
        for index, day in enumerate(DAYS):
            add(f'global.{prefix}.{day}', 'date', f'{label}周{DAY_LABELS[index]}日期', (start + timedelta(days=index)).isoformat())
        add(f'global.{prefix}.duration', 'string', f'{label}日期区间', f'{start.isoformat()} ~ {(start + timedelta(days=6)).isoformat()}')
    add('global.listencount', 'integer', '当前监听规则总数', len(rules))
    add('global.listenenable', 'integer', '当前启用的监听规则数', sum(r['enabled'] for r in rules))
    url = store.settings()['document_url']
    add('global.url', 'url', '当前腾讯表格地址', url)
    people = sorted({r['person'] for r in snapshot.get('records', [])}) if valid else None
    add('global.personcount', 'integer', '未交人数', len(people) if people is not None else None)
    add('global.personlist', 'string', '未交姓名', ','.join(people) if people is not None else None)
    add('global.people', 'list', '未交姓名列表', people)
    health = 2 if running else 0 if snapshot.get('stale') else 1 if valid else None
    add('global.healthy', 'integer', '系统状态', health)
    last = snapshot.get('last_attempt')
    add('global.lastquery', 'datetime', '上次查询开始时间', datetime.fromisoformat(last).astimezone(LOCAL_TZ).isoformat(timespec='seconds') if last else None)
    parts = urlsplit(url)
    for rule in rules:
        prefix = rule['variable_name']
        sheet_url = urlunsplit((parts.scheme, parts.netloc, parts.path, 'tab=' + quote(rule['sheet_id'], safe=''), ''))
        values = [('name', 'string', '监听规则名称', rule['name']),
                  ('sheetname', 'string', '监听工作表名称', rule['sheet_name']),
                  ('sheeturl', 'url', '监听工作表链接', sheet_url),
                  ('distribution', 'string', '分布方式', '行分布' if is_row(rule) else '列分布'),
                  ('enable', 'boolean', '是否启用', rule['enabled'])]
        if is_row(rule):
            values += [('personrow','integer','责任人所在行',rule['owner_row']),
                       ('taskrow','string','需要检查的行',','.join(map(str,rule['target_rows']))),
                       ('startcol','string','起始列',letters(rule['start_column'])),
                       ('endcol','string','结束列',letters(rule['end_column']))]
        else:
            values += [('personcol','string','责任人所在列',letters(rule['owner_column'])),
                       ('taskcol','string','需要检查的列',','.join(letters(c) for c in target_columns(rule))),
                       ('startrow','integer','起始行',rule['start_row']),
                       ('endrow','integer','结束行',rule['end_row'])]
        # Per-rule data must precede dashboard deduplication: overlapping rules
        # can share a task, but both must expose its missing people.
        names = snapshot.get('rule_people', {}).get(rule['id']) if valid and rule['enabled'] else None
        for key, kind, label, value in values:
            add(f'{prefix}.{key}', kind, label, value)
        add(f'{prefix}.personcount', 'integer', '该规则未交人数', len(names) if names is not None else None)
        add(f'{prefix}.personlist', 'string', '该规则未交姓名', ','.join(names) if names is not None else None)
        add(f'{prefix}.people', 'list', '未交姓名列表', names)
    values = {r['name']: r['value'] for r in rows}
    objects = nest(values)
    listeners = [dict.fromkeys(LISTENER_SCHEMA) | objects[r['variable_name']] for r in rules]
    add('global.alllistener', 'list', '全部监听器', listeners)
    values['global.alllistener'] = listeners
    schema = nest({r['name']: r['type'] for r in rows})
    schema['global']['people'] = ['string']
    schema['global']['alllistener'] = [LISTENER_SCHEMA]
    for rule in rules:
        schema[rule['variable_name']]['people'] = ['string']
    return dict(schema=schema, listener_names=[r['variable_name'] for r in rules], generated_at=current.isoformat(timespec='seconds'), timezone='Asia/Shanghai',
                query_running=running, results_available=valid,
                rows=rows, values=values)

