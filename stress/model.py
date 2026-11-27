"""M5 스트레스 지수 (#23, FR-DIA-02·03·06, SDD 5.3, NFR-MNT-02).

1. 특징: 1시간 윈도우마다
     p_bottom, p_mid, p_surface, act_mean, act_std, cohesion          (소나·융합, 항상)
     still_ratio, feeding, rubbing_rate                                (카메라, 윈도우 절반 이상 있을 때)
2. 기준선: 가두리마다 낮(KST 06~18시)·밤을 따로 학습 (정상 14일).
   특징 묶음별로 Isolation Forest를 따로 학습하고, 묶음 점수 중 가장 큰 값을 지수로 쓴다.
     sonar   : 소나 6개                  camera : 카메라 3개
     rubbing : 몸 비비기 1개 (1차원)     still  : 정지 비율 1개 (1차원)
   Isolation Forest는 여러 특징 중 하나만 벗어난 점을 약하게 본다. 비비기·정지 비율은
   한 지표만 늘어도 의미 있는 행동 이벤트라 1차원 모델을 따로 둔다. 급이 반응은
   UC-04(급이만 감소 → 경보 없이 사료 감량)를 지키려고 카메라 묶음에만 둔다.
   비비기·정지 비율은 보조 지표(🔺)라 단독으로는 79점(경고)까지만 올린다.
   카메라 특징이 없는 윈도우(밤, 탁함, 카메라 없음)는 소나 점수만 쓴다.
3. 점수: PyOD Isolation Forest 이상 점수 → 학습 분포 백분위 → 0~100
     학습 범위 안(≤ 99백분위)   : 0.4 × 백분위          → 0~39 (정상)
     학습 범위 밖(> 99백분위)   : 40 + 60 × ((s − s99)/(s_ext − s99))^1.6
       s_ext = 학습 범위에서 아주 멀리 떨어진 점의 점수 (Isolation Forest 점수 상한 근사)
   학습 범위 밖 보정 (#110): Isolation Forest는 학습 범위 밖의 점을 범위 끝 점과 같은 깊이에서
   격리해, 아무리 멀리 벗어나도 '범위 끝 점'만큼만 이상하다고 본다 (특히 1차원 묶음).
   그래서 특징별 학습 범위를 OUT_MARGIN_IQR(0.5 IQR) 넘게 벗어나면
     범위 점수 = 40 + 60 × ((초과 IQR − 0.5) / 2.5)^1.6   (3 IQR 벗어나면 100)
   를 따로 계산해 Isolation Forest 점수와 큰 값을 쓴다.
4. 콜드 스타트: 기준선 윈도우가 168개(7일치) 미만이면 warming_up, 점수 없음.
5. model_version: 'm5-iforest-<버전>+<가두리>.<학습 종료일>.<학습 데이터 해시 8자리>'

점수와 함께 기준선(중앙값) 대비 근거 지표 `evidence`를 낸다 (원인 규칙 R1·R2·R7·R8 입력).
"""

from __future__ import annotations

import copy
import re
import hashlib
import pickle
import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Sequence

import numpy as np
from pyod.models.iforest import IForest

from common.config_loader import ConfigError
from common.data_config import snapshot_or_current, fusion_parameters
from fusion.fuse import FusedFeatures, configuration_signature

MODEL_VERSION = "m5-iforest-1.1"   # 1.1: 학습 범위 밖 보정 (#110)
OUT_MARGIN_IQR = 0.5           # 학습 범위를 이만큼(IQR 단위) 넘게 벗어나야 범위 점수 시작 (40)
OUT_FULL_IQR = 3.0             # 이만큼 벗어나면 범위 점수 100
RANGE_FEATURE_SETS = {("rubbing_rate",), ("still_ratio",)}   # 범위 밖 보정을 쓰는 묶음 (1차원 행동 이벤트). 다묶음에 쓰면 급이 저하·탁도에서 거짓 경보
SCORE_GAMMA = 1.6               # 학습 범위 밖 점수 곡선 (1보다 크면 아주 멀리 벗어나야 80 이상)


def _hour(value):
    h, m = value.split(':')
    return int(h) + int(m) / 60


# These define the in-process feature extractor. Changes require a new process;
# persisted training metadata additionally prevents old-model/new-process mixing.
_PROCESS = snapshot_or_current()['thresholds']
CYCLE_MINUTES = float(_PROCESS['cycle_minutes'])
WINDOW_HOURS = float(_PROCESS['baseline']['window_hours'])
MIN_BASELINE_WINDOWS = _PROCESS['baseline']['min_windows']
MIN_DAYPART_WINDOWS = 24
WINDOW_CYCLES = max(1, int(round(WINDOW_HOURS * 60 / CYCLE_MINUTES)))
MIN_CYCLES_IN_WINDOW = max(1, WINDOW_CYCLES // 2)
DAY_START = _hour(_PROCESS['baseline']['day_start'])
DAY_END = _hour(_PROCESS['baseline']['day_end'])
KST = timezone(timedelta(hours=9))

SONAR_FEATURES = ["p_bottom", "p_mid", "p_surface", "act_mean", "act_std", "cohesion"]
CAMERA_FEATURES = ["still_ratio", "feeding", "rubbing_rate"]
FULL_FEATURES = SONAR_FEATURES + CAMERA_FEATURES
FEATURE_SETS = {"sonar": SONAR_FEATURES, "camera": CAMERA_FEATURES,
                "rubbing": ["rubbing_rate"], "still": ["still_ratio"]}
COMPONENT_CAP = {"rubbing": 79, "still": 79}   # 보조 지표 단독으로는 '위험'을 만들지 않음


def training_settings(snapshot):
    t = snapshot.to_dict()['thresholds']
    return {'schema': 1, 'algorithm': MODEL_VERSION, 'cycle_minutes': t['cycle_minutes'],
            'baseline': {k: t['baseline'][k] for k in ('window_hours', 'day_start', 'day_end')},
            'fusion': t['fusion'], 'quality': t['quality'], 'feature_sets': copy.deepcopy(FEATURE_SETS)}


def require_process_settings(snapshot):
    t = snapshot['thresholds']
    actual = (t['cycle_minutes'], t['baseline']['window_hours'], t['baseline']['min_windows'],
              _hour(t['baseline']['day_start']), _hour(t['baseline']['day_end']))
    if actual != (CYCLE_MINUTES, WINDOW_HOURS, MIN_BASELINE_WINDOWS, DAY_START, DAY_END):
        raise ConfigError('M5 feature settings changed; restart and retrain when training features change')


# ---------------------------------------------------------------- 특징

def cycle_matrix(fused: Sequence[FusedFeatures]) -> tuple:
    """FusedFeatures 시계열 → (ts 배열, 주기×원시 지표 행렬). 없는 값은 NaN."""
    cols = ["p_bottom", "p_mid", "p_surface", "activity", "cohesion"] + CAMERA_FEATURES
    X = np.full((len(fused), len(cols)), np.nan)
    ts = np.array([f.ts for f in fused])
    for i, f in enumerate(fused):
        vd = f.features.get("vertical_dist")
        if vd is not None:
            X[i, 0:3] = vd
        for j, k in enumerate(["activity", "cohesion"] + CAMERA_FEATURES, start=3):
            v = f.features.get(k)
            if v is not None:
                X[i, j] = v
    return ts, X


def window_features(Xw: np.ndarray) -> Dict[str, float]:
    """한 윈도우(주기×원시 지표) → 특징 dict. 소나 특징이 모자라면 빈 dict."""
    act = Xw[:, 3]
    ok = ~np.isnan(act)
    if ok.sum() < MIN_CYCLES_IN_WINDOW:
        return {}
    f = {
        "p_bottom": np.nanmean(Xw[:, 0]), "p_mid": np.nanmean(Xw[:, 1]), "p_surface": np.nanmean(Xw[:, 2]),
        "act_mean": float(act[ok].mean()), "act_std": float(act[ok].std()),
        "cohesion": np.nanmean(Xw[:, 4]) if (~np.isnan(Xw[:, 4])).any() else np.nan,
    }
    for j, k in enumerate(CAMERA_FEATURES, start=5):
        col = Xw[:, j]
        f[k] = float(np.nanmean(col)) if (~np.isnan(col)).sum() >= Xw.shape[0] / 2 else np.nan
    return {k: float(v) for k, v in f.items()}


def is_day(ts: datetime) -> bool:
    t = ts.astimezone(KST)
    h = t.hour + t.minute / 60
    return DAY_START <= h < DAY_END if DAY_START < DAY_END else h >= DAY_START or h < DAY_END


def hourly_windows(fused: Sequence[FusedFeatures]) -> List[tuple]:
    """학습용: 정시 기준으로 겹치지 않는 1시간 윈도우 → [(윈도우 시작, 특징 dict)]."""
    ts, X = cycle_matrix(fused)
    if not len(ts):
        return []
    step = int(WINDOW_HOURS * 3600)
    hour_key = np.array([datetime.fromtimestamp(int(t.timestamp()) // step * step, tz=timezone.utc) for t in ts])
    out = []
    for h in sorted(set(hour_key)):
        f = window_features(X[hour_key == h])
        if f:
            out.append((h, f))
    return out


# ---------------------------------------------------------------- 모델

@dataclass
class StressScore:
    """stress_score 테이블 한 행 + 설명용 부가 정보."""
    cage_id: str
    ts: datetime                         # 윈도우 끝(=이 주기) 시각 (UTC)
    score: Optional[int]                 # 0~100, warming_up이면 None
    baseline_status: str                 # ready | warming_up
    model_version: Optional[str]
    daypart: str = ""                    # day | night
    feature_set: str = ""                # 쓴 묶음, 예: sonar+camera+rubbing+still
    components: Dict[str, int] = field(default_factory=dict)   # 묶음별 점수
    baseline_windows: int = 0
    evidence: Dict[str, float] = field(default_factory=dict)
    features: Dict[str, float] = field(default_factory=dict)
    level_held_due_to_missing: bool = False
    level: Optional[str] = None          # #24 경보 단계 판정 후 채움

    @property
    def storable(self) -> bool:
        """DB 계약(#68): ready면 score·level 필수. 윈도우가 덜 찬 ready 행은 저장하지 않는다."""
        return self.baseline_status == "warming_up" or (self.score is not None and self.level is not None)

    def to_row(self) -> dict:
        """Supabase stress_score 테이블 행 (cage_id, ts, score, level, baseline_status, model_version).

        warming_up이면 score·level 모두 null (#68)."""
        warm = self.baseline_status == "warming_up"
        return {"cage_id": self.cage_id, "ts": self.ts.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "score": None if warm else self.score, "level": None if warm else self.level,
                "baseline_status": self.baseline_status, "model_version": self.model_version}

    def to_dict(self) -> dict:
        d = asdict(self)
        d["ts"] = self.ts.strftime("%Y-%m-%dT%H:%M:%SZ")
        return d


@dataclass
class _Detector:
    features: List[str]
    clf: IForest
    s99: float
    s_ext: float
    train_scores: np.ndarray
    lo: Optional[np.ndarray] = None      # 특징별 학습 최솟값·최댓값·IQR (학습 범위 밖 보정)
    hi: Optional[np.ndarray] = None
    iqr: Optional[np.ndarray] = None

    def iforest_score(self, X: np.ndarray) -> np.ndarray:
        raw = self.clf.decision_function(np.atleast_2d(X))
        pct = 100.0 * np.searchsorted(self.train_scores, raw, side="right") / self.train_scores.size
        inside = np.minimum(0.4 * pct, 39.0)
        span = max(self.s_ext - self.s99, 1e-6)
        outside = 40.0 + 60.0 * np.clip((raw - self.s99) / span, 0.0, 1.0) ** SCORE_GAMMA
        return np.where(raw <= self.s99, inside, outside)

    def range_score(self, X: np.ndarray) -> np.ndarray:
        """학습 범위를 벗어난 정도 → 0(범위 안·여유 안) 또는 40~100."""
        X = np.atleast_2d(X)
        if self.lo is None or tuple(self.features) not in RANGE_FEATURE_SETS:
            return np.zeros(len(X))
        excess = np.maximum(X - self.hi, self.lo - X) / self.iqr        # 범위 밖 거리 (IQR 단위)
        e = np.max(np.maximum(excess, 0.0), axis=1)
        over = e > OUT_MARGIN_IQR
        val = 40.0 + 60.0 * np.clip((e - OUT_MARGIN_IQR) / (OUT_FULL_IQR - OUT_MARGIN_IQR), 0, 1) ** SCORE_GAMMA
        return np.where(over, val, 0.0)

    def score(self, X: np.ndarray) -> np.ndarray:
        """특징 행렬 (n, d) → 0~100 점수 배열."""
        return np.maximum(self.iforest_score(X), self.range_score(X))


def _extreme_score(clf: IForest, X: np.ndarray) -> float:
    """학습 범위 밖 먼 점들의 점수 중앙값 → 점수 상한 근사."""
    lo, hi = X.min(0), X.max(0)
    rng = np.random.default_rng(0)
    pts = []
    for _ in range(64):
        far = np.where(rng.random(X.shape[1]) < 0.5, lo - 3 * (hi - lo + 1e-9), hi + 3 * (hi - lo + 1e-9))
        pts.append(far)
    return float(np.median(clf.decision_function(np.array(pts))))


class StressModel:
    """한 가두리의 밤낮 기준선 (특징 묶음별 Isolation Forest)."""

    def __init__(self, cage_id: str, n_estimators: int = 300, seed: int = 0):
        self.cage_id = cage_id
        self.n_estimators = n_estimators
        self.seed = seed
        self.detectors: Dict[tuple, _Detector] = {}     # (daypart, feature_set) → detector
        self.baseline: Dict[str, Dict[str, float]] = {}  # daypart → 특징 중앙값
        self.n_windows = 0
        self.model_version: Optional[str] = None
        self.training_settings = None
        self.training_revision = None

    @property
    def status(self) -> str:
        return "ready" if self.n_windows >= MIN_BASELINE_WINDOWS and self.detectors else "warming_up"

    def fit(self, fused: Sequence[FusedFeatures], *, snapshot=None) -> "StressModel":
        snapshot = snapshot_or_current(snapshot)
        require_process_settings(snapshot)
        self._require_fusion(fused, snapshot)
        self.training_settings = training_settings(snapshot)
        self.training_revision = snapshot.revision
        wins = [(t, f) for t, f in hourly_windows([x for x in fused if x.cage_id == self.cage_id])]
        self.n_windows = len(wins)
        self.detectors.clear()
        self.baseline.clear()
        for part in ("day", "night"):
            rows = [f for t, f in wins if is_day(t) == (part == "day")]
            if rows:
                self.baseline[part] = {k: float(np.nanmedian([r.get(k, np.nan) for r in rows]))
                                       for k in FULL_FEATURES if any(not np.isnan(r.get(k, np.nan)) for r in rows)}
            for fs, names in FEATURE_SETS.items():
                X = np.array([[r[k] for k in names] for r in rows if all(not np.isnan(r[k]) for k in names)])
                if len(X) < MIN_DAYPART_WINDOWS:
                    continue
                clf = IForest(n_estimators=self.n_estimators, max_samples=min(256, len(X)),
                              contamination=0.01, random_state=self.seed)
                clf.fit(X)
                tr = np.sort(clf.decision_scores_)
                q1, q3 = np.percentile(X, [25, 75], axis=0)
                spread = np.maximum(q3 - q1, np.maximum((X.max(0) - X.min(0)) / 4, 1e-6))
                self.detectors[(part, fs)] = _Detector(names, clf, float(np.percentile(tr, 99)),
                                                       _extreme_score(clf, X), tr,
                                                       X.min(0), X.max(0), spread)
        identity = {'windows': [(t.isoformat(), {k: None if np.isnan(v) else v for k,v in f.items()}) for t,f in wins],
                    'seed': self.seed, 'n_estimators': self.n_estimators}
        h = hashlib.sha256(json.dumps(identity,sort_keys=True,allow_nan=False).encode()).hexdigest()[:8]
        end = wins[-1][0].strftime("%Y%m%d") if wins else "none"
        settings_hash = hashlib.sha256(json.dumps(self.training_settings, sort_keys=True).encode()).hexdigest()[:8]
        self.model_version = f"{MODEL_VERSION}+{self.cage_id}.{end}.{h}.{settings_hash}"
        return self

    def validate_configuration(self, snapshot=None):
        snapshot = snapshot_or_current(snapshot)
        require_process_settings(snapshot)
        if (getattr(self, 'training_settings', None) != training_settings(snapshot)
                or not isinstance(getattr(self, 'training_revision', None), str)
                or re.fullmatch('[a-f0-9]{64}', self.training_revision) is None):
            raise ConfigError('Missing or incompatible model training metadata; retrain with current settings')
        return snapshot

    @staticmethod
    def _require_fusion(fused, snapshot):
        signature = configuration_signature(fusion_parameters(snapshot))
        if any(getattr(f,'fusion_signature',None) != signature for f in fused):
            raise ConfigError('Fused features do not match the model training configuration')

    # -------------------------------------------------------- 근거 지표
    def evidence(self, f: Dict[str, float], part: str) -> Dict[str, float]:
        b = self.baseline.get(part, {})
        ev: Dict[str, float] = {}

        def ratio(k, name):
            if k in f and k in b and not np.isnan(f[k]) and b[k] > 1e-9:
                ev[name] = round(f[k] / b[k], 3)

        def change(k, name):
            if k in f and k in b and not np.isnan(f[k]) and b[k] > 1e-9:
                ev[name] = round(f[k] / b[k] - 1.0, 3)

        ratio("p_mid", "mid_layer_ratio_x")
        ratio("p_surface", "surface_ratio_x")
        if all(k in f and k in b for k in ("p_mid", "p_bottom")):
            ev["lower_layers_ratio_x"] = round((f["p_mid"] + f["p_bottom"]) / max(b["p_mid"] + b["p_bottom"], 1e-9), 3)
        change("act_mean", "activity_change")
        change("cohesion", "cohesion_change")
        change("feeding", "feeding_change")
        change("still_ratio", "still_ratio_change")
        ratio("rubbing_rate", "rubbing_rate_x")
        return ev

    # -------------------------------------------------------- 점수
    def _pick(self, ts: datetime, f: Dict[str, float]):
        """점수 낼 기준선 모델 목록 고르기. → (빈 StressScore, [(daypart, 묶음)])"""
        part = "day" if is_day(ts - timedelta(hours=WINDOW_HOURS / 2)) else "night"   # 윈도우 가운데 시각 기준
        base = StressScore(cage_id=self.cage_id, ts=ts, score=None, baseline_status=self.status,
                           model_version=self.model_version, daypart=part, baseline_windows=self.n_windows,
                           features={k: round(v, 4) for k, v in f.items() if not np.isnan(v)})
        if self.status != "ready" or not f:
            return base, []
        keys = [(part, fs) for fs, names in FEATURE_SETS.items()
                if (part, fs) in self.detectors and all(not np.isnan(f.get(k, np.nan)) for k in names)]
        if not any(k[1] == "sonar" for k in keys):
            return base, []          # 소나(융합) 특징이 없으면 점수를 내지 않는다
        base.feature_set = "+".join(k[1] for k in keys)
        base.evidence = self.evidence(f, part)
        return base, keys

    @staticmethod
    def _finish(s: StressScore, comps: Dict[str, float]) -> None:
        s.components = {k: int(round(min(v, COMPONENT_CAP.get(k, 100)))) for k, v in comps.items()}
        s.score = max(s.components.values())

    def score_window(self, ts: datetime, f: Dict[str, float], *, snapshot=None) -> StressScore:
        """윈도우 하나의 점수 (ts = 윈도우 끝 시각)."""
        self.validate_configuration(snapshot)
        s, keys = self._pick(ts, f)
        if keys:
            comps = {}
            for key in keys:
                det = self.detectors[key]
                comps[key[1]] = float(det.score(np.array([f[k] for k in det.features]))[0])
            self._finish(s, comps)
        return s

    def score_series(self, fused: Sequence[FusedFeatures], *, snapshot=None) -> List[StressScore]:
        """주기마다 직전 1시간(12주기) 윈도우로 점수를 낸다. 같은 모델끼리 묶어 한 번에 계산."""
        snapshot = self.validate_configuration(snapshot)
        fused = [x for x in fused if x.cage_id == self.cage_id]
        self._require_fusion(fused, snapshot)
        ts, X = cycle_matrix(fused)
        out: List[StressScore] = []
        batch: Dict[tuple, list] = {}
        wanted: Dict[int, list] = {}
        for i in range(len(fused)):
            end = ts[i] + timedelta(minutes=CYCLE_MINUTES)
            j0 = i - WINDOW_CYCLES + 1
            f = window_features(X[j0:i + 1]) if j0 >= 0 else {}
            s, keys = self._pick(end, f)
            out.append(s)
            for key in keys:
                batch.setdefault(key, []).append((i, f))
            if keys:
                wanted[i] = keys
        comps: Dict[int, Dict[str, float]] = {i: {} for i in wanted}
        for key, items in batch.items():
            det = self.detectors[key]
            scores = det.score(np.array([[f[k] for k in det.features] for _, f in items]))
            for (i, _), v in zip(items, scores):
                comps[i][key[1]] = float(v)
        for i, c in comps.items():
            self._finish(out[i], c)
        return out

    # -------------------------------------------------------- 저장
    def save(self, path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode='wb', dir=path.parent, prefix='.model-', delete=False) as f:
                temporary = Path(f.name)
                pickle.dump(self, f)
                f.flush()
                os.fsync(f.fileno())
            os.replace(temporary, path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        return path

    @staticmethod
    def load(path, *, snapshot=None) -> "StressModel":
        # Only load our trusted artifacts: pickle is executable, not a safe upload format.
        with Path(path).open("rb") as f:
            model = pickle.load(f)
        if not isinstance(model, StressModel):
            raise ConfigError('Invalid stress model artifact')
        model.validate_configuration(snapshot)
        return model
