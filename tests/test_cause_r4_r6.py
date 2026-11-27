"""R4 적조·R6 작업 스트레스 (#174)."""
import json
import shutil
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

from cause import CauseContext, CauseEngine
from common.config_loader import ConfigError, ConfigLoader, DEFAULT_DIRECTORY
from tests.test_cause import T, pair


def calm(step, score, act=0.0):
    """고수온·저산소·기생충 조건이 없는 주기 (R4·R6만 볼 수 있게)."""
    s, f = pair(T + timedelta(minutes=5 * step), score=score, surface=22, mid=19,
                evidence={"activity_change": act, "lower_layers_ratio_x": 1.0})
    f.env["do"] = 7.0
    return s, f


def run(pairs, contexts):
    ss, ff = map(list, zip(*pairs))
    return CauseEngine().evaluate_series(ss, ff, contexts=contexts)


def top(r):
    return r.estimates[0].rule_id if r.estimates else None


class ContextTest(unittest.TestCase):
    def test_validation(self):
        at = T
        CauseContext(red_tide_advisory=True, work_log_at=at, work_log_type="net_change")
        for bad in (dict(red_tide_advisory=1), dict(work_log_at=at), dict(work_log_type="net_change"),
                    dict(work_log_at=datetime(2026, 8, 10), work_log_type="net_change"),
                    dict(work_log_at=at, work_log_type=" ")):
            with self.assertRaises(ValueError, msg=bad):
                CauseContext(**bad)


class R4Test(unittest.TestCase):
    def test_requires_advisory_and_activity_jump(self):
        for advisory, act, expect in ((True, 0.6, "R4"), (True, -0.55, "R4"), (True, 0.49, None),
                                      (None, 0.6, None), (False, 0.6, None)):
            s, f = calm(1, 55, act)
            r = CauseEngine().evaluate(s, f, context=CauseContext(red_tide_advisory=advisory))
            self.assertEqual(top(r), expect, (advisory, act))
        s, f = calm(1, 55, 0.6)
        r = CauseEngine().evaluate(s, f, context=CauseContext(red_tide_advisory=True))
        ev = r.estimates[0].evidence
        self.assertTrue(ev["red_tide_advisory"])
        self.assertEqual(ev["conditions"], {"red_tide_advisory": True, "activity_jump": True})

    def test_low_stress_suppressed(self):
        s, f = calm(1, 30, 0.8)
        r = CauseEngine().evaluate(s, f, context=CauseContext(red_tide_advisory=True))
        self.assertEqual((r.estimates, r.suppression), ((), "low_stress"))


class R6Test(unittest.TestCase):
    WORK_STEP = 72                      # 6시간 뒤 작업

    def series(self, after_scores):
        """작업 전 6시간 지수 20, 작업 후 after_scores(시간 → 지수)."""
        work_at = T + timedelta(minutes=5 * self.WORK_STEP)
        pairs, ctx = [], []
        for i in range(1, self.WORK_STEP + 12 * 80):
            ts = T + timedelta(minutes=5 * i)
            h = (ts - work_at).total_seconds() / 3600
            score = 20 if h <= 0 else after_scores(h)
            pairs.append(calm(i, score))
            done = ts >= work_at
            ctx.append(CauseContext(advisory_active=False, work_log_at=work_at if done else None,
                                    work_log_type="net_change" if done else None))
        return run(pairs, ctx), work_at

    def test_not_recovered_after_24h(self):
        results, work_at = self.series(lambda h: 80 if h < 30 else 50)
        by_h = {round((r.ts - work_at).total_seconds() / 3600, 2): top(r) for r in results}
        self.assertIsNone(by_h[23.0])          # 24시간 전에는 아직 '회복 안 됨'이 아님
        self.assertEqual(by_h[24.0], "R6")
        self.assertEqual(by_h[60.0], "R6")
        self.assertIsNone(by_h[73.0])          # 72시간 지나면 작업 기록을 원인 후보로 보지 않음
        r = next(r for r in results if r.ts - work_at == timedelta(hours=30))
        ev = r.estimates[0].evidence
        self.assertEqual((ev["pre_work_mean_score"], ev["post_work_peak_score"], ev["score_rise_after_work"]),
                         (20, 80, 60))
        self.assertEqual(ev["work_log_type"], "net_change")

    def test_no_rise_after_work_means_no_r6(self):
        results, _ = self.series(lambda h: 35 if h <= 6 else 50)   # 직후 6시간(정각 포함) 상승 15점 < 20점, 그 뒤 상승은 작업 탓으로 보지 않음
        self.assertFalse(any(top(r) == "R6" for r in results))

    def test_recovered_or_unknown_work_type(self):
        results, _ = self.series(lambda h: 80 if h < 20 else 20)
        self.assertFalse(any(top(r) == "R6" for r in results))
        work_at = T
        s, f = calm(300, 70)
        r = CauseEngine().evaluate(s, f, context=CauseContext(work_log_at=work_at, work_log_type="feeding"))
        self.assertFalse(r.estimates)


class ConfigTest(unittest.TestCase):
    def test_invalid_r4_r6_config_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "config"
            shutil.copytree(DEFAULT_DIRECTORY, folder)
            path = folder / "cause_rules.yaml"
            config = yaml.safe_load(path.read_text(encoding="utf-8"))
            for mutate in (lambda d: d["rules"]["R4"]["behavior"].update(activity_change_abs_min=0),
                           lambda d: d["rules"]["R4"]["env"].update(red_tide_advisory=False),
                           lambda d: d["rules"]["R6"]["behavior"].update(not_recovered_hours=100),
                           lambda d: d["rules"]["R6"]["env"].update(work_log=[]),
                           lambda d: d["probability"].update(tie_break_order=["R2", "R3", "R8", "R1", "R5"])):
                data = json.loads(json.dumps(config))
                mutate(data)
                path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
                with self.assertRaises(ConfigError):
                    ConfigLoader(folder).snapshot()


class ScenarioTest(unittest.TestCase):
    def test_red_tide_and_net_change_top_rank(self):
        from pipeline.contexts import scenario_contexts
        from tests.pipeline_cache import scenario, scores
        p = lambda s: datetime.fromisoformat(s.replace("Z", "+00:00"))
        for name, expected in (("red_tide", "R4"), ("net_change", "R6")):
            sim, _, fused, labels = scenario(name)
            ss = scores(name)
            results = CauseEngine().evaluate_series(ss, fused, contexts=scenario_contexts(sim.scenario, [s.ts for s in ss]))
            ivs = [iv for iv in labels["intervals"] if iv["rule_id"] == expected]
            self.assertEqual(len(ivs), 1, name)
            inside = [r for r in results if p(ivs[0]["start"]) <= r.ts < p(ivs[0]["end"]) and r.estimates]
            self.assertGreater(len(inside), 50, name)
            self.assertTrue(all(top(r) == expected for r in inside), name)
            outside = [r for r in results if not p(ivs[0]["start"]) <= r.ts < p(ivs[0]["end"]) and r.estimates]
            self.assertTrue(all(top(r) == expected for r in outside), name)   # 다른 원인으로 새지 않음

    def test_contexts_from_scenario(self):
        from pipeline.contexts import scenario_contexts
        from simulator.scenario import load_scenario
        sc = load_scenario("red_tide")
        at = lambda h: sc.start + timedelta(hours=h)
        c = scenario_contexts(sc, [at(25), at(26), at(65.9), at(66)])
        self.assertEqual([x.red_tide_advisory for x in c], [False, True, True, False])
        self.assertIsNone(scenario_contexts(load_scenario("heatwave_typhoon"), [at(1)])[0].red_tide_advisory)
        sc = load_scenario("net_change")
        c = scenario_contexts(sc, [at(33.9), at(34), at(90)])
        self.assertEqual([x.work_log_at for x in c], [None, at(34), at(34)])
        self.assertEqual(c[1].work_log_type, "net_change")
        self.assertEqual(c[1].work_log_at.tzinfo, timezone.utc)


if __name__ == "__main__":
    unittest.main()
