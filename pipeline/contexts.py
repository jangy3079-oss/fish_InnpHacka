"""원인 엔진 외부 입력 CauseContext 만들기 (#174, #45).

- scenario_contexts: 시나리오 YAML 이벤트 → 주기별 컨텍스트 (시연·검증)
- observed_contexts: DB 분석 입력의 작업 기록·적조 특보 → 주기별 컨텍스트 (pipeline/analysis.py)

시나리오 YAML 규칙:

실서비스에서는 특보·작업 기록을 외부에서 받는다 (#45). 시연·검증에서는 시나리오에 적힌 값으로 대신한다.

- advisory_active    env.advisory_active (고수온 등 특보 여부, 없으면 미확인=None)
- red_tide_advisory  red_tide 이벤트의 advisory_h(기본 start_h) ~ end_h 동안 True.
                     시나리오에 적조 이벤트가 있으면 그 밖은 False, 없으면 env.red_tide_advisory(없으면 None)
- work_log_at/type   그 주기까지 기록된 가장 최근 work 이벤트 (at_h, kind)
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Iterable, List, Optional, Sequence

from cause import CauseContext


def scenario_contexts(sc, timestamps: Sequence) -> List[CauseContext]:
    """timestamps: 원인 엔진에 넣을 StressScore.ts (주기 끝, aware datetime)."""
    tides = sc.events_of("red_tide")
    works = sorted(sc.events_of("work"), key=lambda e: float(e["at_h"]))
    out = []
    for ts in timestamps:
        h = (ts - sc.start).total_seconds() / 3600
        red = sc.env.get("red_tide_advisory")
        if tides:
            red = any(float(e.get("advisory_h", e["start_h"])) <= h
                      and (e.get("end_h") is None or h < float(e["end_h"])) for e in tides)
        done = [e for e in works if float(e["at_h"]) <= h]
        last = done[-1] if done else None
        out.append(CauseContext(
            advisory_active=sc.env.get("advisory_active"),
            red_tide_advisory=red,
            work_log_at=None if last is None else sc.start + timedelta(hours=float(last["at_h"])),
            work_log_type=None if last is None else str(last.get("kind", "net_change"))))
    return out


def _aware(value) -> datetime:
    stamp = datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else value
    if not isinstance(stamp, datetime) or stamp.tzinfo is None or stamp.utcoffset() is None:
        raise ValueError("An aware timestamp is required")
    return stamp.astimezone(timezone.utc)


def observed_contexts(timestamps: Sequence, *, advisory_active: Optional[bool] = None,
                      work_logs: Iterable[dict] = (), red_tide_advisories: Iterable[dict] = (),
                      advisory_stages: Sequence[str] = ("advisory", "warning")) -> List[CauseContext]:
    """DB 입력 → 주기별 CauseContext.

    - work_logs: [{kind, performed_at}] 그 주기 시각까지 기록된 가장 최근 작업을 넘긴다
    - red_tide_advisories: [{stage, starts_at, ends_at}] advisory_stages 단계가 그 시각을 덮으면 True.
      덮는 특보가 없으면 None(미확인). 특보 자료가 끊겼을 수 있으므로 '특보 없음'으로 단정하지 않는다
    """
    works = sorted(((_aware(w["performed_at"]), str(w["kind"])) for w in work_logs), key=lambda x: x[0])
    tides = [(_aware(a["starts_at"]), None if a.get("ends_at") is None else _aware(a["ends_at"]))
             for a in red_tide_advisories if a.get("stage") in advisory_stages]
    out = []
    for ts in timestamps:
        ts = _aware(ts)
        done = [w for w in works if w[0] <= ts]
        red = True if any(a <= ts and (b is None or ts < b) for a, b in tides) else None
        out.append(CauseContext(advisory_active=advisory_active, red_tide_advisory=red,
                                work_log_at=done[-1][0] if done else None,
                                work_log_type=done[-1][1] if done else None))
    return out
