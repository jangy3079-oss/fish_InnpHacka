"""M4 소나 지표 추출: 에코그램 → 수직 분포·활동량·응집도 (#8, FR-MEA-01·02, SDD 5.2).

    p_k(t) = Σ_{z∈k} E(t,z) / Σ_z E(t,z)                       수직 분포 (✅)
    A(t)   = Σ_z |E(t,z) − E(t−Δt,z)| / Σ_z (E(t,z)+E(t−Δt,z))/2   활동량 (✅)
    응집도  = 1 / (에코 가중 수심 표준편차)                        (🔺)

A는 SDD 식(구간 평균 변화량)을 평균 에코로 나눠 어군 양·장비 이득에 무관하게 만들고,
`activity_scale`(정상 낮 평균 A)로 나눠 "정상 낮 ≈ 1.0" 지수로 낸다.
5분 주기마다 MetricRecord(source=sonar)를 낸다. ts = 주기 시작 시각(UTC).

전처리
  - 잡음 바닥(meta.noise_floor_db)의 1.5배를 빼고 0 아래는 0
  - 트랜스듀서 근거리 0.3m는 제외 (blanking)
  - 활동량은 0.3m 구간으로 묶어 계산 (핑 사이 자세 변화로 생기는 바닥값 감소)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

import numpy as np

from common import MetricRecord
from common.layers import layer_edges
from simulator.echogram import Echogram

DEFAULT_ACTIVITY_SCALE = 0.161   # 합성 소나 정상 낮 평균 A (calibrate_activity_scale로 다시 구함)
BLANK_M = 0.3
ACTIVITY_BIN_M = 0.3
MIN_SD_M = 0.1


@dataclass
class PingMetrics:
    """핑 단위 중간 결과."""
    ping_time: np.ndarray
    layer_energy: np.ndarray   # (n_pings, 3) [저, 중, 표] 선형 에코 합
    activity_raw: np.ndarray   # (n_pings,)
    depth_sd: np.ndarray       # (n_pings,)
    snr_db: np.ndarray         # (n_pings,)


def _clean(eg: Echogram) -> np.ndarray:
    E = eg.linear
    noise = 10 ** (float(eg.meta.get("noise_floor_db", -80.0)) / 10)
    E = np.clip(E - 1.5 * noise, 0.0, None)
    E[:, eg.depth < BLANK_M] = 0.0
    return E


def ping_metrics(eg: Echogram, prev_last_ping: Optional[np.ndarray] = None) -> PingMetrics:
    E = _clean(eg)
    depth = eg.depth.astype(float)
    depth_m = float(eg.meta.get("depth_m", depth[-1] + (depth[1] - depth[0]) / 2))
    a, b = layer_edges(depth_m)
    layer = np.stack([E[:, depth >= b].sum(1), E[:, (depth >= a) & (depth < b)].sum(1), E[:, depth < a].sum(1)], 1)

    bin_m = float(eg.meta.get("bin_m", depth[1] - depth[0]))
    agg = max(1, int(round(ACTIVITY_BIN_M / bin_m)))
    nz = (E.shape[1] // agg) * agg
    Ea = E[:, :nz].reshape(E.shape[0], -1, agg).sum(2)
    prev = np.vstack([Ea[:1] if prev_last_ping is None else prev_last_ping[None, :], Ea[:-1]])
    denom = ((Ea + prev) / 2).sum(1)
    act = np.where(denom > 0, np.abs(Ea - prev).sum(1) / np.maximum(denom, 1e-30), 0.0)
    if prev_last_ping is None and act.size > 1:
        act[0] = act[1]

    tot = E.sum(1)
    w = E / np.maximum(tot, 1e-30)[:, None]
    mu = (w * depth).sum(1)
    sd = np.sqrt((w * (depth[None, :] - mu[:, None]) ** 2).sum(1))

    noise = 10 ** (float(eg.meta.get("noise_floor_db", -80.0)) / 10)
    occupied = np.maximum((E > 0).sum(1), 1)
    snr = 10 * np.log10(np.maximum(tot / occupied, 1e-30) / noise)
    return PingMetrics(eg.ping_time, layer, act, sd, snr)


def calibrate_activity_scale(eg_normal: Echogram, day_hours_local=(9, 16)) -> float:
    """정상 구간 에코그램의 낮(KST) 평균 A → activity_scale. 설치할 때 한 번 구한다."""
    pm = ping_metrics(eg_normal)
    secs = pm.ping_time.astype("datetime64[s]").astype(np.int64) + 9 * 3600
    hl = (secs % 86400) / 3600
    m = (hl >= day_hours_local[0]) & (hl < day_hours_local[1])
    return float(pm.activity_raw[m].mean())


def extract(eg: Echogram, cage_id: Optional[str] = None, cycle_minutes: int = 5,
            activity_scale: Optional[float] = None) -> List[MetricRecord]:
    """에코그램 → 5분 주기 MetricRecord(sonar): vertical_dist, activity, cohesion."""
    cage_id = cage_id or str(eg.meta.get("cage_id", "unknown"))
    scale = activity_scale or float(eg.meta.get("activity_scale", DEFAULT_ACTIVITY_SCALE))
    pm = ping_metrics(eg)

    t = pm.ping_time.astype("datetime64[s]")
    cyc = (t.astype(np.int64) // (cycle_minutes * 60)) * (cycle_minutes * 60)
    starts, idx = np.unique(cyc, return_inverse=True)
    out: List[MetricRecord] = []
    for c, st in enumerate(starts):
        m = idx == c
        le = pm.layer_energy[m].sum(0)
        tot = le.sum()
        ts = str(np.datetime64(int(st), "s")) + "Z"
        snr = float(np.median(pm.snr_db[m]))
        conf = float(np.clip((snr - 6.0) / 24.0, 0.0, 1.0))
        if tot <= 0 or conf == 0.0:
            continue   # 에코가 없으면 레코드를 내지 않는다 (0을 '물고기 없음'으로 쓰지 않음)
        p = le / tot
        b_, m_ = round(float(p[0]), 6), round(float(p[1]), 6)
        conf = round(conf, 3)
        out.append(MetricRecord(cage_id=cage_id, ts=ts, source="sonar", metric="vertical_dist",
                                value=[b_, m_, 1.0 - b_ - m_], confidence=conf))
        out.append(MetricRecord(cage_id=cage_id, ts=ts, source="sonar", metric="activity",
                                value=round(float(pm.activity_raw[m].mean() / scale), 4), confidence=conf))
        sd = max(float(pm.depth_sd[m].mean()), MIN_SD_M)
        out.append(MetricRecord(cage_id=cage_id, ts=ts, source="sonar", metric="cohesion",
                                value=round(1.0 / sd, 4), confidence=conf))
    return out
