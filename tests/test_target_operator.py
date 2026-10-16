import itertools
import tempfile
import unittest

from aiohttp.test_utils import TestServer

from app.core import Store, missing_tasks, validate_rule
from app.monitor import Monitor
from support import TestClient, create_app, scoped_store
from test_app import cell, rule
from test_distribution import MatrixClient, row_rule


class TargetOperatorTests(unittest.TestCase):
    def test_truth_table_in_both_directions_and_legacy_default(self):
        for horizontal, operator, left, right in itertools.product(
                [False, True], [None, 'or', 'and'], [False, True], [False, True]):
            with self.subTest(horizontal=horizontal, operator=operator, left=left, right=right):
                config = row_rule(end_column=2) if horizontal else rule(end_row=2, target_columns=[3,4])
                if operator is not None:
                    config['target_operator'] = operator
                cells = {(2,1):cell('甲'), (2,3):cell('已填' if left else ''),
                         (2,4):cell('已填' if right else '')}
                if horizontal:
                    cells = {(c,r):v for (r,c),v in cells.items()}
                complete = left and right if operator == 'and' else left or right
                self.assertEqual(len(missing_tasks(config, cells, [], ['甲'])), 0 if complete else 1)

    def test_merged_task_requires_each_target_but_keeps_separate_tasks(self):
        cells = {(2,1):cell('甲'), (5,1):cell('甲'), (2,3):cell('计划'),
                 (4,4):cell('进展'), (5,3):cell('另一项计划')}
        merges = [[2,4,1,1], [5,6,1,1]]
        for horizontal in [False, True]:
            config = (row_rule(end_column=6) if horizontal else rule(end_row=6, target_columns=[3,4]))
            config['target_operator'] = 'and'
            matrix = {(c,r):v for (r,c),v in cells.items()} if horizontal else cells
            layout = [[l,r,t,b] for t,b,l,r in merges] if horizontal else merges
            records = missing_tasks(config, matrix, layout, ['甲'])
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0]['column'] if horizontal else records[0]['row'], 'E' if horizontal else 5)
            config['target_operator'] = 'or'
            self.assertEqual(missing_tasks(config, matrix, layout, ['甲']), [])

    def test_and_uses_merged_anchors_and_existing_text_semantics(self):
        config = rule(end_row=4, target_columns=[3,4], target_operator='and')
        cells = {(2,1):cell('甲'), (2,3):cell('共同进展')}
        self.assertEqual(missing_tasks(config, cells, [[2,4,1,1], [2,4,3,4]], ['甲']), [])
        config = rule(end_row=2, target_columns=[3,4], target_operator='and')
        for value, complete in [(None, False), (cell(' \n'), False),
                                ({'cellValue':{'number':0}}, False),
                                ({'cellValue':{'boolean':True}}, False),
                                ({'cellValue':{'image':'image'}}, False),
                                (cell('0'), True), (cell('无'), True),
                                ({'cellValue':{'link':{'text':'详情'}}}, True)]:
            with self.subTest(value=value):
                matrix = cells | {(2,4):value}
                self.assertEqual(bool(missing_tasks(config, matrix, [], ['甲'])), not complete)

    def test_single_target_and_cross_task_merge_boundary(self):
        for operator in ['or', 'and']:
            config = rule(end_row=2, target_operator=operator)
            self.assertEqual(missing_tasks(config, {(2,1):cell('甲'), (2,3):cell('完成')}, [], ['甲']), [])
            self.assertEqual(len(missing_tasks(config, {(2,1):cell('甲')}, [], ['甲'])), 1)
        with self.assertRaisesRegex(ValueError, '跨越多个责任人任务'):
            missing_tasks(rule(target_columns=[3,4], target_operator='and'),
                          {(2,1):cell('甲'), (2,3):cell('完成')}, [[2,3,4,4]], ['甲'])

    def test_validation_defaults_and_rejects_unknown_operators(self):
        for config in [rule(), row_rule()]:
            self.assertEqual(validate_rule(config)['target_operator'], 'or')
            self.assertEqual(validate_rule(config | {'target_operator':'and'})['target_operator'], 'and')
            for value in ['', 'xor', 'AND', None, True, 1, [], {}]:
                with self.subTest(value=value), self.assertRaisesRegex(ValueError, '填写条件'):
                    validate_rule(config | {'target_operator':value})


class TargetOperatorMonitorTests(unittest.IsolatedAsyncioTestCase):
    async def test_operator_changes_result_even_when_cell_reads_share_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(directory+'/db')
            try:
                store.set('roster', dict(source=dict(sheet_id='names', column=10, start_row=2, end_row=3),
                                        names=[], excluded=[]))
                for horizontal in [False, True]:
                    config = (row_rule(end_column=5) if horizontal else
                              rule(sheet_id='column', target_columns=[3,4], end_row=5))
                    store.set('rules', [config | dict(id='either', target_operator='or'),
                                        config | dict(id='both', target_operator='and')])
                    await Monitor(store, MatrixClient()).check()
                    snapshot = store.get('snapshot')
                    self.assertFalse(snapshot['stale'], snapshot['errors'])
                    self.assertEqual(snapshot['rule_people'], {'either':['张三'], 'both':['张三','李四']})
                    self.assertEqual(snapshot['people_count'], 2)
            finally:
                store.close()


class TargetOperatorAPITests(unittest.IsolatedAsyncioTestCase):
    async def test_create_edit_persistence_and_rejected_update(self):
        with tempfile.TemporaryDirectory() as directory:
            app = create_app(directory, 'test-password-12345', start_scheduler=False)
            async with TestClient(TestServer(app)) as client:
                headers = {'X-Requested-With':'WeeklyReport'}
                await client.post('/api/login', json=dict(username='admin', password='test-password-12345'), headers=headers)
                response = await client.post('/api/rules', json=rule(), headers=headers)
                self.assertEqual(response.status, 200)
                created = (await response.json())[0]
                self.assertEqual(created['target_operator'], 'or')
                path = '/api/rules/'+created['id']
                response = await client.put(path, json=created | {'target_operator':'and'}, headers=headers)
                self.assertEqual(response.status, 200)
                self.assertEqual((await response.json())[0]['target_operator'], 'and')
                persisted = Store(directory+'/weeklyreport.db')
                try:
                    self.assertEqual(scoped_store(persisted).get('rules')[0]['target_operator'], 'and')
                finally:
                    persisted.close()
                response = await client.put(path, json=created | {'target_operator':'xor'}, headers=headers)
                self.assertEqual(response.status, 400)
                self.assertEqual((await (await client.get('/api/rules')).json())[0]['target_operator'], 'and')
                response = await client.put(path, json=created | {'target_operator':'or'}, headers=headers)
                self.assertEqual(response.status, 200)
                self.assertEqual((await response.json())[0]['target_operator'], 'or')
