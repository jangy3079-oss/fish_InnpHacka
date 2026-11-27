"""시나리오 정답 라벨 (#9, 검증 VC-02·VC-03·VC-04·VC-05용).

시뮬레이터 참값(환경·이벤트)에서 5분 주기별 정답 원인을 정하고, 구간으로 묶어 저장한다.
YAML에 구간을 손으로 적지 않고 참값에서 만들기 때문에 수온 곡선을 바꿔도 라벨이 따라간다.

정답 원인 (SDD 6.3 규칙 ID)
  R5 disease_suspect   질병 진행도 ≥ 0.25
  R8 parasite_suspect  기생충(비비기 증가) 정도 ≥ 0.5
  R3 hypoxia           DO < 4.0 mg/L
  R2 refuge_loss       표층 ≥ 25℃, 표층−중층 < 1℃
  R1 heat_hiding       표층 ≥ 25℃, 표층−중층 ≥ 1℃
  R7 feeding_drop      23℃ ≤ 표층 < 25℃ (스트레스 경보 대상 아님, 사료 감량만)
  -  turbidity         영상 품질 급락 (경보가 나오면 거짓 경보)
  -  normal

alert_expected: 스트레스 경보(주의 이상)가 나와야 하는 구간 → VC-02 재현율의 정답 구간
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List

import numpy as np

CAUSES = {
    "normal": (None, "정상", False),
    "turbidity": (None, "탁도 상승(카메라만)", False),
    "feeding_drop": ("R7", "섭식 저하", False),
    "heat_hiding": ("R1", "고수온(피신 중)", True),
    "refuge_loss": ("R2", "피난처 소실", True),
    "hypoxia": ("R3", "저산소", True),
    "disease_suspect": ("R5", "질병 의심", True),
    "parasite_suspect": ("R8", "기생충 의심", True),
}
MIN_SEGMENT_H = 2.0   # 수온 일교차로 경계에서 깜박이는 짧은 구간은 앞 구간에 합친다


def cycle_causes(truth: Dict[str, np.ndarray]) -> List[str]:
    ts, tm, do = truth["temp_surface"], truth["temp_mid"], truth["do"]
    out = []
    for i in range(ts.size):
        if truth["disease"][i] >= 0.25:
            c = "disease_suspect"
        elif truth["parasite"][i] >= 0.5:
            c = "parasite_suspect"
        elif do[i] < 4.0:
            c = "hypoxia"
        elif ts[i] >= 25.0:
            c = "refuge_loss" if ts[i] - tm[i] < 1.0 else "heat_hiding"
        elif ts[i] >= 23.0:
            c = "feeding_drop"
        elif truth.get("turbid") is not None and truth["turbid"][i] > 0.5:
            c = "turbidity"
        else:
            c = "normal"
        out.append(c)
    return out


def _segments(causes: List[str], cycle_h: float):
    segs = []
    for i, c in enumerate(causes):
        if segs and segs[-1][0] == c:
            segs[-1][2] = i + 1
        else:
            segs.append([c, i, i + 1])
    # 짧은 구간 합치기
    min_len = int(round(MIN_SEGMENT_H / cycle_h))
    merged = []
    for s in segs:
        if merged and (s[2] - s[1] < min_len):
            merged[-1][2] = s[2]
        elif merged and merged[-1][0] == s[0]:
            merged[-1][2] = s[2]
        else:
            merged.append(list(s))
    return merged


def build_labels(sim) -> dict:
    sc = sim.scenario
    cycle_h = sc.cycle_minutes / 60.0
    causes = cycle_causes(sim.truth)
    t = sim.cycle_time
    end_t = t[-1] + np.timedelta64(sc.cycle_minutes * 60, "s")
    iso = lambda x: str(x.astype("datetime64[s]")) + "Z"

    intervals = []
    for c, a, b in _segments(causes, cycle_h):
        rule, name_ko, alert = CAUSES[c]
        intervals.append({"start": iso(t[a]), "end": iso(t[b]) if b < t.size else iso(end_t),
                          "cause": c, "cause_ko": name_ko, "rule_id": rule, "alert_expected": alert})

    markers = {}
    # 고수온 특보 기준 도달 (VC-04). SRS는 28℃ 주의보, 수과원 2018 단계표는 주의보 27~28℃·경보 28℃ 이상
    for thr, key in ((27.0, "advisory_27c"), (28.0, "advisory_28c")):
        hot = np.where(sim.truth["temp_surface"] >= thr)[0]
        if hot.size:
            markers[key] = iso(t[hot[0]])
    for ev in sc.events:
        key = ev["type"] + ("_at" if "at_h" in ev else "_start")
        h = ev.get("at_h", ev.get("start_h"))
        markers[key] = iso(t[0] + np.timedelta64(int(h * 3600), "s"))

    ruled = [iv["cause"] for iv in intervals if iv["rule_id"]]
    alerts = [iv for iv in intervals if iv["alert_expected"]]
    return {
        "scenario": sc.name,
        "cage_id": sc.cage_id,
        "start": iso(t[0]),
        "end": iso(end_t),
        "cycle_minutes": sc.cycle_minutes,
        "expected_causes": sorted(set(ruled), key=ruled.index),   # 정답 원인 (VC-03)
        "false_alarm_check": not alerts,      # 경보 구간이 없으면 거짓 경보 0건이어야 함 (VC-05)
        "intervals": intervals,
        "markers": markers,
    }


def save_labels(labels: dict, path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(labels, ensure_ascii=False, indent=2), encoding="utf-8")
    return path
