"""Access boundaries and new HTTP contracts; no model training or remote accounts."""
import os
import unittest
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

from api.main import app
from api.farmer_data import get_farmer_http_client
from api.analysis import get_analysis_loader
from common.config_loader import ConfigLoader
from pipeline.analysis import build_bundle
from tests.test_analysis_api import inputs, StubModel

OWNER='00000000-0000-4000-8000-000000000001'
TIME='2026-08-12T05:00:00Z'


class AccessContractsTest(unittest.TestCase):
    def setUp(self):
        env=patch.dict(os.environ,{'SUPABASE_URL':'https://test.supabase.co','SUPABASE_PUBLISHABLE_KEY':'public-key'})
        env.start(); self.addCleanup(env.stop)
        self.loader=ConfigLoader(); self.calls=[]
        self.user={'id':OWNER}
        self.access={'user_id':OWNER,'area_ids':[1],'cages':[{'id':3,'name':'어가 · 가두리 3','farm_id':1,
            'sea_area_id':1,'can_write_stock':True,'can_read_financial':True}]}
        self.stock={'cage_id':3,'stock':[],'insured':None,'insured_mixed':False}
        self.shapes={'type':'FeatureCollection','features':[],'unverified_area_ids':[1]}
        self.http=httpx.Client(transport=httpx.MockTransport(self.handle)); self.addCleanup(self.http.close)
        app.dependency_overrides[get_farmer_http_client]=lambda:self.http
        app.dependency_overrides[get_analysis_loader]=lambda:self.loader
        self.addCleanup(app.dependency_overrides.clear)
        self.client=TestClient(app); self.addCleanup(self.client.close)
        self.headers={'Authorization':'Bearer owner-token'}

    def handle(self,r):
        self.calls.append(r)
        if r.url.path=='/auth/v1/user': return httpx.Response(200,json=self.user)
        if r.url.path=='/rest/v1/cage': return httpx.Response(200,json=[{'id':3}])
        data={'user_access':self.access,'cage_stock':self.stock,'area_shapes':self.shapes,
              'area_cause_distribution':{'ts':TIME,'config_revision':self.loader.snapshot().revision,'cause_distribution':[]}}
        name=r.url.path.rsplit('/',1)[-1]
        if name not in data: raise AssertionError(r.url.path)
        return httpx.Response(200,json=data[name])

    def get(self,path): return self.client.get(path,headers=self.headers)

    def test_owner_scope_and_current_official_financial_revocation(self):
        response=self.get('/me')
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()['role'],'farmer')
        self.assertEqual(set(response.json()),{'user_id','role','area_ids','cages'})
        self.assertEqual(response.headers['cache-control'],'private, no-store')
        self.user['app_metadata']={'role':'official','area_ids':[1]}
        self.access['cages'][0]['can_write_stock']=False
        self.assertFalse(self.get('/me').json()['cages'][0]['can_read_financial'])
        count=sum(r.url.path.endswith('/cage_stock') for r in self.calls)
        self.assertEqual(self.get('/cages/3/stock').status_code,403)
        self.assertEqual(sum(r.url.path.endswith('/cage_stock') for r in self.calls),count)
        self.user['app_metadata']['financial_farm_ids']=[1]
        self.assertEqual(self.get('/cages/3/stock').status_code,200)
        self.assertTrue(all(r.headers.get('Authorization')=='Bearer owner-token' for r in self.calls))

    def test_stock_empty_mixed_and_safe_integer_contract(self):
        self.assertIsNone(self.get('/cages/3/stock').json()['insured'])
        rows=[{'size_group':g,'count':n,'avg_weight_kg':0.3,'updated_at':TIME,'insured':flag}
              for g,n,flag in [('fry',0,True),('mid',30,False)]]
        self.stock.update(stock=rows,insured_mixed=True)
        response=self.get('/cages/3/stock')
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()['stock'][0]['count'],0)
        self.stock['insured']=False
        self.assertEqual(self.get('/cages/3/stock').status_code,503)
        self.stock['insured']=None; rows[1]['count']=9_007_199_254_740_992
        self.assertEqual(self.get('/cages/3/stock').status_code,503)

    def test_unverified_shapes_empty_and_cross_scope_or_unsafe_ids_rejected(self):
        self.assertEqual(self.get('/areas/shapes').json(),self.shapes)
        self.shapes['unverified_area_ids']=[2]
        self.assertEqual(self.get('/areas/shapes').status_code,503)
        self.access['cages'][0]['id']=9_007_199_254_740_992
        self.assertEqual(self.get('/me').status_code,503)

    def test_metadata_spoof_and_malformed_official_grants_fail_closed(self):
        self.user['user_metadata']={'role':'official','area_ids':[1]}
        self.assertEqual(self.get('/areas/cause-distribution').status_code,403)
        self.user['app_metadata']={'role':'official','area_ids':[True]}
        self.assertEqual(self.get('/me').status_code,403)
        self.assertEqual(self.client.get('/me').status_code,401)

    def test_analysis_bundle_keeps_provenance_visibility_and_actual_source_time(self):
        data=inputs(); snapshot=self.loader.snapshot()
        bundle=build_bundle(data,StubModel(),'cage-003','조피볼락',snapshot)
        status=bundle['status']
        self.assertEqual(status['cause_source'],'cause_engine')
        self.assertTrue(status['cause'][0]['display'])
        self.assertTrue(bundle['causes'][0]['evidence']['display'])
        self.assertIsNone(status['last_seen']['camera'])
        self.assertNotEqual(status['last_seen']['sonar'],status['ts'])
        self.assertEqual(status['cause_pending'],[])
