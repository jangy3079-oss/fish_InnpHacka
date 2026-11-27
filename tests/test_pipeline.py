import json
import tempfile
import unittest
from pathlib import Path

from pipeline.run import run
from pipeline.timeline import Timeline

ROOT = Path(__file__).resolve().parents[1]


class PipelineExportTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        out = Path(cls.tmp.name)
        cls.results = {r["scenario"]: r for r in run(["heatwave_typhoon", "turbidity_event"], out,
                                                      out / "m.pkl")}
        cls.hw = Timeline(out / "heatwave_typhoon" / "api")
        cls.tb = Timeline(out / "turbidity_event" / "api")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_status_matches_frontend_mock_keys(self):
        mock = json.loads((ROOT / "frontend/src/mocks/status.json").read_text(encoding="utf-8"))
        row = self.hw.status_at(125)
        self.assertTrue(set(mock) <= set(row), set(mock) - set(row))
        self.assertEqual(row["cause"], [])
        self.assertIn("temp_surface", row["evidence"])

    def test_timeseries_matches_frontend_mock(self):
        mock = json.loads((ROOT / "frontend/src/mocks/timeseries.json").read_text(encoding="utf-8"))
        for m in mock:
            pts = self.hw.series[m]
            self.assertGreater(len(pts), 100)
            self.assertEqual(set(pts[0]), {"ts", "value"})
        vd = self.hw.series["vertical_dist"][50]["value"]
        self.assertEqual(len(vd), 3)
        self.assertAlmostEqual(sum(vd), 1.0, places=6)
        self.assertIn("stress_score", self.hw.series)

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
