import asyncio
import io
import tempfile
import unittest
import zipfile
from unittest.mock import patch
from aiohttp.test_utils import TestServer
from support import TestClient, create_app, scoped_store
from app.core import (missing_tasks, validate_rule, validate_source, refresh_roster,
                      written_text, Store, colleague_name, migrate_roster)
from app.merge_layout import parse_layout
from app.main import Monitor
from app.mail import SMTPConfig, SMTPMailer
from app.tencent import TencentClient, TencentError, download_export
from test_app import rule, cell, FakeClient


class TaskTests(unittest.TestCase):
    def test_colleague_parentheses_are_preserved(self):
        for raw in ['张三（休假）', '(分公司)李四', ' 王五（分公司(休假)）(备注) ',
                    '（休假）', '张（备注）三', '张三（未闭合']:
            self.assertEqual(colleague_name(raw), raw.strip())
        roster = refresh_roster({}, ['张三（休假）','张三','(分公司)李四','（休假）',' 张三（休假） '],
                                {'excluded':['张三（休假）','张三','王五(分公司)']})
        self.assertEqual(roster['names'], ['张三（休假）','张三','(分公司)李四','（休假）'])
        self.assertEqual(roster['excluded'], ['张三（休假）','张三','王五(分公司)'])

    def test_full_names_are_used_for_task_statistics(self):
        roster = refresh_roster({}, ['张三（分公司）','张三(备注)','李四（休假）'])
        rows = missing_tasks(rule(), {(2,1):cell('张三（分公司）、李四（休假）'),
                                      (3,1):cell('张三、李四')}, [], roster['names'])
        self.assertEqual([row['person'] for row in rows], ['张三（分公司）','李四（休假）'])

    def test_existing_roster_migration_preserves_selection(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(directory+'/db')
            try:
                store.set('roster', dict(source={'sheet_id':'s'}, updated_at='old',
                    names=['张三(分公司)','张三','李四（休假）'], excluded=['李四（休假）']))
                store.set('snapshot', {'stale':False, 'last_success':'old'})
                migrate_roster(store)
                roster = store.get('roster')
                self.assertEqual(roster['names'], ['张三(分公司)','张三','李四（休假）'])
                self.assertEqual(roster['excluded'], ['李四（休假）'])
                self.assertEqual(roster['updated_at'], 'old')
                self.assertFalse(store.get('snapshot')['stale'])
                store.set('snapshot', {'stale':False})
                migrate_roster(store)
                self.assertFalse(store.get('snapshot')['stale'])
            finally:
                store.close()

    def test_multiple_owners_parentheses_exclusion_and_separate_tasks(self):
        r=rule(end_row=7,target_columns=[3,4])
        cells={(2,1):cell('张三（协助李四）、王五'),(3,4):cell('已完成'),
               (5,1):cell('张三、李四'),(7,1):cell('张三（第二任务）')}
        result=missing_tasks(r,cells,[[2,4,1,1],[5,6,1,1]],['张三','李四'])
        self.assertEqual([(x['person'],x['row'],x['end_row']) for x in result],
                         [('张三',5,6),('李四',5,6),('张三',7,7)])
    def test_no_forward_fill_and_substring_matching(self):
        cells={(2,1):cell('张三丰'),(4,1):cell('张三')}
        rows=missing_tasks(rule(),cells,[],['张三','张三丰'])
        self.assertEqual([(x['person'],x['row']) for x in rows],[('张三',2),('张三丰',2),('张三',4)])
    def test_text_only_and_multiple_columns(self):
        cells={(2,1):cell('甲'),(2,3):{'cellValue':{'number':0}},(2,4):cell(' \n')}
        r=rule(start_row=2,end_row=2,target_columns=[3,4])
        self.assertEqual(len(missing_tasks(r,cells,[],['甲'])),1)
        cells[2,4]=cell('0')
        self.assertEqual(missing_tasks(r,cells,[],['甲']),[])
        self.assertFalse(written_text({'cellValue':{'image':'foo'}}))
    def test_horizontal_anchor_and_merged_target(self):
        r=rule(owner_column=2,target_columns=[4],start_row=2,end_row=4)
        cells={(2,1):cell('甲'),(2,3):cell('进展')}
        self.assertEqual(missing_tasks(r,cells,[[2,4,1,2],[2,4,3,4]],['甲']),[])
    def test_partial_owner_and_cross_task_target_fail_closed(self):
        with self.assertRaisesRegex(ValueError,'截断'):
            missing_tasks(rule(start_row=3),{},[[2,4,1,1]],['甲'])
        with self.assertRaisesRegex(ValueError,'跨越'):
            missing_tasks(rule(),{(2,1):cell('甲')},[[2,3,3,3]],['甲'])
    def test_source_validation_and_preserved_exclusions(self):
        source=validate_source(dict(sheet_id='randomID',column='AC',start_row=3,end_row=8))
        self.assertEqual(source['column'],29)
        r=refresh_roster(source,[' 甲 ','乙','甲',''])
        self.assertEqual(r['names'],['甲','乙']);self.assertEqual(r['excluded'],[])
        r['excluded']=['乙']
        r=refresh_roster(source,['甲','丙'],r)
        r=refresh_roster(source,['乙','甲','丙'],r)
        self.assertEqual(r['excluded'],['乙'])
        with self.assertRaises(ValueError):refresh_roster(source,[' '])
    def test_multi_column_validation_and_migration(self):
        self.assertEqual(validate_rule(rule(target_columns='B, D，F'))['target_columns'],[2,4,6])
        self.assertEqual(validate_rule(rule())['target_columns'],[3])
        with self.assertRaises(ValueError):validate_rule(rule(target_columns=[]))


def xlsx():
    f=io.BytesIO()
    with zipfile.ZipFile(f,'w') as z:
        z.writestr('xl/workbook.xml','<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="任意名字" r:id="rId9"/></sheets></workbook>')
        z.writestr('xl/_rels/workbook.xml.rels','<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId9" Target="worksheets/sheet7.xml"/></Relationships>')
        z.writestr('xl/worksheets/sheet7.xml','<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><mergeCells><mergeCell ref="B999:B1002"/></mergeCells></worksheet>')
    return f.getvalue()


class LayoutTests(unittest.IsolatedAsyncioTestCase):
    async def test_parse_true_geometry_not_sheet_order(self):
        self.assertEqual(parse_layout(xlsx()),{'任意名字':[[999,1002,2,2]]})
        with self.assertRaises(ValueError):parse_layout(b'bad file')
    async def test_official_export_contract_cache_and_quota(self):
        with tempfile.TemporaryDirectory() as d:
            store=Store(d+'/db'); client=TencentClient(store,None);calls=[]
            async def request(path,params=None,headers=None,method='GET'):
                calls.append((path,method))
                return {'operationID':'op'} if method=='POST' else {'progress':100,'url':'https://example.com/file'}
            async def download(*args):return xlsx()
            client.request=request
            meta={'ID9':{'sheetId':'ID9','title':'任意名字'}}
            with patch('app.tencent.download_export',download):
                a=await client.layout('fid',{},meta)
                b=await client.layout('fid',{},meta)
                self.assertEqual(a,b);self.assertEqual(len(calls),2)
                self.assertEqual(a['sheets']['ID9'],[[999,1002,2,2]])
                self.assertTrue(calls[0][0].endswith('/async-export'))
                for _ in range(7):await client.layout('fid',{},meta,force=True)
                with self.assertRaisesRegex(TencentError,'上限'):await client.layout('fid',{},meta,force=True)
            store.close()
    async def test_private_download_rejected(self):
        for url in ('http://example.com/file','https://127.0.0.1/file','https://user:pass@example.com/file'):
            with self.assertRaises(ValueError):await download_export(url,5)


class FeatureWebTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.app=create_app(self.tmp.name,'test-password-12345',False)
        self.web=TestClient(TestServer(self.app));await self.web.start_server()
        self.headers={'X-Requested-With':'WeeklyReport'}
        await self.web.post('/api/login',json={'username':'admin','password':'test-password-12345'},headers=self.headers)
        async def roster(source): return ['张三','李四'],source
        self.app['client'].roster=roster
    async def asyncTearDown(self):await self.web.close();self.tmp.cleanup()
    async def test_roster_api_all_default_none_selected_and_refresh(self):
        source={'sheet_id':'anywhere','column':'B','start_row':2,'end_row':3}
        r=await self.web.put('/api/roster',json=source,headers=self.headers)
        self.assertEqual(r.status,200);self.assertEqual((await r.json())['excluded'],[])
        await self.web.put('/api/roster/selection',json={'selected':[]},headers=self.headers)
        r=await self.web.put('/api/roster',json=source,headers=self.headers)
        self.assertEqual((await r.json())['excluded'],['张三','李四'])
        r=await self.web.put('/api/roster/selection',json={'selected':['不在名单']},headers=self.headers)
        self.assertEqual(r.status,400)
    async def test_smtp_password_editing_and_no_delivery(self):
        settings=dict(smtp_host='smtp.example.com',smtp_sender='from@example.com',smtp_port=465,
                      smtp_password='secret-app-password',smtp_sender_name='发送人')
        with patch('smtplib.SMTP') as smtp,patch('smtplib.SMTP_SSL') as ssl:
            r=await self.web.put('/api/admin/smtp',json=settings,headers=self.headers)
            self.assertEqual(r.status,200)
            r=await self.web.get('/api/admin/smtp');data=await r.json()
            self.assertEqual(data['smtp_password'],'secret-app-password')
            config=SMTPConfig.from_settings(self.app['store'].settings())
            self.assertNotIn('secret-app-password',repr(config))
            self.assertNotIn('smtp_recipient', data)
            self.assertNotIn('smtp_recipient_name', data)
            smtp.assert_not_called();ssl.assert_not_called()
        await self.web.put('/api/admin/smtp',json={'smtp_sender_name':'新名称'},headers=self.headers)
        self.assertEqual(self.app['store'].settings()['smtp_password'],'secret-app-password')
        await self.web.put('/api/admin/smtp',json={'smtp_password':''},headers=self.headers)
        self.assertEqual(self.app['store'].settings()['smtp_password'],'')
    async def test_roster_persists_and_document_url_is_immutable(self):
        source={'sheet_id':'x','column':'A','start_row':1,'end_row':2}
        await self.web.put('/api/roster',json=source,headers=self.headers)
        second=Store(self.tmp.name+'/weeklyreport.db')
        self.assertEqual(scoped_store(second).get('roster')['names'],['张三','李四']);second.close()
        response=await self.web.put('/api/settings',json={'document_url':'https://docs.qq.com/sheet/DNEW'},headers=self.headers)
        self.assertEqual(response.status,400)
        self.assertEqual((await (await self.web.get('/api/roster')).json())['names'],['张三','李四'])
    async def test_invalid_smtp_config(self):
        for data in ({'smtp_port':0},{'smtp_sender':'invalid'},{'smtp_sender_name':'bad\r\nHeader: injected'}):
            r=await self.web.put('/api/admin/smtp',json=data,headers=self.headers)
            self.assertEqual(r.status,400)
