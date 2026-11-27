import unittest

import numpy as np

from common import MetricName, MetricSource
from simulator.fish import feeding_response, heat_activity
from simulator.run import simulate
from simulator.scenario import load_scenario


class SimulatorHeatwaveTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.res = simulate(load_scenario("heatwave_typhoon"))
        tr = cls.res.truth
        cls.h = np.arange(cls.res.cycle_time.size) * cls.res.scenario.cycle_minutes / 60
        cls.day = tr["day"] > 0.9

    def phase(self, lo, hi):
        return self.day & (self.h >= lo) & (self.h < hi)

    def mean(self, key, m):
        return float(self.res.truth[key][m].mean())

    def test_env_records(self):
        env = [r for r in self.res.records if r.source == MetricSource.ENV]
        self.assertEqual({r.metric for r in env},
                         {MetricName.TEMP_SURFACE, MetricName.TEMP_MID, MetricName.DO})
        self.assertEqual(len(env), 3 * self.res.cycle_time.size)

    def test_heat_hiding_shifts_down_and_slows(self):
        normal, hot = self.phase(0, 30), self.phase(90, 132)
        vd = self.res.truth["vertical_dist"]
        lower = lambda m: float((vd[m, 0] + vd[m, 1]).mean())   # 저층+중층
        self.assertGreaterEqual(lower(hot) / lower(normal), 1.5)
        self.assertLessEqual(self.mean("activity", hot) / self.mean("activity", normal), 0.8)

    def test_typhoon_removes_refuge(self):
        after = self.phase(145, 192)
        tr = self.res.truth
        self.assertLess(float((tr["temp_surface"][after] - tr["temp_mid"][after]).mean()), 1.0)
        self.assertGreater(self.mean("disorder", after), 0.8)
        self.assertLessEqual(self.mean("activity", after) / self.mean("activity", self.phase(0, 30)), 0.6)
        # 피난처 소실: 수심 퍼짐이 피신 구간보다 커짐 (응집도 급락)
        pos = self.res.positions
        ppc = self.res.scenario.pings_per_cycle
        spread = pos.std(axis=1).reshape(-1, ppc).mean(1)
        self.assertGreater(spread[after].mean(), 1.5 * spread[self.phase(90, 132)].mean())

    def test_feeding_drops_first_at_23(self):
        # UC-04 / #39: 23℃대에서는 급이 반응만 줄고 활동은 거의 그대로
        normal, warm = self.phase(0, 30), self.phase(36, 66)
        self.assertLessEqual(self.mean("feeding", warm), 0.85 * self.mean("feeding", normal))
        self.assertGreater(self.mean("activity", warm), 0.9 * self.mean("activity", normal))

    def test_night_less_active(self):
        night = (self.res.truth["day"] < 0.1) & (self.h < 30)
        self.assertLess(self.mean("activity", night), 0.8 * self.mean("activity", self.phase(0, 30)))

    def test_camera_feeding_records(self):
        feed = [r for r in self.res.records if r.metric == MetricName.FEEDING]
        self.assertEqual(len(feed), self.res.cycle_time.size)


class ResponseCurveTest(unittest.TestCase):
    def test_reference_points(self):
        self.assertEqual(heat_activity(24.0), 1.0)
        self.assertLess(heat_activity(25.0), 1.0)
        self.assertLess(heat_activity(28.0), heat_activity(25.0))
        self.assertLess(feeding_response(23.5), feeding_response(22.0))


if __name__ == "__main__":
    unittest.main()
