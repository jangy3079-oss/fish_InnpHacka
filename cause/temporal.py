"""Explicit external context and per-cage R5/R8 observation history (#26)."""

from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import timedelta
from statistics import fmean


@dataclass(frozen=True)
class CauseContext:
    # Unknown is not evidence that an advisory is absent. No labels/truth inputs.
    advisory_active: bool | None = None
    camera_surface_breathing: bool | None = None

    def __post_init__(self):
        for value in (self.advisory_active, self.camera_surface_breathing):
            if value is not None and type(value) is not bool:
                raise ValueError("Context flags must be booleans or null")


class TemporalRules:
    def __init__(self):
        self.trend = defaultdict(deque)
        self.rubbing = {}
        self.last = {}

    def evaluate(self, cage, ts, score, ready, normal, context, rubbing, quality, cfg, thresholds):
        rules = cfg["rules"]
        cycle = timedelta(minutes=thresholds["cycle_minutes"])
        eligible = ready and normal and context.advisory_active is False
        history = self.trend[cage]
        if not eligible:
            history.clear()
        days = rules["R5"]["behavior"]["score_rising_days_min"]
        window = timedelta(days=days)
        start = ts - window
        while history and history[0][0] <= start:
            history.popleft()
        if eligible and score is not None:
            history.append((ts, score))
        means, coverage = [], []
        # Windows represent observed cycle intervals (timestamp is their end).
        for day in range(days):
            left, right = start + timedelta(days=day), start + timedelta(days=day + 1)
            values = [value for stamp, value in history if left < stamp <= right]
            coverage.append(len(values) * cycle.total_seconds() / timedelta(days=1).total_seconds())
            means.append(fmean(values) if values else None)
        complete = bool(history and history[0][0] - cycle <= start)
        trend_ok = (complete and all(c >= rules["R5"]["behavior"]["min_daily_coverage"] for c in coverage)
                    and means[-1] is not None and means[-1] >= thresholds["cause_min_stress"]
                    and all(a is not None and b is not None and b - a >= rules["R5"]["behavior"]["min_daily_increase"]
                            for a, b in zip(means, means[1:])))
        duration, began = self.rubbing.get(cage, (timedelta(), None))
        gap = cage in self.last and ts - self.last[cage] != cycle
        if not eligible or gap:
            duration, began = timedelta(), None
        observed = (eligible and quality is not None and quality >= thresholds["quality"]["rubbing_min"]
                    and rubbing is not None)
        if observed:
            if rubbing >= rules["R8"]["behavior"]["rubbing_rate_x_min"]:
                if began is None:
                    began = ts - cycle
                duration += cycle
            else:
                duration, began = timedelta(), None
        self.rubbing[cage] = duration, began
        self.last[cage] = ts
        return {"R5": bool(eligible and trend_ok),
                "R8": bool(observed and duration >= timedelta(hours=rules["R8"]["behavior"]["sustained_hours_min"])),
                "details": {"daily_mean_scores": means, "daily_coverage": coverage,
                            "last_daily_mean_min": thresholds["cause_min_stress"],
                            "trend_window_complete": complete, "rubbing_observed_hours": duration.total_seconds() / 3600,
                            "rubbing_window_start": None if began is None else began.isoformat(),
                            "rubbing_current_observed": observed}}
