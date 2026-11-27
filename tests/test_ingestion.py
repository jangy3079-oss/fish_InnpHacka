import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

from api.ingestion import IngestSettings, SupabaseMetricStore, get_metric_store
from api.main import app
from scripts.upload_metrics import UploadError, main as upload_main, read_records, upload, upload_counts


class IngestionTests(unittest.TestCase):
    def setUp(self):
        self.requests = []
        self.user = {"id": "00000000-0000-4000-8000-000000000004",
                     "app_metadata": {"role": "edge", "ingest_cage_ids": [3]}}
        self.auth_status, self.db_status, self.db_body = 200, 200, None
        self.settings = IngestSettings("https://test.supabase.co", "test-publishable", {"cage-001": 3})
        self.upstream = httpx.Client(transport=httpx.MockTransport(self.handle))
        app.dependency_overrides[get_metric_store] = lambda: SupabaseMetricStore(self.settings, self.upstream)
        self.client = TestClient(app)
        self.fixture = json.loads((Path(__file__).resolve().parents[1] / "scenarios/metric_records.example.json").read_text())
        self.headers = {"Authorization": "Bearer test-edge-token"}

    def tearDown(self):
        app.dependency_overrides.clear()
        self.upstream.close()
        self.client.close()

    def handle(self, request):
        self.requests.append(request)
        if request.url.path == "/auth/v1/user":
            return httpx.Response(self.auth_status, json=self.user)
        if request.url.path == "/rest/v1/rpc/ingest_metrics":
            n = len(json.loads(request.content)['p_records'])
            body = self.db_body if self.db_body is not None else {
                'persisted': True, 'received_count': n, 'inserted_count': n, 'duplicate_count': 0}
            return httpx.Response(self.db_status, json=body)
        raise AssertionError("Unexpected upstream path")

    def post(self, body=None):
        return self.client.post("/ingest/metrics", headers=self.headers, json=self.fixture if body is None else body)

    def test_validated_fixture_saved_once_with_user_token(self):
        result = self.post()
        self.assertEqual(result.status_code, 201, result.text)
        self.assertEqual(result.json(), {"persisted": True, "received_count": 10, "inserted_count": 10, 'duplicate_count': 0})
        self.assertEqual(result.headers["x-data-mode"], "database")
        self.assertEqual([r.method for r in self.requests], ["GET", "POST"])
        stored = self.requests[1]
        self.assertEqual(stored.headers["Authorization"], "Bearer test-edge-token")
        self.assertEqual(stored.headers["apikey"], "test-publishable")
        self.assertNotIn('Prefer', stored.headers)
        rows = json.loads(stored.content)['p_records']
        self.assertTrue(all(r["cage_id"] == 3 for r in rows))
        self.assertEqual(rows[0]["value"], [0.5, 0.35, 0.15])

    def test_authentication_and_scope_fail_before_storage(self):
        self.assertEqual(self.client.post("/ingest/metrics", json=self.fixture).status_code, 401)
        self.assertEqual(len(self.requests), 0)
        for code in [401, 403]:
            self.auth_status = code
            self.assertEqual(self.post().status_code, 401)
        self.auth_status = 200
        for user in [
            {"id": self.user["id"], "app_metadata": {}},
            {"id": self.user["id"], "user_metadata": {"role": "edge", "ingest_cage_ids": [3]}},
            {"id": self.user["id"], "app_metadata": {"role": "edge", "ingest_cage_ids": [4]}},
            {"id": self.user["id"], "app_metadata": {"role": "edge", "ingest_cage_ids": [True]}},
        ]:
            self.user = user
            self.assertEqual(self.post().status_code, 403)
        self.assertTrue(all(r.method == "GET" for r in self.requests))

    def test_duplicate_and_mixed_receipts_preserve_database_counts(self):
        for inserted in (0, 4, 10):
            self.db_body = {'persisted': True, 'received_count': 10,
                            'inserted_count': inserted, 'duplicate_count': 10-inserted}
            response = self.post()
            self.assertEqual(response.status_code, 201)
            self.assertEqual(response.json(), self.db_body)

    def test_invalid_rpc_acknowledgements_fail_closed(self):
        good = {'persisted': True, 'received_count': 10, 'inserted_count': 4, 'duplicate_count': 6}
        for bad in ({'inserted_count': 11}, {'duplicate_count': -1}, {'received_count': 9},
                    {'inserted_count': True}, {'duplicate_count': '6'}, {'persisted': 1}):
            self.db_body = {**good, **bad}
            self.assertEqual(self.post().status_code, 503)
        self.db_body = {k:v for k,v in good.items() if k != 'duplicate_count'}
        self.assertEqual(self.post().status_code, 503)

    def test_invalid_records_reject_whole_request_without_upstream(self):
        for payload in [[], [{}], {"records": self.fixture}, self.fixture * 101,
                        [{**self.fixture[0], "value": [0.1, 0.1]}],
                        [{**self.fixture[1], "confidence": 2}],
                        [self.fixture[0], {**self.fixture[1], "value": "1"}]]:
            with self.subTest(payload_type=type(payload).__name__):
                self.assertEqual(self.post(payload).status_code, 422)
        self.assertEqual(self.requests, [])

    def test_duplicate_normalizes_id_and_timestamp(self):
        one = self.fixture[1]
        other = {**one, "cage_id": "3", "ts": "2026-11-27T18:00:00+09:00"}
        result = self.post([one, other])
        self.assertEqual(result.status_code, 422)
        self.assertEqual(result.json()["detail"]["code"], "duplicate_in_batch")
        self.assertEqual([r.method for r in self.requests], ["GET"])

    def test_unknown_and_unscoped_cages(self):
        for cage, code in [("unmapped", 422), ("4", 403), ("0", 422), ("9" * 100, 422)]:
            self.assertEqual(self.post([{**self.fixture[0], "cage_id": cage}]).status_code, code)
        self.assertTrue(all(r.method == "GET" for r in self.requests))

    def test_upstream_errors_are_sanitized_and_not_retried(self):
        for sqlcode, status in [("23505", 409), ("23503", 422), ("23514", 422), ("42501", 403), ("XX000", 503)]:
            self.db_status, self.db_body = 400, {"code": sqlcode, "message": "secret-upstream-details"}
            before = len(self.requests)
            response = self.post()
            self.assertEqual(response.status_code, status)
            self.assertNotIn("secret-upstream-details", response.text)
            self.assertEqual(len(self.requests) - before, 2)
        self.db_status, self.db_body = 401, {}
        self.assertEqual(self.post().status_code, 401)

    def test_transport_failure_returns_503(self):
        def unavailable(request):
            raise httpx.ConnectError("secret-url-and-token")
        with httpx.Client(transport=httpx.MockTransport(unavailable)) as broken:
            app.dependency_overrides[get_metric_store] = lambda: SupabaseMetricStore(self.settings, broken)
            response = self.post()
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("secret-url-and-token", response.text)

    def test_configuration_is_fail_closed(self):
        for env in [{}, {"SUPABASE_URL": "http://remote.example", "SUPABASE_PUBLISHABLE_KEY": "x"},
                    {"SUPABASE_URL": "https://test.supabase.co", "SUPABASE_PUBLISHABLE_KEY": "sb_secret_x"},
                    {"SUPABASE_URL": "https://test.supabase.co", "SUPABASE_PUBLISHABLE_KEY": "x", "INGEST_CAGE_ID_MAP": '{"x":true}'}]:
            with self.subTest(env_keys=list(env)), patch.dict("os.environ", env, clear=True):
                with self.assertRaises(Exception) as error:
                    IngestSettings.from_env()
                self.assertEqual(error.exception.status_code, 503)

    def test_jsonl_uploader_through_api_preserves_producer_output(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "records.jsonl"
            path.write_text("\n".join(json.dumps(r) for r in self.fixture), encoding="utf-8")
            records = read_records(path)
            inserted = upload(records, self.client, "http://testserver", "test-edge-token", batch_size=4)
        self.assertEqual(inserted, 10)
        self.assertEqual(len([r for r in self.requests if r.method == "POST"]), 3)

    def test_uploader_reports_progress_and_does_not_retry_conflicts(self):
        rows = read_records(Path(__file__).resolve().parents[1] / "scenarios/metric_records.example.json")
        calls = []
        def handler(request):
            calls.append(request)
            return httpx.Response(201, json={"persisted": True, "received_count": 4, "inserted_count": 4}) if len(calls) == 1 else httpx.Response(409)
        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            with self.assertRaisesRegex(UploadError, "4 records previously confirmed"):
                upload(rows, client, "http://localhost", "secret-token", batch_size=4)
        self.assertEqual(len(calls), 2)

    def test_dry_run_needs_no_credentials_or_network(self):
        fixture = Path(__file__).resolve().parents[1] / "scenarios/metric_records.example.json"
        with patch.dict("os.environ", {}, clear=True), contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(upload_main([str(fixture), "--dry-run"]), 0)
        self.assertEqual(json.loads(out.getvalue()), {"validated_count": 10, "uploaded": False})

    def test_uploader_counts_legacy_new_and_duplicate_receipts(self):
        records = read_records(Path(__file__).resolve().parents[1] / 'scenarios/metric_records.example.json')
        receipts = [{'persisted': True,'received_count':4,'inserted_count':4},
                    {'persisted': True,'received_count':4,'inserted_count':1,'duplicate_count':3},
                    {'persisted': True,'received_count':2,'inserted_count':0,'duplicate_count':2}]
        with httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(201,json=receipts.pop(0)))) as client:
            self.assertEqual(upload_counts(records,client,'http://localhost','edge',4),
                {'received_count':10,'inserted_count':5,'duplicate_count':5})

    def test_uploader_rejects_false_coerced_or_inconsistent_counts(self):
        records = read_records(Path(__file__).resolve().parents[1] / 'scenarios/metric_records.example.json')
        for receipt in ({'persisted':True,'received_count':10,'inserted_count':10.0},
                        {'persisted':True,'received_count':10,'inserted_count':4},
                        {'persisted':True,'received_count':10,'inserted_count':9,'duplicate_count':True},
                        {'persisted':True,'received_count':10,'inserted_count':9,'duplicate_count':2}):
            with httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(201,json=receipt))) as client:
                with self.assertRaises(UploadError):
                    upload_counts(records,client,'http://localhost','edge')


if __name__ == "__main__":
    unittest.main()
