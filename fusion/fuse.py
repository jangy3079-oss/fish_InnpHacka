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
quality.camera_only_min 을 검증된 snapshot에서 읽는다. 파일·키 오류는 ConfigError이며 기본값으로 대체하지 않는다.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Dict, Iterable, List, Optional, Sequence, Union

from common import MetricName, MetricRecord, MetricSource
from common.data_config import fusion_parameters, snapshot_or_current
import hashlib
import json

def params(snapshot=None) -> dict:
    """Read the validated five-file bundle once, or use a pinned batch snapshot."""
    return fusion_parameters(snapshot_or_current(snapshot))


def configuration_signature(configuration):
    return hashlib.sha256(json.dumps(configuration, sort_keys=True, allow_nan=False).encode()).hexdigest()


SHARED = (MetricName.VERTICAL_DIST, MetricName.ACTIVITY)
SONAR_ONLY = (MetricName.COHESION,)
CAMERA_ONLY = (MetricName.FEEDING, MetricName.STILL_RATIO, MetricName.RUBBING_RATE)
ENV = (MetricName.TEMP_SURFACE, MetricName.TEMP_MID, MetricName.DO)
ALL_SOURCES = ("sonar", "camera", "env")

Value = Union[float, List[float], None]


def camera_weight(q: Optional[float], configuration=None) -> float:
    p = params() if configuration is None else configuration
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
    fusion_signature: str | None = None

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
               expected_sources: Sequence[str] = ALL_SOURCES, configuration=None) -> FusedFeatures:
    vals: Dict[tuple, list] = defaultdict(list)
    confs: Dict[str, list] = defaultdict(list)
    present = set()
    for r in records:
        src = r.source.value
        present.add(src)
        vals[(src, r.metric)].append(r.value)
        confs[src].append(r.confidence)

    prm = params() if configuration is None else configuration
    q = _mean(vals.get(("camera", MetricName.QUALITY), []))
    if q is None and confs.get("camera"):
        q = min(confs["camera"])          # quality 레코드가 없으면 카메라 confidence로 대신
    w = camera_weight(q, prm) if "camera" in present else 0.0

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
                         camera_weight=w, weights=weights, missing_sources=missing, fusion_signature=configuration_signature(prm))


def fuse(records: Iterable[MetricRecord], cycle_minutes: float | None = None,
         expected_sources: Sequence[str] = ALL_SOURCES, configuration=None, *, snapshot=None) -> List[FusedFeatures]:
    """MetricRecord 묶음 → (가두리, 주기)별 FusedFeatures, 시간 순."""
    if configuration is None or cycle_minutes is None:
        snapshot = snapshot_or_current(snapshot)
    if configuration is None:
        configuration = params(snapshot)
    if cycle_minutes is None:
        cycle_minutes = snapshot['thresholds']['cycle_minutes']
    groups: Dict[tuple, list] = defaultdict(list)
    for r in records:
        groups[(r.cage_id, _bucket(r.ts, cycle_minutes))].append(r)
    return [fuse_cycle(cid, ts, groups[(cid, ts)], expected_sources, configuration)
            for cid, ts in sorted(groups, key=lambda k: (k[0], k[1]))]


def load_jsonl(path) -> List[MetricRecord]:
    with open(path, encoding="utf-8") as f:
        return [MetricRecord.model_validate_json(line) for line in f if line.strip()]
