"""M5 학습·채점.

    python -m stress train data/out/normal_14d/metrics.jsonl --model models/cage-003.pkl
    python -m stress score data/out/heatwave_typhoon/metrics.jsonl --model models/cage-003.pkl
      → 입력 옆 stress.jsonl (StressScore + 경보 단계, 근거 지표 포함)
        stress_score.jsonl (stress_score 테이블 행), level_changes.json (단계 변화)
      --supabase 를 주면 SUPABASE_URL / SUPABASE_SERVICE_KEY 로 stress_score 테이블에 upsert
"""
import argparse
import json
from pathlib import Path

from fusion.fuse import fuse, load_jsonl
from stress.levels import apply_levels, refuge_loss_signal
from stress.model import StressModel
from stress.store import save_jsonl, upsert_supabase

ap = argparse.ArgumentParser(description="M5 스트레스 지수")
sub = ap.add_subparsers(dest="cmd", required=True)
t = sub.add_parser("train")
t.add_argument("metrics")
t.add_argument("--model", required=True)
t.add_argument("--cage", default=None)
s = sub.add_parser("score")
s.add_argument("metrics")
s.add_argument("--model", required=True)
s.add_argument("--out", default=None)
s.add_argument("--supabase", action="store_true")
a = ap.parse_args()

fused = fuse(load_jsonl(a.metrics))
if a.cmd == "train":
    cage = a.cage or fused[0].cage_id
    m = StressModel(cage).fit(fused)
    m.save(a.model)
    print(json.dumps({"cage_id": cage, "status": m.status, "windows": m.n_windows,
                      "model_version": m.model_version, "detectors": sorted("/".join(k) for k in m.detectors)}))
else:
    m = StressModel.load(a.model)
    scores = m.score_series(fused)
    changes = apply_levels(scores, refuge_loss_signal(scores, fused))
    out = Path(a.out) if a.out else Path(a.metrics).with_name("stress.jsonl")
    save_jsonl(scores, out.with_name("stress_score.jsonl"))
    out.with_name("level_changes.json").write_text(json.dumps(
        [{"ts": c.ts.strftime("%Y-%m-%dT%H:%M:%SZ"), "from": c.from_level, "to": c.to_level, "reason": c.reason}
         for c in changes], ensure_ascii=False, indent=2), encoding="utf-8")
    if a.supabase:
        print(f"supabase upsert {upsert_supabase(scores)} rows")
    with out.open("w", encoding="utf-8") as f:
        for x in scores:
            f.write(json.dumps(x.to_dict(), ensure_ascii=False) + "\n")
    print(f"{len(scores)} scores -> {out}")
