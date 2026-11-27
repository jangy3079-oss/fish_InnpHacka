import copy
from datetime import datetime,timedelta,timezone
import json
import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

from api.analysis import get_analysis_loader
from api.farmer_data import SupabaseSettings,get_farmer_http_client
from api.main import app
from common.config_loader import ConfigLoader
from pipeline.analysis import build_bundle,recommendation_for,reliability
from common import MetricRecord
from fusion.fuse import FusedFeatures
from pipeline.analysis_store import AnalysisStore,AnalysisError
from pipeline.analyze_db import run
from stress.model import StressScore

END = datetime.now(timezone.utc).replace(second=0,microsecond=0)
END = END.replace(minute=END.minute//5*5)
KEY = '11111111-1111-4111-8111-111111111111'


class StubModel:
    cage_id='cage-003'

    def __init__(self,warm=False):
        self.warm=warm

    def score_series(self,fused):
        return [StressScore(self.cage_id,f.ts+timedelta(minutes=5),None if self.warm else 70,
            'warming_up' if self.warm else 'ready','m5-iforest-1.1+cage-003.20260804.123ccdef',
            evidence={'lower_layers_ratio_x':1.8,'activity_change':-0.3,'cohesion_change':0},
            baseline_windows=1000,components={'sonar':70}) for f in fused]


def inputs():
    start=END-timedelta(hours=3)
    records=[]
    for i in range(36):
        ts=(start+timedelta(minutes=5*i)).isoformat()
        for source,metric,value in [('sonar','activity',0.4),('sonar','vertical_dist',[0.2,0.6,0.2]),
                                     ('sonar','cohesion',0.8),('env','temp_surface',26),('env','temp_mid',21),('env','do',7)]:
            records.append({'cage_id':'3','ts':ts,'source':source,'metric':metric,'value':value,'confidence':0.9})
    return {'cage_id':3,'farm_id':1,'sea_area_id':1,'stock':[{'size_group':'mid','count':1000,'avg_weight_kg':0.3,'insured':False}],
            'stock_version':'f'*32,'metrics':records,'metrics_truncated':False,'from':start.isoformat(),'to':END.isoformat()}


class AnalysisBuilderTest(unittest.TestCase):
    def test_reliability_uses_actual_fusion_weights_and_ignores_zero_weight_source(self):
        f=FusedFeatures('cage-003',END,{'activity':0.6},{},0.8,0.5,weights={'activity':0.5})
        rows=[MetricRecord(cage_id='cage-003',ts=END,source=s,metric='activity',value=0.6,confidence=c)
              for s,c in [('sonar',0.8),('camera',0.4)]]
        policy=ConfigLoader().snapshot()['thresholds']['analysis']
        result=reliability(f,rows,policy)['activity']
        self.assertAlmostEqual(result['confidence'],0.6)
        self.assertEqual(result['grade'],'limited')
        f.weights['activity']=0
        result=reliability(f,rows,policy)['activity']
        self.assertEqual(result['sources'],['sonar'])
        self.assertEqual(result['grade'],'good')

    def test_coherent_r1_status_reliability_and_cause_scoped_options(self):
        b=build_bundle(inputs(),StubModel(),'cage-003','조피볼락',ConfigLoader().snapshot())
        self.assertEqual(b['status']['data_status'],'ready')
        self.assertEqual(b['status']['metric_reliability']['activity']['grade'],'good')
        self.assertEqual(b['status']['metric_reliability']['feeding']['grade'],'unavailable')
        self.assertEqual(b['status']['missing_sources'],['camera'])
        self.assertEqual(b['causes'][0]['rule_id'],'R1')
        allowed={o['id'] for o in b['recommendation']['options'] if o['available']}
        self.assertEqual(allowed,{'keep','oxygen','stop_feed'})
        self.assertEqual(b['status']['ts'],END.isoformat())

    def test_identical_inputs_reloaded_config_produce_identical_bundles(self):
        a=build_bundle(inputs(),StubModel(),'cage-003','조피볼락',ConfigLoader().snapshot())
        b=build_bundle(inputs(),StubModel(),'cage-003','조피볼락',ConfigLoader().snapshot())
        self.assertEqual(a,b)

    def test_missing_current_cycle_does_not_carry_forward_score_or_cause(self):
        data=inputs()
        data['metrics']=[r for r in data['metrics'] if datetime.fromisoformat(r['ts'])<END-timedelta(minutes=5)]
        b=build_bundle(data,StubModel(),'cage-003','조피볼락',ConfigLoader().snapshot())
        self.assertEqual(b['status']['data_status'],'insufficient_data')
        self.assertIsNone(b['status']['stress_score'])
        self.assertTrue(b['status']['level_held_due_to_missing'])
        self.assertEqual(b['causes'],[])
        self.assertIsNone(b['recommendation'])

    def test_warming_empty_and_stock_missing_states(self):
        data=inputs()
        warm=build_bundle(data,StubModel(True),'cage-003','조피볼락',ConfigLoader().snapshot())
        self.assertEqual(warm['status']['data_status'],'warming_up')
        self.assertIsNone(warm['status']['level'])
        data['stock']=[]
        missing=build_bundle(data,StubModel(),'cage-003','조피볼락',ConfigLoader().snapshot())
        self.assertEqual(missing['recommendation_reason'],'stock_required')
        data['metrics']=[]
        empty=build_bundle(data,StubModel(),'cage-003','조피볼락',ConfigLoader().snapshot())
        self.assertIsNone(empty['status']['stress_score'])
        self.assertEqual(empty['status']['metric_reliability']['activity']['sources'],[])

    def test_disease_and_parasite_first_aid_without_stock_or_economic_winner(self):
        for rid in ('R5','R8'):
            rec,_=recommendation_for({'stock':[]},SimpleNamespace(rule_id=rid),'warning',ConfigLoader().snapshot())
            self.assertEqual(rec['mode'],'first_aid_required')
            self.assertIsNone(rec['recommended_option'])
            self.assertTrue(rec['diagnosis_request_required'])
            self.assertTrue(all(o['estimated_loss_krw'] is None for o in rec['options']))
            self.assertEqual(rec['first_aid']['dispatch_status'],'not_created')

    def test_wrong_model_scope_and_truncated_input_fail(self):
        with self.assertRaises(ValueError):
            build_bundle(inputs(),StubModel(),'wrong-cage','조피볼락',ConfigLoader().snapshot())
        data=inputs(); data['metrics_truncated']=True
        with self.assertRaises(ValueError):
            build_bundle(data,StubModel(),'cage-003','조피볼락',ConfigLoader().snapshot())

    def test_real_db_metric_shapes_through_trained_model_and_cause(self):
        from tests.pipeline_cache import scenario,scores,model
        sim,records,fused,_=scenario('hypoxia')
        ss=scores('hypoxia')
        env={f.ts:f.env for f in fused}
        candidates=[s for s in ss if s.score is not None and s.score>=40 and s.evidence.get('surface_ratio_x',0)>=1.5
                    and env[s.ts-timedelta(minutes=5)]['do']<4]
        self.assertTrue(candidates)
        end=max(candidates,key=lambda s:s.score).ts
        data=inputs(); data['from']=sim.scenario.start.isoformat(); data['to']=end.isoformat()
        data['metrics']=[{**r.model_dump(mode='json'),'cage_id':'3'} for r in records if r.ts<end]
        result=build_bundle(data,model(),'cage-003','조피볼락',ConfigLoader().snapshot())
        self.assertEqual(result['causes'][0]['rule_id'],'R3')
        self.assertEqual(result['recommendation']['mode'],'loss_comparison')


class AnalysisWorkerTest(unittest.TestCase):
    def test_db_read_model_publish_pipeline_uses_dedicated_token_and_stable_key(self):
        requests=[]
        data=inputs()
        def handle(r):
            requests.append(r)
            if r.url.path=='/auth/v1/user':
                return httpx.Response(200,json={'id':'00000000-0000-4000-8000-000000000004','app_metadata':{'role':'analysis','analysis_cage_ids':[3]}})
            if r.url.path.endswith('/analysis_input'):
                return httpx.Response(200,json={'input':data,'input_version':'a'*32})
            if r.url.path.endswith('/publish_analysis'):
                body=json.loads(r.content)
                self.assertEqual(body['p_bundle']['causes'][0]['rule_id'],'R1')
                return httpx.Response(200,json={'persisted':True,'analysis_id':1,'cage_id':3,'ts':body['p_to'],'config_revision':body['p_bundle']['config_revision']})
            raise AssertionError(r.url.path)
        with httpx.Client(transport=httpx.MockTransport(handle)) as client:
            store=AnalysisStore(SupabaseSettings('https://test.supabase.co','public-key'),'worker-token',client)
            for _ in range(2):
                run(store,StubModel(),3,'cage-003','조피볼락',datetime.fromisoformat(data['from']),END,ConfigLoader())
        writes=[r for r in requests if r.url.path.endswith('/publish_analysis')]
        self.assertEqual(json.loads(writes[0].content)['p_request_key'],json.loads(writes[1].content)['p_request_key'])
        self.assertEqual(writes[0].headers['Authorization'],'Bearer worker-token')

    def test_worker_role_scope_and_transport_failure_are_fail_closed(self):
        with httpx.Client(transport=httpx.MockTransport(lambda r:httpx.Response(200,json={'id':'00000000-0000-4000-8000-000000000001','app_metadata':{'role':'edge','analysis_cage_ids':[3]}}))) as client:
            store=AnalysisStore(SupabaseSettings('https://test.supabase.co','key'),'edge-token',client)
            with self.assertRaises(AnalysisError): store.authenticate(3)
        def failed(r):
            raise httpx.ConnectError('private transport details',request=r)
        with httpx.Client(transport=httpx.MockTransport(failed)) as client:
            store=AnalysisStore(SupabaseSettings('https://test.supabase.co','key'),'worker-token',client)
            with self.assertRaises(AnalysisError) as error: store.authenticate(3)
            self.assertNotIn('private transport details',str(error.exception))


class FarmerAnalysisApiTest(unittest.TestCase):
    def setUp(self):
        self.requests=[]
        self.loader=ConfigLoader()
        self.snapshot=self.loader.snapshot()
        b=build_bundle(inputs(),StubModel(),'cage-003','조피볼락',self.snapshot)
        self.status={**b['status'],'analysis_id':1,'config_revision':self.snapshot.revision,'valid_until':(END+timedelta(minutes=55)).isoformat()}
        self.rec={**b['recommendation'],'id':11,'cage_id':3,'ts':END.isoformat(),'analysis_id':1,
                  'config_revision':self.snapshot.revision,'valid_until':(END+timedelta(minutes=55)).isoformat(),
                  'stock_changed':False,'chosen_option':None}
        self.visible=True; self.error=None; self.auth_status=200
        env=patch.dict(os.environ,{'SUPABASE_URL':'https://test.supabase.co','SUPABASE_PUBLISHABLE_KEY':'public-key'})
        env.start(); self.addCleanup(env.stop)
        self.http=httpx.Client(transport=httpx.MockTransport(self.handle)); self.addCleanup(self.http.close)
        app.dependency_overrides[get_farmer_http_client]=lambda:self.http
        app.dependency_overrides[get_analysis_loader]=lambda:self.loader
        self.addCleanup(app.dependency_overrides.clear)
        self.client=TestClient(app); self.addCleanup(self.client.close)
        self.headers={'Authorization':'Bearer owner-token'}

    def handle(self,r):
        self.requests.append(r)
        path=r.url.path
        if path=='/auth/v1/user': return httpx.Response(self.auth_status,json={'id':'00000000-0000-4000-8000-000000000001'})
        if path=='/rest/v1/cage': return httpx.Response(200,json=[{'id':3}] if self.visible else [])
        if self.error: return httpx.Response(400,json={'code':self.error,'message':'private details'})
        if path.endswith('/farmer_status'): return httpx.Response(200,json=self.status)
        if path.endswith('/farmer_recommendation'): return httpx.Response(200,json=self.rec)
        if path.endswith('/choose_recommendation'):
            body=json.loads(r.content)
            return httpx.Response(200,json={'persisted':True,'resource_id':11,'option_id':body['p_option_id'],
                'request_key':body['p_request_key'],'chosen_at':END.isoformat(),'execution_status':'not_executed'})
        raise AssertionError(path)

    def test_actual_status_and_recommendation_same_generation(self):
        status=self.client.get('/cages/3/status',headers=self.headers)
        rec=self.client.get('/cages/3/recommendation',headers=self.headers)
        self.assertEqual(status.status_code,200,status.text)
        self.assertEqual(rec.status_code,200,rec.text)
        self.assertEqual(status.json()['analysis_id'],rec.json()['analysis_id'])
        self.assertEqual(status.json()['metric_reliability']['activity']['grade'],'good')
        self.assertTrue(rec.json()['selectable'])
        self.assertEqual(status.headers['x-data-mode'],'database')
        self.assertEqual(status.headers['cache-control'],'private, no-store')

    def test_no_analysis_or_no_current_recommendation_is_not_a_mock(self):
        self.status={'unavailable_reason':'analysis_not_found'}
        self.assertEqual(self.client.get('/cages/3/status',headers=self.headers).status_code,404)
        self.rec={'unavailable_reason':'stock_required'}
        response=self.client.get('/cages/3/recommendation',headers=self.headers)
        self.assertEqual(response.status_code,404)
        self.assertEqual(response.json()['detail']['code'],'recommendation_stock_required')

    def test_stock_config_and_expired_results_cannot_be_selected_in_ui(self):
        for change in ({'stock_changed':True},{'config_revision':'b'*64},{'valid_until':(END-timedelta(days=1)).isoformat()}):
            saved=copy.deepcopy(self.rec); self.rec.update(change)
            r=self.client.get('/cages/3/recommendation',headers=self.headers)
            self.assertEqual(r.status_code,200,r.text)
            self.assertFalse(r.json()['selectable'])
            self.rec=saved

    def test_choice_saved_alias_normalized_and_not_executed(self):
        response=self.client.post('/recommendations/11/choice',headers=self.headers,json={'option_id':'stop_feeding','request_key':KEY})
        self.assertEqual(response.status_code,200,response.text)
        self.assertTrue(response.json()['persisted'])
        self.assertEqual(response.json()['option_id'],'stop_feed')
        self.assertEqual(response.json()['execution_status'],'not_executed')
        post=self.requests[-1]
        self.assertEqual(post.headers['Authorization'],'Bearer owner-token')
        self.assertEqual(json.loads(post.content)['p_config_revision'],self.snapshot.revision)

    def test_auth_access_and_conflicts_are_sanitized(self):
        self.assertEqual(self.client.get('/cages/3/status').status_code,401)
        self.visible=False
        self.assertEqual(self.client.get('/cages/3/status',headers=self.headers).status_code,404)
        self.visible=True; self.error='42501'
        self.assertEqual(self.client.get('/cages/3/recommendation',headers=self.headers).status_code,403)
        self.error='40001'
        response=self.client.post('/recommendations/11/choice',headers=self.headers,json={'option_id':'keep','request_key':KEY})
        self.assertEqual(response.status_code,409)
        self.assertNotIn('private details',response.text)

    def test_poisoned_content_cross_cage_and_choice_body_fail_closed(self):
        self.status['cause'][0]['name']='약품 5mg'
        self.assertEqual(self.client.get('/cages/3/status',headers=self.headers).status_code,503)
        self.status['cage_id']=4
        self.assertEqual(self.client.get('/cages/3/status',headers=self.headers).status_code,503)
        response=self.client.post('/recommendations/11/choice',headers=self.headers,json={'option_id':'약품 5mg','request_key':KEY})
        self.assertEqual(response.status_code,422)
        self.assertNotIn('5mg',response.text)

    def test_first_aid_read_has_no_economic_winner(self):
        r,_=recommendation_for({},SimpleNamespace(rule_id='R8'),'warning',self.snapshot)
        self.rec.update(r)
        response=self.client.get('/cages/3/recommendation',headers=self.headers)
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()['mode'],'first_aid_required')
        self.assertIsNone(response.json()['recommended_option'])
        self.assertTrue(response.json()['diagnosis_request_required'])


if __name__=='__main__': unittest.main()
