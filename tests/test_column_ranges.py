import tempfile
import unittest

from aiohttp.test_utils import TestServer

from app.core import Store, missing_tasks, validate_rule
from support import TestClient, create_app, scoped_store
from test_app import cell, rule


class ColumnRangeTests(unittest.TestCase):
    def test_ranges_single_columns_numbers_and_overlap(self):
        cases = [
            ('A-D,E-G,Z-AK', list(range(1,8))+list(range(26,38))),
            (' a - d ， F, z - aa ', [1,2,3,4,6,26,27]),
            ('A-D,C-F,A', list(range(1,7))),
            ('2-4,AA,30-32', [2,3,4,27,30,31,32]),
            ('C D F', [3,4,6]),
            (['A-C', 5, 'B'], [1,2,3,5]),
            ('A-T', list(range(1,21))),
            ('XFC-XFD', [16383,16384]),
        ]
        for text, expected in cases:
            with self.subTest(text=text):
                config = validate_rule(rule(target_columns=text))
                self.assertEqual(config['target_columns'], expected)
                self.assertEqual(config['target_column'], expected[0])

    def test_invalid_syntax_direction_bounds_and_expanded_limit(self):
        for text in ['', 'A-A', 'D-A', 'AK-Z', '4-2', 'A-', '-D', 'A--D', 'A-D-E',
                     'A:D', 'A-D,,F', ',A-D', 'A-D,', 'A-D， ，F', 'A1-D',
                     '0-3', 'XFD-XFE', 'A-U', 'A-T,Z', [], None, ['A-D', {}]]:
            with self.subTest(text=text), self.assertRaises(ValueError):
                validate_rule(rule(target_columns=text))

    def test_expanded_columns_apply_to_or_and_and(self):
        config = validate_rule(rule(target_columns='C-E,G', start_row=2, end_row=2)) | {'id':'r1'}
        cells = {(2,1):cell('甲'), (2,3):cell('计划')}
        self.assertEqual(missing_tasks(config, cells, [], ['甲']), [])
        config['target_operator'] = 'and'
        self.assertEqual(len(missing_tasks(config, cells, [], ['甲'])), 1)
        cells.update({(2,c):cell('完成') for c in [4,5,7]})
        self.assertEqual(missing_tasks(config, cells, [], ['甲']), [])


class ColumnRangeAPITests(unittest.IsolatedAsyncioTestCase):
    async def test_range_storage_and_invalid_edit(self):
        with tempfile.TemporaryDirectory() as directory:
            app = create_app(directory, 'range-test-password', start_scheduler=False)
            async with TestClient(TestServer(app)) as client:
                headers = {'X-Requested-With':'WeeklyReport'}
                await client.post('/api/login', json=dict(username='admin', password='range-test-password'), headers=headers)
                response = await client.post('/api/rules', json=rule(target_columns='A-D,E-G,Z-AK', target_operator='and'), headers=headers)
                self.assertEqual(response.status, 200)
                saved = (await response.json())[0]
                expected = list(range(1,8))+list(range(26,38))
                self.assertEqual(saved['target_columns'], expected)
                persisted = Store(directory+'/weeklyreport.db')
                try:
                    self.assertEqual(scoped_store(persisted).get('rules')[0]['target_columns'], expected)
                finally:
                    persisted.close()
                response = await client.put('/api/rules/'+saved['id'], json=saved | {'target_columns':'D-A'}, headers=headers)
                self.assertEqual(response.status, 400)
                current = (await (await client.get('/api/rules')).json())[0]
                self.assertEqual(current['target_columns'], expected)
                self.assertEqual(current['target_operator'], 'and')
