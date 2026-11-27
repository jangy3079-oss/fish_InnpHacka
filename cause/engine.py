"""Config-driven R1 through R8 rules. One CauseEngine per ordered stream.

No raw sensor contract is introduced: observations remain MetricRecord; this stage
consumes the existing derived StressScore/FusedFeatures pair, matched by cage/time.
"""

from __future__ import annotations

import json
import math
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from common.config_loader import ConfigLoader
from .feeding import FeedingRule
from .temporal import CauseContext, TemporalRules, WorkRules


def _utc(ts):
    if not isinstance(ts, datetime) or ts.tzinfo is None or ts.utcoffset() is None:
        raise ValueError("An aware datetime is required")
    return ts.astimezone(timezone.utc)


def _number(value):
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError("Evidence must contain finite numeric values or null")
    return float(value)


@dataclass(frozen=True)
class CauseEstimate:
    cage_id: str
    ts: datetime
    cause: str
    rule_id: str
    score: float
    probability: float
    evidence: dict
    display: bool

    def to_dict(self):
        return {"cage_id": self.cage_id, "ts": self.ts.isoformat(), "cause": self.cause,
                "rule_id": self.rule_id, "score": self.score, "probability": self.probability,
                "evidence": self.evidence, "display": self.display}

    def to_row(self, db_cage_id: int):
        """Explicit producer-to-DB mapping, no guessed IDs or remote writes."""
        if type(db_cage_id) is not int or not 0 < db_cage_id <= 9223372036854775807:
            raise ValueError("A positive bigint DB cage ID is required")
        return {"cage_id": db_cage_id, "ts": self.ts.isoformat(), "cause": self.cause,
                "probability": self.probability, "rule_id": self.rule_id, "evidence": self.evidence}


@dataclass(frozen=True)
class CauseResult:
    cage_id: str
    ts: datetime
    estimates: tuple[CauseEstimate, ...]
    r2_triggered: bool
    immediate_danger: bool
    suppression: str | None
    config_revision: str
    pending: tuple[dict, ...] = ()
    observation_details: dict = field(default_factory=dict)

    def metadata(self):
        feeding = self.observation_details
        return {'cause_source': 'cause_engine', 'cause_pending': list(self.pending),
                'r7_held_due_to_missing': feeding.get('feeding_held_due_to_missing', False),
                'feeding_last_observed_at': feeding.get('feeding_last_observed_at'),
                'feeding_reset_reason': feeding.get('feeding_reset_reason')}

    def status_causes(self, *, include_metadata=False):
        if include_metadata:
            return [{'name': c.cause, 'rule_id': c.rule_id, 'probability': c.probability,
                     'display': c.display, 'cause_source': 'cause_engine'} for c in self.estimates]
        return [{"name": c.cause, "rule_id": c.rule_id, "probability": c.probability}
                for c in self.estimates if c.display]


class CauseEngine:
    def __init__(self, loader: ConfigLoader | None = None):
        self.loader = loader or ConfigLoader()
        self._revision = None
        self._history = defaultdict(deque)
        self._last_ts = {}
        self._temporal = TemporalRules()
        self._feeding = FeedingRule()
        self._work = WorkRules()

    def evaluate(self, score, fused, *, context: CauseContext | None = None) -> CauseResult:
        return self.evaluate_series([score], [fused], contexts=[CauseContext() if context is None else context])[0]

    def evaluate_series(self, scores, fused, *, contexts=None) -> list[CauseResult]:
        """One validated config snapshot per batch; exact cage and previous cycle match.

        Batch validation is atomic: bad alignment/order/value cannot advance history.
        Input order may interleave cages, but must increase strictly for each cage.
        """
        snapshot = self.loader.snapshot()  # ConfigError propagates; no stale fallback.
        scores = list(scores)
        contexts = [CauseContext() for _ in scores] if contexts is None else list(contexts)
        if len(contexts) != len(scores) or any(not isinstance(c, CauseContext) for c in contexts):
            raise ValueError("One CauseContext per score is required")
        threshold = snapshot["thresholds"]
        cycle = timedelta(minutes=threshold["cycle_minutes"])
        indexed = {}
        for f in fused:
            key = (f.cage_id, _utc(f.ts))
            if key in indexed:
                raise ValueError("Duplicate fused cage/timestamp")
            indexed[key] = f
        last = dict(self._last_ts)
        pairs = []
        for s, context in zip(scores, contexts):
            ts = _utc(s.ts)
            if not isinstance(s.cage_id, str) or not s.cage_id.strip():
                raise ValueError("A producer cage ID is required")
            key = (s.cage_id, ts - cycle)
            if key not in indexed or (s.cage_id in last and ts - last[s.cage_id] < cycle):
                raise ValueError("Unmatched, overlapping or out-of-order score/fused pair")
            if s.baseline_status not in ("ready", "warming_up"):
                raise ValueError("Unknown baseline status")
            number = _number(s.score)
            if number is not None and not 0 <= number <= 100:
                raise ValueError("Stress score must be within 0..100")
            f = indexed.pop(key)
            # Validate all consumed values before any stream state is changed.
            for name in ("lower_layers_ratio_x", "activity_change", "cohesion_change", "surface_ratio_x", "rubbing_rate_x", "feeding_change"):
                _number(s.evidence.get(name))
            for name in ("temp_surface", "temp_mid", "do"):
                _number(f.env.get(name))
            quality = _number(f.quality)
            if quality is not None and not 0 <= quality <= 1:
                raise ValueError("Camera quality must be within 0..1")
            pairs.append((s, f, ts, context))
            last[s.cage_id] = ts
        if indexed:
            raise ValueError("Extra unmatched fused observations")
        if snapshot.revision != self._revision:
            self._history.clear()  # Never mix history from different config revisions.
            self._temporal = TemporalRules()
            self._feeding = FeedingRule()
            self._work = WorkRules()
            self._revision = snapshot.revision
        rule_details = snapshot.to_dict()["cause_rules"]["rules"]
        results = [self._evaluate(s, f, ts, snapshot, context, rule_details) for s, f, ts, context in pairs]
        self._last_ts = last
        return results

    def _evaluate(self, s, f, ts, snapshot, context, rule_details):
        cfg, thresholds = snapshot["cause_rules"], snapshot["thresholds"]
        rules = cfg["rules"]
        r1, r2 = rules["R1"], rules["R2"]
        history = self._history[s.cage_id]
        cutoff = ts - timedelta(hours=r2["behavior"]["lookback_h"])
        while history and history[0][0] < cutoff:
            history.popleft()
        peak = max((value for _, value in history), default=None)
        ready = s.baseline_status == "ready"
        # Warming-up cannot supply baseline-relative history or danger signals.
        ev = s.evidence if ready else {}
        ratio = _number(ev.get("lower_layers_ratio_x"))
        act, coh = _number(ev.get("activity_change")), _number(ev.get("cohesion_change"))
        surface, mid = _number(f.env.get("temp_surface")), _number(f.env.get("temp_mid"))
        diff = None if surface is None or mid is None else surface - mid
        drop = None if peak is None or coh is None else peak - coh
        oxygen = _number(f.env.get("do"))
        surface_ratio = _number(ev.get("surface_ratio_x"))
        rubbing = _number(ev.get("rubbing_rate_x"))
        normal_bounds = cfg["env_constants"]["normal_env"]
        normal = (surface is not None and surface < normal_bounds["temp_surface_max_c"]
                  and oxygen is not None and oxygen >= normal_bounds["do_min_mg_l"])
        temporal = self._temporal.evaluate(s.cage_id, ts, s.score, ready, normal, context,
                                           rubbing, f.quality, cfg, thresholds)
        work_checks, work_details = self._work.evaluate(s.cage_id, ts, s.score, ready, context, cfg, thresholds)
        observations = {"lower_layers_ratio_x": ratio, "activity_change": act,
                        "cohesion_change": coh, "cohesion_prior_peak": peak,
                        "cohesion_drop_observed": drop, "temp_surface": surface,
                        "temp_mid": mid, "temp_diff": diff, "do": oxygen,
                        "surface_ratio_x": surface_ratio, "rubbing_rate_x": rubbing,
                        "normal_env": normal, "normal_env_bounds": dict(normal_bounds),
                        "advisory_active": context.advisory_active, "quality": f.quality,
                        "rubbing_quality_min": thresholds["quality"]["rubbing_min"],
                        "camera_surface_breathing": context.camera_surface_breathing,
                        "camera_breathing_support": bool(rules["R3"]["behavior"]["camera_surface_breathing"]
                            and context.camera_surface_breathing is True and f.quality is not None
                            and f.quality >= thresholds["quality"]["camera_only_min"]),
                        "red_tide_advisory": context.red_tide_advisory,
                        **temporal["details"], **work_details}
        checks = {
            "R1": {"lower_layers": ratio is not None and ratio >= r1["behavior"]["mid_low_ratio_x_min"],
                   "activity": act is not None and act <= r1["behavior"]["activity_change_max"],
                   "surface": surface is not None and surface >= r1["env"]["temp_surface_min_c"],
                   "difference": diff is not None and diff >= r1["env"]["temp_diff_min_c"]},
            "R2": {"activity_or_cohesion": (act is not None and act <= r2["behavior"]["activity_change_max"])
                       or (drop is not None and drop >= r2["behavior"]["cohesion_drop"]),
                   "surface": surface is not None and surface >= r2["env"]["temp_surface_min_c"],
                   "difference": diff is not None and diff < r2["env"]["temp_diff_max_c"]},
            "R3": {"surface": surface_ratio is not None and surface_ratio >= rules["R3"]["behavior"]["surface_ratio_x_min"],
                   "oxygen": oxygen is not None and oxygen < rules["R3"]["env"]["do_max_mg_l"]},
            "R4": {"red_tide_advisory": context.red_tide_advisory is True,
                   "activity_jump": act is not None and abs(act) >= rules["R4"]["behavior"]["activity_change_abs_min"]},
            "R6": work_checks,
            "R5": {"trend": temporal["R5"], "normal_environment": normal,
                   "advisory_clear": context.advisory_active is False},
            "R8": {"sustained_rubbing": temporal["R8"], "normal_environment": normal,
                   "advisory_clear": context.advisory_active is False},
        }
        triggered = ready and all(checks["R2"].values())
        r7 = rules['R7']
        feeding = _number(ev.get('feeding_change'))
        r7_checks = {'normal_environment':normal,
            'surface':surface is not None and surface>=r7['env']['temp_surface_min_c'],
            'activity':act is not None and abs(act)<r7['behavior']['activity_change_abs_max'],
            'lower_layers':ratio is not None and ratio<r7['behavior']['lower_layers_ratio_x_max'],
            'other_causes_absent':not any(all(c.values()) for c in checks.values()),
            'ready_score':ready and s.score is not None}
        r7_match, feeding_details = self._feeding.evaluate(s.cage_id,ts,all(r7_checks.values()),feeding,
            f.quality,r7['behavior'],thresholds)
        observations.update({'feeding_change':feeding,**feeding_details})
        checks['R7'] = {**r7_checks,'feeding_observed_or_held':r7_match}
        suppression = "warming_up" if not ready else "missing_score" if s.score is None else "low_stress" if s.score < thresholds["cause_min_stress"] else None
        matched = [rid for rid in cfg["probability"]["tie_break_order"] if ready and s.score is not None
                   and (suppression is None or rid in thresholds["cause_min_stress_exceptions"])
                   and all(checks[rid].values())]
        if matched:
            suppression = None  # A configured low-score exception is an actual published cause.
        estimates = []
        for rid in matched:
            rule = rules[rid]
            probability = 1.0 / len(matched)  # YAML binary_all_required: each matched score is 1.
            evidence = {**observations, "stress_score": s.score, "conditions": checks[rid],
                        "thresholds": json.loads(json.dumps(rule_details[rid])),
                        "config_revision": snapshot.revision, "model_version": s.model_version,
                        "fused_ts": _utc(f.ts).isoformat(), "scoring": cfg["probability"]["scoring"],
                        "raw_score": 1.0, "cause_min_stress": thresholds["cause_min_stress"]}
            estimates.append(CauseEstimate(s.cage_id, ts, rule["name"], rid, 1.0, probability, evidence,
                                           probability >= cfg["probability"]["min_display"]))
        if coh is not None:
            history.append((ts, coh))  # Only previous points participate in the current peak.
        immediate = bool(triggered and r2["immediate_danger"] and "R2" in thresholds["immediate_danger_rules"])
        pending = []
        matched_set = set(matched)
        if ready and normal:
            for rid in ('R5', 'R8'):
                if rid in matched_set:
                    continue
                if context.advisory_active is None:
                    pending.append({'rule_id': rid, 'reason': 'advisory_unknown'})
                elif context.advisory_active is False:
                    if rid=='R5' and not temporal['R5'] and not temporal['details']['trend_window_complete']:
                        pending.append({'rule_id': rid, 'reason': 'trend_waiting',
                            'progress': {'done': temporal['details']['trend_observed_hours'],
                                         'needed': temporal['details']['trend_needed_hours'], 'unit': 'hour'}})
                    elif rid=='R5' and not temporal['R5'] and any(v<rules['R5']['behavior']['min_daily_coverage'] for v in temporal['details']['daily_coverage']):
                        pending.append({'rule_id': rid, 'reason': 'observation_gap'})
                    elif rid=='R8' and not temporal['details']['rubbing_current_observed']:
                        pending.append({'rule_id': rid, 'reason': 'observation_gap'})
                    elif rid=='R8' and rubbing is not None and rubbing>=rules['R8']['behavior']['rubbing_rate_x_min'] and not temporal['R8']:
                        pending.append({'rule_id': rid, 'reason': 'observing',
                            'progress': {'done': temporal['details']['rubbing_observed_hours'],
                                         'needed': rules['R8']['behavior']['sustained_hours_min'], 'unit': 'hour'}})
        return CauseResult(s.cage_id, ts, tuple(estimates), triggered, immediate, suppression, snapshot.revision,
                           tuple(pending), {**temporal['details'], **feeding_details})
