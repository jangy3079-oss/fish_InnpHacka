"""M5 학습·채점.

    python -m stress train data/out/normal_14d/metrics.jsonl --model models/cage-003.pkl
    python -m stress score data/out/heatwave_typhoon/metrics.jsonl --model models/cage-003.pkl
      → 입력 옆 stress.jsonl (StressScore, 근거 지표 포함)
"""
import argparse
import json
from pathlib import Path

from fusion.fuse import fuse, load_jsonl
from stress.model import StressModel

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
    out = Path(a.out) if a.out else Path(a.metrics).with_name("stress.jsonl")
    with out.open("w", encoding="utf-8") as f:
        for x in scores:
            f.write(json.dumps(x.to_dict(), ensure_ascii=False) + "\n")
    print(f"{len(scores)} scores -> {out}")
