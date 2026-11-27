"""시나리오 실행기.

    python -m simulator.run heatwave_typhoon --out data/out

출력 (data/out/<시나리오>/)
  records.jsonl  env·camera MetricRecord (5분 주기)
  truth.csv      주기별 참값 (환경, 활동 배율, 급이 반응, 정지 비율, 품질 등)
  positions.npy  핑×개체 수심 (float32)
  echogram.npz   합성 에코그램 Sv(dB) (#7)
  echogram.png   에코그램 미리보기
"""

from __future__ import annotations

import argparse
import csv
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List

import numpy as np

from common import MetricRecord
from common.layers import layer_fractions
from simulator.fish import Drivers, FishSchool, behavior
from simulator.scenario import (EnvSeries, Scenario, build_env, daylight, load_scenario,
                                local_hour, smoothstep, window_level)

RUBBING_BASE_DAY, RUBBING_BASE_NIGHT = 0.3, 0.15   # 회/(마리·h), 가정값
CAMERA_TRACKED = 30                                 # 카메라 화면 안 추적 개체 수 (가정)
FEEDING_HOURS_LOCAL = (7.0, 16.0)                   # 급이 시각 (KST)


@dataclass
class SimulationResult:
    scenario: Scenario
    ping_time: np.ndarray          # datetime64[s] UTC, (n_pings,)
    positions: np.ndarray          # float32 (n_pings, n_fish) 수심 m
    ts_lin: np.ndarray             # 개체별 반사 강도 (선형)
    typhoon: np.ndarray            # 핑별 태풍 영향 0~1
    cycle_time: np.ndarray         # datetime64[s] UTC, (n_cycles,) 주기 시작 시각
    truth: Dict[str, np.ndarray] = field(default_factory=dict)
    records: List[MetricRecord] = field(default_factory=list)


def _ts(t: np.datetime64) -> str:
    return str(t.astype("datetime64[s]")) + "Z"


def _event_levels(sc: Scenario, hours: np.ndarray) -> Dict[str, np.ndarray]:
    disease = np.zeros_like(hours)
    for ev in sc.events_of("disease"):
        ramp = max(float(ev.get("ramp_h", 72)), 1e-6)   # 3일 이상 서서히 (선형)
        disease = np.maximum(disease, np.clip((hours - ev["start_h"]) / ramp, 0, 1) * float(ev.get("severity", 1.0)))
    parasite = np.zeros_like(hours)
    rub_mult = 4.0
    for ev in sc.events_of("parasite"):
        parasite = np.maximum(parasite, window_level(hours, ev["start_h"], ev.get("end_h"), ev.get("ramp_h", 2)))
        rub_mult = float(ev.get("rubbing_multiplier", rub_mult))
    turbid = np.zeros_like(hours)
    turbid_q = np.ones_like(hours)
    for ev in sc.events_of("turbidity"):
        lv = window_level(hours, ev["start_h"], ev.get("end_h"), ev.get("ramp_h", 2))
        turbid = np.maximum(turbid, lv)
        turbid_q = np.minimum(turbid_q, np.where(lv > 0, float(ev.get("quality", 0.15)), 1.0))
    return {"disease": disease, "parasite": parasite, "rub_mult": np.full_like(hours, rub_mult),
            "turbid": turbid, "turbid_q": turbid_q}


def simulate(sc: Scenario) -> SimulationResult:
    rng = np.random.default_rng(sc.seed)
    n_pings, ppc = sc.n_pings, sc.pings_per_cycle
    n_cycles = n_pings // ppc
    n_pings = n_cycles * ppc

    start = np.datetime64(sc.start.replace(tzinfo=None), "s")
    ping_time = start + np.arange(n_pings) * np.timedelta64(sc.ping_interval_s, "s")
    hours = np.arange(n_pings) * sc.ping_interval_s / 3600.0
    hl = local_hour(ping_time)
    day = daylight(hl)
    env: EnvSeries = build_env(sc, hours, hl)
    ev = _event_levels(sc, hours)

    school = FishSchool(sc.n_fish, sc.depth_m, rng)
    positions = np.empty((n_pings, sc.n_fish), dtype=np.float32)

    keys = ["temp_surface", "temp_mid", "do", "day", "activity", "still", "feeding",
            "rubbing_rate", "disorder", "disease", "parasite", "quality", "typhoon"]
    truth = {k: np.empty(n_cycles) for k in keys}
    truth["vertical_dist"] = np.empty((n_cycles, 3))

    last_feeding = None
    for c in range(n_cycles):
        i0 = c * ppc
        dr = Drivers(temp_surface=float(env.temp_surface[i0]), temp_mid=float(env.temp_mid[i0]),
                     do=float(env.do[i0]), day=float(day[i0]),
                     disease=float(ev["disease"][i0]), parasite=float(ev["parasite"][i0]))
        b = behavior(dr, school.grid, rubbing_multiplier=float(ev["rub_mult"][i0]))
        if c == 0:
            school.warm_start(b.pdf)
        for i in range(i0, i0 + ppc):
            positions[i] = school.step(b)

        # 급이 반응: 급이 시각이 지난 첫 주기에 갱신, 그 사이에는 마지막 값 유지
        h = hl[i0]
        if last_feeding is None or any(abs(h - fh) < sc.cycle_minutes / 120.0 for fh in FEEDING_HOURS_LOCAL):
            last_feeding = b.feeding

        q_day = float(sc.camera.get("day_quality", 0.85))
        q_night = float(sc.camera.get("night_quality", 0.35))
        q = q_day * dr.day + q_night * (1 - dr.day)
        q = (1 - ev["turbid"][i0]) * q + ev["turbid"][i0] * min(q, ev["turbid_q"][i0])
        q *= 1 - 0.4 * env.typhoon[i0]

        rub_base = RUBBING_BASE_DAY * dr.day + RUBBING_BASE_NIGHT * (1 - dr.day)
        vals = dict(temp_surface=dr.temp_surface, temp_mid=dr.temp_mid, do=dr.do, day=dr.day,
                    activity=b.activity, still=b.still, feeding=last_feeding,
                    rubbing_rate=rub_base * b.rubbing_mult, disorder=b.disorder,
                    disease=dr.disease, parasite=dr.parasite, quality=q, typhoon=env.typhoon[i0])
        for k, v in vals.items():
            truth[k][c] = v
        truth["vertical_dist"][c] = layer_fractions(positions[i0:i0 + ppc], sc.depth_m)

    cycle_time = ping_time[::ppc][:n_cycles]
    res = SimulationResult(scenario=sc, ping_time=ping_time, positions=positions,
                           ts_lin=school.ts_lin, typhoon=env.typhoon, cycle_time=cycle_time, truth=truth)
    res.records = make_records(res, rng)
    return res


def make_records(res: SimulationResult, rng: np.random.Generator) -> List[MetricRecord]:
    """참값 → 센서가 낼 법한 측정값 (잡음·탁도 왜곡 포함)."""
    sc, tr = res.scenario, res.truth
    noise = sc.env.get("noise") or {}
    t_sd, do_sd = float(noise.get("temp", 0.05)), float(noise.get("do", 0.08))
    cam_on = bool(sc.camera.get("enabled", True))
    out: List[MetricRecord] = []

    def rec(c, source, metric, value, conf):
        out.append(MetricRecord(cage_id=sc.cage_id, ts=_ts(res.cycle_time[c]), source=source,
                                metric=metric, value=value, confidence=float(np.clip(conf, 0, 1))))

    for c in range(res.cycle_time.size):
        rec(c, "env", "temp_surface", round(float(tr["temp_surface"][c] + rng.normal(0, t_sd)), 2), 1.0)
        rec(c, "env", "temp_mid", round(float(tr["temp_mid"][c] + rng.normal(0, t_sd)), 2), 1.0)
        rec(c, "env", "do", round(float(max(tr["do"][c] + rng.normal(0, do_sd), 0.0)), 2), 1.0)
        if not cam_on:
            continue
        q = float(np.clip(tr["quality"][c] + rng.normal(0, 0.03), 0.0, 1.0))
        bad = 1.0 - q
        rec(c, "camera", "quality", round(q, 3), q)

        # 탁하면 밝은 표층 쪽만 보이고 개체를 놓친다 → 분포는 표층으로, 활동은 낮게 왜곡
        vd = tr["vertical_dist"][c] * (1 - 0.6 * bad) + np.array([0.0, 0.0, 1.0]) * 0.6 * bad
        vd = np.clip(vd + rng.normal(0, 0.03 + 0.12 * bad, 3), 0.001, None)
        vd = vd / vd.sum()
        b_, m_ = round(float(vd[0]), 6), round(float(vd[1]), 6)
        rec(c, "camera", "vertical_dist", [b_, m_, 1.0 - b_ - m_], q)

        act = tr["activity"][c] * (0.4 + 0.6 * q) + rng.normal(0, 0.05 + 0.3 * bad)
        rec(c, "camera", "activity", round(float(max(act, 0.0)), 4), q)

        rec(c, "camera", "feeding", round(float(np.clip(tr["feeding"][c] + rng.normal(0, 0.04 + 0.2 * bad), 0, 1)), 3), q)
        still = tr["still"][c] + 0.3 * bad + rng.normal(0, 0.02 + 0.1 * bad)
        rec(c, "camera", "still_ratio", round(float(np.clip(still, 0, 1)), 3), q)

        exposure = CAMERA_TRACKED * sc.cycle_minutes / 60.0   # 마리·h
        n_tracked = exposure * (0.3 + 0.7 * q)
        rub = rng.poisson(tr["rubbing_rate"][c] * n_tracked) / max(n_tracked, 1e-6)
        rec(c, "camera", "rubbing_rate", round(float(rub), 4), q)
    return out


def save(res: SimulationResult, out_dir: Path, echogram: bool = True) -> Path:
    d = Path(out_dir) / res.scenario.name
    d.mkdir(parents=True, exist_ok=True)
    with (d / "records.jsonl").open("w", encoding="utf-8") as f:
        for r in res.records:
            f.write(r.model_dump_json() + "\n")
    with (d / "truth.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        keys = [k for k in res.truth if k != "vertical_dist"]
        w.writerow(["ts"] + keys + ["vd_bottom", "vd_mid", "vd_surface"])
        for c in range(res.cycle_time.size):
            w.writerow([_ts(res.cycle_time[c])] + [round(float(res.truth[k][c]), 4) for k in keys]
                       + [round(float(x), 4) for x in res.truth["vertical_dist"][c]])
    np.save(d / "positions.npy", res.positions)
    if echogram:
        from simulator.echogram import preview_png, save_npz, synthesize
        eg = synthesize(res)
        save_npz(eg, d / "echogram.npz")
        preview_png(eg, d / "echogram.png")
    return d


def main(argv=None):
    ap = argparse.ArgumentParser(description="조피볼락 개체 기반 시뮬레이터")
    ap.add_argument("scenario", help="scenarios/<이름>.yaml 의 이름 또는 경로")
    ap.add_argument("--out", default="data/out")
    ap.add_argument("--no-echogram", action="store_true", help="에코그램 생성 생략")
    a = ap.parse_args(argv)
    res = simulate(load_scenario(a.scenario))
    d = save(res, Path(a.out), echogram=not a.no_echogram)
    print(json.dumps({"scenario": res.scenario.name, "cycles": int(res.cycle_time.size),
                      "records": len(res.records), "out": str(d)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
