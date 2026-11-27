import unittest

import numpy as np

from pipeline.validate import expected_causes, run_seed, summarize, to_markdown
from stress.model import StressModel
from tests.pipeline_cache import scenario


class RangeCorrectionTest(unittest.TestCase):
    """Isolation Forest는 학습 범위 밖 점을 범위 끝 점과 같게 본다 → 1차원 묶음 범위 보정 (#110)."""

    def test_far_rubbing_is_anomalous(self):
        _, _, fused, _ = scenario("normal_14d")
        det = StressModel("cage-003").fit(fused).detectors[("day", "rubbing")]
        hi = float(det.hi[0])
        self.assertEqual(float(det.range_score(np.array([[hi]]))[0]), 0.0)          # 범위 끝은 보정 없음
        self.assertGreaterEqual(float(det.score(np.array([[hi * 3]]))[0]), 79)     # 3배 → 상한 근처
        self.assertGreaterEqual(float(det.score(np.array([[hi + 1.0 * det.iqr[0]]]))[0]), 40)

    def test_multi_feature_sets_not_range_corrected(self):
        _, _, fused, _ = scenario("normal_14d")
        det = StressModel("cage-003").fit(fused).detectors[("day", "camera")]
        far = det.hi + 10 * det.iqr
        self.assertEqual(float(det.range_score(far[None, :])[0]), 0.0)


class ValidateSeed3Test(unittest.TestCase):
    """시드 3에서 기생충 의심을 놓치던 문제 회귀 테스트."""

    @classmethod
    def setUpClass(cls):
        cls.r = run_seed(3)

    def test_vc_on_seed3(self):
        r = self.r
        self.assertGreaterEqual(r["parasite_suspect"]["vc02"]["recall"], 0.8)
        self.assertGreaterEqual(r["heatwave_typhoon"]["vc02"]["recall"], 0.8)
        self.assertTrue(r["heatwave_typhoon"]["vc04"]["pass"])
        self.assertEqual(r["turbidity_event"]["alarms"], 0)
        self.assertEqual(r["feeding_drop"]["alarms"], 0)
        self.assertTrue(r["turbidity_event"]["vc01"]["pass"])
        # VC-03: 원인 엔진 1순위 (특보 없음 명시, 시나리오 YAML advisory_active: false)
        for n in ("heatwave_typhoon", "turbidity_event", "disease_suspect", "parasite_suspect"):
            self.assertTrue(r[n]["vc03"]["pass"], n)
        self.assertEqual(r["turbidity_event"]["vc03"]["cycles_with_cause"], 0)
        # VC-03 R7 (#180): 섭식 저하 주 구간 1순위 R7, 다른 원인 0건, 경보 없음
        v7 = r["feeding_drop"]["vc03_r7"]
        self.assertTrue(v7["main_interval_pass"])
        self.assertEqual(v7["other_cause_cycles"], 0)
        for n in ("red_tide", "net_change"):
            self.assertTrue(r[n]["vc03"]["pass"], n)

    def test_markdown(self):
        res = {"run_at": "test", "config_revision": "x", "runs": [self.r], "summary": summarize([self.r]),
               "vc03_expected": expected_causes()}
        md = to_markdown(res)
        self.assertIn("VC-02", md)
        self.assertIn("R8 기생충 의심", md)
        self.assertIn("R7 섭식 저하", md)


if __name__ == "__main__":
    unittest.main()
