"""검증 수치 VC-01·02·04·05 (#110, #40 결과표용).

    python -m pipeline.validate --seeds 5
      → data/out/validation/vc_results.json, vc_results.md

시드마다 정상 14일(기준선)과 시나리오를 새로 만들어 M5를 학습하고 채점한다.
시드 0은 시나리오 YAML의 seed 그대로(= python -m pipeline 결과), 시드 k는 YAML seed + 1000·k.

VC-01 탁도 구간 지표 안정성  turbidity_event 탁한 구간 낮 시간, 활동량의 카메라 단독 vs 융합
        - 표준편차, 맑을 때 평균 대비 평균 이탈(%)
VC-02 이상 구간 재현율        정답 경보 구간(labels alert_expected)의 5분 주기 중 단계가 '정상'이 아닌 비율,
                              구간 시작부터 첫 경보까지 걸린 시간
VC-04 경고 선행 시간          heatwave_typhoon 28℃ 주의보 기준 도달 − 첫 '주의' (시간)
VC-05 거짓 경보               turbidity_event 경보 발령(정상 → 그 이상) 횟수 (덤: feeding_drop, normal_14d)
VC-03 원인 추정 정확도        정답 경보 구간의 원인 엔진(cause/) 1순위 원인. 구간마다 가장 많이 나온 1순위가 정답이면 합격,
                              경보 구간이 없는 turbidity_event는 원인이 한 번도 안 나와야 합격
                              권장 규칙 R4(red_tide)·R6(net_change)는 VC-02·03을 따로 계산하고 요약 합격에 넣지 않는다 (#174)
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List

import numpy as np

from fusion.fuse import fuse
from simulator.echogram import synthesize
from simulator.labels import build_labels
from simulator.run import simulate
from simulator.scenario import load_scenario
from sonar.metrics import extract
from cause import CauseContext, CauseEngine
from pipeline.contexts import scenario_contexts
from common.config_loader import ConfigLoader
from stress.levels import apply_levels
from stress.model import StressModel

ALERT_SCENARIOS = ("heatwave_typhoon", "disease_suspect", "parasite_suspect")
QUIET_SCENARIOS = ("turbidity_event", "feeding_drop")
VC03_SCENARIOS = ("heatwave_typhoon", "turbidity_event", "disease_suspect", "parasite_suspect")
# 권장 규칙 R4 적조·R6 작업 스트레스 (#174). 기존 요약 수치(VC-02 평균, VC-03 합격)에 섞지 않고 따로 적는다.
EXTENDED_SCENARIOS = ("red_tide", "net_change")
SEED_STEP = 1000


def _p(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def _run(name: str, seed_k: int):
    sc = load_scenario(name)
    sc = dataclasses.replace(sc, seed=sc.seed + SEED_STEP * seed_k)
    sim = simulate(sc)
    recs = sim.records + extract(synthesize(sim))
    return sim, recs, fuse(recs), build_labels(sim)


def _levels(model: StressModel, fused, sc=None):
    """시연 파이프라인(pipeline/run.py)과 같게: 원인 엔진의 R2 즉시 위험 신호로 경보 단계."""
    scores, changes, _ = _levels_causes(model, fused, sc)
    return scores, changes


def _levels_causes(model: StressModel, fused, sc=None):
    """sc: 시나리오. 특보·적조 특보·작업 기록을 원인 엔진 입력으로 (pipeline.contexts). None이면 모두 미확인."""
    scores = model.score_series(fused)
    loader = ConfigLoader()
    contexts = scenario_contexts(sc, [s.ts for s in scores]) if sc is not None else [CauseContext()] * len(scores)
    results = CauseEngine(loader).evaluate_series(scores, fused, contexts=contexts)
    changes = apply_levels(scores, [r.immediate_danger for r in results],
                           cfg=loader.last_good.to_dict()["thresholds"])
    return scores, changes, results


def _top1(result):
    """원인 엔진 결과의 1순위 rule_id (표시 대상 중 확률 최대, 동점은 엔진 순서). 없으면 None."""
    shown = [c for c in result.estimates if c.display]
    if not shown:
        return None
    return max(shown, key=lambda c: c.probability).rule_id


def vc03(results, labels) -> dict:
    """정답 경보 구간별 1순위 원인 집계."""
    from collections import Counter
    out = {}
    intervals = [iv for iv in labels["intervals"] if iv["alert_expected"]]
    if not intervals:
        n = sum(1 for r in results if _top1(r))
        return {"no_alert_scenario": True, "cycles_with_cause": n, "pass": n == 0, "by_interval": {}}
    ok = True
    for iv in intervals:
        sub = [r for r in results if _in(r.ts, iv)]
        tops = [_top1(r) for r in sub]
        named = [t for t in tops if t]
        cnt = Counter(named)
        mode = cnt.most_common(1)[0][0] if cnt else None
        hit = cnt.get(iv["rule_id"], 0)
        out[iv["cause"]] = {"expected": iv["rule_id"], "mode_top1": mode,
                            "top1_accuracy": round(hit / len(named), 3) if named else None,
                            "coverage": round(len(named) / len(sub), 3) if sub else None,
                            "counts": dict(cnt)}
        ok = ok and mode == iv["rule_id"]
    return {"no_alert_scenario": False, "pass": ok, "by_interval": out}


def _in(ts: datetime, iv: dict) -> bool:
    return _p(iv["start"]) <= ts < _p(iv["end"])


def vc01(recs, fused, labels) -> dict:
    """탁한 구간(낮 09~16시 KST)의 활동량 변동: 카메라 단독 vs 융합."""
    cam = {r.ts: float(r.value) for r in recs if r.source.value == "camera" and r.metric.value == "activity"}
    turb = [iv for iv in labels["intervals"] if iv["cause"] == "turbidity"]
    start = _p(labels["start"])

    def day(t):
        h = ((t - start).total_seconds() / 3600) % 24     # 시나리오 시작 = KST 00시
        return 9 <= h < 16

    in_turb = [f for f in fused if any(_in(f.ts, iv) for iv in turb) and day(f.ts) and f.ts in cam
               and f.features.get("activity") is not None]
    clear = [f for f in fused if (f.ts - start) < timedelta(hours=24) and day(f.ts) and f.ts in cam
             and f.features.get("activity") is not None]
    ca = np.array([cam[f.ts] for f in in_turb])
    fa = np.array([f.features["activity"] for f in in_turb])
    ca0 = np.mean([cam[f.ts] for f in clear])
    fa0 = np.mean([f.features["activity"] for f in clear])
    return {
        "n_cycles": int(len(in_turb)),
        "camera_std": round(float(ca.std()), 4),
        "fused_std": round(float(fa.std()), 4),
        "camera_bias_pct": round(float((ca.mean() / ca0 - 1) * 100), 1),
        "fused_bias_pct": round(float((fa.mean() / fa0 - 1) * 100), 1),
        "pass": bool(fa.std() < ca.std()),
    }


def vc02(scores, labels) -> dict:
    """정답 경보 구간의 주기 재현율과 첫 경보까지 걸린 시간."""
    hit = tot = 0
    delays = []
    per_cause: Dict[str, dict] = {}
    for iv in labels["intervals"]:
        if not iv["alert_expected"]:
            continue
        sub = [s for s in scores if _in(s.ts, iv)]
        h = sum(s.level not in (None, "정상") for s in sub)
        hit, tot = hit + h, tot + len(sub)
        first = next((s.ts for s in sub if s.level not in (None, "정상")), None)
        # 앞 구간에서 이미 경보 중이면 지연 0
        delay = 0.0 if first == sub[0].ts else (
            round((first - _p(iv["start"])).total_seconds() / 3600, 2) if first else None)
        delays.append(delay)
        c = per_cause.setdefault(iv["cause"], {"hit": 0, "total": 0, "first_alert_delay_h": delay})
        c["hit"] += h
        c["total"] += len(sub)
    for c in per_cause.values():
        c["recall"] = round(c["hit"] / c["total"], 3) if c["total"] else None
    return {"recall": round(hit / tot, 3) if tot else None, "cycles": tot, "by_cause": per_cause}


def vc04(changes, labels) -> dict:
    """28℃(SRS 주의보 표기) 기준 선행 시간이 합격 판정. 27℃(수과원 2018 주의보 하한) 기준도 함께."""
    adv = labels["markers"].get("advisory_28c")
    adv27 = labels["markers"].get("advisory_27c")
    first = next((c.ts for c in changes if c.to_level != "정상"), None)
    if not adv or not first:
        return {"lead_h": None, "lead_27c_h": None, "pass": False}
    start = _p(labels["start"])
    h = lambda t: round((t - start).total_seconds() / 3600, 2)
    return {"advisory_28c_h": h(_p(adv)), "advisory_27c_h": h(_p(adv27)) if adv27 else None,
            "first_caution_h": h(first),
            "lead_h": round((_p(adv) - first).total_seconds() / 3600, 2),
            "lead_27c_h": round((_p(adv27) - first).total_seconds() / 3600, 2) if adv27 else None,
            "pass": _p(adv) > first}


def alarms(changes) -> int:
    """정상 → 그 이상으로 오른 횟수."""
    return sum(1 for c in changes if c.from_level == "정상" and c.to_level != "정상")


def run_seed(k: int) -> dict:
    t0 = time.time()
    _, _, base_fused, _ = _run("normal_14d", k)
    model = StressModel(base_fused[0].cage_id).fit(base_fused)
    out: Dict[str, dict] = {"seed_k": k, "model_version": model.model_version}

    for name in ALERT_SCENARIOS + QUIET_SCENARIOS + EXTENDED_SCENARIOS:
        sim, recs, fused, labels = _run(name, k)
        scores, changes, causes = _levels_causes(model, fused, sim.scenario)
        r = {"alarms": alarms(changes), "level_changes": len(changes),
             "max_level": max((s.level for s in scores if s.level), key=["정상", "주의", "경고", "위험"].index)}
        if name in ALERT_SCENARIOS + EXTENDED_SCENARIOS:
            r["vc02"] = vc02(scores, labels)
        if name == "heatwave_typhoon":
            r["vc04"] = vc04(changes, labels)
        if name == "turbidity_event":
            r["vc01"] = vc01(recs, fused, labels)
        if name in VC03_SCENARIOS + EXTENDED_SCENARIOS:
            r["vc03"] = vc03(causes, labels)
        out[name] = r
    base_scores, base_changes = _levels(model, base_fused)
    out["normal_14d"] = {"alarms": alarms(base_changes)}
    out["seconds"] = round(time.time() - t0, 1)
    return out


def expected_causes() -> Dict[str, List[str]]:
    res = {}
    for name in VC03_SCENARIOS + EXTENDED_SCENARIOS:
        sim = simulate(load_scenario(name))
        lab = build_labels(sim)
        res[name] = [f'{iv["rule_id"] or "-"} {iv["cause_ko"]}' for iv in lab["intervals"] if iv["alert_expected"]] \
            or ["없음 (경보·원인 추정 대상 아님)"]
    return res


def summarize(runs: List[dict]) -> dict:
    def col(f):
        return [f(r) for r in runs]

    def stat(v):
        v = [x for x in v if x is not None]
        return {"mean": round(float(np.mean(v)), 3), "min": round(float(np.min(v)), 3),
                "max": round(float(np.max(v)), 3)} if v else None

    s = {
        "vc01_camera_std": stat(col(lambda r: r["turbidity_event"]["vc01"]["camera_std"])),
        "vc01_fused_std": stat(col(lambda r: r["turbidity_event"]["vc01"]["fused_std"])),
        "vc01_pass_all": all(col(lambda r: r["turbidity_event"]["vc01"]["pass"])),
        "vc04_lead_h": stat(col(lambda r: r["heatwave_typhoon"]["vc04"]["lead_h"])),
        "vc04_lead_27c_h": stat(col(lambda r: r["heatwave_typhoon"]["vc04"]["lead_27c_h"])),
        "vc04_pass_all": all(col(lambda r: r["heatwave_typhoon"]["vc04"]["pass"])),
        "vc05_turbidity_alarms": col(lambda r: r["turbidity_event"]["alarms"]),
        "vc05_feeding_drop_alarms": col(lambda r: r["feeding_drop"]["alarms"]),
        "vc05_normal_alarms": col(lambda r: r["normal_14d"]["alarms"]),
    }
    s["vc05_pass_all"] = all(a == 0 for a in s["vc05_turbidity_alarms"])
    for n in ALERT_SCENARIOS:
        s[f"vc02_recall_{n}"] = stat(col(lambda r, n=n: r[n]["vc02"]["recall"]))
    rec = [s[f"vc02_recall_{n}"]["mean"] for n in ALERT_SCENARIOS]
    s["vc02_recall_mean"] = round(float(np.mean(rec)), 3)
    s["vc02_pass_all"] = all(r[n]["vc02"]["recall"] >= 0.8 for r in runs for n in ALERT_SCENARIOS)
    s["vc03_pass_by_scenario"] = {n: sum(r[n]["vc03"]["pass"] for r in runs) for n in VC03_SCENARIOS}
    s["vc03_pass_all"] = all(r[n]["vc03"]["pass"] for r in runs for n in VC03_SCENARIOS)
    ext = [n for n in EXTENDED_SCENARIOS if all(n in r for r in runs)]
    for n in ext:
        s[f"vc02_recall_{n}"] = stat(col(lambda r, n=n: r[n]["vc02"]["recall"]))
    s["vc03_ext_pass_by_scenario"] = {n: sum(r[n]["vc03"]["pass"] for r in runs) for n in ext}
    return s


def to_markdown(res: dict) -> str:
    s, runs = res["summary"], res["runs"]
    n = len(runs)
    ok = lambda b: "✅" if b else "❌"
    L = [f"# 검증 수치 VC-01·02·04·05 (데이터 파이프라인)", "",
         f"- 실행: `python -m pipeline.validate --seeds {n}` · {res['run_at']} · 시드 {n}개 (시드 0 = 시나리오 YAML seed)",
         f"- 설정 revision: `{res['config_revision']}`",
         f"- 합성 데이터(시뮬레이터) 기준. 현장 검증 아님", "",
         "## 요약", "",
         "| VC | 측정값 (시드 평균, 최소~최대) | 합격 |", "| --- | --- | --- |",
         f"| VC-01 | 탁한 구간 활동량 표준편차: 카메라 단독 {s['vc01_camera_std']['mean']} / 융합 {s['vc01_fused_std']['mean']} | {ok(s['vc01_pass_all'])} (모든 시드) |",
         f"| VC-02 | 재현율 평균 {s['vc02_recall_mean']} (고수온 {s['vc02_recall_heatwave_typhoon']['mean']}, 질병 {s['vc02_recall_disease_suspect']['mean']}, 기생충 {s['vc02_recall_parasite_suspect']['mean']}) | {ok(s['vc02_pass_all'])} (모든 시드·시나리오 ≥ 0.8) |",
         f"| VC-04 | 28℃ 기준 선행 {s['vc04_lead_h']['mean']}h ({s['vc04_lead_h']['min']}~{s['vc04_lead_h']['max']}h), 27℃ 기준 {s['vc04_lead_27c_h']['mean']}h ({s['vc04_lead_27c_h']['min']}~{s['vc04_lead_27c_h']['max']}h) | {ok(s['vc04_pass_all'])} |",
         f"| VC-03 | 4개 시나리오 정답 원인 1순위: " + ", ".join(f"{k} {v}/{len(runs)}" for k, v in s["vc03_pass_by_scenario"].items()) + f" | {ok(s['vc03_pass_all'])} |",
         f"| VC-03 (권장 R4·R6) | " + ", ".join(f"{k} {v}/{len(runs)} (재현율 {s[f'vc02_recall_{k}']['mean']})"
                                              for k, v in s.get("vc03_ext_pass_by_scenario", {}).items())
         + " | 요약 합격에 넣지 않음 |",
         f"| VC-05 | 탁도 경보 {s['vc05_turbidity_alarms']}건 (덤: 급이 저하 {s['vc05_feeding_drop_alarms']}, 정상 14일 {s['vc05_normal_alarms']}) | {ok(s['vc05_pass_all'])} |",
         "", "## 시드별", "",
         "| 시드 | VC-01 카메라/융합 std | VC-01 탁도 중 평균 이탈 카메라/융합 | VC-02 고수온 | VC-02 질병 | VC-02 기생충 | VC-04 선행(h) | VC-05 탁도 경보 |",
         "| --- | --- | --- | --- | --- | --- | --- | --- |"]
    for r in runs:
        v1 = r["turbidity_event"]["vc01"]
        L.append(f"| {r['seed_k']} | {v1['camera_std']} / {v1['fused_std']} | {v1['camera_bias_pct']}% / {v1['fused_bias_pct']}% | "
                 f"{r['heatwave_typhoon']['vc02']['recall']} | {r['disease_suspect']['vc02']['recall']} | "
                 f"{r['parasite_suspect']['vc02']['recall']} | {r['heatwave_typhoon']['vc04']['lead_h']} | {r['turbidity_event']['alarms']} |")
    r0 = runs[0]
    L += ["", "## VC-02 원인 구간별 (시드 0)", "", "| 시나리오 | 정답 구간 | 재현율 | 첫 경보까지(h) |", "| --- | --- | --- | --- |"]
    for n_ in ALERT_SCENARIOS:
        for c, v in r0[n_]["vc02"]["by_cause"].items():
            L.append(f"| {n_} | {c} | {v['recall']} | {v['first_alert_delay_h']} |")
    v4 = r0["heatwave_typhoon"]["vc04"]
    L += ["", f"VC-04 (시드 0): 27℃ 도달 {v4['advisory_27c_h']}h, 28℃ 도달 {v4['advisory_28c_h']}h, 첫 '주의' {v4['first_caution_h']}h, "
          f"선행 28℃ 기준 {v4['lead_h']}h / 27℃ 기준 {v4['lead_27c_h']}h", "",
          f"## VC-03 원인 추정 정확도 (시드 {n}개 중 합격 시드 수)", "",
          "| 시나리오 | 정답 원인 | 합격 시드 | 시드 0: 구간별 최빈 1순위 · 1순위 정답률 · 원인 나온 비율 |", "| --- | --- | --- | --- |"]
    passes = {**s["vc03_pass_by_scenario"], **s.get("vc03_ext_pass_by_scenario", {})}
    for n_, cs in res["vc03_expected"].items():
        if n_ not in r0:
            continue
        v = r0[n_]["vc03"]
        if v["no_alert_scenario"]:
            det = f"원인 나온 주기 {v['cycles_with_cause']}건"
        else:
            det = "; ".join(f"{c}: {d['mode_top1']} · {d['top1_accuracy']} · {d['coverage']}" for c, d in v["by_interval"].items())
        L.append(f"| `{n_}` | {' → '.join(cs)} | {passes[n_]}/{n} | {det} |")
    L += ["", "## 정의", "",
          "- VC-01: `turbidity_event` 탁도 정답 구간 중 낮(KST 09~16시) 5분 주기, 활동량(activity). 이탈은 첫날 같은 시간대 평균 대비",
          "- VC-02: labels.json의 `alert_expected` 구간 5분 주기 중 경보 단계가 '정상'이 아닌 비율. 첫 경보까지 = 구간 시작 → 첫 비정상 단계 (앞 구간부터 경보 중이면 0)",
          "- VC-04: `advisory_28c`(표층 참값 28℃ 첫 도달, SRS 주의보 표기) − 첫 '주의' 이상 단계 시각. "
          "수과원 2018 단계표는 주의보 27~28℃라 27℃ 기준(`advisory_27c`)도 함께 적음",
          "- 경보 단계는 시연 파이프라인과 같게 원인 엔진(`cause/`)의 R2 즉시 위험 신호를 씀",
          "- VC-05: 경보 단계가 '정상'에서 올라간 횟수",
          "- VC-03: 정답 경보 구간 5분 주기마다 원인 엔진의 표시 대상 중 확률 최대(동점은 엔진 순서) = 1순위. 구간 최빈 1순위가 정답이면 합격. "
          "1순위 정답률 = 원인이 나온 주기 중 1순위가 정답인 비율, 원인 나온 비율 = 구간 주기 중 원인이 하나라도 나온 비율"]
    return "\n".join(L) + "\n"


def config_revision() -> str:
    try:
        from common.config_loader import ConfigLoader
        return ConfigLoader().snapshot().revision
    except Exception as e:   # 로더 오류여도 데이터 검증은 진행
        return f"(조회 실패: {type(e).__name__})"


def main(argv=None):
    ap = argparse.ArgumentParser(description="검증 수치 VC-01·02·04·05")
    ap.add_argument("--seeds", type=int, default=5)
    ap.add_argument("--out", default="data/out/validation")
    a = ap.parse_args(argv)
    runs = []
    for k in range(a.seeds):
        r = run_seed(k)
        runs.append(r)
        print(json.dumps({"seed_k": k, "seconds": r["seconds"]}))
    res = {"run_at": datetime.now().astimezone().strftime("%Y-%m-%d %H:%M %Z"), "config_revision": config_revision(),
           "runs": runs, "summary": summarize(runs), "vc03_expected": expected_causes()}
    d = Path(a.out)
    d.mkdir(parents=True, exist_ok=True)
    (d / "vc_results.json").write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
    (d / "vc_results.md").write_text(to_markdown(res), encoding="utf-8")
    print(json.dumps(res["summary"], ensure_ascii=False))


if __name__ == "__main__":
    main()
