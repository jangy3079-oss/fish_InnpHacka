"""Data/model follow-up regression tests. Run after all implementations are complete."""
import copy
from dataclasses import replace
from datetime import timedelta
import json
import os
import subprocess
import sys
from pathlib import Path
import pickle
import shutil
import tempfile
import unittest
from unittest.mock import patch

import httpx
import yaml

from cause import CauseContext, CauseEngine
from common.config_loader import ConfigError, ConfigLoader, DEFAULT_DIRECTORY
from common.data_config import FixedSnapshot
from fusion.fuse import fuse, params, configuration_signature
from pipeline.export import status_rows
from pipeline.frontend import scenario_frames
from pipeline.run import score_scenario
from scripts.upload_metrics import upload_report, read_records, UploadError
from scripts.prewarm_replay import parse_point, warm, WarmupError
from stress.model import StressModel
from tests.pipeline_cache import model, scenario
from tests.test_cause_extended import observation, run_observations, CLEAR


class BundleTestCase(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        for name in ('thresholds','cause_rules','release_rules','costs','first_aid'):
            shutil.copy(DEFAULT_DIRECTORY/f'{name}.yaml',self.root/f'{name}.yaml')
        self.loader = ConfigLoader(self.root)

    def edit(self,name,change):
        p=self.root/f'{name}.yaml'
        document=yaml.safe_load(p.read_text())
        change(document)
        p.write_text(yaml.safe_dump(document,allow_unicode=True))
        return self.loader.snapshot()


class AnalysisImportIsolationTest(unittest.TestCase):
    def test_invalid_analysis_bundle_does_not_block_raw_api_startup(self):
        with tempfile.TemporaryDirectory() as folder:
            code = """from fastapi.testclient import TestClient
from api.main import app
c = TestClient(app)
assert c.get('/health').status_code == 200
assert c.post('/ingest/metrics', json=[]).status_code == 401
assert c.post('/demo/scenario/heatwave_typhoon?t=0').status_code == 503
print('isolated')
"""
            result = subprocess.run([sys.executable,'-c',code],cwd=Path(__file__).resolve().parents[1],
                env={**os.environ,'ROCKFISH_CONFIG_DIR':folder},capture_output=True,text=True,timeout=60)
            self.assertEqual(result.returncode,0,result.stderr[-1200:])
            self.assertEqual(result.stdout.strip(),'isolated')


class ModelConfigurationTest(BundleTestCase):
    def test_real_model_roundtrip_and_current_nontraining_changes(self):
        m=copy.deepcopy(model())
        path=m.save(self.root/'trained.pkl')
        restored=StressModel.load(path,snapshot=self.loader.snapshot())
        fused=scenario('turbidity_event')[2][:50]
        self.assertEqual([s.score for s in restored.score_series(fused)], [s.score for s in m.score_series(fused)])
        changed=self.edit('thresholds',lambda d:d['levels'].__setitem__('주의',41))
        self.assertIs(m.validate_configuration(changed),changed)
        self.assertEqual(m.training_settings['schema'],1)
        self.assertEqual(len(m.training_revision),64)

    def test_old_metadata_missing_or_wrong_artifact_is_rejected(self):
        for bad in ({'not':'a model'},copy.deepcopy(model())):
            if isinstance(bad,StressModel):
                del bad.training_settings
            p=self.root/'old.pkl'
            with p.open('wb') as f:pickle.dump(bad,f)
            with self.assertRaises(ConfigError):StressModel.load(p,snapshot=self.loader.snapshot())

    def test_changed_fusion_quality_or_daypart_rejects_old_model_even_after_restart(self):
        m=model()
        for key,value in (('fusion',0.49),('quality',0.61)):
            changed=self.edit('thresholds',lambda d:d[key].__setitem__('max_camera_weight' if key=='fusion' else 'camera_only_min',value))
            with self.assertRaises(ConfigError):m.validate_configuration(changed)
            shutil.copy(DEFAULT_DIRECTORY/'thresholds.yaml',self.root/'thresholds.yaml')
        changed=self.edit('thresholds',lambda d:d['baseline'].__setitem__('day_start','07:00'))
        # Simulate a restarted process with new module constants; metadata must still reject old training.
        with patch('stress.model.DAY_START',7):
            with self.assertRaisesRegex(ConfigError,'training metadata'):m.validate_configuration(changed)

    def test_min_windows_change_needs_restart_but_not_retraining(self):
        changed=self.edit('thresholds',lambda d:d['baseline'].__setitem__('min_windows',400))
        with self.assertRaises(ConfigError):model().validate_configuration(changed)
        with patch('stress.model.MIN_BASELINE_WINDOWS',400):
            model().validate_configuration(changed)
            self.assertEqual(model().status,'warming_up')

    def test_fused_configuration_mismatch_is_rejected(self):
        _,records,_,_=scenario('normal_14d')
        altered={**params(), 'w_max':0.1}
        mismatched=fuse(records[:50],configuration=altered)
        with self.assertRaises(ConfigError):StressModel('cage-003',n_estimators=5).fit(mismatched)
        with self.assertRaises(ConfigError):model().score_series(mismatched)

    def test_pinned_snapshot_survives_file_edit_and_invalid_bundle_fails(self):
        pinned=self.loader.snapshot()
        self.edit('thresholds',lambda d:d['fusion'].__setitem__('max_camera_weight',0.4))
        self.assertNotEqual(params(pinned),params(self.loader.snapshot()))
        (self.root/'costs.yaml').write_text('invalid: [')
        with self.assertRaises(ConfigError):self.loader.snapshot()
        self.assertEqual(params(pinned)['w_max'],0.5)


class ObservationMetadataTest(unittest.TestCase):
    def test_pause_exact_sixteen_hours_recovers_previous_observed_time(self):
        # 6 observed hours + exactly16h of blind cycles, then one observed interval.
        pairs=[observation(i) for i in range(1,73)]
        pairs += [observation(i,rubbing=None,quality=0.2) for i in range(73,265)]
        pairs += [observation(265)]
        results=run_observations(pairs)
        self.assertEqual(results[-2].observation_details['rubbing_pause_hours'],16)
        self.assertTrue(any(c.rule_id=='R8' for c in results[-1].estimates))
        self.assertGreater(results[-1].observation_details['rubbing_observed_hours'],6)

    def test_pause_over_sixteen_resets_and_never_counts_blind_cycles(self):
        pairs=[observation(i) for i in range(1,73)]
        pairs += [observation(i,rubbing=None,quality=0.2) for i in range(73,266)]
        pairs += [observation(266)]
        results=run_observations(pairs)
        self.assertEqual(results[-2].observation_details['rubbing_reset_reason'],'pause_expired')
        self.assertAlmostEqual(results[-1].observation_details['rubbing_observed_hours'],1/12)
        self.assertFalse(any(c.rule_id=='R8' for c in results[-1].estimates))

    def test_pending_is_actual_observed_progress_and_unknown_is_not_clear(self):
        result=run_observations([observation(i) for i in range(1,13)])[-1]
        p={v['rule_id']:v for v in result.pending}
        self.assertEqual(p['R8']['progress']['done'],1)
        self.assertEqual(p['R5']['progress']['done'],1)
        r=CauseEngine().evaluate(*observation(),context=CauseContext())
        self.assertTrue(all(p['reason']=='advisory_unknown' for p in r.pending))
        self.assertTrue(all('progress' not in p for p in r.pending))

    def test_export_observed_source_time_not_analysis_time_and_field_passthrough(self):
        _,records,fused,_=scenario('turbidity_event')
        subset=fused[:20]
        scores=model().score_series(subset)
        causes=CauseEngine().evaluate_series(scores,subset)
        rows=status_rows(scores,subset,subset[0].ts,causes,records=records)
        row=rows[-1]
        self.assertLess(row['last_observation_at'],row['ts'])
        self.assertEqual(row['last_seen']['env'],max(r.ts for r in records if r.source.value=='env' and r.ts<scores[-1].ts).strftime('%Y-%m-%dT%H:%M:%SZ'))
        self.assertIn('cause_pending',row)
        self.assertIn('feeding_reset_reason',row)
        self.assertEqual(status_rows(scores,subset,subset[0].ts,causes)[-1]['last_seen'],dict(sonar=None,camera=None,env=None))

    def test_last_seen_does_not_leak_future_when_rows_interleave_cages(self):
        from common import MetricRecord
        a, fa = observation(2, cage='A')
        b, fb = observation(1, cage='B')
        records=[MetricRecord(cage_id='A',ts=fa.ts,source='env',metric='do',value=7,confidence=1),
                 MetricRecord(cage_id='B',ts=fb.ts+timedelta(minutes=1),source='env',metric='do',value=7,confidence=1)]
        result=status_rows([a,b],[fa,fb],fa.ts,records=records)
        self.assertLess(result[1]['last_seen']['env'],result[1]['ts'])

    def test_exported_frame_preserves_r7_observation_and_revision(self):
        from types import SimpleNamespace
        row=dict(t=1,ts='2026-08-05T01:00:00Z',stress_score=30,level='정상',baseline_status='ready',
                 cause=[],evidence={},camera_weight=0,missing_sources=[],cage_id='A',
                 r7_held_due_to_missing=True,feeding_last_observed_at='2026-08-05T00:00:00Z',
                 cause_pending=[{'rule_id':'R8','reason':'observation_gap'}],last_seen={'camera':None},
                 cause_config_revision='a'*64)
        timeline=SimpleNamespace(duration_h=1,status=[row],meta={'t_step_h':1},status_at=lambda t:row,series={})
        f=scenario_frames(timeline,1)['frames'][0]
        for k in ('r7_held_due_to_missing','feeding_last_observed_at','cause_pending','last_seen','cause_config_revision'):
            self.assertEqual(f[k],row[k])


class UploaderReceiptTest(unittest.TestCase):
    def records(self):
        return read_records(Path(__file__).resolve().parents[1]/'scenarios/metric_records.example.json')

    def test_both_ack_versions_and_duplicate_counts(self):
        def handler(request):
            n=len(json.loads(request.content))
            return httpx.Response(201,json={'persisted':True,'received_count':n,'inserted_count':0,'duplicate_count':n})
        with httpx.Client(transport=httpx.MockTransport(handler)) as c:
            report=upload_report(self.records(),c,'http://localhost','token',4)
        self.assertEqual((report.confirmed_count,report.inserted_count,report.duplicate_count),(10,0,10))

    def test_invalid_counts_and_nonintegers_fail_closed(self):
        for fields in ({'inserted_count':True,'duplicate_count':9},{'inserted_count':1,'duplicate_count':8},
                       {'inserted_count':0,'duplicate_count':-1},{'inserted_count':0}):
            def handler(request):
                return httpx.Response(201,json={'persisted':True,'received_count':10,**fields})
            with httpx.Client(transport=httpx.MockTransport(handler)) as c:
                with self.assertRaises(UploadError):upload_report(self.records(),c,'http://localhost','token')


class WarmupToolTest(unittest.TestCase):
    def test_points_and_only_synthetic_responses_are_accepted(self):
        for v in ('../real:1','x:nan','x:-1','x:inf'):
            with self.assertRaises(WarmupError):parse_point(v)
        self.assertEqual(parse_point('feeding_drop:0.5'),('feeding_drop',0.5))
        calls=[]
        def handler(req):
            calls.append(req)
            return httpx.Response(200,json={'scenario':'feeding_drop','t':0.5,'effective_t':0.5,'simulated':True,
                'persisted':False,'cause_source':'cause_engine','config_revision':'a'*64,'scenario_revision':'b'*64,
                'status':{'model_version':'fixture'}})
        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            r=warm([('feeding_drop',0.5)],client,'http://localhost')
        self.assertEqual(r['measurements'][0]['model_version'],'fixture')
        self.assertEqual(calls[0].url.path,'/demo/scenario/feeding_drop')

    def test_failure_is_not_retried(self):
        calls=[]
        def handler(req):
            calls.append(req)
            return httpx.Response(503)
        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            with self.assertRaises(WarmupError):warm([('feeding_drop',0)],client,'http://localhost')
        self.assertEqual(len(calls),1)
