"""#45 작업 기록 API·적조 특보 적재·DB 분석 경로의 R4/R6 입력."""
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

from api.farmer_data import get_farmer_http_client
from api.main import app
from common.config_loader import ConfigLoader
from pipeline import red_tide
from pipeline.analysis import build_bundle
from pipeline.contexts import observed_contexts
from stress.model import StressScore

KEY = "44444444-4444-4444-8444-444444444444"
WHEN = "2026-11-27T01:00:00+00:00"
END = datetime.now(timezone.utc).replace(second=0, microsecond=0)
END = END.replace(minute=END.minute // 5 * 5)


class WorkLogApiTest(unittest.TestCase):
    def setUp(self):
        self.requests, self.rpc_status, self.rpc_body = [], 200, None
        self.env = patch.dict(os.environ, {"SUPABASE_URL": "https://test.supabase.co", "SUPABASE_PUBLISHABLE_KEY": "test-publishable"})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.upstream = httpx.Client(transport=httpx.MockTransport(self.handle))
        self.addCleanup(self.upstream.close)
        app.dependency_overrides[get_farmer_http_client] = lambda: self.upstream
        self.addCleanup(app.dependency_overrides.clear)
        self.client = TestClient(app)
        self.addCleanup(self.client.close)
        self.headers = {"Authorization": "Bearer test-user-token"}

    def handle(self, request):
        self.requests.append(request)
        if request.url.path == "/auth/v1/user":
            return httpx.Response(200, json={"id": "00000000-0000-4000-8000-000000000001"})
        if request.url.path == "/rest/v1/cage":
            return httpx.Response(200, json=[{"id": 3}])
        if request.url.path == "/rest/v1/rpc/record_work_log":
            sent = json.loads(request.content)
            body = self.rpc_body or {"persisted": True, "work_log_id": 7, "cage_id": sent["p_cage_id"],
                                     "kind": sent["p_kind"], "performed_at": sent["p_performed_at"],
                                     "created_at": "2026-11-27T01:05:00+00:00"}
            return httpx.Response(self.rpc_status, json=body)
        return httpx.Response(404)

    def post(self, body=None):
        return self.client.post("/cages/3/work-log", headers=self.headers,
                                json=body or {"kind": "net_change", "performed_at": WHEN, "request_key": KEY})

    def test_records_work_log_with_owner_token(self):
        r = self.post()
        self.assertEqual(r.status_code, 201, r.text)
        self.assertEqual((r.json()["work_log_id"], r.json()["kind"]), (7, "net_change"))
        self.assertEqual(r.headers["X-Data-Mode"], "database")
        self.assertEqual(r.headers["Cache-Control"], "private, no-store")
        rpc = json.loads(self.requests[-1].content)
        self.assertEqual(rpc, {"p_cage_id": 3, "p_kind": "net_change", "p_performed_at": WHEN, "p_request_key": KEY})
        self.assertEqual(self.requests[-1].headers["Authorization"], "Bearer test-user-token")   # RLS는 사용자 토큰으로

    def test_validation_and_upstream_errors(self):
        for bad in ({"kind": "feeding", "performed_at": WHEN, "request_key": KEY},
                    {"kind": "sorting", "performed_at": "2026-11-27T01:00:00", "request_key": KEY},
                    {"kind": "sorting", "performed_at": WHEN, "request_key": KEY, "note": "x"}):
            r = self.post(bad)
            self.assertEqual((r.status_code, r.json()["detail"]["code"]), (422, "invalid_work_log_request"), bad)
        self.assertEqual(self.client.post("/cages/3/work-log", json={"kind": "sorting", "performed_at": WHEN,
                                                                     "request_key": KEY}).status_code, 401)
        for status, code, expect in ((400, "40001", 409), (400, "22023", 422), (403, "42501", 403)):
            self.rpc_status, self.rpc_body = status, {"code": code}
            self.assertEqual(self.post().status_code, expect, code)
        self.rpc_status, self.rpc_body = 200, {"persisted": True, "work_log_id": 7, "cage_id": 3, "kind": "sorting",
                                               "performed_at": WHEN, "created_at": WHEN}
        r = self.post()                       # 영수증 내용이 요청과 다르면 저장 확인 실패
        self.assertEqual((r.status_code, r.json()["detail"]["code"]), (503, "invalid_upstream_response"))


class ObservedContextTest(unittest.TestCase):
    def test_latest_work_and_covering_advisory(self):
        t0 = datetime(2026, 8, 10, tzinfo=timezone.utc)
        at = lambda h: t0 + timedelta(hours=h)
        c = observed_contexts([at(0), at(2), at(5), at(9)], advisory_active=False,
                              work_logs=[{"kind": "sorting", "performed_at": at(4).isoformat()},
                                         {"kind": "net_change", "performed_at": at(1).isoformat()}],
                              red_tide_advisories=[{"stage": "advisory", "starts_at": at(1), "ends_at": at(6)},
                                                   {"stage": "watch", "starts_at": at(8), "ends_at": None}])
        self.assertEqual([x.work_log_type for x in c], [None, "net_change", "sorting", "sorting"])
        self.assertEqual([x.red_tide_advisory for x in c], [None, True, True, None])   # 예비주의보는 특보 아님, 없으면 미확인
        self.assertTrue(all(x.advisory_active is False for x in c))
        c = observed_contexts([at(9)], red_tide_advisories=[{"stage": "watch", "starts_at": at(8), "ends_at": None}],
                              advisory_stages=["watch"])
        self.assertTrue(c[0].red_tide_advisory)
        with self.assertRaises(ValueError):
            observed_contexts([at(0)], work_logs=[{"kind": "sorting", "performed_at": "2026-08-10T00:00:00"}])


class Stub:
    cage_id = "cage-003"

    def __init__(self, score_at, act=0.0):
        self.score_at, self.act = score_at, act

    def score_series(self, fused, *, snapshot=None):
        return [StressScore(self.cage_id, f.ts + timedelta(minutes=5), self.score_at(f.ts + timedelta(minutes=5)), "ready",
                            "m5-iforest-1.1+cage-003.20260804.123ccdef",
                            evidence={"lower_layers_ratio_x": 1.0, "activity_change": self.act, "cohesion_change": 0},
                            baseline_windows=1000, components={"sonar": 70}) for f in fused]


def inputs(hours, **extra):
    start = END - timedelta(hours=hours)
    records = []
    for i in range(hours * 12):
        ts = (start + timedelta(minutes=5 * i)).isoformat()
        for source, metric, value in [("sonar", "activity", 0.4), ("sonar", "vertical_dist", [0.3, 0.4, 0.3]),
                                      ("sonar", "cohesion", 0.8), ("env", "temp_surface", 22), ("env", "temp_mid", 19),
                                      ("env", "do", 7)]:
            records.append({"cage_id": "3", "ts": ts, "source": source, "metric": metric, "value": value, "confidence": 0.9})
    return {"cage_id": 3, "farm_id": 1, "sea_area_id": 1,
            "stock": [{"size_group": "mid", "count": 1000, "avg_weight_kg": 0.3, "insured": False}],
            "stock_version": "f" * 32, "metrics": records, "metrics_truncated": False,
            "from": start.isoformat(), "to": END.isoformat(), **extra}, start


class DbAnalysisTest(unittest.TestCase):
    def test_red_tide_advisory_drives_r4(self):
        data, start = inputs(3)
        data["red_tide_advisories"] = [{"stage": "warning", "starts_at": start.isoformat(), "ends_at": None, "source_ref": "t"}]
        b = build_bundle(data, Stub(lambda t: 60, act=0.7), "cage-003", "조피볼락", ConfigLoader().snapshot(),
                         advisory_active=False)
        self.assertEqual(b["causes"][0]["rule_id"], "R4")
        self.assertTrue(b["causes"][0]["evidence"]["red_tide_advisory"])
        rec = b["recommendation"]                 # 적조 대응 요령은 안내 조치라 손익 선택지에서 빠진다
        self.assertEqual(rec["mode"], "loss_comparison")
        self.assertEqual({o["id"] for o in rec["options"] if o["available"]}, {"keep", "stop_feed"})
        data["red_tide_advisories"] = []
        b = build_bundle(data, Stub(lambda t: 60, act=0.7), "cage-003", "조피볼락", ConfigLoader().snapshot(),
                         advisory_active=False)
        self.assertEqual(b["causes"], [])

    def test_work_log_drives_r6(self):
        data, start = inputs(31, work_logs=[{"kind": "net_change", "performed_at": None}])
        work = start + timedelta(hours=6)
        data["work_logs"][0]["performed_at"] = work.isoformat()
        stub = Stub(lambda t: 20 if t <= work else 70)
        b = build_bundle(data, stub, "cage-003", "조피볼락", ConfigLoader().snapshot(), advisory_active=False)
        self.assertEqual(b["causes"][0]["rule_id"], "R6")
        ev = b["causes"][0]["evidence"]
        self.assertEqual((ev["work_log_type"], ev["pre_work_mean_score"], ev["post_work_peak_score"]), ("net_change", 20, 70))
        self.assertEqual({o["id"] for o in b["recommendation"]["options"] if o["available"]}, {"keep", "reduce_feed"})
        del data["work_logs"]                     # 예전 입력 형식(키 없음)도 그대로 동작
        b = build_bundle(data, stub, "cage-003", "조피볼락", ConfigLoader().snapshot(), advisory_active=False)
        self.assertEqual(b["causes"], [])


class RedTideLoaderTest(unittest.TestCase):
    def write(self, text):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        p = Path(d.name) / "a.csv"
        p.write_text(text, encoding="utf-8")
        return p

    HEAD = "sea_area_id,stage,starts_at,ends_at,source,source_ref\n"

    def test_parse_and_load(self):
        p = self.write(self.HEAD + "1,주의보,2026-08-10T14:00:00+09:00,,국립수산과학원 적조속보,2026-07\n"
                                   "1,warning,2026-08-11T09:00:00+09:00,2026-08-13T18:00:00+09:00,국립수산과학원 적조속보,2026-08\n")
        rows = red_tide.parse(p)
        self.assertEqual([r["stage"] for r in rows], ["advisory", "warning"])
        self.assertIsNone(rows[0]["ends_at"])
        sent = []

        def handle(request):
            sent.append(request)
            return httpx.Response(200, json={"persisted": True, "received_count": len(json.loads(request.content)["p_rows"])})
        with httpx.Client(transport=httpx.MockTransport(handle)) as c:
            self.assertEqual(red_tide.load(rows, c, "https://x.supabase.co", "service"), 2)
        self.assertEqual(sent[0].url.path, "/rest/v1/rpc/load_red_tide_advisories")
        with httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(403, json={}))) as c:
            with self.assertRaises(RuntimeError):
                red_tide.load(rows, c, "https://x.supabase.co", "service")

    def test_rejects_bad_rows(self):
        for line in ("1,해제,2026-08-10T14:00:00+09:00,,s,r", "1,advisory,2026-08-10T14:00:00,,s,r",
                     "1,advisory,2026-08-10T14:00:00+09:00,2026-08-10T13:00:00+09:00,s,r",
                     "0,advisory,2026-08-10T14:00:00+09:00,,s,r", "1,advisory,2026-08-10T14:00:00+09:00,,,r"):
            with self.assertRaises(ValueError, msg=line):
                red_tide.parse(self.write(self.HEAD + line + "\n"))
        dup = "1,advisory,2026-08-10T14:00:00+09:00,,s,r\n1,주의보,2026-08-10T05:00:00+00:00,,s,r2\n"
        with self.assertRaises(ValueError):
            red_tide.parse(self.write(self.HEAD + dup))
        with self.assertRaises(ValueError):
            red_tide.parse(self.write("area,stage\n1,advisory\n"))


if __name__ == "__main__":
    unittest.main()
