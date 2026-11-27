import tempfile
import unittest
from pathlib import Path

import numpy as np

from stress.model import MIN_BASELINE_WINDOWS, StressModel
from tests.pipeline_cache import in_interval, model, scenario, scores


def vals(ss, key=None):
    if key is None:
        return np.array([s.score for s in ss if s.score is not None])
    return np.array([s.evidence[key] for s in ss if key in s.evidence])


class StressModelTest(unittest.TestCase):
    def test_ready_and_version(self):
        m = model()
        self.assertEqual(m.status, "ready")
        self.assertGreaterEqual(m.n_windows, MIN_BASELINE_WINDOWS)
        self.assertTrue(m.model_version.startswith("m5-iforest-"))
        row = scores("heatwave_typhoon")[-1].to_row()
        self.assertEqual(set(row), {"cage_id", "ts", "score", "level", "baseline_status", "model_version"})
        self.assertEqual(row["model_version"], m.model_version)

    def test_warming_up(self):
        _, _, fused, _ = scenario("normal_14d")
        m = StressModel("cage-003").fit(fused[: 12 * 24 * 5])      # 5일 = 120 윈도우 < 168
        self.assertEqual(m.status, "warming_up")
        s = m.score_series(fused[:200])
        self.assertTrue(all(x.score is None and x.baseline_status == "warming_up" for x in s))

    def test_normal_low(self):
        _, _, _, lab = scenario("heatwave_typhoon")
        v = vals(in_interval(scores("heatwave_typhoon"), lab, "normal"))
        self.assertLess(v.mean(), 40)
        self.assertLess((v >= 40).mean(), 0.1)

    def test_heat_and_refuge_high_with_evidence(self):
        ss, (_, _, _, lab) = scores("heatwave_typhoon"), scenario("heatwave_typhoon")
        hid, ref = in_interval(ss, lab, "heat_hiding"), in_interval(ss, lab, "refuge_loss")
        self.assertGreater(np.median(vals(hid)), 60)
        self.assertGreater(np.median(vals(ref)), 60)
        day_hid = [s for s in hid if s.daypart == "day"]
        self.assertGreaterEqual(np.median(vals(day_hid, "lower_layers_ratio_x")), 1.5)   # R1 근거
        self.assertLessEqual(np.median(vals(hid, "activity_change")), -0.2)
        self.assertLessEqual(np.median(vals(ref, "activity_change")), -0.4)              # R2 근거
        self.assertLess(np.median(vals(ref, "cohesion_change")), np.median(vals(hid, "cohesion_change")))

    def test_disease_rises(self):
        ss, (_, _, _, lab) = scores("disease_suspect"), scenario("disease_suspect")
        daily = [np.mean([s.score for s in ss[d * 288:(d + 1) * 288] if s.score is not None]) for d in range(6)]
        self.assertLess(daily[0], 40)
        self.assertGreater(daily[-1], 60)
        self.assertGreater(daily[-1], daily[3])

    def test_parasite_rubbing_evidence(self):
        ss, (_, _, _, lab) = scores("parasite_suspect"), scenario("parasite_suspect")
        day = [s for s in in_interval(ss, lab, "parasite_suspect") if s.daypart == "day"]
        self.assertGreaterEqual(np.median(vals(day, "rubbing_rate_x")), 3.0)
        self.assertGreaterEqual(np.median(vals(day)), 40)
        self.assertLessEqual(max(s.components.get("rubbing", 0) for s in day), 79)

    def test_save_load(self):
        m = model()
        with tempfile.TemporaryDirectory() as d:
            m2 = StressModel.load(m.save(Path(d) / "m.pkl"))
        _, _, fused, _ = scenario("turbidity_event")
        a = [s.score for s in m.score_series(fused[:300])]
        b = [s.score for s in m2.score_series(fused[:300])]
        self.assertEqual(a, b)


if __name__ == "__main__":
    unittest.main()
