"""Document polling and atomic publication of check results."""
import asyncio
import logging
import time

from .core import (now, written_text, target_columns, task_ranges, missing_tasks,
                   task_identity, is_row, rule_axes, source_axes, oriented_merges,
                   refresh_roster)
from .tencent import TencentError

LOG = logging.getLogger(__name__)


class Monitor:
    def __init__(self, store, client):
        self.store, self.client = store, client
        self.lock = asyncio.Lock()
        self.wake = asyncio.Event()
        self.running = False
        self.next_check = None
        self.manual_requested = False

    async def check(self):
        if self.lock.locked():
            return False
        async with self.lock:
            self.running = True
            started = now()
            rules = [r for r in self.store.get('rules', []) if r['enabled']]
            results, errors = [], []
            try:
                if rules:
                    async with self.client.lock:
                        headers = await self.client.headers()
                        fid = await self.client.file_id(headers)
                        metadata = {x['sheetId']: x for x in await self.client.metadata(fid, headers)}
                        roster = self.store.get('roster')
                        if not roster or not roster.get('source'):
                            raise TencentError('请先在设置中选择同事名单数据源')
                        source = roster['source']
                        source_meta = metadata.get(source['sheet_id'])
                        if not source_meta:
                            raise TencentError('名单工作表已不存在，请重新选择数据源')
                        source_axis, source_start, source_end = source_axes(source)
                        reader = self.client.read_row if is_row(source) else self.client.read_column
                        names_cells = await reader(fid,source['sheet_id'],source_axis,source_start,source_end,headers)
                        roster = refresh_roster(source | {'sheet_name':source_meta['title']},
                            [written_text(names_cells.get(r)) for r in range(source_start,source_end+1)], roster)
                        self.store.set('roster',roster)
                        names = [n for n in roster['names'] if n not in roster['excluded']]
                        layout = await self.client.layout(fid,headers,metadata) if names else {'sheets':{}}
                        cache = {}
                        for rule in rules if names else []:
                            try:
                                meta = metadata.get(rule['sheet_id'])
                                if meta is None:
                                    raise TencentError('工作表不存在，请重新选择工作表')
                                rule = rule | {'sheet_name':meta['title']}
                                horizontal = is_row(rule)
                                axes = rule_axes(rule)
                                total = meta.get('columnTotal' if horizontal else 'rowTotal')
                                if isinstance(total,int) and total>0:
                                    if axes['start_row']>total:
                                        raise TencentError('起始列超出工作表范围' if horizontal else '起始行超出工作表范围')
                                    rule = rule | {('end_column' if horizontal else 'end_row'):min(axes['end_row'],total)}
                                    axes = rule_axes(rule)
                                merges = layout['sheets'][rule['sheet_id']]
                                spans = task_ranges(rule,merges)
                                tracks = set(target_columns(axes)) | {axes['owner_column']} | {x[2] for x in spans}
                                # Include merged-cell anchors on the opposite axis too.
                                for top,bottom,left,right in oriented_merges(rule,merges):
                                    if top<=axes['end_row'] and bottom>=axes['start_row'] and any(left<=c<=right for c in target_columns(axes)):
                                        tracks.add(left)
                                cells = {}
                                reader = self.client.read_row if horizontal else self.client.read_column
                                for track in sorted(tracks):
                                    key = (horizontal,rule['sheet_id'],track,axes['start_row'],axes['end_row'])
                                    if key not in cache:
                                        cache[key] = await reader(fid,*key[1:],headers)
                                    cells.update({((track,index) if horizontal else (index,track)):value for index,value in cache[key].items()})
                                results.extend(missing_tasks(rule,cells,merges,names))
                            except (TencentError, ValueError) as exc:
                                errors.append(dict(rule=rule['name'], message=str(exc)))
            except (TencentError, ValueError) as exc:
                errors.append(dict(rule='文档连接', message=str(exc)))
            except Exception:
                LOG.exception('Unexpected monitor failure')
                errors.append(dict(rule='检查任务', message='检查发生内部错误，请查看服务器日志'))
            finally:
                previous = self.store.get('snapshot', {})
                if previous.get('semantics_version') != 2:
                    previous = {}
                snapshot = dict(semantics_version=2, last_attempt=started, finished_at=now(), errors=errors,
                                last_success=previous.get('last_success'),
                                records=previous.get('records', []),
                                people_count=previous.get('people_count'),
                                rule_count=len(rules), stale=bool(errors),
                                document_url=(previous.get('document_url') if errors else None)
                                or self.store.settings()['document_url'])
                # Publish atomically only after ALL enabled rules succeeded.
                if not errors:
                    seen = set()
                    unique = []
                    for r in results:
                        key = task_identity(r)
                        if key not in seen:
                            seen.add(key)
                            unique.append(r)
                    snapshot.update(rule_people={rule['id']: sorted({r['person'] for r in results if r['rule_id'] == rule['id']}) for rule in rules},
                                    records=unique, people_count=len({r['person'] for r in unique}),
                                    last_success=snapshot['finished_at'])
                self.store.set('snapshot', snapshot)
                self.running = False
                self.next_check = (time.time() + self.store.settings()['interval_seconds']
                                   if self.store.settings()['auto_query_enabled'] else None)
        return True

    async def loop(self):
        while True:
            self.wake.clear()
            manual = self.manual_requested
            self.manual_requested = False
            if manual or self.store.settings()['auto_query_enabled']:
                await self.check()
            if not self.store.settings()['auto_query_enabled']:
                self.next_check = None
                await self.wake.wait()
                continue
            delay = max(0, (self.next_check or time.time()) - time.time())
            try:
                await asyncio.wait_for(self.wake.wait(), delay)
            except asyncio.TimeoutError:
                pass
