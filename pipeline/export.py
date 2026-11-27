"""데이터 파이프라인 → API용 파일 (#83).

시나리오 하나를 끝까지 돌려 백엔드·프론트가 그대로 쓰는 파일을 만든다.

    data/out/<시나리오>/api/
      status_timeline.jsonl  5분마다 /cages/{id}/status 응답 모양 (+ t, components, model_version)
      timeseries.json        {metric: [{ts, value}]} 1시간 간격 (프론트 mocks/timeseries.json 형식)
      meta.json              시작·끝, t 단위, 주요 시점, 정답 구간, 단계 변화

t = 시나리오 시작 후 경과 시간(시간 단위, #62). status의 ts = 점수를 낸 1시간 윈도우의 끝.
cause는 R1/R2/R3/R5/R8 엔진의 결과로 채운다. R4/R6/R7은 후속 구현이다.
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Sequence

import numpy as np

STATUS_ENV_KEYS = ("temp_surface", "temp_mid", "do")
TIMESERIES_METRICS = ("vertical_dist", "activity", "cohesion", "feeding", "still_ratio", "rubbing_rate",
                      "quality", "temp_surface", "temp_mid", "do", "stress_score")


def _iso(t: datetime) -> str:
    return t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def _hours(t: datetime, start: datetime) -> float:
    return round((t - start).total_seconds() / 3600, 4)


def status_rows(scores: Sequence, fused: Sequence, start: datetime, causes: Optional[Sequence] = None) -> List[dict]:
    """StressScore + FusedFeatures(같은 순서·같은 주기) → status 응답 모양 목록."""
    if len(scores) != len(fused):
        raise ValueError("scores와 fused는 같은 주기 목록이어야 함")
    if causes is not None and (len(causes) != len(scores) or any(
            (c.cage_id, c.ts) != (s.cage_id, s.ts) for c, s in zip(causes, scores))):
        raise ValueError("cause 결과는 점수와 동일한 가두리·시각이어야 함")
    rows = []
    for i, (s, f) in enumerate(zip(scores, fused)):
        warm = s.baseline_status == "warming_up"
        evidence = dict(s.evidence or {})
        for k in STATUS_ENV_KEYS:
            v = f.env.get(k)
            if v is not None:
                evidence[k] = round(float(v), 2)
        rows.append({
            "cage_id": s.cage_id,
            "ts": _iso(s.ts),
            "t": _hours(s.ts, start),
            "stress_score": None if warm else s.score,
            "level": None if warm else s.level,
            "baseline_status": s.baseline_status,
            "baseline_windows": s.baseline_windows,
            "cause": [] if causes is None else causes[i].status_causes(),
            "cause_config_revision": None if causes is None else causes[i].config_revision,
            "cause_suppression": None if causes is None else causes[i].suppression,
            "r2_triggered": False if causes is None else causes[i].r2_triggered,
            "evidence": evidence,
            "camera_weight": round(float(f.camera_weight), 3),
            "missing_sources": list(f.missing_sources),
            "components": dict(s.components or {}),
            "model_version": s.model_version,
        })
    return rows


def hourly_timeseries(fused: Sequence, scores: Sequence) -> Dict[str, List[dict]]:
    """FusedFeatures·StressScore → {metric: [{ts, value}]} (정시 기준 1시간 평균, 값 없는 시간은 뺌)."""
    acc: Dict[str, Dict[datetime, list]] = defaultdict(lambda: defaultdict(list))
    for f in fused:
        h = f.ts.replace(minute=0, second=0, microsecond=0)
        for k, v in f.features.items():
            if v is not None:
                acc[k][h].append(v)
        for k, v in f.env.items():
            if v is not None:
                acc[k][h].append(v)
        if f.quality is not None:
            acc["quality"][h].append(f.quality)
    for s in scores:
        if s.score is not None:
            # 점수 ts는 윈도우 끝이라 5분을 빼서 같은 시간대로 묶는다
            h = (s.ts - timedelta(minutes=5)).replace(minute=0, second=0, microsecond=0)
            acc["stress_score"][h].append(s.score)

    out: Dict[str, List[dict]] = {}
    for m in TIMESERIES_METRICS:
        pts = []
        for h in sorted(acc.get(m, {})):
            vals = acc[m][h]
            if isinstance(vals[0], list):
                v = np.mean(np.array(vals, dtype=float), axis=0)
                v = v / v.sum()
                b, mid = round(float(v[0]), 4), round(float(v[1]), 4)
                value = [b, mid, round(1.0 - b - mid, 4)]
            else:
                value = round(float(np.mean(vals)), 4)
            pts.append({"ts": _iso(h), "value": value})
        out[m] = pts
    return out


def meta(labels: dict, changes: Sequence, scores: Sequence, model_version: Optional[str],
         cycle_minutes: int) -> dict:
    start = _parse(labels["start"])
    markers = {k: {"ts": v, "t": _hours(_parse(v), start)} for k, v in labels.get("markers", {}).items()}
    intervals = [{**iv, "t_start": _hours(_parse(iv["start"]), start), "t_end": _hours(_parse(iv["end"]), start)}
                 for iv in labels.get("intervals", [])]
    level_changes = [{"ts": _iso(c.ts), "t": _hours(c.ts, start), "from": c.from_level, "to": c.to_level,
                      "reason": c.reason} for c in changes]
    first_alert = next((c for c in level_changes if c["to"] != "정상"), None)
    adv = markers.get("advisory_28c")
    return {
        "scenario": labels["scenario"],
        "cage_id": labels["cage_id"],
        "start": labels["start"],
        "end": labels["end"],
        "duration_h": _hours(_parse(labels["end"]), start),
        "t_unit": "hours",
        "t_step_h": round(cycle_minutes / 60, 6),
        "cycle_minutes": cycle_minutes,
        "model_version": model_version,
        "expected_causes": labels.get("expected_causes", []),
        "markers": markers,
        "intervals": intervals,
        "level_changes": level_changes,
        "lead_time_h": round(adv["t"] - first_alert["t"], 2) if adv and first_alert else None,   # VC-04
        "max_level": max((s.level for s in scores if s.level),
                         key=["정상", "주의", "경고", "위험"].index, default="정상"),
    }


def write(out_dir, status: List[dict], timeseries: Dict[str, List[dict]], meta_: dict) -> Path:
    d = Path(out_dir)
    d.mkdir(parents=True, exist_ok=True)
    with (d / "status_timeline.jsonl").open("w", encoding="utf-8") as f:
        for r in status:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    (d / "timeseries.json").write_text(json.dumps(timeseries, ensure_ascii=False), encoding="utf-8")
    (d / "meta.json").write_text(json.dumps(meta_, ensure_ascii=False, indent=2), encoding="utf-8")
    return d
