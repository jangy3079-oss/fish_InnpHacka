import unittest

import numpy as np

from common import MetricRecord
from fusion.fuse import camera_weight, fuse, fuse_cycle
from simulator.echogram import synthesize
from simulator.run import simulate
from simulator.scenario import load_scenario
from sonar.metrics import extract

TS = "2026-08-05T00:00:00Z"


def rec(source, metric, value, conf=0.9, ts=TS):
    return MetricRecord(cage_id="cage-003", ts=ts, source=source, metric=metric, value=value, confidence=conf)


def cycle(q=None, sonar=True, camera=True, env=True):
    rs = []
    if sonar:
        rs += [rec("sonar", "activity", 1.0), rec("sonar", "vertical_dist", [0.2, 0.4, 0.4]),
               rec("sonar", "cohesion", 0.6)]
    if camera:
        rs += [rec("camera", "quality", q, q), rec("camera", "activity", 0.5, q),
               rec("camera", "vertical_dist", [0.0, 0.2, 0.8], q), rec("camera", "feeding", 0.8, q),
               rec("camera", "still_ratio", 0.05, q), rec("camera", "rubbing_rate", 0.3, q)]
    if env:
        rs += [rec("env", "temp_surface", 22.0, 1.0), rec("env", "temp_mid", 19.0, 1.0), rec("env", "do", 7.0, 1.0)]
    return fuse(rs)[0]


class WeightTest(unittest.TestCase):
    def test_weight_curve(self):
        self.assertEqual(camera_weight(0.29), 0.0)
        self.assertAlmostEqual(camera_weight(0.5), 0.25)
        self.assertEqual(camera_weight(0.7), 0.5)
        self.assertEqual(camera_weight(1.0), 0.5)
        self.assertEqual(camera_weight(None), 0.0)


class FuseCycleTest(unittest.TestCase):
    def test_clear_water_blends(self):
        f = cycle(q=0.9)
        self.assertAlmostEqual(f.features["activity"], 0.75)
        self.assertAlmostEqual(sum(f.features["vertical_dist"]), 1.0)
        self.assertEqual(f.features["feeding"], 0.8)
        self.assertEqual(f.missing_sources, [])

    def test_mid_quality_drops_camera_only_metrics(self):
        f = cycle(q=0.5)
        self.assertAlmostEqual(f.features["activity"], 0.75 * 1 + 0.25 * 0.5)
        for m in ("feeding", "still_ratio", "rubbing_rate"):
            self.assertIsNone(f.features[m])

    def test_turbid_uses_sonar_only(self):
        f = cycle(q=0.1)
        self.assertEqual(f.camera_weight, 0.0)
        self.assertEqual(f.features["activity"], 1.0)

    def test_no_sonar_camera_only(self):
        f = cycle(q=0.8, sonar=False)
        self.assertEqual(f.features["activity"], 0.5)
        self.assertIsNone(f.features["cohesion"])
        self.assertEqual(f.missing_sources, ["sonar"])

    def test_no_sonar_and_turbid_empties(self):
        f = cycle(q=0.2, sonar=False)
        self.assertIsNone(f.features["activity"])
        self.assertIsNone(f.features["vertical_dist"])

    def test_missing_camera(self):
        f = cycle(camera=False)
        self.assertEqual(f.missing_sources, ["camera"])
        self.assertEqual(f.camera_weight, 0.0)
        self.assertIsNone(f.quality)

    def test_bucketing(self):
        rs = [rec("sonar", "activity", 1.0, ts="2026-08-05T00:01:00Z"),
              rec("env", "do", 7.0, 1.0, ts="2026-08-05T00:04:59Z"),
              rec("env", "do", 6.0, 1.0, ts="2026-08-05T00:05:00Z")]
        out = fuse(rs)
        self.assertEqual(len(out), 2)
        self.assertEqual(out[0].env["do"], 7.0)


class TurbidityVC01Test(unittest.TestCase):
    """VC-01: 탁도 높은 구간에서 융합 지표가 카메라 단독보다 덜 흔들린다."""

    def test_fused_more_stable(self):
        sim = simulate(load_scenario("turbidity_event"))
        recs = sim.records + extract(synthesize(sim))
        fused = fuse(recs)
        cam = {r.ts: r.value for r in recs if r.source.value == "camera" and r.metric.value == "activity"}
        h = np.array([(f.ts - fused[0].ts).total_seconds() / 3600 for f in fused])
        day = (h % 24 >= 9) & (h % 24 < 16)
        turbid = day & (h >= 27) & (h < 41)
        clear = day & (h < 24)
        fa = np.array([f.features["activity"] for f in fused])
        ca = np.array([cam[f.ts] for f in fused])
        self.assertLess(np.std(fa[turbid]), np.std(ca[turbid]))
        # 탁한 동안 융합 활동량은 맑을 때와 거의 같다 (카메라는 낮게 왜곡됨)
        self.assertAlmostEqual(fa[turbid].mean() / fa[clear].mean(), 1.0, delta=0.15)
        self.assertLess(ca[turbid].mean() / ca[clear].mean(), 0.8)


if __name__ == "__main__":
    unittest.main()
