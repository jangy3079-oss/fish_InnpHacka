"""합성 에코그램 E(t, z) 생성·저장·미리보기 (#7, FR-MEA-03).

저장 형식은 echopype의 Sv(체적 후방산란 강도, dB re 1 m^-1)와 같은 모양이다.
  Sv        float32 (ping_time, depth)   dB
  ping_time datetime64[s] UTC
  depth     float32 수심 구간 중심 (m)
  meta      dict  (cage_id, frequency_khz, ping_interval_s, bin_m, noise_floor_db, source ...)

실제 장비로 바꿀 때는 `from_echopype(ds)`로 echopype `compute_Sv` 결과를 같은 Echogram으로
변환하면 sonar(M4) 이후 모듈은 그대로 쓸 수 있다.
"""

from __future__ import annotations

import io
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional, Union

import numpy as np

FORMAT_VERSION = 1
FISH_UNIT = 1e-6        # 개체 1마리(σ_bs=1)가 한 구간에 주는 선형 후방산란 (임의 단위)
NOISE_FLOOR = 1e-8      # 배경 잡음 (-80 dB)


@dataclass
class Echogram:
    Sv: np.ndarray
    ping_time: np.ndarray
    depth: np.ndarray
    meta: Dict[str, Any] = field(default_factory=dict)

    @property
    def linear(self) -> np.ndarray:
        """선형 강도 E(t, z) = 10^(Sv/10)."""
        return np.power(10.0, self.Sv.astype(np.float64) / 10.0)

    def slice(self, start=None, end=None) -> "Echogram":
        m = np.ones(self.ping_time.size, dtype=bool)
        if start is not None:
            m &= self.ping_time >= np.datetime64(_naive(start), "s")
        if end is not None:
            m &= self.ping_time < np.datetime64(_naive(end), "s")
        return Echogram(self.Sv[m], self.ping_time[m], self.depth, dict(self.meta))


def _naive(t):
    """datetime/str → tz 없는 UTC 값 (numpy datetime64 비교용)."""
    if isinstance(t, str):
        t = t.replace("Z", "+00:00")
        from datetime import datetime, timezone
        t = datetime.fromisoformat(t)
    if hasattr(t, "tzinfo") and t.tzinfo is not None:
        from datetime import timezone
        t = t.astimezone(timezone.utc).replace(tzinfo=None)
    return t


def synthesize(sim, bin_m: float = 0.1, seed: Optional[int] = None,
               pulse_sd_m: float = 0.15, ping_jitter_sd: float = 0.08) -> Echogram:
    """시뮬레이터 결과(개체 위치) → 합성 에코그램.

    - 개체 반사 강도(σ_bs) × 핑마다 자세 변화(로그정규 jitter)를 수심 구간에 쌓고
    - 펄스 길이만큼 가우시안으로 번지게 한 뒤
    - 배경 잡음(로그정규)과 태풍 때 표층 기포 잡음을 더한다.
    """
    sc = sim.scenario
    rng = np.random.default_rng(sc.seed + 1000 if seed is None else seed)
    edges = np.arange(0.0, sc.depth_m + 1e-9, bin_m)
    depth = (edges[:-1] + edges[1:]) / 2
    nz = depth.size
    pos = sim.positions
    n_pings, n_fish = pos.shape

    idx = np.clip((pos / bin_m).astype(np.int64), 0, nz - 1)
    w = sim.ts_lin[None, :] * rng.lognormal(0.0, ping_jitter_sd, size=pos.shape)
    flat = (idx + np.arange(n_pings)[:, None] * nz).ravel()
    E = np.bincount(flat, weights=w.ravel(), minlength=n_pings * nz).reshape(n_pings, nz) * FISH_UNIT

    # 펄스 번짐 (깊이 방향 가우시안 합성곱)
    k_half = int(np.ceil(3 * pulse_sd_m / bin_m))
    kx = np.arange(-k_half, k_half + 1) * bin_m
    kern = np.exp(-0.5 * (kx / pulse_sd_m) ** 2)
    kern /= kern.sum()
    E = np.apply_along_axis(lambda r: np.convolve(r, kern, mode="same"), 1, E)

    noise = NOISE_FLOOR * rng.lognormal(0.0, 0.5, size=E.shape)
    bubbles = 4e-6 * np.exp(-depth / 0.5)[None, :] * sim.typhoon[:, None] * rng.lognormal(0, 0.6, size=E.shape)
    E = E + noise + bubbles

    meta = {
        "format_version": FORMAT_VERSION,
        "source": "synthetic",
        "scenario": sc.name,
        "cage_id": sc.cage_id,
        "frequency_khz": 200,
        "ping_interval_s": sc.ping_interval_s,
        "bin_m": bin_m,
        "depth_m": sc.depth_m,
        "noise_floor_db": float(10 * np.log10(NOISE_FLOOR)),
        "units": "Sv dB re 1 m^-1",
    }
    return Echogram(Sv=(10 * np.log10(E)).astype(np.float32), ping_time=sim.ping_time.astype("datetime64[s]"),
                    depth=depth.astype(np.float32), meta=meta)


# ---------------------------------------------------------------- 저장·읽기

def save_npz(eg: Echogram, path: Union[str, Path]) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(path, Sv=eg.Sv, ping_time=eg.ping_time.astype("datetime64[s]").astype(np.int64),
                        depth=eg.depth, meta=np.array(json.dumps(eg.meta, ensure_ascii=False)))
    return path


def load_npz(path: Union[str, Path]) -> Echogram:
    with np.load(path, allow_pickle=False) as z:
        return Echogram(Sv=z["Sv"], ping_time=z["ping_time"].astype("datetime64[s]"),
                        depth=z["depth"], meta=json.loads(str(z["meta"])))


def from_echopype(ds, channel: int = 0, depth_var: str = "echo_range", meta: Optional[dict] = None) -> Echogram:
    """echopype `compute_Sv` 결과(xarray.Dataset) → Echogram. [실장비, 확장]

    ds["Sv"] 차원: (channel, ping_time, range_sample), ds[depth_var]: 같은 차원의 거리(m).
    트랜스듀서가 수면 아래를 보는 설치 기준으로 거리 = 수심으로 둔다.
    """
    sv = ds["Sv"].isel(channel=channel).values
    rng_m = ds[depth_var].isel(channel=channel).values
    rng_m = rng_m[0] if rng_m.ndim == 2 else rng_m
    pt = ds["ping_time"].values.astype("datetime64[s]")
    m = dict(meta or {})
    m.setdefault("source", "echopype")
    m.setdefault("format_version", FORMAT_VERSION)
    return Echogram(Sv=np.nan_to_num(sv, nan=-120.0).astype(np.float32), ping_time=pt,
                    depth=rng_m.astype(np.float32), meta=m)


# ---------------------------------------------------------------- 미리보기 (/echogram API)

def preview_png(eg: Echogram, path: Optional[Union[str, Path]] = None, start=None, end=None,
                max_cols: int = 1200, vmin: float = -80, vmax: float = -50,
                width_in: float = 10, height_in: float = 3) -> bytes:
    """에코그램 축소 이미지(PNG bytes). path를 주면 파일로도 저장."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates

    e = eg.slice(start, end)
    if e.ping_time.size == 0:
        raise ValueError("선택한 구간에 핑이 없음")
    step = max(1, int(np.ceil(e.ping_time.size / max_cols)))
    n = (e.ping_time.size // step) * step or e.ping_time.size
    lin = e.linear[:n]
    if step > 1:
        lin = lin.reshape(-1, step, lin.shape[1]).mean(axis=1)
    sv = 10 * np.log10(lin)
    t = e.ping_time[:n:step]

    fig, ax = plt.subplots(figsize=(width_in, height_in), dpi=100)
    x0, x1 = mdates.date2num(t[0].astype("O")), mdates.date2num(t[-1].astype("O"))
    ax.imshow(sv.T, aspect="auto", origin="upper", cmap="viridis", vmin=vmin, vmax=vmax,
              extent=[x0, x1, float(e.depth[-1]), float(e.depth[0])], interpolation="nearest")
    ax.xaxis_date()
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d %H:%M"))
    ax.set_ylabel("depth (m)")
    ax.set_title(f"{e.meta.get('cage_id', '')} Sv (dB)  UTC", fontsize=9)
    fig.autofmt_xdate()
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png")
    plt.close(fig)
    data = buf.getvalue()
    if path is not None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_bytes(data)
    return data
