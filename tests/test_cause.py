import copy
import json
import shutil
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

from cause import CauseEngine
from cause.store import save_jsonl
from common.config_loader import ConfigError, ConfigLoader, DEFAULT_DIRECTORY
from fusion.fuse import FusedFeatures
from stress.levels import apply_levels, refuge_loss_signal
from stress.model import StressScore


T = datetime(2026, 8, 10, 0, 0, tzinfo=timezone.utc)


def pair(ts=T, cage="cage-003", score=60, surface=25, mid=22, evidence=None, baseline="ready"):
    s = StressScore(cage, ts, score, baseline, "test-m5", evidence={
        "lower_layers_ratio_x": 1.5, "activity_change": -0.2, **(evidence or {})})
    f = FusedFeatures(cage, ts - timedelta(minutes=5), {},
                      {"temp_surface": surface, "temp_mid": mid}, None, 0)
    return s, f


class CauseRulesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        shutil.copytree(DEFAULT_DIRECTORY, self.root / "config")
        self.loader = ConfigLoader(self.root / "config")
        self.engine = CauseEngine(self.loader)

    def modify(self, name, mutate):
        path = self.root / "config" / (name + ".yaml")
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        mutate(data)
        path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")

    def test_r1_exact_boundary_and_all_required_conditions(self):
        result = self.engine.evaluate(*pair())
        c = result.estimates[0]
        self.assertEqual((c.rule_id, c.score, c.probability), ("R1", 1.0, 1.0))
        self.assertEqual(sum(x.probability for x in result.estimates), 1.0)
        self.assertTrue(all(c.evidence["conditions"].values()))
        self.assertFalse(result.immediate_danger)
        for kwargs in ({"surface": 24.99}, {"mid": 22.01},
                       {"evidence": {"lower_layers_ratio_x": 1.49}},
                       {"evidence": {"activity_change": -0.199}}):
            with self.subTest(kwargs=kwargs):
                self.assertEqual(CauseEngine(self.loader).evaluate(*pair(**kwargs)).estimates, ())

    def test_r2_activity_or_cohesion_and_strict_environment_boundary(self):
        for mid in (24.0, 24.01):
            r = CauseEngine(self.loader).evaluate(*pair(mid=mid, evidence={"activity_change": -0.4}))
            self.assertEqual(r.r2_triggered, mid > 24.0)
            self.assertEqual(r.immediate_danger, mid > 24.0)
        r = self.engine.evaluate(*pair(mid=24.5, evidence={"activity_change": -0.4}))
        self.assertEqual(r.estimates[0].rule_id, "R2")
        self.assertIsNone(r.estimates[0].evidence["cohesion_prior_peak"])

    def test_r2_uses_prior_peak_and_excludes_current_point(self):
        first = self.engine.evaluate(*pair(evidence={"cohesion_change": 0.5}))
        self.assertFalse(first.r2_triggered)
        r = self.engine.evaluate(*pair(T + timedelta(minutes=5), mid=24.5,
                                      evidence={"cohesion_change": 0.2, "activity_change": -0.1}))
        self.assertTrue(r.r2_triggered)
        self.assertEqual(r.estimates[0].evidence["cohesion_prior_peak"], 0.5)
        self.assertAlmostEqual(r.estimates[0].evidence["cohesion_drop_observed"], 0.3)

    def test_lookback_inclusive_boundary_and_expiry(self):
        for elapsed, expected in ((12, True), (12.001, False)):
            engine = CauseEngine(self.loader)
            engine.evaluate(*pair(evidence={"cohesion_change": 0.5}))
            r = engine.evaluate(*pair(T + timedelta(hours=elapsed), mid=24.5,
                                     evidence={"cohesion_change": 0.1, "activity_change": -0.1}))
            self.assertEqual(r.r2_triggered, expected)

    def test_history_isolated_by_cage(self):
        self.engine.evaluate(*pair(cage="A", evidence={"cohesion_change": 1.0}))
        result = self.engine.evaluate(*pair(cage="B", mid=24.5, evidence={"cohesion_change": 0.0}))
        self.assertFalse(result.r2_triggered)
        self.assertEqual(result.estimates, ())

    def test_low_score_hides_causes_but_preserves_r2_danger_signal(self):
        for score in (39, None):
            r = CauseEngine(self.loader).evaluate(*pair(score=score, mid=24.5, evidence={"activity_change": -0.4}))
            self.assertEqual(r.estimates, ())
            self.assertTrue(r.immediate_danger)
            self.assertEqual(r.suppression, "low_stress" if score == 39 else "missing_score")
            s, _ = pair(score=score)
            changes = apply_levels([s], [r.immediate_danger])
            self.assertEqual(s.level, "위험")
            self.assertEqual(changes[0].reason, "refuge_loss")
        self.assertEqual(self.engine.evaluate(*pair(score=40)).estimates[0].rule_id, "R1")

    def test_warming_up_produces_no_causes_or_history(self):
        r = self.engine.evaluate(*pair(baseline="warming_up", mid=24.5,
                                      evidence={"activity_change": -0.5, "cohesion_change": 2}))
        self.assertEqual(r.estimates, ())
        self.assertFalse(r.immediate_danger)
        r = self.engine.evaluate(*pair(T + timedelta(minutes=5), mid=24.5, evidence={"cohesion_change": 0}))
        self.assertFalse(r.r2_triggered)

    def test_missing_environment_or_evidence_does_not_become_zero(self):
        for key in ("lower_layers_ratio_x", "activity_change"):
            s, f = pair()
            s.evidence.pop(key)
            self.assertEqual(CauseEngine(self.loader).evaluate(s, f).estimates, ())
        for kwargs in ({"surface": None}, {"mid": None}):
            self.assertEqual(CauseEngine(self.loader).evaluate(*pair(**kwargs)).estimates, ())
        s, f = pair(mid=24.5)
        s.evidence = {}
        self.assertFalse(self.engine.evaluate(s, f).r2_triggered)

    def test_wrong_cage_timestamp_and_duplicate_are_rejected_without_progress(self):
        s, f = pair()
        for bad in (replace(f, cage_id="other"), replace(f, ts=T), replace(f, ts=T.replace(tzinfo=None))):
            with self.assertRaises(ValueError):
                self.engine.evaluate(s, bad)
        with self.assertRaises(ValueError):
            self.engine.evaluate_series([s], [f, f])
        self.assertEqual(self.engine.evaluate(s, f).estimates[0].rule_id, "R1")
        with self.assertRaises(ValueError):
            self.engine.evaluate(s, f)

    def test_invalid_numeric_input_cannot_corrupt_history(self):
        for value in (True, float("nan"), float("inf"), "0.3"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.engine.evaluate(*pair(evidence={"cohesion_change": value}))
        for score in (-1, 101, True):
            with self.assertRaises(ValueError):
                self.engine.evaluate(*pair(score=score))
        self.assertEqual(self.engine.evaluate(*pair()).estimates[0].rule_id, "R1")

    def test_interleaved_cages_and_batch_validation_are_atomic(self):
        pairs = [pair(cage="A"), pair(cage="B"), pair(T + timedelta(minutes=5), cage="A")]
        scores, fused = map(list, zip(*pairs))
        with self.assertRaises(ValueError):
            self.engine.evaluate_series(scores + [scores[0]], fused)
        results = self.engine.evaluate_series(scores, list(reversed(fused)))
        self.assertEqual([r.cage_id for r in results], ["A", "B", "A"])

    def test_config_reload_changes_rule_and_clears_old_peak(self):
        old = self.engine.evaluate(*pair(evidence={"cohesion_change": 1.0}))
        self.modify("cause_rules", lambda d: d["rules"]["R1"]["env"].update(temp_surface_min_c=26))
        new = self.engine.evaluate(*pair(T + timedelta(minutes=5), mid=24.5,
                                        evidence={"cohesion_change": 0.0}))
        self.assertFalse(new.r2_triggered)
        self.assertNotEqual(old.config_revision, new.config_revision)
        self.assertEqual(CauseEngine(self.loader).evaluate(*pair()).estimates, ())

    def test_invalid_rule_configs_fail_closed(self):
        original = (self.root / "config/cause_rules.yaml").read_bytes()
        edits = [lambda d: d["rules"]["R2"]["behavior"].update(lookback_h=0),
                 lambda d: d["rules"]["R2"]["behavior"].pop("cohesion_drop"),
                 lambda d: d["rules"]["R2"]["behavior"].update(unknown_condition=1),
                 lambda d: d["rules"]["R2"].update(immediate_danger="true"),
                 lambda d: d["probability"].update(scoring="unimplemented"),
                 lambda d: d["rules"]["R1"]["env"].update(temp_diff_min_c=0.5)]
        for edit in edits:
            self.modify("cause_rules", edit)
            with self.assertRaises(ConfigError):
                self.engine.evaluate(*pair())
            (self.root / "config/cause_rules.yaml").write_bytes(original)
        self.assertTrue(self.engine.evaluate(*pair()).estimates)

    def test_snapshot_and_display_policy_are_recorded(self):
        self.modify("thresholds", lambda d: d.update(cause_min_stress=70))
        self.assertEqual(self.engine.evaluate(*pair()).estimates, ())
        r = self.engine.evaluate(*pair(T + timedelta(minutes=5), score=70))
        c = r.estimates[0]
        self.assertTrue(c.display)
        self.assertEqual(c.evidence["config_revision"], r.config_revision)
        self.assertEqual(c.evidence["thresholds"]["env"]["temp_surface_min_c"], 25)
        self.assertEqual(c.evidence["model_version"], "test-m5")

    def test_immediate_danger_is_config_driven(self):
        self.modify("thresholds", lambda d: d.update(immediate_danger_rules=[]))
        r = self.engine.evaluate(*pair(mid=24.5, evidence={"activity_change": -0.4}))
        self.assertTrue(r.r2_triggered)
        self.assertFalse(r.immediate_danger)

    def test_jsonl_and_explicit_db_mapping(self):
        r = self.engine.evaluate(*pair())
        path = save_jsonl([r], self.root / "cause.jsonl")
        saved = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(saved["rule_id"], "R1")
        self.assertEqual(saved["evidence"]["config_revision"], r.config_revision)
        row = r.estimates[0].to_row(3)
        self.assertEqual(set(row), {"cage_id", "ts", "cause", "probability", "rule_id", "evidence"})
        self.assertEqual(row["cage_id"], 3)
        for bad in (True, "3", -1):
            with self.assertRaises(ValueError):
                r.estimates[0].to_row(bad)


class CauseScenarioTest(unittest.TestCase):
    def test_real_heatwave_r1_r2_and_data_side_r2_parity(self):
        from tests.pipeline_cache import scenario, scores
        _, _, fused, labels = scenario("heatwave_typhoon")
        ss = scores("heatwave_typhoon")
        results = CauseEngine().evaluate_series(ss, fused)
        self.assertEqual([r.r2_triggered for r in results], refuge_loss_signal(ss, fused))
        ids = {c.rule_id for r in results for c in r.estimates}
        self.assertEqual(ids, {"R1", "R2", "R7"})
        copy_scores = copy.deepcopy(ss)
        changes = apply_levels(copy_scores, [r.immediate_danger for r in results])
        first_r2 = next(r for r in results if r.immediate_danger)
        self.assertEqual(next(s.level for s in copy_scores if s.ts == first_r2.ts), "위험")
        # This scenario may already be in danger before R2; there need not be a new transition.
        for rule, cause in (("R1", "heat_hiding"), ("R2", "refuge_loss")):
            intervals = [i for i in labels["intervals"] if i["cause"] == cause]
            self.assertTrue(intervals)
            self.assertTrue(any(c.rule_id == rule for r in results for c in r.estimates
                                if any(datetime.fromisoformat(iv["start"]) <= r.ts < datetime.fromisoformat(iv["end"]) for iv in intervals)))

    def test_no_false_r1_r2_for_normal_turbidity_and_feeding(self):
        from tests.pipeline_cache import scenario, scores
        for name in ("normal_14d", "turbidity_event", "feeding_drop"):
            _, _, fused, _ = scenario(name)
            results = CauseEngine().evaluate_series(scores(name), fused)
            self.assertFalse(any(any(c.rule_id!='R7' for c in r.estimates) or r.immediate_danger for r in results), name)
            if name!='feeding_drop':
                self.assertFalse(any(r.estimates for r in results),name)
