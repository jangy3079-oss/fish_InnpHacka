"""시나리오 데이터 일괄 생성 (#9).

    python -m simulator.generate              # 기본 6개 전부
    python -m simulator.generate parasite_suspect --out data/out

시나리오마다 data/out/<이름>/ 에
  metrics.jsonl  env + camera + sonar MetricRecord (ts 순, /ingest/metrics·fusion 입력)
  labels.json    정답 구간·정답 원인 (VC-02·03·04·05)
  echogram.npz / echogram.png / truth.csv / records.jsonl / positions.npy
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from simulator.echogram import preview_png, save_npz, synthesize
from simulator.labels import build_labels, save_labels
from simulator.run import save, simulate
from simulator.scenario import load_scenario
from sonar.metrics import extract

DEFAULT = ["normal_14d", "heatwave_typhoon", "turbidity_event", "disease_suspect",
           "parasite_suspect", "feeding_drop"]


def generate(name: str, out_dir: Path) -> dict:
    sim = simulate(load_scenario(name))
    d = save(sim, out_dir, echogram=False)
    eg = synthesize(sim)
    save_npz(eg, d / "echogram.npz")
    preview_png(eg, d / "echogram.png")
    sonar = extract(eg, cage_id=sim.scenario.cage_id, cycle_minutes=sim.scenario.cycle_minutes)
    order = {"env": 0, "sonar": 1, "camera": 2}
    recs = sorted(sim.records + sonar, key=lambda r: (r.ts, order[r.source.value], r.metric.value))
    with (d / "metrics.jsonl").open("w", encoding="utf-8") as f:
        for r in recs:
            f.write(r.model_dump_json() + "\n")
    labels = build_labels(sim)
    save_labels(labels, d / "labels.json")
    return {"scenario": name, "records": len(recs), "expected": labels["expected_causes"], "out": str(d)}


def main(argv=None):
    ap = argparse.ArgumentParser(description="시나리오 데이터 일괄 생성")
    ap.add_argument("names", nargs="*", default=DEFAULT)
    ap.add_argument("--out", default="data/out")
    a = ap.parse_args(argv)
    for n in a.names:
        print(json.dumps(generate(n, Path(a.out)), ensure_ascii=False))


if __name__ == "__main__":
    main()
