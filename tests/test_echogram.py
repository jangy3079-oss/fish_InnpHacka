import tempfile
import unittest
from pathlib import Path

import numpy as np

from simulator.echogram import load_npz, preview_png, save_npz, synthesize
from simulator.run import simulate
from simulator.scenario import load_scenario


class EchogramTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sim = simulate(load_scenario("heatwave_typhoon"))
        cls.eg = synthesize(cls.sim)

    def test_shape_and_meta(self):
        self.assertEqual(self.eg.Sv.shape, (self.sim.ping_time.size, self.eg.depth.size))
        self.assertEqual(self.eg.meta["cage_id"], "cage-003")
        self.assertTrue(np.isfinite(self.eg.Sv).all())

    def test_echo_follows_fish(self):
        # 고수온 피신 구간(90~132h 낮)에는 에코 무게중심이 정상(0~30h 낮)보다 깊다
        lin = self.eg.linear
        center = (lin * self.eg.depth).sum(1) / lin.sum(1)
        h = np.arange(center.size) / 60
        day = np.repeat(self.sim.truth["day"], self.sim.scenario.pings_per_cycle) > 0.9
        normal = center[day & (h < 30)].mean()
        hiding = center[day & (h > 90) & (h < 132)].mean()
        self.assertGreater(hiding, normal + 0.7)

    def test_roundtrip_and_preview(self):
        with tempfile.TemporaryDirectory() as d:
            p = save_npz(self.eg, Path(d) / "e.npz")
            e2 = load_npz(p)
            np.testing.assert_array_equal(e2.Sv, self.eg.Sv)
            np.testing.assert_array_equal(e2.ping_time, self.eg.ping_time)
            self.assertEqual(e2.meta, self.eg.meta)
            png = preview_png(e2, Path(d) / "e.png", start="2026-08-09T00:00:00Z", end="2026-08-10T00:00:00Z")
            self.assertTrue(png.startswith(b"\x89PNG"))
            self.assertTrue((Path(d) / "e.png").exists())


if __name__ == "__main__":
    unittest.main()
