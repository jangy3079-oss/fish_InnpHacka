"""시연(파일) 모드용 에코그램 PNG를 만든다 (#252).

data/out/<시나리오>/echogram.npz(시뮬레이터 합성 Sv, 시간 x 수심)를 축 없는 열지도 PNG로 바꿔
frontend/src/mocks/echograms/<시나리오>.png 와 echograms.json(시작·끝 시각, 최대 수심)에 쓴다.
색 범위와 축소 방식은 서버 렌더러(api/echogram.py)와 같다: -80~-50 dB, viridis, 선형 강도 평균.
축과 '현재 시각' 선은 화면에서 겹쳐 그린다.

사용: python -m scripts.export_frontend_echograms
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
SCENARIOS = ["heatwave_typhoon", "disease_suspect", "parasite_suspect", "turbidity_event", "feeding_drop"]
OUT = ROOT / "frontend" / "src" / "mocks" / "echograms"
COLS = 960      # 시간 방향 픽셀 수
ROW_REPEAT = 3  # 수심 한 칸을 세로로 늘려 PNG 높이를 만든다


def render(npz_path: Path):
    import matplotlib
    from matplotlib import cm, colors
    from matplotlib.image import imsave

    z = np.load(npz_path, allow_pickle=False)
    ping, depth, sv = z["ping_time"], z["depth"], z["Sv"]
    lin = 10 ** (sv / 10)
    groups = np.array_split(np.arange(ping.size), min(COLS, ping.size))
    binned = np.stack([lin[g].mean(axis=0) for g in groups])
    db = 10 * np.log10(binned)
    norm = colors.Normalize(vmin=-80, vmax=-50)
    rgba = matplotlib.colormaps["viridis"](norm(db.T))  # 행=수심(위가 얕음), 열=시간
    rgba = np.repeat(rgba, ROW_REPEAT, axis=0)
    return rgba, imsave, int(ping[0]), int(ping[-1]), float(depth[-1])


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    meta = {}
    for name in SCENARIOS:
        src = ROOT / "data" / "out" / name / "echogram.npz"
        if not src.exists():
            print("건너뜀(npz 없음):", name)
            continue
        rgba, imsave, start, end, depth_max = render(src)
        imsave(OUT / f"{name}.png", rgba)
        meta[name] = {"start_ms": start * 1000, "end_ms": end * 1000, "depth_max": round(depth_max + 0.05, 2)}
        print(name, rgba.shape, (OUT / f"{name}.png").stat().st_size // 1024, "KB")
    (OUT / "echograms.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
