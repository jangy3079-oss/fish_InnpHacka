import importlib
import os
import tempfile
import unittest
from pathlib import Path

from common import config
from fusion import fuse as fuse_mod
from tests.pipeline_cache import scenario, scores


class ConfigTest(unittest.TestCase):
    def tearDown(self):
        os.environ.pop("ROCKFISH_CONFIG_DIR", None)
        config.clear_cache()

    def use(self, text):
        d = tempfile.mkdtemp()
        Path(d, "thresholds.yaml").write_text(text, encoding="utf-8")
        os.environ["ROCKFISH_CONFIG_DIR"] = d
        config.clear_cache()

    def test_repo_yaml_is_read(self):
        p = fuse_mod.params()
        self.assertEqual(config.get("thresholds", "fusion.gate_low", None), p["q_min"])
        self.assertEqual(config.get("thresholds", "quality.camera_only_min", None), p["q_camera_only"])
        from stress import model
        self.assertEqual(model.MIN_BASELINE_WINDOWS, config.get("thresholds", "baseline.min_windows", None))
        self.assertEqual(model.DAY_START, 6.0)
        self.assertEqual(model.WINDOW_CYCLES, 12)

    def test_fusion_follows_yaml(self):
        self.use("fusion: {gate_low: 0.5, full_at: 0.9, max_camera_weight: 0.4}\nquality: {camera_only_min: 0.8}\n")
        self.assertEqual(fuse_mod.camera_weight(0.45), 0.0)
        self.assertAlmostEqual(fuse_mod.camera_weight(0.7), 0.2)
        self.assertEqual(fuse_mod.camera_weight(0.95), 0.4)

    def test_missing_file_uses_defaults(self):
        os.environ["ROCKFISH_CONFIG_DIR"] = tempfile.mkdtemp()
        config.clear_cache()
        self.assertEqual(fuse_mod.camera_weight(0.7), fuse_mod.W_MAX)

    def test_stress_min_windows_from_yaml(self):
        self.use("baseline: {min_windows: 400}\n")
        from stress import model
        m = importlib.reload(model)
        try:
            self.assertEqual(m.MIN_BASELINE_WINDOWS, 400)
            _, _, fused, _ = scenario("normal_14d")
            self.assertEqual(m.StressModel("cage-003").fit(fused).status, "warming_up")   # 336 < 400
        finally:
            os.environ.pop("ROCKFISH_CONFIG_DIR", None)
            config.clear_cache()
            importlib.reload(model)


class StressRowContractTest(unittest.TestCase):
    """#68 DB 계약: ready면 score·level 필수, warming_up이면 둘 다 null."""

    def test_rows(self):
        from stress.levels import apply_levels
        from stress.model import StressModel
        from stress.store import rows
        ss = scores("turbidity_event")
        apply_levels(ss)
        out = rows(ss)
        self.assertLess(len(out), len(ss))                       # 첫 1시간(윈도우 덜 참) 제외
        self.assertTrue(all(r["score"] is not None and r["level"] for r in out if r["baseline_status"] == "ready"))

        _, _, fused, _ = scenario("normal_14d")
        warm = StressModel("cage-003").fit(fused[:12 * 24 * 3]).score_series(fused[:100])
        apply_levels(warm)
        wr = rows(warm)
        self.assertEqual(len(wr), len(warm))
        self.assertTrue(all(r["score"] is None and r["level"] is None for r in wr))


if __name__ == "__main__":
    unittest.main()
