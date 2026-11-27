"""시나리오 생성 → M5 학습 → 채점 → 경보 단계 → API 파일, 한 번에 (#83)."""

from __future__ import annotations

import json
import hashlib
import time
from pathlib import Path
from typing import List, Optional, Sequence

from fusion.fuse import fuse, load_jsonl
from cause import CauseEngine
from cause.store import save_jsonl as save_causes
from common.config_loader import ConfigLoader, ConfigError
from common.data_config import FixedSnapshot, snapshot_or_current
from pipeline import export
from pipeline.contexts import scenario_contexts
from simulator.generate import DEFAULT, generate
from simulator.scenario import load_scenario
from stress.levels import apply_levels
from stress.model import StressModel
from stress.store import save_jsonl

BASELINE = "normal_14d"


def train(baseline: str, out_dir: Path, model_path: Path, *, snapshot=None) -> StressModel:
    snapshot = snapshot_or_current(snapshot)
    fused = fuse(load_jsonl(out_dir / baseline / "metrics.jsonl"), snapshot=snapshot)
    m = StressModel(fused[0].cage_id).fit(fused, snapshot=snapshot)
    m.save(model_path)
    return m


def score_scenario(name: str, out_dir: Path, model: StressModel, *, snapshot=None) -> dict:
    snapshot = snapshot_or_current(snapshot)
    d = out_dir / name
    records = load_jsonl(d / "metrics.jsonl")
    fused = [f for f in fuse(records, snapshot=snapshot) if f.cage_id == model.cage_id]
    scores = model.score_series(fused, snapshot=snapshot)
    sc = load_scenario(name)
    if sc.cycle_minutes != snapshot["thresholds"]["cycle_minutes"]:
        raise ConfigError("Scenario and model cycle settings must match")
    snapshot_loader = FixedSnapshot(snapshot)
    contexts = scenario_contexts(sc, [s.ts for s in scores])
    results = CauseEngine(snapshot_loader).evaluate_series(scores, fused, contexts=contexts)
    changes = apply_levels(scores, [r.immediate_danger for r in results],
                           cfg=snapshot.to_dict()["thresholds"])
    labels = json.loads((d / "labels.json").read_text(encoding="utf-8"))
    start = export._parse(labels["start"])
    status = export.status_rows(scores, fused, start, results, records=records)
    series = export.hourly_timeseries(fused, scores)
    meta = export.meta(labels, changes, scores, model.model_version, sc.cycle_minutes)
    source_path = Path(sc.source_path) if hasattr(sc, 'source_path') else Path(__file__).resolve().parents[1]/'scenarios'/f'{name}.yaml'
    meta.update(config_revision=snapshot.revision,
                scenario_revision=hashlib.sha256(source_path.read_bytes()).hexdigest(),
                training_revision=model.training_revision,
                training_settings=model.training_settings, cause_source='cause_engine')
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
    snapshot = ConfigLoader().snapshot()
    if regenerate:
        for n in [BASELINE] + [n for n in names if n != BASELINE]:
            generate(n, out_dir)
    model = train(BASELINE, out_dir, model_path, snapshot=snapshot)
    results = [score_scenario(n, out_dir, model, snapshot=snapshot) for n in names if n != BASELINE]
    print(json.dumps({"model_version": model.model_version, "model": str(model_path),
                      "seconds": round(time.time() - t0, 1)}, ensure_ascii=False))
    return results
