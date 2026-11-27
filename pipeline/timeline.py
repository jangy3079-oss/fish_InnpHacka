"""API용 산출물 조회 도우미 (백엔드 #29·#30용).

python -m pipeline 으로 만든 data/out/<시나리오>/api/ 를 읽는다.
"""

from __future__ import annotations

import bisect
import json
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional, Union

DEFAULT_OUT = Path(__file__).resolve().parents[1] / "data" / "out"


def _parse(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


class Timeline:
    def __init__(self, api_dir: Union[str, Path]):
        d = Path(api_dir)
        self.dir = d
        with (d / "status_timeline.jsonl").open(encoding="utf-8") as f:
            self.status: List[dict] = [json.loads(line) for line in f if line.strip()]
        self.series: Dict[str, List[dict]] = json.loads((d / "timeseries.json").read_text(encoding="utf-8"))
        self.meta: dict = json.loads((d / "meta.json").read_text(encoding="utf-8"))
        self._t = [r["t"] for r in self.status]

    @classmethod
    def load(cls, scenario: str, out_dir: Union[str, Path] = DEFAULT_OUT) -> "Timeline":
        return _load_cached(str(Path(out_dir) / scenario / "api"))

    @property
    def duration_h(self) -> float:
        return float(self.meta["duration_h"])

    def status_at(self, t: float) -> dict:
        """t(시작 후 시간) 시점에 화면에 보일 status. t 이전의 마지막 주기 값 (범위 밖이면 양 끝으로 맞춤)."""
        i = bisect.bisect_right(self._t, float(t)) - 1
        return self.status[min(max(i, 0), len(self.status) - 1)]

    def status_at_ts(self, ts: Union[str, datetime]) -> dict:
        ts = _parse(ts) if isinstance(ts, str) else ts
        start = _parse(self.meta["start"])
        return self.status_at((ts - start).total_seconds() / 3600)

    def timeseries(self, metric: str, from_: Optional[Union[str, datetime]] = None,
                   to: Optional[Union[str, datetime]] = None) -> List[dict]:
        """{ts, value} 목록. from·to 양 끝 포함 (API 계약 #4)."""
        a = _parse(from_) if isinstance(from_, str) else from_
        b = _parse(to) if isinstance(to, str) else to
        out = []
        for p in self.series.get(metric, []):
            t = _parse(p["ts"])
            if (a is None or t >= a) and (b is None or t <= b):
                out.append(p)
        return out

    def timeseries_upto(self, metric: str, t: float, hours: float = 24.0) -> List[dict]:
        """시연 슬라이더용: t 시점까지 최근 hours 시간."""
        end = self.status_at(t)["ts"]
        endt = _parse(end)
        from datetime import timedelta
        return self.timeseries(metric, endt - timedelta(hours=hours), endt)


@lru_cache(maxsize=16)
def _load_cached(path: str) -> Timeline:
    return Timeline(path)
