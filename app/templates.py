"""Mail templates and a persistent single-process delivery state machine."""
import asyncio
import logging
import operator
import re
import secrets
from datetime import date, datetime, time

from .mail import SMTPConfig, SMTPMailer, DeliveryError, recipients_list
from .variables import LOCAL_TZ, build_variables
from .template_language import Program, TemplateSyntaxError

TOKEN = re.compile(r'{{\s*([A-Za-z][A-Za-z0-9]*(?:\.[A-Za-z][A-Za-z0-9]*)+)\s*}}')
OPS = {'gt': operator.gt, 'lt': operator.lt, 'eq': operator.eq}
COMPARABLE = {'integer', 'boolean'}
CONFIG_KEYS = ('recipients', 'subject', 'body', 'mode', 'schedule', 'condition')
LOG = logging.getLogger(__name__)


class TemplateConflict(Exception):
    def __init__(self, message, **details):
        super().__init__(message)
        self.details = details


def stamp(current=None):
    return (current or datetime.now(LOCAL_TZ)).astimezone(LOCAL_TZ).isoformat(timespec='seconds')


def parse_datetime(value):
    if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2})?(?:Z|[+-]\d{2}:\d{2})?', value):
        raise ValueError('时间点须为完整日期和时间，例如 2026-09-25T09:00')
    result = datetime.fromisoformat(value)
    return (result if result.tzinfo else result.replace(tzinfo=LOCAL_TZ)).astimezone(LOCAL_TZ)


def typed_value(kind, value):
    if value is None:
        raise ValueError('变量当前值未知，请等待成功查询')
    if kind == 'integer':
        if isinstance(value, bool) or not re.fullmatch(r'-?\d{1,16}', str(value)):
            raise ValueError('比较值须为整数')
        return int(value)
    if kind == 'boolean':
        if str(value).lower() not in ('true', 'false'):
            raise ValueError('布尔比较值须为 true 或 false')
        return str(value).lower() == 'true'
    if kind == 'date':
        if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', str(value)):
            raise ValueError('日期须为 YYYY-MM-DD')
        return date.fromisoformat(value)
    if kind == 'time':
        if not re.fullmatch(r'\d{2}:\d{2}(?::\d{2})?', str(value)):
            raise ValueError('时刻须为 HH:mm 或 HH:mm:ss')
        return time.fromisoformat(value)
    if kind == 'datetime':
        return parse_datetime(value)
    raise ValueError('该变量类型不能用于触发比较')


def references(text, catalog):
    return sorted(Program(text, catalog).references)


def render(text, catalog):
    return Program(text, catalog).render()


def check_syntax(raw, catalog):
    errors, tokens = [], {}
    for field, limit in [('subject', 300), ('body', 20000)]:
        text = raw.get(field, '')
        if not isinstance(text, str) or len(text) > limit:
            errors.append(dict(field=field, message='模板内容格式错误或过长', line=1, column=1))
            continue
        try:
            spans = Program(text, catalog).tokens
        except TemplateSyntaxError as exc:
            spans = getattr(exc, 'tokens', [])
            errors.append(dict(field=field, **exc.detail()))
        tokens[field] = [dict(start=len(text[:t['start']].encode('utf-16-le', errors='surrogatepass')) // 2,
                             end=len(text[:t['end']].encode('utf-16-le', errors='surrogatepass')) // 2)
                         for t in spans]
    return dict(syntax_errors=errors, tokens=tokens)


def resolve_time(schedule, catalog):
    if schedule['kind'] == 'fixed':
        return parse_datetime(schedule['value'])
    name = schedule['value']
    row = next((r for r in catalog['rows'] if r['name'] == name), None)
    if not row or row['type'] not in ('date', 'datetime'):
        raise ValueError('时间点只能引用日期或日期时间变量')
    value = catalog['values'][name]
    if value is None:
        raise ValueError('时间点变量当前值未知')
    if row['type'] == 'datetime':
        return parse_datetime(value)
    day = typed_value('date', value)
    clock = typed_value('time', schedule['clock'])
    return datetime.combine(day, clock, LOCAL_TZ)


def validate_template(raw, catalog, *, check_references=True):
    if not isinstance(raw, dict):
        raise ValueError('模板格式错误')
    result = {'recipients': recipients_list(raw.get('recipients'))}
    for key, label, limit in [('subject', '标题', 300), ('body', '内容', 20000)]:
        value = raw.get(key)
        if not isinstance(value, str) or not value.strip() or len(value) > limit or '\x00' in value:
            raise ValueError(f'邮件{label}须为 1–{limit} 字')
        if key == 'subject' and any(c in value for c in '\r\n'):
            raise ValueError('邮件标题不能换行')
        if check_references:
            references(value, catalog)
        result[key] = value
    mode = raw.get('mode')
    if mode not in ('manual', 'auto'):
        raise ValueError('请选择手动触发或自动触发')
    result.update(mode=mode, schedule=None, condition=None)
    if mode == 'manual':
        return result
    schedule = raw.get('schedule')
    condition = raw.get('condition')
    if not isinstance(schedule, dict) or not isinstance(condition, dict):
        raise ValueError('请配置时间点和触发规则')
    kind, value = schedule.get('kind'), schedule.get('value')
    if not isinstance(value, str):
        raise ValueError('请填写合法时间点')
    value = value.strip()
    if kind == 'fixed':
        result['schedule'] = dict(kind=kind, value=parse_datetime(value).isoformat(timespec='seconds'))
    elif kind == 'variable':
        match = TOKEN.fullmatch(value)
        value = match.group(1) if match else value
        row = next((r for r in catalog['rows'] if r['name'] == value), None)
        if not row or row['type'] not in ('date', 'datetime'):
            raise ValueError('时间点只能引用日期或日期时间变量，不能使用仅含时分秒的变量')
        clock = schedule.get('clock', '00:00') if row['type'] == 'date' else '00:00'
        clock = typed_value('time', clock).isoformat()
        result['schedule'] = dict(kind=kind, value=value, clock=clock)
    else:
        raise ValueError('请选择固定时间或日期变量')
    row = next((r for r in catalog['rows'] if r['name'] == condition.get('variable')), None)
    if not row or row['type'] not in COMPARABLE:
        raise ValueError('触发变量须为整数或布尔值')
    if condition.get('operator') not in OPS:
        raise ValueError('关系须为大于、小于或等于')
    typed_value(row['type'], condition.get('value'))
    result['condition'] = dict(variable=row['name'], operator=condition['operator'], value=str(condition['value']))
    return result


class MailEngine:
    def __init__(self, store, monitor, sender=None, clock=None):
        self.store, self.monitor = store, monitor
        self.sender = sender
        self.clock = clock or (lambda: datetime.now(LOCAL_TZ))
        self.lock = asyncio.Lock()
        self.wake = asyncio.Event()
        self.stopping = False
        items = self.items()
        for item in items:
            if item['status'] == 'sending':
                item.update(status='error', error='上次发送被中断，结果不确定；请核实邮箱后再重置或重发')
        self.store.set('mail_templates', items)

    def items(self):
        return self.store.get('mail_templates', [])

    def catalog(self, current=None):
        current = current or self.clock()
        catalog = build_variables(self.store, self.monitor.running, current)
        last = self.store.get('snapshot', {}).get('last_success')
        if last and (current - datetime.fromisoformat(last)).total_seconds() > self.store.settings()['interval_seconds'] + 60:
            for key in catalog['values']:
                if key.endswith(('.personcount', '.personlist', '.people')):
                    catalog['values'][key] = None
            catalog['results_available'] = False
        return catalog

    def describe(self, item, catalog=None):
        catalog = catalog if catalog is not None else self.catalog()
        errors = check_syntax(item, catalog)['syntax_errors']
        if not errors:
            try:
                validate_template(item, catalog)
            except ValueError as exc:
                errors.append(dict(field='condition', message=str(exc), line=1, column=1))
        return item | {'syntax_errors': errors}

    def listed(self):
        catalog = self.catalog()
        return [self.describe(item, catalog) for item in self.items()]

    def require_valid(self, item, catalog):
        errors = self.describe(item, catalog)['syntax_errors']
        if errors:
            raise ValueError('存在语法错误：' + errors[0]['message'])

    def ensure_idle(self):
        if self.lock.locked():
            raise TemplateConflict('邮件正在发送或检查，请稍后操作')

    def write(self, item):
        self.store.set('mail_templates', [item if x['id'] == item['id'] else x for x in self.items()])

    def get(self, identifier):
        item = next((x for x in self.items() if x['id'] == identifier), None)
        if item is None:
            raise ValueError('邮件模板不存在')
        if item['status'] == 'sending':
            raise TemplateConflict('邮件正在发送，或上次请求中断；请等待发送完成')
        return item

    def save(self, raw, identifier=None):
        self.ensure_idle()
        value = validate_template(raw, self.catalog(), check_references=False)
        items = self.items()
        if identifier:
            previous = self.get(identifier)
            if raw.get('revision') != previous['revision']:
                raise TemplateConflict('模板已在其他页面修改，请刷新后重试')
            changed = any(previous[k] != value[k] for k in CONFIG_KEYS)
            item = previous | value
            if changed:
                item.update(status='waiting', cycle=None, error='', note='', revision=secrets.token_hex(12))
            items = [item if x['id'] == identifier else x for x in items]
        else:
            if len(items) >= 50:
                raise ValueError('最多创建 50 个邮件模板')
            item = value | dict(id=secrets.token_hex(12), revision=secrets.token_hex(12),
                status='waiting', cycle=None, error='', note='', last_trigger=None, last_success=None, attempt=None)
            items.append(item)
        self.store.set('mail_templates', items)
        self.wake.set()
        return self.describe(item)

    def delete(self, identifier, revision):
        self.ensure_idle()
        if self.get(identifier)['revision'] != revision:
            raise TemplateConflict('模板已修改，请刷新后重试')
        self.store.set('mail_templates', [x for x in self.items() if x['id'] != identifier])

    def reset(self, identifier, revision):
        self.ensure_idle()
        item = self.get(identifier)
        if item['revision'] != revision or item['mode'] != 'auto':
            raise TemplateConflict('模板已修改或不是自动触发模板，请刷新')
        item.update(status='waiting', error='', note='', revision=secrets.token_hex(12))
        self.write(item)
        self.wake.set()
        return item

    def recent(self, item, current, confirmation):
        if item.get('last_trigger') and (current - datetime.fromisoformat(item['last_trigger'])).total_seconds() < 300:
            if confirmation != item['attempt']:
                raise TemplateConflict(f"你已在 {item['last_trigger']} 触发过此规则，确定要再次触发吗？",
                    confirmation_required=True, confirm_attempt=item['attempt'], last_trigger=item['last_trigger'])

    async def manual(self, identifier, raw):
        self.ensure_idle()
        async with self.lock:
            item = self.get(identifier)
            if item['mode'] != 'manual' or raw.get('revision') != item['revision']:
                raise TemplateConflict('模板已修改或不是手动模板，请刷新')
            current = self.clock()
            catalog = self.catalog(current)
            self.require_valid(item, catalog)
            self.recent(item, current, raw.get('confirm_attempt'))
            await self.deliver(item, catalog, current)
            return item

    async def deliver(self, item, catalog, current):
        self.require_valid(item, catalog)
        # Render everything from one snapshot before claiming a delivery attempt.
        subject, body = render(item['subject'], catalog), render(item['body'], catalog)
        if '\n' in subject or '\r' in subject or len(subject) > 998 or len(body) > 200000:
            raise ValueError('替换变量后的标题包含换行、过长，或正文超过 20 万字')
        item.update(status='sending', error='', note='', last_trigger=stamp(current), attempt=secrets.token_hex(16))
        self.write(item)  # durable claim BEFORE the irreversible SMTP operation
        try:
            sender = self.sender or SMTPMailer(SMTPConfig.from_settings(self.store.settings())).send
            await sender(subject=subject, text=body, recipients=item['recipients'])
        except (DeliveryError, ValueError) as exc:
            item.update(status='error', error=str(exc))
            self.write(item)
            raise DeliveryError(str(exc)) from None
        except Exception:
            item.update(status='error', error='发送异常，结果不确定，请核实邮箱后再重置或重发')
            self.write(item)
            raise DeliveryError(item['error']) from None
        item.update(status='sent', last_success=stamp(self.clock()))
        self.write(item)

    async def tick(self):
        if self.lock.locked():
            return
        async with self.lock:
            for item in self.items():
                if item['mode'] != 'auto':
                    continue
                current, target = self.clock(), None
                catalog = self.catalog(current)
                try:
                    self.require_valid(item, catalog)
                    target = resolve_time(item['schedule'], catalog)
                    cycle = stamp(target)
                    if item['cycle'] != cycle:
                        item.update(cycle=cycle, status='waiting', error='', note='')
                        self.write(item)
                    if item['status'] != 'waiting':
                        continue
                    if current < target:
                        item['note'] = '等待到达触发时间：' + cycle
                    elif self.monitor.running:
                        item['note'] = '正在查询表格，等待完整结果'
                    else:
                        condition = item['condition']
                        row = next((r for r in catalog['rows'] if r['name'] == condition['variable']), None)
                        if not row or row['type'] not in COMPARABLE:
                            raise ValueError('触发变量已不存在或类型不适用，请编辑模板')
                        keys = references(item['subject'] + '\n' + item['body'], catalog) + [condition['variable']]
                        if any(k.endswith(('.personcount', '.personlist', '.people')) for k in keys):
                            last = self.store.get('snapshot', {}).get('last_success')
                            if not last or datetime.fromisoformat(last) < target:
                                raise ValueError('等待触发时间之后的一次成功表格查询')
                        left = typed_value(row['type'], catalog['values'][row['name']])
                        right = typed_value(row['type'], condition['value'])
                        if OPS[condition['operator']](left, right):
                            await self.deliver(item, catalog, current)
                        else:
                            item['note'] = '触发条件尚未满足'
                except ValueError as exc:
                    item['note'] = str(exc)
                except DeliveryError:
                    pass  # persistent error; no blind retries after uncertain delivery
                self.write(item)

    async def loop(self):
        while not self.stopping:
            self.wake.clear()
            try:
                await self.tick()
            except Exception:
                LOG.error('Mail scheduler check failed; will check again next interval')
            try:
                await asyncio.wait_for(self.wake.wait(), 15)
            except asyncio.TimeoutError:
                pass

