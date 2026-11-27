"""python -m fusion data/out/heatwave_typhoon/metrics.jsonl [--out fused.jsonl]"""
import argparse
import json
from pathlib import Path

from fusion.fuse import fuse, load_jsonl

ap = argparse.ArgumentParser(description="MetricRecord → FusedFeatures")
ap.add_argument("metrics")
ap.add_argument("--out", default=None, help="기본: 입력 옆 fused.jsonl")
a = ap.parse_args()
fused = fuse(load_jsonl(a.metrics))
out = Path(a.out) if a.out else Path(a.metrics).with_name("fused.jsonl")
with out.open("w", encoding="utf-8") as f:
    for x in fused:
        f.write(json.dumps(x.to_dict(), ensure_ascii=False) + "\n")
print(f"{len(fused)} cycles -> {out}")
