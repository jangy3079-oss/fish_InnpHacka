"""수과원 수온 → env MetricRecord / 시나리오 키프레임.

  실시간 (NIFS_API_KEY 필요):
    python -m nifs realtime --map s0017=cage-003,cage-004 --out data/out/nifs_env.jsonl
  과거 자료 CSV → 시나리오 YAML env 키프레임:
    python -m nifs keyframes past.csv --station s0017 --start 2023-08-05T00:00:00+09:00
"""
import argparse
import json
from datetime import datetime
from pathlib import Path

from nifs.risa import load_csv, realtime_records, to_keyframes

ap = argparse.ArgumentParser()
sub = ap.add_subparsers(dest="cmd", required=True)
r = sub.add_parser("realtime")
r.add_argument("--map", action="append", required=True, help="관측소코드=가두리1,가두리2")
r.add_argument("--out", default="data/out/nifs_env.jsonl")
k = sub.add_parser("keyframes")
k.add_argument("csv")
k.add_argument("--station", required=True)
k.add_argument("--start", required=True)
k.add_argument("--step-h", type=float, default=3.0)
a = ap.parse_args()

if a.cmd == "realtime":
    mapping = {m.split("=")[0]: m.split("=")[1].split(",") for m in a.map}
    recs = realtime_records(mapping)
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as f:
        for x in recs:
            f.write(x.model_dump_json() + "\n")
    print(f"{len(recs)} records -> {a.out}")
else:
    kf = to_keyframes(load_csv(a.csv), a.station, datetime.fromisoformat(a.start), a.step_h)
    print(json.dumps(kf, ensure_ascii=False))
