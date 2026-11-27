import copy
import json
import os
import shutil
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import httpx
import yaml
from fastapi.testclient import TestClient

from api.diagnosis import build_packet, get_diagnosis_loader
from api.farmer_data import get_farmer_http_client
from api.main import app
from common.config_loader import ConfigError, ConfigLoader, DEFAULT_DIRECTORY
from decision.first_aid import UnsafeFirstAid, ensure_safe, reviewed_guide

KEY = '11111111-1111-4111-8111-111111111111'
NOW = datetime.now(timezone.utc).replace(microsecond=0)


def context():
    return {"cage_id": 3, "cause_ts": NOW.isoformat(), "lookback_hours": 72,
            "causes": [{"id": 1, "ts": NOW.isoformat(), "cause": "질병 의심", "rule_id": "R5", "probability": 1.0}],
            "metrics": [{"cage_id": "3", "ts": NOW.isoformat(), "source": "sonar", "metric": "activity", "value": 0.4, "confidence": 0.9}],
            "stress": [{"ts": (NOW-timedelta(minutes=10)).isoformat(), "score": 30, "baseline_status": "ready"},
                       {"ts": (NOW-timedelta(minutes=5)).isoformat(), "score": 60, "baseline_status": "ready"},
                       {"ts": NOW.isoformat(), "score": 65, "baseline_status": "ready"}],
            "metrics_truncated": False, "stress_truncated": False}


class FirstAidTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        shutil.copytree(DEFAULT_DIRECTORY, self.root / 'config')
        self.loader = ConfigLoader(self.root / 'config')

    def change(self, mutate):
        path = self.root / 'config/first_aid.yaml'
        config = yaml.safe_load(path.read_text(encoding='utf-8'))
        mutate(config)
        path.write_text(yaml.safe_dump(config, allow_unicode=True), encoding='utf-8')

    def test_reviewed_actions_only_and_no_sent_claim(self):
        for rid in ('R5', 'R8'):
            guide = reviewed_guide(rid, self.loader.snapshot())
            self.assertEqual({a['id'] for a in guide['actions']}, {'feed_reduction', 'density_control', 'dead_fish_removal'})
            self.assertEqual({a['id'] for a in guide['withheld_actions']}, {'tool_separation', 'oxygen_boost'})
            self.assertNotIn('보냈습니다', guide['message'])
            ensure_safe(guide, self.loader.snapshot())

    def test_dictionary_and_obfuscated_dose_cases_are_blocked(self):
        snapshot = self.loader.snapshot()
        cfg = snapshot['first_aid']['blocked_terms']
        cases = list(cfg['categories']) + list(cfg['ingredients']) + ['５ ｍｇ', '5 mg/kg', '20 PPM', '2 g', '3 μg', '1캡슐', '5 밀리그램', '옥시\u200b테트라사이클린', '약 품']
        for text in cases:
            with self.subTest(text=text), self.assertRaises(UnsafeFirstAid):
                ensure_safe({'nested': [{'text': text}]}, snapshot)

    def test_poisoned_or_unmatched_config_text_is_not_emitted(self):
        self.change(lambda d: d['common_first_aid'][0].update(text='임의의 문구'))
        with self.assertRaises(ConfigError):
            reviewed_guide('R5', self.loader.snapshot())
        self.change(lambda d: d['common_first_aid'][0].update(name='약품 안내'))
        with self.assertRaises(ConfigError):
            reviewed_guide('R5', self.loader.snapshot())

    def test_source_path_escape_and_unavailable_docs_fail_closed(self):
        self.change(lambda d: d['common_first_aid'][0].update(source='../README.md'))
        with self.assertRaises(ConfigError):
            reviewed_guide('R5', self.loader.snapshot())

    def test_unsafe_label_is_blocked_even_if_quote_is_valid(self):
        self.change(lambda d: d['common_first_aid'][0].update(name='약품 안내'))
        with self.assertRaises(UnsafeFirstAid):
            reviewed_guide('R5', self.loader.snapshot())

    def test_opaque_ids_are_not_dose_text_but_unsafe_revision_is_rejected(self):
        ensure_safe({'config_revision': '5cc'+'a'*61, 'request_key': KEY}, self.loader.snapshot())
        with self.assertRaises(UnsafeFirstAid):
            ensure_safe({'config_revision': '5 cc'}, self.loader.snapshot())

    def test_disease_handoff_also_checks_echoed_input(self):
        from decision import LossCalculator
        from tests.test_decision import request
        with self.assertRaises(UnsafeFirstAid):
            LossCalculator(self.loader).compare(request(primary_rule_id='R5', unavailable_actions={'keep': '약품 5mg'}))

    def test_packet_contains_observed_onset_graph_provenance_and_cause(self):
        packet = build_packet(context(), 3, self.loader.snapshot())
        self.assertEqual(packet['onset_at'], (NOW-timedelta(minutes=5)).isoformat())
        self.assertFalse(packet['onset_censored'])
        self.assertEqual(packet['primary_cause_id'], 1)
        self.assertEqual(packet['graphs']['activity'][0]['source'], 'sonar')
        self.assertEqual(packet['suspected_causes'][0]['probability'], 1)
        ensure_safe(packet, self.loader.snapshot())

    def test_missing_history_or_gap_marks_observed_start_censored(self):
        data = context()
        data['stress'] = [data['stress'][-1]]
        p = build_packet(data, 3, self.loader.snapshot())
        self.assertEqual(p['onset_at'], NOW.isoformat())
        self.assertTrue(p['onset_censored'])


class DiagnosisApiTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name) / 'config'
        shutil.copytree(DEFAULT_DIRECTORY, self.folder)
        self.loader = ConfigLoader(self.folder)
        self.requests = []
        self.data = context()
        self.replay = False
        self.visible = True
        self.auth_status = 200
        self.rpc_error = None
        self.create_error = None
        self.query_error = None
        self.summary = {'persisted':True,'id':10,'cage_id':3,'request_key':KEY,'primary_cause_id':1,
                        'created_at':NOW.isoformat(),'observed_at':NOW.isoformat(),
                        'status':'pending','dispatch_status':'pending'}
        self.user = {'id': '00000000-0000-4000-8000-000000000001'}
        env = patch.dict(os.environ, {'SUPABASE_URL': 'https://test.supabase.co', 'SUPABASE_PUBLISHABLE_KEY': 'test-publishable'})
        env.start()
        self.addCleanup(env.stop)
        self.http = httpx.Client(transport=httpx.MockTransport(self.handle))
        self.addCleanup(self.http.close)
        app.dependency_overrides[get_farmer_http_client] = lambda: self.http
        app.dependency_overrides[get_diagnosis_loader] = lambda: self.loader
        self.addCleanup(app.dependency_overrides.clear)
        self.client = TestClient(app)
        self.addCleanup(self.client.close)
        self.headers = {'Authorization': 'Bearer owner-access-token'}

    def receipt(self):
        return {'persisted': True, 'id': 10, 'cage_id': 3, 'request_key': KEY, 'primary_cause_id': 1,
                'created_at': NOW.isoformat(), 'status': 'pending', 'config_revision': 'a'*64,
                'context_version': 'digest', 'context': self.data}

    def handle(self, request):
        self.requests.append(request)
        path = request.url.path
        if path == '/auth/v1/user':
            return httpx.Response(self.auth_status, json=self.user)
        if path == '/rest/v1/cage':
            return httpx.Response(200, json=[{'id': 3}] if self.visible else [])
        if path == '/rest/v1/rpc/diagnosis_context':
            if self.rpc_error:
                return httpx.Response(400, json={'code': self.rpc_error, 'message': 'private details'})
            return httpx.Response(200, json=self.receipt() if self.replay else {'persisted': False, 'context_version': 'digest', 'context': self.data})
        if path == '/rest/v1/rpc/create_diagnosis_request':
            if self.create_error:
                return httpx.Response(400, json={'code': self.create_error, 'message': 'private details'})
            return httpx.Response(200, json=self.receipt())
        if path == '/rest/v1/rpc/read_diagnosis_request':
            if self.query_error:
                return httpx.Response(400,json={'code':self.query_error,'message':'private details'})
            return httpx.Response(200,content=json.dumps(self.summary),headers={'content-type':'application/json'})
        raise AssertionError(path)

    def post(self, body=None):
        return self.client.post('/cages/3/diagnosis-request', headers=self.headers, json={'request_key': KEY} if body is None else body)

    def test_owner_request_saved_with_reviewed_actions_and_graphs(self):
        response = self.post()
        self.assertEqual(response.status_code, 201, response.text)
        result = response.json()
        self.assertTrue(result['persisted'])
        self.assertEqual(result['dispatch_status'], 'pending')
        self.assertEqual(result['packet']['primary_cause_id'], 1)
        self.assertTrue(result['packet']['graphs']['activity'])
        ensure_safe(result, self.loader.snapshot())
        self.assertEqual(response.headers['x-data-mode'], 'database')
        self.assertEqual(response.headers['cache-control'], 'private, no-store')
        self.assertIn('Authorization', response.headers['vary'])
        writes = [r for r in self.requests if r.url.path.endswith('/create_diagnosis_request')]
        self.assertEqual(len(writes), 1)
        body = json.loads(writes[0].content)
        self.assertNotIn('context', body)
        self.assertNotIn('packet', body)
        self.assertEqual(body['p_context_version'], 'digest')
        self.assertEqual(writes[0].headers['Authorization'], 'Bearer owner-access-token')

    def test_get_summary_is_read_only_and_does_not_need_current_config_or_cause(self):
        with patch.object(self.loader,'snapshot',side_effect=ConfigError('bad config')):
            for suffix in ('','?request_key='+KEY):
                result=self.client.get('/cages/3/diagnosis-request'+suffix,headers=self.headers)
                self.assertEqual(result.status_code,200,result.text)
                from api.schemas import DiagnosisSummary
                self.assertEqual(result.json(),DiagnosisSummary.model_validate(self.summary).model_dump(mode='json'))
                self.assertEqual(result.headers['cache-control'],'private, no-store')
                self.assertEqual(result.headers['x-data-mode'],'database')
        self.assertTrue(all(r.url.path in ('/auth/v1/user','/rest/v1/cage','/rest/v1/rpc/read_diagnosis_request') for r in self.requests))
        self.assertNotIn('packet',result.json())

    def test_get_missing_or_inaccessible_requests_and_owner_denial(self):
        self.summary=None
        self.assertEqual(self.client.get('/cages/3/diagnosis-request',headers=self.headers).status_code,404)
        self.query_error='42501'
        self.assertEqual(self.client.get('/cages/3/diagnosis-request',headers=self.headers).status_code,403)
        self.query_error=None; self.visible=False
        self.assertEqual(self.client.get('/cages/3/diagnosis-request',headers=self.headers).status_code,404)

    def test_get_rejects_malformed_keys_and_unscoped_receipts(self):
        self.assertEqual(self.client.get('/cages/3/diagnosis-request',headers={}).status_code,401)
        bad=self.client.get('/cages/3/diagnosis-request?request_key=private-input',headers=self.headers)
        self.assertEqual(bad.status_code,422)
        self.assertNotIn('private-input',bad.text)
        for change in ({'cage_id':4},{'status':'sent'},{'packet':{'private':True}}):
            original=self.summary.copy(); self.summary.update(change)
            self.assertEqual(self.client.get('/cages/3/diagnosis-request',headers=self.headers).status_code,503)
            self.summary=original
        self.summary['request_key']='22222222-2222-4222-8222-222222222222'
        self.assertEqual(self.client.get('/cages/3/diagnosis-request?request_key='+KEY,headers=self.headers).status_code,503)

    def test_replay_uses_original_snapshot_without_second_write(self):
        self.replay = True
        r = self.post()
        self.assertEqual(r.status_code, 201, r.text)
        self.assertFalse(any(x.url.path.endswith('/create_diagnosis_request') for x in self.requests))
        self.assertEqual(r.json()['creation_config_revision'], 'a'*64)

    def test_parasite_primary_has_common_reviewed_flow(self):
        self.data['causes'][0].update(rule_id='R8', cause='기생충 의심')
        r = self.post()
        self.assertEqual(r.status_code, 201, r.text)
        self.assertEqual(r.json()['packet']['first_aid']['rule_id'], 'R8')
        self.assertNotIn('보냈습니다', r.text)

    def test_invalid_distribution_and_future_point_fail_before_write(self):
        original = context()
        for mutate in (lambda d: d['causes'][0].update(probability=0.5),
                       lambda d: d['metrics'][0].update(ts=(NOW+timedelta(minutes=5)).isoformat())):
            self.data = copy.deepcopy(original)
            mutate(self.data)
            self.assertEqual(self.post().status_code, 503)
        self.assertFalse(any(x.url.path.endswith('/create_diagnosis_request') for x in self.requests))

    def test_missing_invalid_and_anonymous_auth_fail_before_write(self):
        self.assertEqual(self.client.post('/cages/3/diagnosis-request', json={'request_key': KEY}).status_code, 401)
        self.auth_status = 401
        self.assertEqual(self.post().status_code, 401)
        self.auth_status = 200
        self.user['is_anonymous'] = True
        self.assertEqual(self.post().status_code, 401)
        self.assertFalse(any(x.url.path.endswith('/create_diagnosis_request') for x in self.requests))

    def test_inaccessible_cage_and_owner_rpc_denial(self):
        self.visible = False
        self.assertEqual(self.post().status_code, 404)
        self.visible = True
        self.rpc_error = '42501'
        self.assertEqual(self.post().status_code, 403)

    def test_invalid_dose_or_freeform_input_is_not_echoed(self):
        for body in ({'request_key': '옥시테트라사이클린 5mg'}, {'request_key': KEY, 'note': '5 mg'}):
            r = self.post(body)
            self.assertEqual(r.status_code, 422)
            ensure_safe(r.json(), self.loader.snapshot())
            self.assertNotIn('5mg', r.text)

    def test_missing_graph_current_stress_or_non_family_prevents_insert(self):
        original = context()
        for mutate in (lambda d: d.update(metrics=[]), lambda d: d.update(stress=[]),
                       lambda d: d['causes'][0].update(rule_id='R1')):
            self.data = copy.deepcopy(original)
            mutate(self.data)
            self.assertEqual(self.post().status_code, 409)
        self.assertFalse(any(x.url.path.endswith('/create_diagnosis_request') for x in self.requests))

    def test_unsafe_db_content_is_blocked_before_insert(self):
        self.data['causes'][0]['cause'] = '약품 5mg'
        r = self.post()
        self.assertEqual(r.status_code, 503)
        ensure_safe(r.json(), self.loader.snapshot())
        self.assertFalse(any(x.url.path.endswith('/create_diagnosis_request') for x in self.requests))

    def test_conflict_and_upstream_errors_are_sanitized_without_retry(self):
        self.create_error = '40001'
        r = self.post()
        self.assertEqual(r.status_code, 409)
        self.assertNotIn('private details', r.text)
        self.assertEqual(sum(x.url.path.endswith('/create_diagnosis_request') for x in self.requests), 1)

    def test_invalid_or_cross_cage_graph_is_not_returned(self):
        self.data['metrics'][0]['cage_id'] = '4'
        r = self.post()
        self.assertEqual(r.status_code, 503)
        self.assertFalse(any(x.url.path.endswith('/create_diagnosis_request') for x in self.requests))


if __name__ == '__main__':
    unittest.main()
