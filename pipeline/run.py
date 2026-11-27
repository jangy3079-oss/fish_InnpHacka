"""시나리오 생성 → M5 학습 → 채점 → 경보 단계 → API 파일, 한 번에 (#83)."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import List, Optional, Sequence

from fusion.fuse import fuse, load_jsonl
from cause import CauseEngine
from cause.store import save_jsonl as save_causes
from common.config_loader import ConfigLoader
from pipeline import export
from simulator.generate import DEFAULT, generate
from simulator.scenario import load_scenario
from stress.levels import apply_levels
from stress.model import StressModel
from stress.store import save_jsonl

BASELINE = "normal_14d"


def train(baseline: str, out_dir: Path, model_path: Path) -> StressModel:
    fused = fuse(load_jsonl(out_dir / baseline / "metrics.jsonl"))
    m = StressModel(fused[0].cage_id).fit(fused)
    m.save(model_path)
    return m


def score_scenario(name: str, out_dir: Path, model: StressModel) -> dict:
    d = out_dir / name
    fused = [f for f in fuse(load_jsonl(d / "metrics.jsonl")) if f.cage_id == model.cage_id]
    scores = model.score_series(fused)
    snapshot_loader = ConfigLoader()
    results = CauseEngine(snapshot_loader).evaluate_series(scores, fused)
    changes = apply_levels(scores, [r.immediate_danger for r in results],
                           cfg=snapshot_loader.last_good.to_dict()["thresholds"])
    labels = json.loads((d / "labels.json").read_text(encoding="utf-8"))
    sc = load_scenario(name)
    start = export._parse(labels["start"])
    status = export.status_rows(scores, fused, start, results)
    series = export.hourly_timeseries(fused, scores)
    meta = export.meta(labels, changes, scores, model.model_version, sc.cycle_minutes)
    export.write(d / "api", status, series, meta)
    save_jsonl(scores, d / "stress_score.jsonl")
    save_causes(results, d / "cause_estimate.jsonl")
    return {"scenario": name, "cycles": len(status), "max_level": meta["max_level"],
            "level_changes": len(meta["level_changes"]), "lead_time_h": meta["lead_time_h"]}


def run(names: Optional[Sequence[str]] = None, out_dir="data/out", model_path="models/cage-003.pkl",
        regenerate: bool = True) -> List[dict]:
    out_dir, model_path = Path(out_dir), Path(model_path)
    names = list(names or [n for n in DEFAULT if n != BASELINE])
    t0 = time.time()
    if regenerate:
        for n in [BASELINE] + [n for n in names if n != BASELINE]:
            generate(n, out_dir)
    model = train(BASELINE, out_dir, model_path)
    results = [score_scenario(n, out_dir, model) for n in names if n != BASELINE]
    print(json.dumps({"model_version": model.model_version, "model": str(model_path),
                      "seconds": round(time.time() - t0, 1)}, ensure_ascii=False))
    return results
