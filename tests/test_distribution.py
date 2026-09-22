import asyncio
import tempfile
import unittest
from unittest.mock import AsyncMock

from app.core import Store, validate_rule, validate_source, missing_tasks, task_ranges, refresh_roster
from app.main import Monitor
from app.tencent import TencentClient, TencentError
from app.variables import build_variables
from app.templates import render
from test_app import cell, rule


def row_rule(**changes):
    return dict(id='horizontal',variable_name='horizontal',name='横向任务',sheet_id='row',sheet_name='横向表',
                distribution='row',owner_row=1,target_rows=[3,4],start_column=2,end_column=7,enabled=True) | changes


class DistributionTests(unittest.TestCase):
    def test_validate_real_axes_and_legacy_default(self):
        self.assertEqual(validate_rule(rule())['distribution'],'column')
        value=validate_rule(row_rule(owner_row='100000',target_rows='2,4，6',start_column='B',end_column='AA'))
        self.assertEqual((value['owner_row'],value['target_rows'],value['start_column'],value['end_column']),(100000,[2,4,6],2,27))
        self.assertNotIn('owner_column',value);self.assertNotIn('start_row',value)
        source=validate_source(dict(sheet_id='names',distribution='row',row='2',start_column='AA',end_column='AC'))
        self.assertEqual((source['row'],source['start_column'],source['end_column']),(2,27,29))
        for change in [dict(distribution='diagonal'),dict(owner_row='A'),dict(target_rows='B,D'),dict(target_rows=[]),
                       dict(start_column='D',end_column='B'),dict(end_column='XFE'),dict(end_column=10002)]:
            with self.assertRaises(ValueError,msg=str(change)):validate_rule(row_rule(**change))
        with self.assertRaisesRegex(ValueError,'姓名所在行和起止列'):refresh_roster(source,[])

    def test_transpose_preserves_tasks_and_merged_anchors(self):
        vertical=rule(end_row=7,target_columns=[3,4])
        cells={(2,1):cell('张三'),(5,1):cell('李四'),(7,1):cell('张三'),(5,4):cell('已填')}
        merges=[[2,4,1,1],[5,6,1,1]]
        expected=missing_tasks(vertical,cells,merges,['张三','李四'])
        horizontal=missing_tasks(row_rule(),{(c,r):v for (r,c),v in cells.items()},[[l,r,t,b] for t,b,l,r in merges],['张三','李四'])
        self.assertEqual([r['person'] for r in horizontal],[r['person'] for r in expected])
        self.assertEqual([(r['column'],r['end_column'],r['rows']) for r in horizontal],[('B','D','3,4'),('G','G','3,4')])
        self.assertEqual(missing_tasks(row_rule(owner_row=2,target_rows=[4],end_column=4),
            {(1,2):cell('甲'),(3,2):cell('完成')},[[1,2,2,4],[3,4,2,4]],['甲']),[])

    def test_transposed_merge_boundaries_report_real_coordinates(self):
        with self.assertRaisesRegex(ValueError,'B1:D1.*起止列'):
            task_ranges(row_rule(start_column=3),[[1,1,2,4]])
        with self.assertRaisesRegex(ValueError,'填写行合并区域跨越'):
            missing_tasks(row_rule(),{(1,2):cell('甲')},[[3,3,2,3]],['甲'])


class RowAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.store=Store(self.tmp.name+'/db');self.client=TencentClient(self.store,None)
    async def asyncTearDown(self):self.store.close();self.tmp.cleanup()

    async def test_row_pagination_200_columns_and_sparse_offsets(self):
        paths=[]
        async def request(path,params,headers):
            paths.append(path)
            return {'gridData':{'startRow':6,'startColumn':2 if len(paths)==1 else 201,'rows':[{'values':[cell('甲'),None,cell('乙')]}]}}
        self.client.request=request
        result=await self.client.read_row('file','sheet',7,2,205,{})
        self.assertEqual(set(result),{3,4,5,202,203,204})
        self.assertIn('B7%3AGS7',paths[0]);self.assertIn('GT7%3AGW7',paths[1])

    async def test_malformed_rows_offsets_and_overflow_fail_closed(self):
        for data in [{}, {'gridData':{'startRow':1,'startColumn':1,'rows':[]}},
                     {'gridData':{'startRow':6,'startColumn':0,'rows':[]}},
                     {'gridData':{'rows':[{},{}]}}, {'gridData':{'rows':[{'values':[{}, {}, {}]}]}},
                     {'gridData':{'rows':[{'values':'wrong'}]}}]:
            self.client.request=AsyncMock(return_value=data)
            with self.assertRaises(TencentError,msg=str(data)):
                await self.client.read_row('file','sheet',7,2,3,{})

    async def test_roster_uses_selected_row_and_preserves_names(self):
        self.client.headers=AsyncMock(return_value={});self.client.file_id=AsyncMock(return_value='f')
        self.client.metadata=AsyncMock(return_value=[{'sheetId':'names','title':'同事名单'}])
        self.client.read_row=AsyncMock(return_value={2:cell('张三（休假）'),3:cell(''),4:cell('李四')})
        names,source=await self.client.roster(validate_source(dict(sheet_id='names',distribution='row',row=4,start_column='B',end_column='D')))
        self.assertEqual(names,['张三（休假）','','李四']);self.assertEqual(source['sheet_name'],'同事名单')
        self.client.read_row.assert_awaited_once_with('f','names',4,2,4,{})


class MatrixClient:
    def __init__(self):
        self.lock=asyncio.Lock();self.calls=[]
        row={(1,2):cell('张三'),(1,4):cell('李四'),(1,5):cell('张三'),(4,4):cell('完成')}
        self.cells={'row':row,'column':{(c,r):v for (r,c),v in row.items()},
                    'names':{(10,2):cell('张三'),(10,3):cell('李四'),(2,10):cell('张三'),(3,10):cell('李四')}}
    async def headers(self):return {}
    async def file_id(self,headers):return 'file'
    async def metadata(self,fid,headers):return [dict(sheetId=s,title=s,rowTotal=30,columnTotal=30) for s in self.cells]
    async def layout(self,*args):return {'sheets':{'row':[[1,1,2,3]],'column':[[2,3,1,1]],'names':[]}}
    async def read_row(self,fid,sheet,track,start,end,headers):
        self.calls.append(('row',sheet,track,start,end))
        return {c:self.cells[sheet].get((track,c)) for c in range(start,end+1)}
    async def read_column(self,fid,sheet,track,start,end,headers):
        self.calls.append(('column',sheet,track,start,end))
        return {r:self.cells[sheet].get((r,track)) for r in range(start,end+1)}


class DistributionMonitorTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):self.tmp=tempfile.TemporaryDirectory();self.store=Store(self.tmp.name+'/db')
    async def asyncTearDown(self):self.store.close();self.tmp.cleanup()

    async def test_sources_and_rules_allow_all_four_direction_combinations(self):
        for source_direction in ['row','column']:
            for rule_direction in ['row','column']:
                source=validate_source(dict(distribution=source_direction,sheet_id='names',row=10,column=10,start_row=2,end_row=3,start_column=2,end_column=3))
                self.store.set('roster',dict(source=source,names=[],excluded=[]))
                config=row_rule(end_column=5) if rule_direction=='row' else rule(sheet_id='column',target_columns=[3,4],end_row=5,variable_name='vertical')
                self.store.set('rules',[config]);client=MatrixClient()
                await Monitor(self.store,client).check();snapshot=self.store.get('snapshot')
                self.assertFalse(snapshot['stale'],snapshot['errors']);self.assertEqual(snapshot['people_count'],1)
                self.assertEqual(len(snapshot['records']),2);self.assertEqual(snapshot['rule_people'][config['id']],['张三'])
                self.assertEqual(client.calls[0][0],source_direction)
                self.assertTrue(all(c[0]==rule_direction for c in client.calls[1:]))

    async def test_mixed_directions_on_same_sheet_do_not_share_wrong_cache(self):
        client=MatrixClient();client.cells['row']={(1,2):cell('张三'),(2,1):cell('李四')}
        client.layout=AsyncMock(return_value={'sheets':{'row':[]}})
        self.store.set('roster',dict(source=validate_source(dict(sheet_id='names',column=10,start_row=2,end_row=3)),names=[],excluded=[]))
        self.store.set('rules',[row_rule(target_rows=[3],end_column=2),rule(sheet_id='row',end_row=2,variable_name='vertical')])
        await Monitor(self.store,client).check();snapshot=self.store.get('snapshot')
        self.assertFalse(snapshot['stale'],snapshot['errors']);self.assertEqual(snapshot['people_count'],2)
        self.assertEqual(snapshot['rule_people'],{'horizontal':['张三'],'r1':['李四']})
        catalog=build_variables(self.store)
        self.assertEqual(render('{{horizontal.distribution}}：第 {{horizontal.personrow}} 行，{{horizontal.startcol}}–{{horizontal.endcol}} 列',catalog),'行分布：第 1 行，B–B 列')
        self.assertEqual(catalog['values']['horizontal.personrow'],1)
        self.assertEqual(catalog['values']['horizontal.startcol'],'B')
        self.assertEqual(catalog['values']['vertical.personcol'],'A')
        self.assertNotIn('horizontal.personcol',catalog['values'])
        self.assertNotIn('vertical.personrow',catalog['values'])
        self.assertEqual(catalog['schema']['horizontal']['personrow'],'integer')
        self.assertIn('personrow',catalog['schema']['global']['alllistener'][0])

    async def test_same_physical_task_deduplicates_across_directions(self):
        client=MatrixClient();client.cells['row']={(1,2):cell('张三'),(2,1):cell('张三')}
        client.layout=AsyncMock(return_value={'sheets':{'row':[]}})
        self.store.set('roster',dict(source=validate_source(dict(sheet_id='names',column=10,start_row=2,end_row=3)),names=[],excluded=[]))
        self.store.set('rules',[row_rule(target_rows=[2],end_column=2),rule(sheet_id='row',target_column=2,end_row=2,variable_name='vertical')])
        await Monitor(self.store,client).check();snapshot=self.store.get('snapshot')
        self.assertFalse(snapshot['stale'],snapshot['errors']);self.assertEqual(len(snapshot['records']),1)
        self.assertEqual(snapshot['rule_people'],{'horizontal':['张三'],'r1':['张三']})

    async def test_horizontal_extent_is_clipped_and_invalid_start_is_stale(self):
        client=MatrixClient()
        self.store.set('roster',dict(source=validate_source(dict(sheet_id='names',column=10,start_row=2,end_row=3)),names=[],excluded=[]))
        self.store.set('rules',[row_rule(end_column=40)])
        await Monitor(self.store,client).check()
        self.assertTrue(all(c[4]<=30 for c in client.calls));self.assertFalse(self.store.get('snapshot')['stale'])
        self.store.set('rules',[row_rule(start_column=31,end_column=40)])
        await Monitor(self.store,client).check()
        self.assertIn('起始列超出',self.store.get('snapshot')['errors'][0]['message'])
