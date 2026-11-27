"""시나리오 YAML 읽기와 환경(수온·DO) 시계열 생성.

시나리오 YAML 형식 (scenarios/*.yaml)

    name: heatwave_typhoon
    cage_id: cage-003
    start: "2026-08-05T00:00:00+09:00"   # 현지 시각(KST) 권장, 내부는 UTC
    duration_days: 8
    seed: 42
    baseline: normal_14d                  # M5 기준선 학습에 쓸 정상 시나리오 (선택)
    cage:   {depth_m: 9.0, n_fish: 300}
    sampling: {ping_interval_s: 60, cycle_minutes: 5}
    env:
      temp_surface: [[0, 22.0], [48, 24.5], ...]   # [시작 후 시간(h), 값] 키프레임, 선형 보간
      temp_mid:     [[0, 20.0], ...]
      do:           [[0, 7.0], ...]
      diurnal_amp:  {temp_surface: 0.3}             # 표층 일교차 진폭(℃), 15시 최고
      noise:        {temp: 0.05, do: 0.08}           # 센서 잡음 표준편차
    camera: {enabled: true, day_quality: 0.85, night_quality: 0.35}
    events:
      - {type: typhoon,   at_h: 132, mixing_h: 10, mid_offset: -0.3}
      - {type: turbidity, start_h: 24, end_h: 40, ramp_h: 2, quality: 0.15}
      - {type: disease,   start_h: 24, ramp_h: 72, severity: 1.0}
      - {type: parasite,  start_h: 24, end_h: 72, ramp_h: 2, rubbing_multiplier: 4.0}
      - {type: hypoxia,   start_h: 10, end_h: 20, ramp_h: 2, do: 3.0}
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Union
from zoneinfo import ZoneInfo

import numpy as np
import yaml

KST = ZoneInfo("Asia/Seoul")
SCENARIO_DIR = Path(__file__).resolve().parents[1] / "scenarios"


@dataclass
class Scenario:
    name: str
    cage_id: str
    start: datetime  # UTC
    duration_days: float
    seed: int
    depth_m: float = 9.0
    n_fish: int = 300
    ping_interval_s: int = 60
    cycle_minutes: int = 5
    env: Dict[str, Any] = field(default_factory=dict)
    camera: Dict[str, Any] = field(default_factory=dict)
    events: List[Dict[str, Any]] = field(default_factory=list)
    baseline: Optional[str] = None
    description: str = ""

    @property
    def n_pings(self) -> int:
        return int(round(self.duration_days * 86400 / self.ping_interval_s))

    @property
    def pings_per_cycle(self) -> int:
        return int(self.cycle_minutes * 60 // self.ping_interval_s)

    def events_of(self, kind: str) -> List[Dict[str, Any]]:
        return [e for e in self.events if e.get("type") == kind]


def load_scenario(name_or_path: Union[str, Path]) -> Scenario:
    p = Path(name_or_path)
    if not p.suffix:
        p = SCENARIO_DIR / f"{name_or_path}.yaml"
    with p.open(encoding="utf-8") as f:
        d = yaml.safe_load(f)
    start = datetime.fromisoformat(str(d["start"]))
    if start.tzinfo is None:
        start = start.replace(tzinfo=KST)
    cage = d.get("cage", {}) or {}
    samp = d.get("sampling", {}) or {}
    return Scenario(
        name=d["name"],
        cage_id=str(d.get("cage_id", "cage-001")),
        start=start.astimezone(timezone.utc),
        duration_days=float(d["duration_days"]),
        seed=int(d.get("seed", 0)),
        depth_m=float(cage.get("depth_m", 9.0)),
        n_fish=int(cage.get("n_fish", 300)),
        ping_interval_s=int(samp.get("ping_interval_s", 60)),
        cycle_minutes=int(samp.get("cycle_minutes", 5)),
        env=d.get("env", {}) or {},
        camera=d.get("camera", {}) or {},
        events=d.get("events", []) or [],
        baseline=d.get("baseline"),
        description=d.get("description", ""),
    )


# ---------------------------------------------------------------- 시간 도우미

def smoothstep(x: np.ndarray) -> np.ndarray:
    x = np.clip(x, 0.0, 1.0)
    return x * x * (3 - 2 * x)


def window_level(hours: np.ndarray, start_h: float, end_h: Optional[float], ramp_h: float) -> np.ndarray:
    """start_h~end_h 구간에서 1, 앞뒤로 ramp_h 동안 부드럽게 오르내리는 0~1 곡선."""
    ramp = max(ramp_h, 1e-6)
    up = smoothstep((hours - start_h) / ramp)
    if end_h is None:
        return up
    down = 1.0 - smoothstep((hours - end_h) / ramp)
    return up * down


def local_hour(ts_utc: np.ndarray) -> np.ndarray:
    """datetime64[s] UTC 배열 → KST 시각(0~24, 소수)."""
    secs = ts_utc.astype("datetime64[s]").astype(np.int64) + 9 * 3600
    return (secs % 86400) / 3600.0


def daylight(hour_local: np.ndarray) -> np.ndarray:
    """낮 정도 0~1 (06시·18시 전후 1시간에 걸쳐 전환). SDD 5.3의 낮=06~18시 기준."""
    return smoothstep(hour_local - 5.5) * (1.0 - smoothstep(hour_local - 17.5))


# ---------------------------------------------------------------- 환경 시계열

@dataclass
class EnvSeries:
    """핑 단위 '참' 환경값과 센서 측정값."""
    temp_surface: np.ndarray
    temp_mid: np.ndarray
    do: np.ndarray
    typhoon: np.ndarray  # 태풍 영향 0~1 (표층 기포 잡음·수직 혼합)


def _keyframes(series: Any, hours: np.ndarray, default: float) -> np.ndarray:
    if series is None:
        return np.full_like(hours, default, dtype=float)
    pts = np.asarray(series, dtype=float)
    return np.interp(hours, pts[:, 0], pts[:, 1])


def build_env(sc: Scenario, hours: np.ndarray, hour_local: np.ndarray) -> EnvSeries:
    env = sc.env
    ts = _keyframes(env.get("temp_surface"), hours, 21.0)
    tm = _keyframes(env.get("temp_mid"), hours, 19.0)
    do = _keyframes(env.get("do"), hours, 7.0)

    amp = (env.get("diurnal_amp") or {}).get("temp_surface", 0.3)
    ts = ts + amp * np.cos(2 * np.pi * (hour_local - 15.0) / 24.0)

    typhoon = np.zeros_like(hours)
    for ev in sc.events_of("typhoon"):
        at, mix = float(ev["at_h"]), float(ev.get("mixing_h", 10))
        k = smoothstep((hours - at) / mix)
        # 태풍 혼합: 중층 수온이 표층 수온에 가까워져 층간 수온 차가 사라진다
        tm = (1 - k) * tm + k * (ts + float(ev.get("mid_offset", -0.3)))
        storm = window_level(hours, at - 6, at + float(ev.get("storm_h", 18)), 4)
        typhoon = np.maximum(typhoon, storm)

    for ev in sc.events_of("hypoxia"):
        lv = window_level(hours, ev["start_h"], ev.get("end_h"), ev.get("ramp_h", 2))
        do = (1 - lv) * do + lv * float(ev.get("do", 3.0))

    return EnvSeries(temp_surface=ts, temp_mid=tm, do=do, typhoon=typhoon)
