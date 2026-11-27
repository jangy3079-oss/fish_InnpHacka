import copy
from datetime import datetime,timedelta,timezone
from pathlib import Path
import shutil
import tempfile
import unittest

import yaml
from fastapi.testclient import TestClient

from api.main import app
from cause import CauseEngine
from common.config_loader import ConfigLoader,ConfigError,DEFAULT_DIRECTORY
from fusion.fuse import FusedFeatures
from pipeline.analysis import recommendation_for,build_bundle
from stress.model import StressScore

T = datetime(2026,8,10,tzinfo=timezone.utc)


def pair(i=0,**kw):
    ts=T+timedelta(minutes=5*i)
    ev={'feeding_change':kw.get('feeding',-0.2),'activity_change':kw.get('activity',0.01),
        'lower_layers_ratio_x':kw.get('ratio',1.0),'surface_ratio_x':kw.get('surface_ratio',1.0)}
    s=StressScore(kw.get('cage','cage-003'),ts,kw.get('score',30),kw.get('baseline','ready'),'test-m5',evidence=ev)
    f=FusedFeatures(s.cage_id,ts-timedelta(minutes=5),{},
        {'temp_surface':kw.get('surface',23.5),'temp_mid':20,'do':kw.get('oxygen',7)},kw.get('quality',0.8),0)
    return s,f


class FeedingRuleTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.config=Path(self.tmp.name)/'config'
        shutil.copytree(DEFAULT_DIRECTORY,self.config)
        self.loader=ConfigLoader(self.config)

    def mutate(self,name,fn):
        path=self.config/(name+'.yaml')
        data=yaml.safe_load(path.read_text(encoding='utf-8'))
        fn(data)
        path.write_text(yaml.safe_dump(data,allow_unicode=True),encoding='utf-8')

    def test_low_score_exception_and_exact_observation_boundaries(self):
        for kw in ({},{'feeding':-0.15},{'surface':23},{'quality':0.6},{'oxygen':4},{'score':0}):
            with self.subTest(kw=kw):
                r=CauseEngine(self.loader).evaluate(*pair(**kw))
                self.assertEqual([c.rule_id for c in r.estimates],['R7'])
                self.assertIsNone(r.suppression)
                self.assertFalse(r.immediate_danger)
                self.assertFalse(r.estimates[0].evidence['feeding_held_due_to_missing'])
                self.assertEqual(r.estimates[0].probability,1)

    def test_required_conditions_and_no_initial_hold_from_missing_evidence(self):
        for kw in ({'feeding':-0.149},{'feeding':None},{'activity':0.2},{'activity':-0.2},
                   {'activity':None},{'ratio':1.5},{'ratio':None},{'surface':22.99},
                   {'surface':25},{'surface':None},{'oxygen':3.99},{'oxygen':None},
                   {'quality':0.599},{'quality':None},{'score':None},{'baseline':'warming_up'}):
            with self.subTest(kw=kw):
                self.assertFalse(CauseEngine(self.loader).evaluate(*pair(**kw)).estimates)

    def test_gate_exception_is_read_from_yaml(self):
        self.mutate('thresholds',lambda d:d.update(cause_min_stress_exceptions=[]))
        r=CauseEngine(self.loader).evaluate(*pair())
        self.assertFalse(r.estimates)
        self.assertEqual(r.suppression,'low_stress')

    def test_night_hold_has_original_timestamp_and_no_fabricated_feeding(self):
        e=CauseEngine(self.loader)
        e.evaluate(*pair())
        r=e.evaluate(*pair(1,feeding=None,quality=0.35))
        self.assertEqual([c.rule_id for c in r.estimates],['R7'])
        ev=r.estimates[0].evidence
        self.assertTrue(ev['feeding_held_due_to_missing'])
        self.assertIsNone(ev['feeding_change'])
        self.assertEqual(ev['feeding_change_last_observed'],-0.2)
        self.assertEqual(ev['feeding_last_observed_at'],T.isoformat())

    def test_observed_recovery_and_other_abnormality_clear_hold(self):
        for kw in ({'feeding':-0.01},{'activity':0.3},{'ratio':1.6},{'surface':25},
                   {'oxygen':3},{'score':None},{'baseline':'warming_up'}):
            with self.subTest(kw=kw):
                e=CauseEngine(self.loader)
                e.evaluate(*pair())
                e.evaluate(*pair(1,**kw))
                self.assertFalse(e.evaluate(*pair(2,feeding=None,quality=None)).estimates)
        e=CauseEngine(self.loader)
        e.evaluate(*pair())
        r=e.evaluate(*pair(1,score=70,surface=26,activity=-0.4,ratio=1.8))
        self.assertEqual([c.rule_id for c in r.estimates],['R1'])

    def test_gap_cage_and_revision_do_not_share_hold(self):
        e=CauseEngine(self.loader)
        e.evaluate(*pair())
        self.assertFalse(e.evaluate(*pair(1,cage='other',feeding=None)).estimates)
        self.assertFalse(e.evaluate(*pair(2,feeding=None)).estimates)
        e.evaluate(*pair(3))
        self.mutate('cause_rules',lambda d:d['rules']['R7']['behavior'].update(feeding_change_max=-0.16))
        self.assertFalse(e.evaluate(*pair(4,feeding=None)).estimates)

    def test_hold_expiry_inclusive_boundary(self):
        self.mutate('cause_rules',lambda d:d['rules']['R7']['behavior'].update(max_hold_hours=0.25))
        e=CauseEngine(self.loader)
        e.evaluate(*pair())
        for i in (1,2,3):
            self.assertTrue(e.evaluate(*pair(i,feeding=None)).estimates)
        self.assertFalse(e.evaluate(*pair(4,feeding=None)).estimates)

    def test_batch_validation_is_atomic_for_new_evidence(self):
        e=CauseEngine(self.loader)
        e.evaluate(*pair())
        s1,f1=pair(1,feeding=-0.01)
        s2,f2=pair(2,feeding=float('nan'))
        with self.assertRaises(ValueError):
            e.evaluate_series([s1,s2],[f1,f2])
        self.assertTrue(e.evaluate(*pair(1,feeding=None)).estimates)

    def test_invalid_r7_schema_is_rejected(self):
        for value in (0,True,-1.1,'-0.15'):
            self.mutate('cause_rules',lambda d:d['rules']['R7']['behavior'].update(feeding_change_max=value))
            with self.assertRaises(ConfigError):
                self.loader.snapshot()

    def test_recommendation_separates_rule_guidance_from_loss_minimum(self):
        snapshot=self.loader.snapshot()
        primary=CauseEngine(self.loader).evaluate(*pair()).estimates[0]
        context={'cage_id':3,'farm_id':1,'sea_area_id':1,'species':'조피볼락',
            'stock':[{'size_group':'mid','count':1000,'avg_weight_kg':0.3,'insured':False}]}
        rec,reason=recommendation_for(context,primary,'normal',snapshot)
        self.assertIsNone(reason)
        self.assertEqual(rec['recommended_option'],'reduce_feed')
        self.assertEqual(rec['details']['recommendation_basis'],'R7_feeding_management')
        self.assertEqual(rec['details']['lowest_loss_option'],'keep')
        self.assertEqual({o['id'] for o in rec['options'] if o['available']},{'keep','reduce_feed'})


class FeedingScenarioTest(unittest.TestCase):
    def test_real_m5_feeding_drop_and_normal_controls(self):
        from tests.pipeline_cache import scenario,scores
        for name in ('feeding_drop','normal_14d','turbidity_event','heatwave_typhoon'):
            _,_,fused,_=scenario(name)
            ss=copy.deepcopy(scores(name))
            results=CauseEngine().evaluate_series(ss,fused)
            r7=[r for r in results if r.estimates and r.estimates[0].rule_id=='R7']
            if name in ('normal_14d','turbidity_event'):
                self.assertFalse(r7,name)
            else:
                self.assertTrue(r7,name)
                self.assertTrue(any(c.evidence['stress_score']<40 for r in r7 for c in r.estimates),name)
                self.assertTrue(any(c.evidence['feeding_held_due_to_missing'] for r in r7 for c in r.estimates),name)
            if name=='heatwave_typhoon':
                first_r1=next(r.ts for r in results if any(c.rule_id=='R1' for c in r.estimates))
                self.assertLess(r7[0].ts,first_r1)

    def test_replay_http_feeding_scenario_returns_non_alert_management(self):
        from api.demo import get_replay_engine
        from pipeline.replay import ReplayEngine
        engine=ReplayEngine()
        self.addCleanup(app.dependency_overrides.pop,get_replay_engine,None)
        app.dependency_overrides[get_replay_engine]=lambda:engine
        response=TestClient(app).post('/demo/scenario/feeding_drop?t=48')
        self.assertEqual(response.status_code,200,response.text)
        r=response.json()
        self.assertEqual(r['status']['cause'][0]['rule_id'],'R7')
        self.assertEqual(r['status']['level'],'정상')
        self.assertEqual(r['recommendation']['recommended_option'],'reduce_feed')
        self.assertFalse(r['recommendation']['selectable'])
        self.assertFalse(r['persisted'])

    def test_real_metric_db_bundle_keeps_night_provenance(self):
        from tests.pipeline_cache import scenario,model
        sim,records,_,_=scenario('feeding_drop')
        end=sim.scenario.start+timedelta(hours=48)
        context={'cage_id':3,'farm_id':1,'sea_area_id':1,'species':'조피볼락',
            'from':sim.scenario.start,'to':end,'metrics_truncated':False,
            'stock':[{'size_group':'mid','count':1000,'avg_weight_kg':0.3,'insured':False}],
            'metrics':[r.model_copy(update={'cage_id':'3'}).model_dump(mode='json') for r in records if r.ts<end]}
        b=build_bundle(context,model(),'cage-003','조피볼락',ConfigLoader().snapshot())
        self.assertEqual(b['status']['cause'][0]['rule_id'],'R7')
        self.assertTrue(b['status']['r7_held_due_to_missing'])
        self.assertIsNotNone(b['status']['feeding_last_observed_at'])
        self.assertEqual(b['recommendation']['recommended_option'],'reduce_feed')


if __name__=='__main__':
    unittest.main()
