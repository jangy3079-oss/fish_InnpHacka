import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
import numpy as np
from fastapi.testclient import TestClient
from PIL import Image

from api.echogram import EchogramRenderer, get_echogram_renderer
from api.farmer_data import get_farmer_http_client
from api.main import app


class FarmerDataTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.requests = []
        self.auth_status, self.visible, self.error_code = 200, True, None
        self.series = {"cage_id": 3, "metric": "activity", "points": [
            {"ts": "2026-08-12T05:00:00Z", "value": 0.6, "source": "sonar", "confidence": 0.9}],
            "has_more": False, "next_before": None}
        self.user = {"id": "00000000-0000-4000-8000-000000000001"}
        self.env = patch.dict(os.environ, {"SUPABASE_URL": "https://test.supabase.co", "SUPABASE_PUBLISHABLE_KEY": "test-publishable"})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.upstream = httpx.Client(transport=httpx.MockTransport(self.handle))
        self.addCleanup(self.upstream.close)
        app.dependency_overrides[get_farmer_http_client] = lambda: self.upstream
        app.dependency_overrides[get_echogram_renderer] = lambda: EchogramRenderer(self.root, {"3": "cage-003"})
        self.addCleanup(app.dependency_overrides.clear)
        self.client = TestClient(app)
        self.addCleanup(self.client.close)
        self.headers = {"Authorization": "Bearer test-user-token"}
        self.stock = {"stock": [{"size_group": "mid", "count": 100, "avg_weight_kg": 0.3}], "insured": True}
        self.write_asset()

    def write_asset(self, **overrides):
        directory = self.root / "3"
        directory.mkdir(exist_ok=True)
        fields = {"Sv": np.array([[-80., -60.], [-65., -55.], [-70., -50.]], dtype=np.float32),
                  "ping_time": np.array([1786510800, 1786510860, 1786510920], dtype=np.int64),
                  "depth": np.array([0.5, 1.5], dtype=np.float32),
                  "meta": np.array(json.dumps({"cage_id": "cage-003", "format_version": 1}))}
        fields.update(overrides)
        np.savez_compressed(directory / "echogram.npz", **fields)

    def handle(self, request):
        self.requests.append(request)
        path = request.url.path
        if path == "/auth/v1/user":
            return httpx.Response(self.auth_status, json=self.user)
        if path == "/rest/v1/cage":
            return httpx.Response(200, json=[{"id": 3}] if self.visible else [])
        if self.error_code:
            return httpx.Response(400, json={"code": self.error_code, "message": "secret-database-detail"})
        if path == "/rest/v1/rpc/replace_cage_stock":
            data = json.loads(request.content)
            return httpx.Response(200, json={"persisted": True, "cage_id": 3,
                "received_count": len(data["p_stock"]), "insured": data["p_insured"],
                "stock": [{**row, "updated_at": "2026-11-27T10:00:00Z"} for row in data["p_stock"]]})
        if path == "/rest/v1/rpc/cage_metric_series":
            return httpx.Response(200, json=self.series)
        raise AssertionError(path)

    def get(self, path):
        return self.client.get(path, headers=self.headers)

    def test_stock_is_single_rpc_under_user_token(self):
        response = self.client.put("/cages/3/stock", json=self.stock, headers=self.headers)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["persisted"])
        self.assertEqual(response.json()["stock"][0]["count"], 100)
        self.assertEqual(len(self.requests), 3)
        self.assertEqual(self.requests[-1].url.path, "/rest/v1/rpc/replace_cage_stock")
        self.assertTrue(all(r.headers["Authorization"] == "Bearer test-user-token" for r in self.requests))
        self.assertEqual(response.headers["cache-control"], "private, no-store")
        self.assertEqual(response.headers["x-data-mode"], "database")

    def test_invalid_stock_never_reaches_mutation(self):
        for body in [
            {**self.stock, "stock": []}, {**self.stock, "stock": self.stock["stock"] * 2},
            {**self.stock, "insured": "true"},
            {**self.stock, "stock": [{"size_group": "mid", "count": -1, "avg_weight_kg": 0.3}]},
            {**self.stock, "stock": [{"size_group": "mid", "count": True, "avg_weight_kg": 0.3}]},
            {**self.stock, "stock": [{"size_group": "mid", "count": 1, "avg_weight_kg": "0.3"}]},
            {**self.stock, "stock": [{"size_group": "mid", "count": 1, "avg_weight_kg": 0}]},
        ]:
            self.assertEqual(self.client.put("/cages/3/stock", json=body, headers=self.headers).status_code, 422)
        self.assertFalse(any(r.method == "POST" for r in self.requests))

    def test_missing_invalid_and_anonymous_auth_fail_closed(self):
        self.assertEqual(self.client.get("/cages/3/timeseries?metric=activity").status_code, 401)
        self.assertEqual(self.requests, [])
        self.auth_status = 401
        self.assertEqual(self.get("/cages/3/timeseries?metric=activity").status_code, 401)
        self.auth_status = 200
        self.user["is_anonymous"] = True
        self.assertEqual(self.get("/cages/3/echogram").status_code, 401)
        self.assertTrue(all(r.url.path == "/auth/v1/user" for r in self.requests))

    def test_nonexistent_and_forbidden_cage_are_indistinguishable(self):
        self.visible = False
        for path in ["/cages/3/timeseries?metric=activity", "/cages/3/echogram"]:
            self.assertEqual(self.get(path).status_code, 404)
        self.assertEqual(self.client.put("/cages/3/stock", json=self.stock, headers=self.headers).status_code, 404)
        self.assertTrue(all(r.method == "GET" for r in self.requests))

    def test_series_parameters_provenance_and_pagination(self):
        self.series["points"][0]["source"] = "camera"
        response = self.get("/cages/3/timeseries?metric=activity&source=camera&limit=25&from=2026-08-12T14:00:00%2B09:00&to=2026-08-12T06:00:00Z&before=2026-08-12T06:00:00Z")
        self.assertEqual(response.status_code, 200, response.text)
        payload = json.loads(self.requests[-1].content)
        self.assertEqual(payload["p_cage_id"], 3)
        self.assertEqual(payload["p_source"], "camera")
        self.assertEqual(payload["p_limit"], 25)
        self.assertEqual(response.json()["points"][0]["source"], "camera")
        self.assertFalse(response.json()["has_more"])
        self.assertIn("Authorization", response.headers["vary"])

    def test_series_validation_and_empty_result(self):
        for suffix in ["metric=unknown", "metric=activity&limit=1001", "metric=activity&limit=0",
                       "metric=activity&from=2026-08-12T05:00:00", "metric=activity&source=unknown",
                       "metric=activity&from=2026-08-13T00:00:00Z&to=2026-08-12T00:00:00Z"]:
            self.assertEqual(self.get("/cages/3/timeseries?" + suffix).status_code, 422)
        self.series["points"] = []
        self.assertEqual(self.get("/cages/3/timeseries?metric=activity").json()["points"], [])

    def test_upstream_errors_are_sanitized_and_not_retried(self):
        for code, status in [("42501", 403), ("22023", 422), ("XX000", 503)]:
            self.error_code = code
            before = len(self.requests)
            response = self.client.put("/cages/3/stock", json=self.stock, headers=self.headers)
            self.assertEqual(response.status_code, status)
            self.assertNotIn("secret-database-detail", response.text)
            self.assertEqual(len(self.requests)-before, 3)

    def test_echogram_is_real_png_and_window_changes_image(self):
        full = self.get("/cages/3/echogram")
        self.assertEqual(full.status_code, 200, full.text[:100] if full.status_code != 200 else "")
        self.assertEqual(full.headers["content-type"], "image/png")
        self.assertEqual(full.headers["x-data-mode"], "asset")
        with Image.open(io.BytesIO(full.content)) as image:
            self.assertEqual(image.size, (900, 300))
        cropped = self.get("/cages/3/echogram?from=2026-08-12T05:01:00Z&to=2026-08-12T05:01:00Z")
        self.assertEqual(cropped.status_code, 200)
        self.assertNotEqual(full.content, cropped.content)

    def test_empty_missing_or_wrong_cage_asset_is_not_fabricated(self):
        self.assertEqual(self.get("/cages/3/echogram?from=2027-01-01T00:00:00Z").status_code, 404)
        self.write_asset(meta=np.array('{"cage_id":"another-cage"}'))
        self.assertEqual(self.get("/cages/3/echogram").status_code, 503)
        (self.root / "3/echogram.npz").unlink()
        self.assertEqual(self.get("/cages/3/echogram").status_code, 404)

    def test_bad_asset_shapes_and_values_are_rejected(self):
        for change in [{"Sv": np.array([[float("nan"), 1]])}, {"depth": np.array([1., 0.])},
                       {"ping_time": np.array([1, 1, 2])}, {"Sv": np.array(["text"])},
                       {"meta": np.array([{"cage_id": "3"}], dtype=object)}, {"meta": np.array('[]')}]:
            self.write_asset(**change)
            self.assertEqual(self.get("/cages/3/echogram").status_code, 503)

    def test_unauthorized_cage_does_not_read_asset(self):
        self.visible = False
        with patch.object(EchogramRenderer, "render", side_effect=AssertionError("Asset must not be read")):
            self.assertEqual(self.get("/cages/3/echogram").status_code, 404)


if __name__ == "__main__":
    unittest.main()
