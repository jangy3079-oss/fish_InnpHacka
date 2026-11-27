import unittest
from datetime import datetime, timedelta, timezone

import numpy as np

from stress.levels import LevelJudge, apply_levels, load_thresholds, refuge_loss_signal
from tests.pipeline_cache import in_interval, scenario, scores

T0 = datetime(2026, 8, 5, tzinfo=timezone.utc)
STEP = timedelta(minutes=5)


def run(seq, r2=None):
    """[(분 단위 지속 시간, 점수)] → 주기별 단계 목록."""
    j = LevelJudge(cfg=load_thresholds())
    out, t, i = [], T0, 0
    for minutes, score in seq:
        for _ in range(minutes // 5):
            out.append(j.update(t, score, bool(r2 and r2(i))))
            t += STEP
            i += 1
    return out, j


class LevelJudgeTest(unittest.TestCase):
    def test_caution_needs_2h(self):
        lv, _ = run([(115, 50)])
        self.assertEqual(lv[-1], "정상")
        lv, _ = run([(120, 50)])
        self.assertEqual(lv[-1], "주의")

    def test_interrupted_run_resets(self):
        lv, _ = run([(60, 50), (5, 30), (115, 50)])
        self.assertEqual(lv[-1], "정상")

    def test_danger_needs_1h(self):
        lv, _ = run([(55, 90)])
        self.assertEqual(lv[-1], "정상")
        lv, _ = run([(60, 90)])
        self.assertEqual(lv[-1], "위험")

    def test_refuge_loss_immediate(self):
        lv, j = run([(5, 30)], r2=lambda i: True)
        self.assertEqual(lv[0], "위험")
        self.assertEqual(j.changes[0].reason, "refuge_loss")

    def test_release_needs_6h(self):
        lv, _ = run([(120, 65), (355, 20)])
        self.assertEqual(lv[-1], "경고")
        lv, j = run([(120, 65), (360, 20)])
        self.assertEqual(lv[-1], "정상")
        self.assertEqual(j.changes[-1].reason, "released")

    def test_release_steps_to_peak_of_window(self):
        lv, _ = run([(120, 85), (360, 45)])
        self.assertEqual(lv[-1], "주의")

    def test_brief_rise_restarts_release(self):
        # 해제는 '6시간 연속' 조건이라 중간에 다시 오르면 처음부터 센다
        lv, _ = run([(120, 65), (300, 20), (5, 70), (300, 20)])
        self.assertEqual(lv[-1], "경고")
        lv, _ = run([(120, 65), (300, 20), (5, 70), (360, 20)])
        self.assertEqual(lv[-1], "정상")

    def test_missing_score_holds(self):
        j = LevelJudge(cfg=load_thresholds())
        t = T0
        for _ in range(24):
            j.update(t, 65); t += STEP
        for _ in range(200):
            self.assertEqual(j.update(t, None), "경고"); t += STEP


class ScenarioLevelTest(unittest.TestCase):
    @staticmethod
    def levels(name):
        _, _, fused, lab = scenario(name)
        ss = scores(name)
        ch = apply_levels(ss, refuge_loss_signal(ss, fused))
        return ss, ch, lab

    def test_vc05_no_false_alarm(self):
        for name in ("turbidity_event", "feeding_drop", "normal_14d"):
            ss, ch, lab = self.levels(name)
            self.assertTrue(lab["false_alarm_check"])
            self.assertEqual(ch, [], name)

    def test_vc04_lead_time_positive(self):
        ss, ch, lab = self.levels("heatwave_typhoon")
        first = next(c.ts for c in ch if c.to_level != "정상")
        adv = datetime.fromisoformat(lab["markers"]["advisory_28c"].replace("Z", "+00:00"))
        self.assertGreater(adv - first, timedelta(hours=24))

    def test_refuge_loss_is_danger(self):
        ss, ch, lab = self.levels("heatwave_typhoon")
        ref = in_interval(ss, lab, "refuge_loss")
        after_r2 = [s for s in ref if s.ts >= ref[0].ts + timedelta(hours=2)]
        self.assertTrue(all(s.level == "위험" for s in after_r2))

    def test_vc02_recall(self):
        for name in ("heatwave_typhoon", "disease_suspect", "parasite_suspect"):
            ss, ch, lab = self.levels(name)
            hit, tot = 0, 0
            for iv in lab["intervals"]:
                if not iv["alert_expected"]:
                    continue
                sub = in_interval(ss, {"intervals": [iv]}, iv["cause"])
                tot += len(sub)
                hit += sum(s.level != "정상" for s in sub)
            self.assertGreaterEqual(hit / tot, 0.8, name)

    def test_rows_have_level(self):
        ss, _, _ = self.levels("parasite_suspect")
        self.assertIn(ss[-1].to_row()["level"], ("주의", "경고", "위험"))


if __name__ == "__main__":
    unittest.main()
