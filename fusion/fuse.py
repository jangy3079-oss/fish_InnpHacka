"""품질 가중 센서 융합 (#22, FR-DIA-01, SDD 6.1, NFR-REL-01·03).

지표마다 소나 값 s와 카메라 값 c를 영상 품질 q로 섞는다.

    f = (1 − w)·s + w·c,   w(q) = 0                 (q < 0.3)
                                  0.5·(q − 0.3)/0.4  (0.3 ≤ q < 0.7)
                                  0.5                (q ≥ 0.7)

- 카메라 비중은 최대 0.5 (카메라는 가두리 일부만 본다)
- 카메라 전용 지표(feeding, still_ratio, rubbing_rate)는 q < 0.6이면 제외
- 소나가 없으면 f = c, 단 q < 0.3이면 지표를 비운다 (거짓 경보 방지)
- 들어오지 않은 센서는 가중치 0, `missing_sources`에 기록
- 같은 5분 주기(ts를 주기 시작으로 내림) 안의 레코드를 한 묶음으로 본다

임계값은 config/thresholds.yaml 의 fusion.gate_low, fusion.full_at, fusion.max_camera_weight,
quality.camera_only_min 을 읽는다 (#77). 파일·키가 없으면 아래 기본값(SDD 6.1).
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Dict, Iterable, List, Optional, Sequence, Union

from common import MetricName, MetricRecord, MetricSource
from common.config import get as cfg_get

# 기본값 (config/thresholds.yaml 이 없을 때)
Q_MIN = 0.3            # 이 아래면 카메라 가중치 0
Q_FULL = 0.7           # 이 이상이면 카메라 가중치 최대
W_MAX = 0.5
Q_CAMERA_ONLY = 0.6    # 카메라 전용 지표 사용 하한


def params() -> dict:
    """융합 임계값 (config/thresholds.yaml → 없으면 기본값)."""
    return {
        "q_min": float(cfg_get("thresholds", "fusion.gate_low", Q_MIN)),
        "q_full": float(cfg_get("thresholds", "fusion.full_at", Q_FULL)),
        "w_max": float(cfg_get("thresholds", "fusion.max_camera_weight", W_MAX)),
        "q_camera_only": float(cfg_get("thresholds", "quality.camera_only_min", Q_CAMERA_ONLY)),
    }

SHARED = (MetricName.VERTICAL_DIST, MetricName.ACTIVITY)
SONAR_ONLY = (MetricName.COHESION,)
CAMERA_ONLY = (MetricName.FEEDING, MetricName.STILL_RATIO, MetricName.RUBBING_RATE)
ENV = (MetricName.TEMP_SURFACE, MetricName.TEMP_MID, MetricName.DO)
ALL_SOURCES = ("sonar", "camera", "env")

Value = Union[float, List[float], None]


def camera_weight(q: Optional[float]) -> float:
    p = params()
    if q is None or q < p["q_min"]:
        return 0.0
    if q >= p["q_full"]:
        return p["w_max"]
    return p["w_max"] * (q - p["q_min"]) / (p["q_full"] - p["q_min"])


@dataclass
class FusedFeatures:
    cage_id: str
    ts: datetime                       # 주기 시작 (UTC)
    features: Dict[str, Value]         # vertical_dist [저,중,표], activity, cohesion, feeding, still_ratio, rubbing_rate
    env: Dict[str, Optional[float]]    # temp_surface, temp_mid, do
    quality: Optional[float]           # 영상 품질 q
    camera_weight: float               # 공용 지표에 쓴 카메라 가중치 w(q)
    weights: Dict[str, float] = field(default_factory=dict)   # 지표별 실제 카메라 비중 (0~1)
    missing_sources: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["ts"] = self.ts.strftime("%Y-%m-%dT%H:%M:%SZ")
        return d


def _bucket(ts: datetime, cycle_minutes: int) -> datetime:
    ts = ts.astimezone(timezone.utc)
    sec = int(ts.timestamp())
    step = cycle_minutes * 60
    return datetime.fromtimestamp(sec - sec % step, tz=timezone.utc)


def _mean(values: Sequence[Value]) -> Value:
    """같은 주기에 같은 source·metric 레코드가 여럿이면 평균."""
    if not values:
        return None
    if isinstance(values[0], list):
        n = len(values[0])
        return [sum(v[i] for v in values) / len(values) for i in range(n)]
    return sum(values) / len(values)


def _blend(s: Value, c: Value, w: float) -> Value:
    if isinstance(s, list):
        f = [(1 - w) * a + w * b for a, b in zip(s, c)]
        tot = sum(f)
        return [x / tot for x in f] if tot > 0 else f
    return (1 - w) * s + w * c


def fuse_cycle(cage_id: str, ts: datetime, records: Iterable[MetricRecord],
               expected_sources: Sequence[str] = ALL_SOURCES) -> FusedFeatures:
    vals: Dict[tuple, list] = defaultdict(list)
    confs: Dict[str, list] = defaultdict(list)
    present = set()
    for r in records:
        src = r.source.value
        present.add(src)
        vals[(src, r.metric)].append(r.value)
        confs[src].append(r.confidence)

    prm = params()
    q = _mean(vals.get(("camera", MetricName.QUALITY), []))
    if q is None and confs.get("camera"):
        q = min(confs["camera"])          # quality 레코드가 없으면 카메라 confidence로 대신
    w = camera_weight(q) if "camera" in present else 0.0

    feats: Dict[str, Value] = {}
    weights: Dict[str, float] = {}
    for m in SHARED:
        s = _mean(vals.get(("sonar", m), []))
        c = _mean(vals.get(("camera", m), []))
        if s is not None and c is not None:
            feats[m.value], weights[m.value] = _blend(s, c, w), w
        elif s is not None:
            feats[m.value], weights[m.value] = s, 0.0
        elif c is not None and q is not None and q >= prm["q_min"]:
            feats[m.value], weights[m.value] = c, 1.0
        else:
            feats[m.value], weights[m.value] = None, 0.0
    for m in SONAR_ONLY:
        feats[m.value] = _mean(vals.get(("sonar", m), []))
        weights[m.value] = 0.0
    for m in CAMERA_ONLY:
        c = _mean(vals.get(("camera", m), []))
        ok = c is not None and q is not None and q >= prm["q_camera_only"]
        feats[m.value] = c if ok else None
        weights[m.value] = 1.0 if ok else 0.0

    env = {m.value: _mean(vals.get(("env", m), [])) for m in ENV}
    missing = [s for s in expected_sources if s not in present]
    return FusedFeatures(cage_id=cage_id, ts=ts, features=feats, env=env, quality=q,
                         camera_weight=w, weights=weights, missing_sources=missing)


def fuse(records: Iterable[MetricRecord], cycle_minutes: int = 5,
         expected_sources: Sequence[str] = ALL_SOURCES) -> List[FusedFeatures]:
    """MetricRecord 묶음 → (가두리, 주기)별 FusedFeatures, 시간 순."""
    groups: Dict[tuple, list] = defaultdict(list)
    for r in records:
        groups[(r.cage_id, _bucket(r.ts, cycle_minutes))].append(r)
    return [fuse_cycle(cid, ts, groups[(cid, ts)], expected_sources)
            for cid, ts in sorted(groups, key=lambda k: (k[0], k[1]))]


def load_jsonl(path) -> List[MetricRecord]:
    with open(path, encoding="utf-8") as f:
        return [MetricRecord.model_validate_json(line) for line in f if line.strip()]
