from datetime import datetime
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from api.demo import get_replay_engine
from api.main import app
from api.schemas import DemoResult
from pipeline.replay import ReplayEngine, ReplayRangeError, ROOT


class ReplayTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = get_replay_engine()

    def result(self,name,t):
        return DemoResult.model_validate(self.engine.replay(name,t)).model_dump(mode='json')

    def test_zero_and_incomplete_window_do_not_show_future_or_fake_score(self):
        r = self.result('heatwave_typhoon',0)
        self.assertIsNone(r['status']['stress_score'])
        self.assertIsNone(r['status']['last_observation_at'])
        self.assertEqual(r['status']['data_status'],'insufficient_data')
        self.assertTrue(all(not p for p in r['timeseries'].values()))
        self.assertIsNone(r['recommendation'])
        r = self.result('heatwave_typhoon',0.5)
        self.assertIsNone(r['status']['stress_score'])
        self.assertFalse(r['status']['cause'])
        self.assertEqual(r['effective_t'],0.5)

    def test_heatwave_r1_then_typhoon_r2_use_real_rules_and_loss_calculator(self):
        refuge = self.result('heatwave_typhoon',108)
        loss = self.result('heatwave_typhoon',144)
        self.assertEqual(refuge['status']['cause'][0]['rule_id'],'R1')
        self.assertEqual(loss['status']['cause'][0]['rule_id'],'R2')
        self.assertEqual(loss['status']['level'],'위험')
        rec = loss['recommendation']
        self.assertEqual(rec['mode'],'loss_comparison')
        self.assertEqual(len(rec['options']),9)
        self.assertTrue(any(o['estimated_loss_krw'] is not None for o in rec['options']))
        self.assertFalse(rec['selectable'])
        self.assertEqual(rec['id'],0)
        self.assertEqual(rec['details']['config_revision'],loss['config_revision'])
        self.assertFalse(next(o for o in rec['options'] if o['id']=='release')['available'])

    def test_turbidity_gates_camera_without_fabricating_cause(self):
        r = self.result('turbidity_event',30)
        self.assertEqual(r['status']['camera_weight'],0)
        self.assertEqual(r['status']['level'],'정상')
        self.assertFalse(r['status']['cause'])
        self.assertIsNone(r['recommendation'])
        self.assertEqual(r['recommendation_reason'],'no_current_cause')
        self.assertEqual(r['status']['metric_reliability']['activity']['sources'],['sonar'])

    def test_disease_and_parasite_are_reviewed_non_economic_guidance(self):
        for name,t,rid in [('disease_suspect',144,'R5'),('parasite_suspect',38.5,'R8')]:
            with self.subTest(name=name):
                r = self.result(name,t)
                self.assertEqual(r['status']['cause'][0]['rule_id'],rid)
                rec = r['recommendation']
                self.assertEqual(rec['mode'],'first_aid_required')
                self.assertIsNone(rec['recommended_option'])
                self.assertTrue(all(o['estimated_loss_krw'] is None for o in rec['options']))
                self.assertTrue(rec['diagnosis_request_required'])
                self.assertEqual(rec['first_aid']['dispatch_status'],'not_created')

    def test_completed_cycle_time_and_prefix_hour_averages(self):
        r = self.result('heatwave_typhoon',1.04)
        exact = self.result('heatwave_typhoon',1)
        self.assertEqual(r['effective_t'],1)
        self.assertEqual(r['status'],exact['status'])
        cutoff = datetime.fromisoformat(r['status']['ts'])
        self.assertTrue(all(datetime.fromisoformat(p['ts'])<cutoff for pts in r['timeseries'].values() for p in pts))
        # A late query must not change a previous time, nor change its partially filled hour average.
        before = self.result('heatwave_typhoon',24.5)
        self.result('heatwave_typhoon',192)
        after = self.result('heatwave_typhoon',24.5)
        self.assertEqual(before,after)
        self.assertEqual(before['status']['ts'],before['recommendation']['ts'] if before['recommendation'] else before['status']['ts'])

    def test_cache_is_bounded_and_results_are_independent(self):
        before = self.result('heatwave_typhoon',108)
        r = self.engine.replay('heatwave_typhoon',108)
        r['status']['cause'].clear()
        r['recommendation']['options'].clear()
        self.assertEqual(before,self.result('heatwave_typhoon',108))
        self.assertLessEqual(len(self.engine._prepared),4)
        self.assertLessEqual(len(self.engine._responses),32)

    def test_rejects_names_nonfinite_and_out_of_range_before_simulation(self):
        engine = ReplayEngine()
        with patch.object(engine,'_records',side_effect=AssertionError('should not simulate')):
            for name,t in [('../normal_14d',0),('heatwave_typhoon',-1),('heatwave_typhoon',float('nan')),
                           ('heatwave_typhoon',float('inf')),('heatwave_typhoon',192.1),('disease_suspect',145)]:
                with self.subTest(name=name,t=t), self.assertRaises(ReplayRangeError):
                    engine.replay(name,t)

    def test_synthetic_settings_change_invalidates_replay_not_baseline(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'demo.yaml'
            original = (ROOT/'config/demo.yaml').read_text(encoding='utf-8')
            path.write_text(original,encoding='utf-8')
            engine = ReplayEngine(settings_path=path)
            engine._model,engine._model_key = self.engine._model,self.engine._model_key
            with patch.object(engine,'_records',wraps=engine._records) as generate:
                a = engine.replay('heatwave_typhoon',144)
                path.write_text(original.replace('count: 1000','count: 2000'),encoding='utf-8')
                b = engine.replay('heatwave_typhoon',144)
                self.assertNotEqual(a['scenario_revision'],b['scenario_revision'])
                self.assertEqual(a['status'],b['status'])
                self.assertNotEqual(a['recommendation']['options'],b['recommendation']['options'])
                self.assertEqual(generate.call_count,2)

    def test_no_future_fused_input_reaches_model_or_cause(self):
        self.engine.replay('heatwave_typhoon',2)
        self.engine._responses.clear()
        model = self.engine._model
        with patch.object(model,'score_series',wraps=model.score_series) as score:
            self.result('heatwave_typhoon',1.5)
            passed = score.call_args.args[0]
            self.assertEqual(len(passed),18)
            self.assertEqual((passed[-1].ts-passed[0].ts).total_seconds(),17*300)


class ReplayHttpTest(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def tearDown(self):
        app.dependency_overrides.pop(get_replay_engine,None)

    def test_http_contract_is_synthetic_private_store_not_contacted(self):
        # Use the real existing engine; any accidental DB/HTTP request is an error.
        engine = get_replay_engine()
        app.dependency_overrides[get_replay_engine] = lambda: engine
        with patch('httpx.Client.request',side_effect=AssertionError('No outbound request')):
            # The adapter runs in process; all outbound HTTP would fail this check.
            from api.demo import replay
            result = replay(engine,'heatwave_typhoon',144)
            self.assertFalse(result.persisted)
        response = self.client.post('/demo/scenario/heatwave_typhoon?t=144')
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.headers['x-data-mode'],'simulation')
        self.assertEqual(response.headers['cache-control'],'no-store')
        self.assertFalse(response.json()['persisted'])
        self.assertFalse(response.json()['mock'])
        self.assertTrue(response.json()['simulated'])

    def test_errors_are_sanitized_and_not_mock_success(self):
        app.dependency_overrides[get_replay_engine] = lambda: ReplayEngine(settings_path=Path('missing-secret-file.yaml'))
        r = self.client.post('/demo/scenario/heatwave_typhoon?t=4')
        self.assertEqual(r.status_code,503)
        self.assertNotIn('missing-secret',r.text)
        self.assertEqual(r.json()['detail']['code'],'replay_unavailable')
        app.dependency_overrides[get_replay_engine] = lambda: ReplayEngine()
        for suffix in ['?t=193','?t=inf','?t=-1','?t=nan']:
            self.assertEqual(self.client.post('/demo/scenario/heatwave_typhoon'+suffix).status_code,422)
        self.assertEqual(self.client.post('/demo/scenario/unknown').status_code,422)


if __name__=='__main__':
    unittest.main()
