"""python -m sonar data/out/heatwave_typhoon/echogram.npz [--out sonar_records.jsonl]"""
import argparse
from pathlib import Path

from simulator.echogram import load_npz
from sonar.metrics import extract

ap = argparse.ArgumentParser(description="에코그램 → sonar MetricRecord")
ap.add_argument("echogram")
ap.add_argument("--out", default=None, help="기본: 에코그램 옆 sonar_records.jsonl")
ap.add_argument("--cycle-minutes", type=int, default=5)
a = ap.parse_args()
recs = extract(load_npz(a.echogram), cycle_minutes=a.cycle_minutes)
out = Path(a.out) if a.out else Path(a.echogram).with_name("sonar_records.jsonl")
with out.open("w", encoding="utf-8") as f:
    for r in recs:
        f.write(r.model_dump_json() + "\n")
print(f"{len(recs)} records -> {out}")
