"""프론트 시연용 scenarios.json 내보내기 (#119).

    python -m pipeline              # 먼저 API용 파일 생성
    python -m pipeline.frontend     # → data/out/frontend/scenarios.json

프론트 목업(frontend/src/mocks/scenarios.json)과 같은 형식:
    {시나리오: {label, frames: [{t, ts, stress_score, level, baseline_status, cause, evidence,
                                 camera_weight, missing_sources, ...}],
                series: {metric: [{ts, value}]}}}

- demo.js는 고수온 시나리오의 frame 수로 슬라이더 끝(MAX_T)을 정하므로 **모든 시나리오 frame 수를 같게**
  만든다. 시나리오 전체 길이를 고르게 나눠 뽑는다 (frame 간격은 시나리오마다 다름, `meta.frame_step_h`).
- series는 frames와 같은 시각·같은 길이 (demo.js가 인덱스로 자른다).
- cause: 원인 엔진(#25, `cause/`) 결과가 있으면 그대로 쓰고 `cause_source: "cause_engine"`.
  엔진의 조건/관측 대기 등으로 비어 있고 경보 단계가 '정상'이 아니면 시나리오 정답 라벨로
  임시로 채우고 `cause_source: "scenario_label"`로 표시한다 (엔진 결과가 아님).
"""

from __future__ import annotations

import argparse
import json
from datetime import timedelta
from pathlib import Path
from typing import Dict, List, Optional

from pipeline.timeline import DEFAULT_OUT, Timeline, _parse

LABELS = {
    "heatwave_typhoon": "고수온·태풍",
    "turbidity_event": "탁도 급증",
    "disease_suspect": "질병 의심",
    "parasite_suspect": "기생충 의심",
    "feeding_drop": "급이 저하",
}
FRONT_KEYS = ("ts", "stress_score", "level", "baseline_status", "cause", "evidence", "camera_weight",
              "missing_sources")
SERIES = ("vertical_dist", "activity", "temp_surface", "temp_mid", "cohesion", "stress_score")


def _label_cause(meta: dict, ts: str) -> List[dict]:
    t = _parse(ts)
    for iv in meta.get("intervals", []):
        if iv.get("rule_id") and _parse(iv["start"]) <= t < _parse(iv["end"]):
            return [{"name": iv["cause_ko"], "probability": 1.0, "rule_id": iv["rule_id"]}]
    return []


def _series_at(points: List[dict], ts: str):
    """ts 이전(포함) 마지막 1시간 점의 값."""
    t = _parse(ts)
    val = None
    for p in points:
        if _parse(p["ts"]) <= t:
            val = p["value"]
        else:
            break
    return val


def scenario_frames(tl: Timeline, n_frames: int, label_causes: bool = True) -> dict:
    dur = tl.duration_h
    step = dur / n_frames
    first_scored = next((r["t"] for r in tl.status if r["stress_score"] is not None), 0.0)
    frames, series = [], {m: [] for m in SERIES}
    for i in range(n_frames):
        t = max(first_scored, (i + 1) * step - tl.meta["t_step_h"])     # 각 구간의 끝 시점
        row = tl.status_at(t)
        f = {"t": i, **{k: row[k] for k in FRONT_KEYS}, "scenario_t_h": row["t"]}
        # 원인 대기 사유 (#127): warming_up / missing_score / low_stress, 없으면 None
        f["cause_suppression"] = row.get("cause_suppression")
        f["r2_triggered"] = bool(row.get("r2_triggered", False))
        f["cage_id"] = row["cage_id"]
        if f["cause"]:
            f["cause_source"] = "cause_engine"
        elif label_causes and f["level"] not in (None, "정상"):
            c = _label_cause(tl.meta, row["ts"])
            if c:
                f["cause"], f["cause_source"] = c, "scenario_label"
        frames.append(f)
        for m in SERIES:
            series[m].append({"ts": row["ts"], "value": _series_at(tl.series.get(m, []), row["ts"])})
    return {"frames": frames, "series": series, "frame_step_h": round(step, 4)}


def build(names: Optional[List[str]] = None, out_dir=DEFAULT_OUT, n_frames: int = 48,
          label_causes: bool = True) -> Dict[str, dict]:
    out: Dict[str, dict] = {}
    for name in names or list(LABELS):
        tl = Timeline(Path(out_dir) / name / "api")
        sf = scenario_frames(tl, n_frames, label_causes)
        out[name] = {"label": LABELS.get(name, name), "frames": sf["frames"], "series": sf["series"],
                     "meta": {"scenario": name, "start": tl.meta["start"], "duration_h": tl.duration_h,
                              "frame_step_h": sf["frame_step_h"], "markers": tl.meta["markers"],
                              "model_version": tl.meta["model_version"],
                              "cause_source": "cause_engine, 엔진이 비운 경보 구간은 scenario_label(임시)" if label_causes else "cause_engine"}}
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="프론트 시연용 scenarios.json")
    ap.add_argument("names", nargs="*")
    ap.add_argument("--out-dir", default=str(DEFAULT_OUT))
    ap.add_argument("--frames", type=int, default=48)
    ap.add_argument("--no-label-causes", action="store_true")
    ap.add_argument("--dest", default=None, help="기본: <out-dir>/frontend/scenarios.json")
    a = ap.parse_args(argv)
    data = build(a.names or None, a.out_dir, a.frames, not a.no_label_causes)
    dest = Path(a.dest) if a.dest else Path(a.out_dir) / "frontend" / "scenarios.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({n: {"frames": len(v["frames"]), "step_h": v["meta"]["frame_step_h"],
                          "levels": sorted({f["level"] for f in v["frames"] if f["level"]})} for n, v in data.items()},
                     ensure_ascii=False))
    print(f"-> {dest} ({dest.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
