"""python -m pipeline [시나리오 ...] [--out data/out] [--model models/cage-003.pkl] [--no-generate]"""
import argparse
import json

from pipeline.run import run

ap = argparse.ArgumentParser(description="데이터 파이프라인 일괄 실행 → data/out/<시나리오>/api/")
ap.add_argument("names", nargs="*")
ap.add_argument("--out", default="data/out")
ap.add_argument("--model", default="models/cage-003.pkl")
ap.add_argument("--no-generate", action="store_true", help="이미 만든 metrics.jsonl 재사용")
a = ap.parse_args()
for r in run(a.names or None, a.out, a.model, regenerate=not a.no_generate):
    print(json.dumps(r, ensure_ascii=False))
