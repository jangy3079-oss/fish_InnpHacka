import json
import tempfile
import unittest
from pathlib import Path

from pipeline.run import run
from pipeline.timeline import Timeline

ROOT = Path(__file__).resolve().parents[1]
# SDD 8장 GET /cages/{id}/status 예시의 키 (프론트 scenarios.json frames와 같음)
STATUS_KEYS = {"cage_id", "ts", "stress_score", "level", "baseline_status", "cause", "evidence",
               "camera_weight", "missing_sources"}
# 프론트 근거 그래프가 쓰는 시계열 지표
FRONT_SERIES = ("vertical_dist", "activity", "temp_surface", "temp_mid")


class PipelineExportTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        out = Path(cls.tmp.name)
        cls.out = out
        cls.results = {r["scenario"]: r for r in run(["heatwave_typhoon", "turbidity_event", "hypoxia", "disease_suspect", "parasite_suspect"], out,
                                                      out / "m.pkl")}
        cls.hw = Timeline(out / "heatwave_typhoon" / "api")
        cls.tb = Timeline(out / "turbidity_event" / "api")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_status_has_api_keys(self):
        row = self.hw.status_at(125)
        self.assertTrue(STATUS_KEYS <= set(row), STATUS_KEYS - set(row))
        self.assertIsInstance(row["cause"], list)
        cause_row = next(r for r in self.hw.status if r["cause"] and r["cause"][0]["rule_id"] == "R1")
        self.assertEqual(cause_row["cause"][0]["probability"], 1.0)
        self.assertEqual(len(row["cause_config_revision"]), 64)
        self.assertIn("temp_surface", row["evidence"])

    def test_timeseries_has_front_metrics(self):
        for m in FRONT_SERIES:
            pts = self.hw.series[m]
            self.assertGreater(len(pts), 100)
            self.assertEqual(set(pts[0]), {"ts", "value"})
        vd = self.hw.series["vertical_dist"][50]["value"]
        self.assertEqual(len(vd), 3)
        self.assertAlmostEqual(sum(vd), 1.0, places=6)
        self.assertIn("stress_score", self.hw.series)

    def test_cause_evidence_is_saved_and_matches_status(self):
        path = self.out / "heatwave_typhoon/cause_estimate.jsonl"
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        self.assertEqual({r["rule_id"] for r in rows}, {"R1", "R2", "R7"})
        by_ts = {row["ts"]: row for row in self.hw.status}
        for row in rows:
            ts = row["ts"].replace("+00:00", "Z")
            self.assertEqual(row["evidence"]["config_revision"], by_ts[ts]["cause_config_revision"])
            self.assertEqual(row["rule_id"], by_ts[ts]["cause"][0]["rule_id"])
        self.assertEqual((self.out / "turbidity_event/cause_estimate.jsonl").read_text(), "")

    def test_extended_causes_reach_saved_timeline_and_unlabelled_frontend_export(self):
        from pipeline.frontend import scenario_frames
        for name, rid in (("hypoxia", "R3"), ("disease_suspect", "R5"), ("parasite_suspect", "R8")):
            tl = Timeline(self.out / name / "api")
            rows = [json.loads(s) for s in (self.out / name / "cause_estimate.jsonl").read_text(encoding="utf-8").splitlines()]
            self.assertTrue(any(r["rule_id"] == rid for r in rows), name)
            frames = scenario_frames(tl, 48, label_causes=False)["frames"]
            emitted = [f for f in frames if f["cause"]]
            self.assertTrue(emitted, name)
            self.assertTrue(any(f["cause"][0]["rule_id"] == rid for f in emitted), name)
            self.assertTrue(all(f["cause_source"] == "cause_engine" for f in emitted))

    def test_t_axis(self):
        ts = [r["t"] for r in self.hw.status]
        self.assertEqual(ts, sorted(ts))
        self.assertAlmostEqual(ts[1] - ts[0], 5 / 60, places=3)
        self.assertEqual(self.hw.meta["t_unit"], "hours")
        self.assertAlmostEqual(self.hw.duration_h, 192.0)

    def test_status_at_follows_story(self):
        self.assertIsNone(self.hw.status_at(0)["stress_score"])            # 첫 1시간은 윈도우가 덜 참
        self.assertEqual(self.hw.status_at(10)["level"], "정상")
        refuge = next(i for i in self.hw.meta["intervals"] if i["cause"] == "refuge_loss")
        self.assertEqual(self.hw.status_at(refuge["t_start"] + 3)["level"], "위험")
        self.assertEqual(self.hw.status_at(-5), self.hw.status[0])          # 범위 밖은 양 끝
        self.assertEqual(self.hw.status_at(999), self.hw.status[-1])

    def test_meta(self):
        m = self.hw.meta
        self.assertGreater(m["lead_time_h"], 24)                             # VC-04
        self.assertIn("typhoon_at", m["markers"])
        self.assertEqual(m["max_level"], "위험")
        self.assertEqual(self.tb.meta["max_level"], "정상")                  # VC-05
        self.assertEqual(self.tb.meta["level_changes"], [])

    def test_timeseries_range_inclusive(self):
        pts = self.hw.timeseries("activity", "2026-08-09T00:00:00Z", "2026-08-09T05:00:00Z")
        self.assertEqual([p["ts"][11:13] for p in pts], ["00", "01", "02", "03", "04", "05"])
        self.assertEqual(len(self.hw.timeseries_upto("activity", 125, hours=24)), 25)


if __name__ == "__main__":
    unittest.main()
