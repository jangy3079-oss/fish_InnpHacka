"""Auth/RPC boundary tests; PostgreSQL aggregation arithmetic lives in areas.sql."""
import copy
import json
import unittest
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

from api.farmer_data import SupabaseSettings, get_farmer_http_client
from api.analysis import get_analysis_loader
from api.main import app
from common.config_loader import ConfigError, ConfigLoader

REVISION = ConfigLoader().snapshot().revision
TS = "2026-11-27T05:00:00Z"
SUMMARY = dict(area_id=35, name="해역", level="위험", stress_score=80, cage_count=3,
               assessed_cage_count=1, held_cage_count=1, unavailable_cage_count=1,
               data_status="partial", latest_analysis_at=TS)
MAP = dict(ts=TS, config_revision=REVISION, aggregation_method="max_current_level_and_score", areas=[SUMMARY])
DEMAND = dict(area_id=35, ts=TS, config_revision=REVISION, basis="recommended_not_chosen",
              estimated_fish_count=700, cage_count=3, recommended_cage_count=1,
              assessed_cage_count=1, unavailable_cage_count=2, data_status="partial")


class AreasApiTests(unittest.TestCase):
    def setUp(self):
        self.user = dict(id="00000000-0000-4000-8000-000000000003", is_anonymous=False,
                         app_metadata=dict(role="official", area_ids=[35, 36, 40]))
        self.result = copy.deepcopy(MAP)
        self.status = 200
        self.calls = []

        def handler(request):
            self.calls.append(request)
            self.assertEqual(request.headers["authorization"], "Bearer official-access")
            self.assertEqual(request.headers["apikey"], "publishable")
            if request.url.path == "/auth/v1/user":
                return httpx.Response(200, json=self.user)
            self.assertIn(request.url.path, ("/rest/v1/rpc/area_stress_map", "/rest/v1/rpc/area_release_demand"))
            self.assertEqual(request.method, "POST")
            return httpx.Response(self.status, json=self.result)

        self.upstream = httpx.Client(transport=httpx.MockTransport(handler))
        app.dependency_overrides[get_farmer_http_client] = lambda: self.upstream
        self.settings = patch.object(SupabaseSettings, "from_env", return_value=SupabaseSettings("https://example.test", "publishable"))
        self.settings.start()
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        self.upstream.close()
        self.settings.stop()
        app.dependency_overrides.clear()

    def get(self, path="/areas/stress-map"):
        return self.client.get(path, headers={"Authorization": "Bearer official-access"})

    def test_map_forwards_current_auth_scope_same_bearer_and_revision(self):
        self.user["app_metadata"]["area_ids"] = [36, 35, 35]
        response = self.get()
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["areas"], [SUMMARY])
        self.assertEqual(json.loads(self.calls[-1].content), dict(p_area_ids=[35, 36], p_config_revision=REVISION))
        self.assertEqual(response.headers["x-data-mode"], "database")
        self.assertEqual(response.headers["cache-control"], "private, no-store")
        self.assertIn("Authorization", response.headers["vary"])

    def test_demand_preserves_partial_lower_bound(self):
        self.result = copy.deepcopy(DEMAND)
        r = self.get("/areas/35/release-demand")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json(), DEMAND)
        self.assertEqual(json.loads(self.calls[-1].content), dict(p_area_id=35, p_config_revision=REVISION))

    def test_unavailable_is_null_and_empty_demand_is_known_zero(self):
        self.result = {**DEMAND, "data_status": "unavailable", "assessed_cage_count": 0,
                       "unavailable_cage_count": 3, "recommended_cage_count": 0, "estimated_fish_count": None}
        r = self.get("/areas/35/release-demand")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIsNone(r.json()["estimated_fish_count"])
        self.result = {**DEMAND, "cage_count": 0, "assessed_cage_count": 0, "unavailable_cage_count": 0,
                       "recommended_cage_count": 0, "estimated_fish_count": 0, "data_status": "complete"}
        self.assertEqual(self.get("/areas/35/release-demand").status_code, 200)

    def test_empty_scope_returns_no_areas(self):
        self.user["app_metadata"]["area_ids"] = []
        self.result = {**MAP, "areas": []}
        self.assertEqual(self.get().json()["areas"], [])
        self.assertEqual(json.loads(self.calls[-1].content)["p_area_ids"], [])

    def test_held_only_or_unknown_map_never_invents_a_score(self):
        for level, held, state in (("위험", 1, "partial"), (None, 0, "unavailable")):
            self.result = {**MAP, "areas": [{**SUMMARY, "level": level, "stress_score": None,
                "assessed_cage_count": 0, "held_cage_count": held,
                "unavailable_cage_count": 3-held, "data_status": state}]}
            r = self.get()
            self.assertEqual(r.status_code, 200, r.text)
            self.assertIsNone(r.json()["areas"][0]["stress_score"])

    def test_invalid_current_config_fails_before_aggregate_rpc(self):
        loader = unittest.mock.Mock()
        loader.snapshot.side_effect = ConfigError("invalid config")
        app.dependency_overrides[get_analysis_loader] = lambda: loader
        self.assertEqual(self.get().status_code, 503)
        self.assertEqual(len(self.calls), 1)

    def test_no_bearer_never_calls_auth_or_database(self):
        for path in ("/areas/stress-map", "/areas/35/release-demand"):
            self.assertEqual(self.client.get(path).status_code, 401)
        self.assertEqual(self.calls, [])

    def test_anonymous_invalid_user_and_metadata_spoof(self):
        for user, expected in (({**self.user, "is_anonymous": True}, 401),
                               ({**self.user, "id": "invalid"}, 401),
                               ({"id": self.user["id"], "user_metadata": self.user["app_metadata"]}, 403),
                               ({**self.user, "app_metadata": {"role": "farmer", "area_ids": [35]}}, 403)):
            self.user = user
            self.assertEqual(self.get().status_code, expected)
            self.assertEqual(self.calls[-1].url.path, "/auth/v1/user")

    def test_scope_ids_are_strict_positive_safe_integers(self):
        for ids in (None, "35", ["35"], [True], [0], [-1], [2**63]):
            self.user["app_metadata"]["area_ids"] = ids
            self.assertEqual(self.get().status_code, 403, ids)
            self.assertEqual(self.calls[-1].url.path, "/auth/v1/user")

    def test_latest_auth_scope_rejects_unassigned_area_before_rpc(self):
        self.assertEqual(self.get("/areas/39/release-demand").status_code, 403)
        self.assertEqual(len(self.calls), 1)

    def test_jwt_scope_must_also_authorize_in_sql(self):
        self.status, self.result = 403, {"code": "42501", "message": "private database error"}
        r = self.get()
        self.assertEqual(r.status_code, 403)
        self.assertNotIn("private database error", r.text)

    def test_assigned_missing_area_is_404(self):
        self.result = {"unavailable_reason": "area_not_found"}
        self.assertEqual(self.get("/areas/40/release-demand").status_code, 404)

    def test_upstream_cannot_leak_extra_fields_or_wrong_area(self):
        for bad in ({**SUMMARY, "estimated_loss_krw": 900}, {**SUMMARY, "cage_id": 350},
                    {**SUMMARY, "area_id": 39}, {**SUMMARY, "stress_score": 101},
                    {**SUMMARY, "assessed_cage_count": 3}, {**SUMMARY, "level": None}):
            self.result = {**MAP, "areas": [bad]}
            r = self.get()
            self.assertEqual(r.status_code, 503, r.text)
            self.assertNotIn("900", r.text)
        self.result = {**MAP, "areas": [SUMMARY, SUMMARY]}
        self.assertEqual(self.get().status_code, 503)
        self.result = {**MAP, "config_revision": "b" * 64}
        self.assertEqual(self.get().status_code, 503)

    def test_demand_contract_rejects_finances_bad_counts_and_wrong_scope(self):
        for change in ({"options": []}, {"area_id": 36}, {"estimated_fish_count": -1},
                       {"estimated_fish_count": 2**53}, {"data_status": "complete"},
                       {"estimated_fish_count": None}, {"recommended_cage_count": 0}):
            self.result = {**DEMAND, **change}
            r = self.get("/areas/35/release-demand")
            self.assertEqual(r.status_code, 503, r.text)

    def test_upstream_failure_is_not_mock_success(self):
        self.status, self.result = 500, {"message": "private storage failure"}
        r = self.get()
        self.assertEqual(r.status_code, 503)
        self.assertNotIn("private storage failure", r.text)
        self.assertEqual(r.headers["x-data-mode"], "database")

    def test_invalid_area_id_is_422_with_valid_auth(self):
        for area_id in ("0", "-1", "abc", str(2**63)):
            self.assertEqual(self.get(f"/areas/{area_id}/release-demand").status_code, 422)
            self.assertEqual(self.calls[-1].url.path, "/auth/v1/user")
