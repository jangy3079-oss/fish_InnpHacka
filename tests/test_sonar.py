import unittest
from collections import defaultdict

import numpy as np

from common import MetricName, MetricSource
from simulator.echogram import synthesize
from simulator.run import simulate
from simulator.scenario import load_scenario
from sonar.metrics import extract


class SonarMetricsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sim = simulate(load_scenario("heatwave_typhoon"))
        cls.recs = extract(synthesize(sim))
        by = defaultdict(dict)
        for r in cls.recs:
            by[r.ts][r.metric] = r.value
        cls.ts = sorted(by)
        cls.by = by
        h = np.arange(len(cls.ts)) * 5 / 60
        cls.h = h
        cls.day = (h % 24 >= 9) & (h % 24 < 16)   # 시작이 KST 00시

    def series(self, metric, lo, hi, k=None):
        m = self.day & (self.h >= lo) & (self.h < hi)
        v = [self.by[self.ts[i]][metric] for i in np.where(m)[0]]
        v = np.array(v)
        return v[:, k].mean() if k is not None else v.mean()

    def test_records_are_sonar(self):
        self.assertTrue(all(r.source == MetricSource.SONAR for r in self.recs))
        self.assertEqual({r.metric for r in self.recs},
                         {MetricName.VERTICAL_DIST, MetricName.ACTIVITY, MetricName.COHESION})

    def test_mid_ratio_rises_when_hiding(self):
        # 쏠림 시나리오에서 중층 비율이 실제로 오르는지 (이슈 #8 단위 테스트)
        self.assertGreaterEqual(self.series("vertical_dist", 90, 132, 1) / self.series("vertical_dist", 0, 30, 1), 1.5)

    def test_activity_index(self):
        normal = self.series("activity", 0, 30)
        self.assertAlmostEqual(normal, 1.0, delta=0.1)
        self.assertLessEqual(self.series("activity", 90, 132) / normal, 0.8)    # R1: -20% 이하
        self.assertLessEqual(self.series("activity", 150, 192) / normal, 0.6)   # R2: -40% 이하

    def test_cohesion_collapses_after_typhoon(self):
        hiding = self.series("cohesion", 90, 132)
        self.assertGreater(hiding, self.series("cohesion", 0, 30))
        self.assertLess(self.series("cohesion", 150, 192), 0.7 * hiding)


if __name__ == "__main__":
    unittest.main()
