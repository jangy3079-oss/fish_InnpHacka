import unittest
from collections import defaultdict

import numpy as np

from simulator.echogram import synthesize
from simulator.labels import build_labels
from simulator.run import simulate
from simulator.scenario import load_scenario
from sonar.metrics import extract


def run(name):
    sim = simulate(load_scenario(name))
    recs = sim.records + extract(synthesize(sim))
    s = defaultdict(dict)
    for r in recs:
        s[(r.source.value, r.metric.value)][r.ts] = r.value
    ts = sorted(s[("env", "do")])
    get = lambda src, m: np.array([s[(src, m)].get(t, np.nan) if not isinstance(s[(src, m)].get(t), list)
                                   else s[(src, m)][t] for t in ts], dtype=object if m == "vertical_dist" else float)
    h = np.arange(len(ts)) * sim.scenario.cycle_minutes / 60
    return sim, get, h, build_labels(sim)


class ScenarioDataTest(unittest.TestCase):
    def test_normal_14d(self):
        sim, get, h, lab = run("normal_14d")
        self.assertGreaterEqual(h[-1], 14 * 24 - 1)
        self.assertEqual(lab["expected_causes"], [])
        self.assertTrue(lab["false_alarm_check"])
        self.assertTrue((get("env", "temp_surface") < 23).all())

    def test_turbidity_camera_only(self):
        sim, get, h, lab = run("turbidity_event")
        q, act = get("camera", "quality"), get("sonar", "activity")
        day = (h % 24 >= 9) & (h % 24 < 16)
        before, during = day & (h < 24), day & (h >= 27) & (h < 41)
        self.assertLess(q[during].mean(), 0.3)
        self.assertGreater(q[before].mean(), 0.7)
        self.assertAlmostEqual(act[during].mean() / act[before].mean(), 1.0, delta=0.15)   # 소나는 정상
        self.assertTrue(lab["false_alarm_check"])

    def test_disease_rises_slowly(self):
        sim, get, h, lab = run("disease_suspect")
        act = get("sonar", "activity")
        day = (h % 24 >= 9) & (h % 24 < 16)
        daily = [act[day & (h >= d * 24) & (h < d * 24 + 24)].mean() for d in range(6)]
        self.assertTrue(all(b < a for a, b in zip(daily[1:], daily[2:])))   # 2일차부터 매일 감소
        self.assertLess(daily[-1], 0.7 * daily[0])
        dis = [iv for iv in lab["intervals"] if iv["cause"] == "disease_suspect"]
        dur_h = (np.datetime64(dis[0]["end"][:-1]) - np.datetime64(dis[0]["start"][:-1])) / np.timedelta64(1, "h")
        self.assertGreaterEqual(dur_h, 72)
        self.assertEqual(lab["expected_causes"], ["disease_suspect"])

    def test_parasite_rubbing_3x_for_6h(self):
        sim, get, h, lab = run("parasite_suspect")
        rub = get("camera", "rubbing_rate")
        day = (h % 24 >= 9) & (h % 24 < 16)
        base = rub[day & (h < 24)].mean()
        hourly = np.array([rub[(h >= k) & (h < k + 1)].mean() for k in range(int(h[-1]))])
        hk = np.arange(hourly.size)
        hot = ((hk % 24 >= 9) & (hk % 24 < 16) & (hk >= 32))
        self.assertGreaterEqual(np.median(hourly[hot]) / base, 3.0)
        # 6시간 이동 평균이 기준선 3배 이상인 구간이 있다
        ma6 = np.convolve(hourly, np.ones(6) / 6, mode="valid")
        self.assertGreaterEqual(ma6.max() / base, 3.0)
        temp = get("env", "temp_surface")
        self.assertTrue((temp < 23).all())
        self.assertEqual(lab["expected_causes"], ["parasite_suspect"])

    def test_heatwave_labels(self):
        sim, get, h, lab = run("heatwave_typhoon")
        self.assertEqual(lab["expected_causes"], ["feeding_drop", "heat_hiding", "refuge_loss"])
        self.assertIn("advisory_28c", lab["markers"])
        self.assertLess(lab["markers"]["typhoon_at"], lab["markers"]["advisory_28c"])

    def test_feeding_drop_only_feeding(self):
        sim, get, h, lab = run("feeding_drop")
        self.assertEqual(lab["expected_causes"], ["feeding_drop"])
        self.assertTrue(lab["false_alarm_check"])


if __name__ == "__main__":
    unittest.main()
