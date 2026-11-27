import json
import tempfile
import unittest
from pathlib import Path

from pipeline.frontend import build
from pipeline.run import run

ROOT = Path(__file__).resolve().parents[1]
MOCK = ROOT / "frontend/src/mocks/scenarios.json"


class FrontendExportTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        out = Path(cls.tmp.name)
        cls.names = ["heatwave_typhoon", "parasite_suspect", "turbidity_event"]
        run(cls.names, out, out / "m.pkl")
        cls.data = build(cls.names, out, n_frames=40)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_same_frame_count_and_aligned_series(self):
        for n, s in self.data.items():
            self.assertEqual(len(s["frames"]), 40, n)
            self.assertEqual([f["t"] for f in s["frames"]], list(range(40)))
            for m, pts in s["series"].items():
                self.assertEqual([p["ts"] for p in pts], [f["ts"] for f in s["frames"]], (n, m))

    @unittest.skipUnless(MOCK.exists(), "프론트 목업 없음")
    def test_drop_in_for_frontend_mock(self):
        mock = json.loads(MOCK.read_text(encoding="utf-8"))
        for n in self.names:
            if n not in mock:
                continue
            self.assertTrue(set(mock[n]["frames"][0]) <= set(self.data[n]["frames"][0]), n)
            self.assertTrue(set(mock[n]["series"]) <= set(self.data[n]["series"]), n)

    def test_cause_suppression_passed(self):
        for f in self.data["parasite_suspect"]["frames"]:
            self.assertIn(f["cause_suppression"], (None, "warming_up", "missing_score", "low_stress"))
            self.assertIn("r2_triggered", f)
        self.assertTrue(any(f["r2_triggered"] for f in self.data["heatwave_typhoon"]["frames"]))

    def test_label_causes_marked(self):
        hw = self.data["heatwave_typhoon"]["frames"]
        rules = {f["cause"][0]["rule_id"] for f in hw if f["cause"]}
        self.assertTrue({"R1", "R2"} <= rules)
        self.assertTrue(all(f.get("cause_source") in ("cause_engine", "scenario_label") for f in hw if f["cause"]))
        # R1·R2는 원인 엔진 결과
        self.assertTrue(any(f.get("cause_source") == "cause_engine" for f in hw if f["cause"]))
        normal_causes = [f for f in hw if f['level']=='정상' and f['cause']]
        self.assertTrue(normal_causes)  # UC-04: R7 is management guidance without an alert.
        self.assertTrue(all({c['rule_id'] for c in f['cause']}=={'R7'}
                            and f['cause_source']=='cause_engine' and f['cause_suppression'] is None for f in normal_causes))
        para = self.data["parasite_suspect"]["frames"]
        self.assertIn("R8", {f["cause"][0]["rule_id"] for f in para if f["cause"]})
        self.assertTrue(any(f["cause_source"] == "cause_engine" for f in para if f["cause"]
                            and f["cause"][0]["rule_id"] == "R8"))   # #26에서 R8 엔진 결과 연결
        self.assertTrue(all(f["level"] == "정상" for f in self.data["turbidity_event"]["frames"]))


if __name__ == "__main__":
    unittest.main()
