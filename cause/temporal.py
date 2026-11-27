"""Explicit external context and per-cage R5/R6/R8 observation history (#26, #174)."""

from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from statistics import fmean


@dataclass(frozen=True)
class CauseContext:
    # Unknown is not evidence that an advisory is absent. No labels/truth inputs.
    advisory_active: bool | None = None
    camera_surface_breathing: bool | None = None
    # R4: red-tide advisory for the cage's sea area (unknown is not "no advisory").
    red_tide_advisory: bool | None = None
    # R6: most recent recorded handling work (net change, sorting) at or before this cycle.
    work_log_at: datetime | None = None
    work_log_type: str | None = None

    def __post_init__(self):
        for value in (self.advisory_active, self.camera_surface_breathing, self.red_tide_advisory):
            if value is not None and type(value) is not bool:
                raise ValueError("Context flags must be booleans or null")
        if (self.work_log_at is None) != (self.work_log_type is None):
            raise ValueError("work_log_at and work_log_type must be given together")
        if self.work_log_at is not None:
            if (not isinstance(self.work_log_at, datetime) or self.work_log_at.tzinfo is None
                    or self.work_log_at.utcoffset() is None):
                raise ValueError("work_log_at must be an aware datetime")
            if type(self.work_log_type) is not str or not self.work_log_type.strip():
                raise ValueError("work_log_type must be a non-empty string")


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
        duration, began, paused = self.rubbing.get(cage, (timedelta(), None, timedelta()))
        gap = cage in self.last and ts - self.last[cage] != cycle
        reset_reason = None
        if not eligible or gap:
            duration, began, paused = timedelta(), None, timedelta()
            reset_reason = 'observation_gap' if gap else 'context_unavailable'
        observed = (eligible and quality is not None and quality >= thresholds["quality"]["rubbing_min"]
                    and rubbing is not None)
        if observed:
            paused = timedelta()
            if rubbing >= rules["R8"]["behavior"]["rubbing_rate_x_min"]:
                if began is None:
                    began = ts - cycle
                duration += cycle
            else:
                duration, began = timedelta(), None
                reset_reason = 'observed_normal'
        elif eligible and began is not None:
            # Count only unobserved cycle intervals. A resumed valid observation
            # is not another paused interval; exactly max_pause_hours is allowed.
            paused += cycle
            if paused > timedelta(hours=rules["R8"]["behavior"]["max_pause_hours"]):
                duration, began, paused = timedelta(), None, timedelta()
                reset_reason = 'pause_expired'
        self.rubbing[cage] = duration, began, paused
        self.last[cage] = ts
        return {"R5": bool(eligible and trend_ok),
                "R8": bool(observed and duration >= timedelta(hours=rules["R8"]["behavior"]["sustained_hours_min"])),
                "details": {"daily_mean_scores": means, "daily_coverage": coverage,
                            "last_daily_mean_min": thresholds["cause_min_stress"],
                            "trend_window_complete": complete, "rubbing_observed_hours": duration.total_seconds() / 3600,
                            "rubbing_window_start": None if began is None else began.isoformat(),
                            "rubbing_pause_hours": paused.total_seconds() / 3600,
                            "rubbing_current_observed": observed,
                            "rubbing_reset_reason": reset_reason,
                            "trend_observed_hours": sum(coverage)*24,
                            "trend_needed_hours": days*24}}


class WorkRules:
    """R6: score rise right after recorded work, still not recovered later (#174).

    Keeps per-cage (ts, score) of ready cycles. A missing or warming-up score is not
    evidence either way, so it is skipped rather than counted as recovered.
    """

    def __init__(self):
        self.scores = defaultdict(deque)

    def evaluate(self, cage, ts, score, ready, context, cfg, thresholds):
        b = cfg["rules"]["R6"]["behavior"]
        kinds = cfg["rules"]["R6"]["env"]["work_log"]
        pre_h, rise_h = timedelta(hours=b["pre_work_hours"]), timedelta(hours=b["rise_window_hours"])
        recover_h, max_h = timedelta(hours=b["not_recovered_hours"]), timedelta(hours=b["max_hours_after_work"])
        history = self.scores[cage]
        while history and history[0][0] < ts - (pre_h + max_h):
            history.popleft()
        if ready and score is not None:
            history.append((ts, score))
        work_at = None if context.work_log_at is None else context.work_log_at.astimezone(timezone.utc)
        logged = work_at is not None and context.work_log_type in kinds and work_at <= ts
        elapsed = ts - work_at if logged else None
        pre = [v for t, v in history if logged and work_at - pre_h <= t < work_at]
        post = [v for t, v in history if logged and work_at < t <= work_at + rise_h]
        pre_mean = fmean(pre) if pre else None
        post_peak = max(post) if post else None
        rise = None if pre_mean is None or post_peak is None else post_peak - pre_mean
        checks = {
            "work_logged": bool(logged),
            "within_window": bool(logged and elapsed <= max_h),
            "score_rose_after_work": bool(rise is not None and rise >= b["min_score_rise"]),
            "not_recovered": bool(logged and elapsed >= recover_h and ready and score is not None
                                  and score >= thresholds["cause_min_stress"]),
        }
        details = {"work_log_type": context.work_log_type if logged else None,
                   "work_log_at": work_at.isoformat() if logged else None,
                   "hours_since_work": None if elapsed is None else elapsed.total_seconds() / 3600,
                   "pre_work_mean_score": pre_mean, "post_work_peak_score": post_peak,
                   "score_rise_after_work": rise}
        return checks, details
