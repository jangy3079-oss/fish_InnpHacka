import json
import shutil
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

import yaml

from cause import CauseContext, CauseEngine
from common.config_loader import ConfigError, ConfigLoader, DEFAULT_DIRECTORY
from tests.test_cause import T, pair

CLEAR = CauseContext(advisory_active=False)


def observation(step=1, *, score=60, rubbing=4.0, quality=0.9, oxygen=7.0, surface=22.0, cage="A"):
    s, f = pair(T + timedelta(minutes=5 * step), cage=cage, score=score, surface=surface, mid=19,
                evidence={"rubbing_rate_x": rubbing, "surface_ratio_x": 1.0})
    f.env["do"] = oxygen
    f.quality = quality
    return s, f


def run_observations(pairs, contexts=None, engine=None):
    ss, ff = map(list, zip(*pairs))
    return (engine or CauseEngine()).evaluate_series(ss, ff, contexts=[CLEAR] * len(ss) if contexts is None else contexts)


def trend_pairs(values=(40, 50, 60), rubbing=1.0):
    return [observation(i + 1, score=values[i // 288], rubbing=rubbing) for i in range(288 * len(values))]


class ExtendedCauseTest(unittest.TestCase):
    def test_r3_boundary_and_optional_camera_support(self):
        for oxygen, expect in ((4.0, False), (3.999, True), (None, False)):
            s, f = observation(oxygen=oxygen)
            s.evidence["surface_ratio_x"] = 1.5
            r = CauseEngine().evaluate(s, f, context=CauseContext(camera_surface_breathing=True))
            self.assertEqual(bool(r.estimates), expect)
            if expect:
                self.assertEqual(r.estimates[0].rule_id, "R3")
                self.assertTrue(r.estimates[0].evidence["camera_breathing_support"])
        for ratio in (1.499, None):
            s, f = observation(oxygen=3)
            s.evidence["surface_ratio_x"] = ratio
            self.assertEqual(CauseEngine().evaluate(s, f).estimates, ())

    def test_r3_does_not_require_camera_or_advisory(self):
        s, f = observation(oxygen=3, quality=None)
        s.evidence["surface_ratio_x"] = 2
        r = CauseEngine().evaluate(s, f)
        self.assertEqual(r.estimates[0].rule_id, "R3")
        self.assertFalse(r.estimates[0].evidence["camera_breathing_support"])

    def test_r3_and_r1_normalize_and_use_configured_tie_order(self):
        s, f = pair(evidence={"surface_ratio_x": 2})
        f.env["do"] = 3
        r = CauseEngine().evaluate(s, f)
        self.assertEqual([c.rule_id for c in r.estimates], ["R3", "R1"])
        self.assertEqual([c.probability for c in r.estimates], [0.5, 0.5])

    def test_r8_exact_six_observed_hours_and_current_observation(self):
        r = run_observations([observation(i) for i in range(1, 73)])
        self.assertFalse(any(x.estimates for x in r[:-1]))
        self.assertEqual(r[-1].estimates[0].rule_id, "R8")
        self.assertEqual(r[-1].estimates[0].evidence["rubbing_observed_hours"], 6)
        self.assertEqual(r[-1].estimates[0].evidence["rubbing_rate_x"], 4)

    def test_r8_observed_below_threshold_resets(self):
        pairs = [observation(i, rubbing=2.99 if i == 60 else 4) for i in range(1, 133)]
        results = run_observations(pairs)
        self.assertFalse(results[130].estimates)
        self.assertEqual(results[131].estimates[0].rule_id, "R8")

    def test_r8_night_pause_does_not_count_blind_time(self):
        pairs = [observation(i, rubbing=None if 36 < i <= 48 else 4,
                             quality=0.2 if 36 < i <= 48 else 0.9) for i in range(1, 85)]
        results = run_observations(pairs)
        self.assertFalse(any(r.estimates for r in results[:-1]))
        self.assertEqual(results[-1].estimates[0].evidence["rubbing_observed_hours"], 6)

    def test_r8_stream_gap_and_other_cage_cannot_complete_duration(self):
        engine = CauseEngine()
        run_observations([observation(i) for i in range(1, 73)], engine=engine)
        self.assertFalse(engine.evaluate(*observation(74), context=CLEAR).estimates)
        self.assertFalse(engine.evaluate(*observation(74, cage="B"), context=CLEAR).estimates)

    def test_r8_exact_max_pause_keeps_only_observed_duration(self):
        # 5h55 observed + 16h unobserved + 5min observed = 6h, not 22h.
        pairs = [observation(i, quality=0.2 if 71 < i <= 263 else 0.9)
                 for i in range(1, 265)]
        results = run_observations(pairs)
        self.assertFalse(any(r.estimates for r in results[:-1]))
        evidence = results[-1].estimates[0].evidence
        self.assertEqual(evidence['rubbing_observed_hours'], 6)
        self.assertEqual(evidence['rubbing_window_start'], T.isoformat())

    def test_r8_one_cycle_over_max_pause_requires_six_new_hours(self):
        pairs = [observation(i, rubbing=None if 71 < i <= 264 else 4)
                 for i in range(1, 337)]
        results = run_observations(pairs)
        self.assertFalse(any(r.estimates for r in results[:-1]))
        evidence = results[-1].estimates[0].evidence
        self.assertEqual(evidence['rubbing_observed_hours'], 6)
        self.assertEqual(evidence['rubbing_window_start'], (T + timedelta(minutes=5*264)).isoformat())

    def test_r8_confirmed_cause_is_not_published_during_pause_or_low_score(self):
        engine = CauseEngine()
        run_observations([observation(i) for i in range(1, 73)], engine=engine)
        paused = engine.evaluate(*observation(73, quality=0.2), context=CLEAR)
        self.assertFalse(paused.estimates)
        low = engine.evaluate(*observation(74, score=35), context=CLEAR)
        self.assertFalse(low.estimates)
        self.assertEqual(low.suppression, 'low_stress')
        missing = engine.evaluate(*observation(75, score=None), context=CLEAR)
        self.assertFalse(missing.estimates)
        self.assertEqual(missing.suppression, 'missing_score')
        self.assertEqual(engine.evaluate(*observation(76), context=CLEAR).estimates[0].rule_id, 'R8')

    def test_r8_each_valid_observation_restarts_pause_budget(self):
        # Independent pauses, each 16h, never add to the six observed hours.
        pairs = [observation(1)]
        pairs += [observation(i, quality=None) for i in range(2, 194)]
        pairs += [observation(194)]
        pairs += [observation(i, rubbing=None) for i in range(195, 387)]
        pairs += [observation(i) for i in range(387, 457)]
        results = run_observations(pairs)
        self.assertFalse(any(r.estimates for r in results[:-1]))
        self.assertEqual(results[-1].estimates[0].evidence['rubbing_observed_hours'], 6)

    def test_r8_pause_budget_comes_from_validated_yaml(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / 'config'
            shutil.copytree(DEFAULT_DIRECTORY, folder)
            path = folder / 'cause_rules.yaml'
            config = yaml.safe_load(path.read_text(encoding='utf-8'))
            config['rules']['R8']['behavior']['max_pause_hours'] = 1
            path.write_text(yaml.safe_dump(config, allow_unicode=True), encoding='utf-8')
            for missing_cycles, expected in ((12, True), (13, False)):
                end_pause = 71 + missing_cycles
                pairs = [observation(i, quality=0.2 if 71 < i <= end_pause else 0.9)
                         for i in range(1, end_pause + 2)]
                result = run_observations(pairs, engine=CauseEngine(ConfigLoader(folder)))[-1]
                self.assertEqual(bool(result.estimates), expected)

    def test_r8_normal_observation_after_pause_resets_immediately(self):
        pairs = [observation(i, quality=0.2 if 72 <= i <= 83 else 0.9,
                             rubbing=2.99 if i == 84 else 4) for i in range(1, 157)]
        results = run_observations(pairs)
        self.assertFalse(any(r.estimates for r in results[:-1]))
        self.assertEqual(results[-1].estimates[0].evidence['rubbing_observed_hours'], 6)

    def test_r8_ineligible_cycle_during_pause_discards_prior_observations(self):
        for context in (CauseContext(), CauseContext(advisory_active=True)):
            pairs = [observation(i, quality=0.2 if i == 72 else 0.9) for i in range(1, 145)]
            contexts = [CLEAR]*len(pairs)
            contexts[71] = context
            results = run_observations(pairs, contexts)
            self.assertFalse(any(r.estimates for r in results[:-1]))
            self.assertEqual(results[-1].estimates[0].evidence['rubbing_observed_hours'], 6)

    def test_r8_advisory_active_unknown_or_missing_environment_blocks(self):
        for context in (CauseContext(), CauseContext(advisory_active=True)):
            pairs = [observation(i) for i in range(1, 90)]
            self.assertFalse(any(r.estimates for r in run_observations(pairs, [context] * len(pairs))))
        for oxygen, surface in ((None, 22), (3.9, 22), (7, 25)):
            results = run_observations([observation(i, oxygen=oxygen, surface=surface) for i in range(1, 90)])
            self.assertFalse(any(c.rule_id in ("R5", "R8") for r in results for c in r.estimates))

    def test_r8_quality_and_ratio_exact_thresholds(self):
        pairs = [observation(i, rubbing=3, quality=0.6) for i in range(1, 73)]
        self.assertEqual(run_observations(pairs)[-1].estimates[0].rule_id, "R8")
        pairs = [observation(i, rubbing=3, quality=0.599) for i in range(1, 90)]
        self.assertFalse(any(r.estimates for r in run_observations(pairs)))

    def test_r5_completed_daily_windows_and_saved_evidence(self):
        results = run_observations(trend_pairs())
        self.assertFalse(any(r.estimates for r in results[:-1]))
        c = results[-1].estimates[0]
        self.assertEqual(c.rule_id, "R5")
        self.assertEqual(c.evidence["daily_mean_scores"], [40, 50, 60])
        self.assertEqual(c.evidence["daily_coverage"], [1, 1, 1])
        json.dumps(c.to_dict(), allow_nan=False)

    def test_r5_flat_spike_or_sparse_history_is_not_gradual_trend(self):
        for values in ((50, 50, 50), (40, 70, 70), (60, 50, 40)):
            self.assertFalse(any(r.estimates for r in run_observations(trend_pairs(values))))
        sparse = [p for i, p in enumerate(trend_pairs()) if i % 12 == 0]
        self.assertFalse(any(r.estimates for r in run_observations(sparse)))

    def test_r5_normal_daily_averages_with_one_high_point_are_not_disease(self):
        pairs = trend_pairs((25, 28, 31))
        pairs[-1][0].score = 79
        self.assertFalse(any(r.estimates for r in run_observations(pairs)))

    def test_r5_advisory_unknown_does_not_mean_no_advisory(self):
        pairs = trend_pairs()
        contexts = [CLEAR] * len(pairs)
        contexts[400] = CauseContext()
        self.assertFalse(any(r.estimates for r in run_observations(pairs, contexts)))

    def test_r5_and_r8_candidates_keep_specific_sign_first(self):
        r = run_observations(trend_pairs(rubbing=4))[-1]
        self.assertEqual([c.rule_id for c in r.estimates], ["R8", "R5"])
        self.assertEqual(sum(c.probability for c in r.estimates), 1)

    def test_r5_low_current_score_does_not_automatically_keep_previous_cause(self):
        engine = CauseEngine()
        first = run_observations(trend_pairs(), engine=engine)[-1]
        self.assertEqual(first.estimates[0].rule_id, 'R5')
        low = engine.evaluate(*observation(865, score=35, rubbing=1), context=CLEAR)
        self.assertFalse(low.estimates)
        self.assertEqual(low.suppression, 'low_stress')
        resumed = engine.evaluate(*observation(866, score=60, rubbing=1), context=CLEAR)
        self.assertEqual(resumed.estimates[0].rule_id, 'R5')

    def test_invalid_context_or_numeric_input_does_not_advance_history(self):
        for flag in (1, "false"):
            with self.assertRaises(ValueError):
                CauseContext(advisory_active=flag)
        engine = CauseEngine()
        s, f = observation()
        with self.assertRaises(ValueError):
            engine.evaluate_series([s], [f], contexts=[])
        with self.assertRaises(ValueError):
            engine.evaluate(s, f, context=False)
        for field, value in (("do", float("nan")),):
            f.env[field] = value
            with self.assertRaises(ValueError):
                engine.evaluate(s, f, context=CLEAR)
        f.env["do"] = 7
        self.assertEqual(engine.evaluate(s, f, context=CLEAR).estimates, ())
        with self.assertRaises(ValueError):
            engine.evaluate(*observation(1.2), context=CLEAR)  # Overlapping cycles cannot inflate temporal coverage.

    def test_config_change_resets_new_temporal_history_and_invalid_schema_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "config"
            shutil.copytree(DEFAULT_DIRECTORY, folder)
            engine = CauseEngine(ConfigLoader(folder))
            run_observations([observation(i) for i in range(1, 73)], engine=engine)
            path = folder / "cause_rules.yaml"
            config = yaml.safe_load(path.read_text(encoding="utf-8"))
            config["rules"]["R8"]["behavior"]["sustained_hours_min"] = 7
            path.write_text(yaml.safe_dump(config, allow_unicode=True), encoding="utf-8")
            self.assertFalse(engine.evaluate(*observation(73), context=CLEAR).estimates)
            for mutate in (lambda d: d["rules"]["R5"]["behavior"].update(min_daily_coverage=0),
                           lambda d: d["rules"]["R8"]["behavior"].update(missing_observation="invent"),
                           lambda d: d["rules"]["R8"]["behavior"].pop('max_pause_hours'),
                           lambda d: d["rules"]["R8"]["behavior"].update(max_pause_hours=0),
                           lambda d: d["rules"]["R8"]["behavior"].update(max_pause_hours=-1),
                           lambda d: d["probability"].update(tie_break_order=["R5"]),
                           lambda d: d["rules"]["R3"]["env"].update(do_max_mg_l=-1)):
                data = json.loads(json.dumps(config))
                mutate(data)
                path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
                with self.assertRaises(ConfigError):
                    engine.evaluate(*observation(74), context=CLEAR)


class ExtendedScenarioTest(unittest.TestCase):
    def test_real_scenarios_top_rank_and_no_truth_leakage(self):
        from tests.pipeline_cache import scenario, scores
        for name, expected in (("hypoxia", "R3"), ("disease_suspect", "R5"), ("parasite_suspect", "R8")):
            _, _, fused, labels = scenario(name)
            ss = scores(name)
            results = CauseEngine().evaluate_series(ss, fused, contexts=[CLEAR] * len(ss))
            ivs = [iv for iv in labels["intervals"] if iv["rule_id"] == expected]
            self.assertTrue(ivs, name)
            candidates = [r for r in results if r.estimates and any(
                datetime.fromisoformat(iv["start"]) <= r.ts < datetime.fromisoformat(iv["end"]) for iv in ivs)]
            self.assertTrue(candidates, name)
            self.assertTrue(all(r.estimates[0].rule_id == expected for r in candidates), name)
            self.assertTrue(all(abs(sum(c.probability for c in r.estimates) - 1) < 1e-9 for r in candidates))

    def test_no_causes_in_normal_turbidity_feeding(self):
        from tests.pipeline_cache import scenario, scores
        for name in ("normal_14d", "turbidity_event", "feeding_drop"):
            _, _, fused, _ = scenario(name)
            ss = scores(name)
            results = CauseEngine().evaluate_series(ss, fused, contexts=[CLEAR] * len(ss))
            self.assertFalse(any(c.rule_id in ('R3','R5','R8') for r in results for c in r.estimates), name)
            if name!='feeding_drop':
                self.assertFalse(any(r.estimates for r in results),name)
