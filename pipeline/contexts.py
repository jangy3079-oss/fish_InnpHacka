"""시나리오 YAML → 주기별 원인 엔진 외부 입력 CauseContext (#174).

실서비스에서는 특보·작업 기록을 외부에서 받는다 (#45). 시연·검증에서는 시나리오에 적힌 값으로 대신한다.

- advisory_active    env.advisory_active (고수온 등 특보 여부, 없으면 미확인=None)
- red_tide_advisory  red_tide 이벤트의 advisory_h(기본 start_h) ~ end_h 동안 True.
                     시나리오에 적조 이벤트가 있으면 그 밖은 False, 없으면 env.red_tide_advisory(없으면 None)
- work_log_at/type   그 주기까지 기록된 가장 최근 work 이벤트 (at_h, kind)
"""

from __future__ import annotations

from datetime import timedelta
from typing import List, Sequence

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
