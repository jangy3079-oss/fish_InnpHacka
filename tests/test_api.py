import unittest

from fastapi.testclient import TestClient

from api.main import app, status_fixture


class ApiContractTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def test_remaining_mock_fixture_matches_sdd_example_exactly(self):
        expected = {
            "cage_id": 3, "ts": "2026-08-12T05:00:00Z", "stress_score": 64,
            "level": "경고", "baseline_status": "ready",
            "cause": [{"name": "고수온(피신 중)", "probability": 0.72, "rule_id": "R1"},
                      {"name": "저산소", "probability": 0.18, "rule_id": "R3"}],
            "evidence": {"mid_layer_ratio_x": 1.8, "activity_change": -0.35, "temp_surface": 26.4, "temp_mid": 22.1},
            "camera_weight": 0.2, "missing_sources": [],
        }
        status = status_fixture()
        self.assertEqual({k:status[k] for k in expected}, expected)

    def test_openapi_exposes_all_sdd_operations_and_docs(self):
        response = self.client.get("/openapi.json")
        self.assertEqual(response.status_code, 200)
        schema = response.json()
        expected = {
            ("/health", "get"), ("/ingest/metrics", "post"),
            ("/cages/{id}/status", "get"), ("/cages/{id}/timeseries", "get"),
            ("/cages/{id}/echogram", "get"), ("/cages/{id}/recommendation", "get"),
            ("/recommendations/{id}/choice", "post"), ("/cages/{id}/stock", "put"),
            ("/areas/stress-map", "get"), ("/areas/{id}/release-demand", "get"),
            ("/cages/{id}/diagnosis-request", "post"), ("/cages/{id}/work-log", "post"), ("/demo/scenario/{name}", "post"),
            ("/admin/config/{file}", "get"), ("/admin/config/{file}", "put"),
        }
        actual = {(path, method) for path, item in schema["paths"].items() for method in item if method in {"get", "post", "put"}}
        self.assertEqual(actual, expected)
        for path, method in expected:
            self.assertIn("201" if path in ("/ingest/metrics", "/cages/{id}/diagnosis-request", "/cages/{id}/work-log") else "200", schema["paths"][path][method]["responses"])
        self.assertEqual(self.client.get("/docs").status_code, 200)
        self.assertEqual(self.client.get("/redoc").status_code, 200)
        params = schema["paths"]["/cages/{id}/timeseries"]["get"]["parameters"]
        self.assertTrue({"from", "to", "metric"}.issubset({p["name"] for p in params}))

    def test_health_remains_available_without_auth(self):
        for path in ["/health"]:
            with self.subTest(path=path):
                result = self.client.get(path)
                self.assertEqual(result.status_code, 200, result.text)
                self.assertEqual(result.headers["x-data-mode"], "mock")

    def test_admin_config_requires_authentication_before_read_or_write(self):
        self.assertEqual(self.client.get('/admin/config/thresholds').status_code,401)
        self.assertEqual(self.client.put('/admin/config/thresholds',json={'values':{'warning':60}}).status_code,401)

    def test_invalid_paths_bodies_and_parameters(self):
        requests = [
            ("post", "/demo/scenario/unknown", None),
            ("post", "/demo/scenario/heatwave_typhoon?t=-1", None),
            ("post", "/demo/scenario/heatwave_typhoon?t=nan", None),
        ]
        for method, path, payload in requests:
            with self.subTest(path=path):
                self.assertEqual(self.client.request(method, path, json=payload).status_code, 422)

    def test_real_data_routes_require_authentication(self):
        self.assertEqual(self.client.post("/ingest/metrics", json=[]).status_code, 401)
        for path in ["/cages/3/timeseries?metric=activity", "/cages/3/echogram",'/cages/3/status','/cages/3/recommendation', '/areas/stress-map', '/areas/1/release-demand']:
            self.assertEqual(self.client.get(path).status_code, 401)
        self.assertEqual(self.client.put("/cages/3/stock", json={"stock": [], "insured": False}).status_code, 401)

    def test_demo_scenarios_are_explicit_simulations(self):
        for scenario in ["heatwave_typhoon", "turbidity_event", "disease_suspect", "parasite_suspect"]:
            result = self.client.post(f"/demo/scenario/{scenario}?t=5")
            self.assertEqual(result.status_code, 200)
            self.assertEqual(result.json()["scenario"], scenario)
            self.assertEqual(result.json()["t"], 5)
            self.assertFalse(result.json()["mock"])
            self.assertTrue(result.json()['simulated'])
            self.assertFalse(result.json()['persisted'])
            self.assertEqual(result.headers['x-data-mode'],'simulation')

    def test_cors_supports_frontend_dev_server(self):
        result = self.client.options("/cages/3/stock", headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "PUT", "Access-Control-Request-Headers": "content-type"})
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.headers["access-control-allow-origin"], "http://localhost:5173")

    def test_area_summary_excludes_individual_finances(self):
        item = self.client.get("/openapi.json").json()["components"]["schemas"]["AreaSummary"]
        self.assertTrue({"area_id", "level", "unavailable_cage_count"}.issubset(item["properties"]))
        self.assertFalse({"cage_id", "stock", "options", "estimated_loss_krw"} & set(item["properties"]))
        self.assertFalse(item["additionalProperties"])


if __name__ == "__main__":
    unittest.main()
