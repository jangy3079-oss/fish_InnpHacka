"""경보 단계 판정 (#24, FR-DIA-04, SDD 6.2).

| 지수   | 단계 | 올라갈 때                                     |
| 0~39   | 정상 | —                                             |
| 40~59  | 주의 | 2시간 연속 유지                                |
| 60~79  | 경고 | 2시간 연속 유지                                |
| 80~100 | 위험 | 1시간 연속 유지, 또는 '피난처 소실'(R2) 발동 시 즉시 |

- 내려갈 때는 현재 단계 조건(지수 ≥ 하한, R2)이 6시간 연속으로 풀려야 한다.
  내려가는 단계는 그 6시간 동안 가장 높았던 지수의 단계다.
- 점수가 없는 주기(warming_up, 센서 끊김)는 단계를 그대로 두고 시간 계산도 이어가지 않는다.
- 단계를 올린 근거 묶음(예: 비비기·카메라)이 안 보이는 주기(밤, 탁함)는 '정보 없음'으로 본다.
  근거가 안 보이는 것과 증상이 사라진 것은 다르므로, 그 동안은 해제 시간을 세지 않는다.
- 같은 단계 알림 6시간 1회 억제(NFR-REL-04)는 notify가 맡는다. 여기서는 단계 변화만 기록한다.

임계값은 `config/thresholds.yaml`이 있으면 거기서 읽고, 없으면 아래 기본값을 쓴다.

    levels:        {주의: 40, 경고: 60, 위험: 80}
    hold_hours:    {주의: 2, 경고: 2, 위험: 1}
    release_hours: 6
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Sequence

LEVELS = ["정상", "주의", "경고", "위험"]
DEFAULTS = {
    "levels": {"주의": 40, "경고": 60, "위험": 80},
    "hold_hours": {"주의": 2, "경고": 2, "위험": 1},
    "release_hours": 6,
}
CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "thresholds.yaml"


def load_thresholds(path: Optional[Path] = None) -> dict:
    cfg = {k: (dict(v) if isinstance(v, dict) else v) for k, v in DEFAULTS.items()}
    p = Path(path) if path else CONFIG_PATH
    if p.exists():
        import yaml
        with p.open(encoding="utf-8") as f:
            d = yaml.safe_load(f) or {}
        for k in ("levels", "hold_hours"):
            if isinstance(d.get(k), dict):
                cfg[k].update({kk: d[k][kk] for kk in d[k] if kk in LEVELS})
        if "release_hours" in d:
            cfg["release_hours"] = d["release_hours"]
    return cfg


def level_of(score: float, cfg: dict) -> int:
    """지수 → 단계 인덱스 (0=정상 … 3=위험), 유지 시간 고려 전."""
    idx = 0
    for i, name in enumerate(LEVELS[1:], start=1):
        if score >= cfg["levels"][name]:
            idx = i
    return idx


@dataclass
class LevelChange:
    ts: datetime
    from_level: str
    to_level: str
    reason: str          # sustained | refuge_loss | released


@dataclass
class LevelJudge:
    """주기 단위로 지수를 넣으면 현재 단계를 돌려주는 상태 기계. 가두리마다 하나."""
    cfg: dict = field(default_factory=load_thresholds)
    level: int = 0
    _run_start: Dict[int, Optional[datetime]] = field(default_factory=lambda: {1: None, 2: None, 3: None})
    _release_start: Optional[datetime] = None
    _release_peak: int = 0
    _last_ts: Optional[datetime] = None
    changes: List[LevelChange] = field(default_factory=list)

    def _hold(self, idx: int) -> timedelta:
        return timedelta(hours=float(self.cfg["hold_hours"][LEVELS[idx]]))

    def update(self, ts: datetime, score: Optional[float], refuge_loss: bool = False,
               cycle: timedelta = timedelta(minutes=5)) -> str:
        if score is None and not refuge_loss:
            self._last_ts = ts
            return LEVELS[self.level]
        cand = level_of(score, self.cfg) if score is not None else 0

        # 각 단계 하한 이상이 연속으로 유지된 시작 시각 (빈 주기가 끼면 끊긴 것으로 본다)
        gap = self._last_ts is not None and ts - self._last_ts > cycle * 1.5
        for i in (1, 2, 3):
            if cand >= i and not gap and self._run_start[i] is not None:
                continue
            self._run_start[i] = ts if cand >= i else None
        self._last_ts = ts

        by_score = 0
        for i in (1, 2, 3):
            st = self._run_start[i]
            if st is not None and ts - st + cycle >= self._hold(i):
                by_score = i
        sustained = 3 if refuge_loss else by_score

        prev = self.level
        if sustained > self.level:
            self.level = sustained
            self._release_start = None
            reason = "refuge_loss" if refuge_loss and by_score < 3 else "sustained"
            self.changes.append(LevelChange(ts, LEVELS[prev], LEVELS[self.level], reason))
        elif self.level > 0:
            holding = cand >= self.level or (refuge_loss and self.level == 3)
            if holding:
                self._release_start = None
            else:
                if self._release_start is None or gap:
                    self._release_start, self._release_peak = ts, cand
                self._release_peak = max(self._release_peak, cand)
                if ts - self._release_start + cycle >= timedelta(hours=float(self.cfg["release_hours"])):
                    self.level = self._release_peak
                    self._release_start = None
                    self.changes.append(LevelChange(ts, LEVELS[prev], LEVELS[self.level], "released"))
        return LEVELS[self.level]


def apply_levels(scores: Sequence, refuge_loss: Optional[Sequence[bool]] = None,
                 cfg: Optional[dict] = None) -> List[LevelChange]:
    """StressScore 시계열(한 가두리, 시간 순)에 level을 채운다. 단계 변화 목록을 돌려준다."""
    judge = LevelJudge(cfg=cfg or load_thresholds())
    raised_by: Optional[str] = None       # 지금 단계를 만든 묶음 (가장 높은 묶음 점수)
    for i, s in enumerate(scores):
        r2 = bool(refuge_loss[i]) if refuge_loss is not None else False
        comps = getattr(s, "components", None) or {}
        score = s.score
        if judge.level > 0 and raised_by and comps and raised_by not in comps and not r2:
            score = None                  # 근거 묶음이 안 보임 → 정보 없음
        before = judge.level
        s.level = judge.update(s.ts, score, r2)
        if judge.level > before and comps:
            raised_by = max(comps, key=comps.get)
        elif judge.level == 0:
            raised_by = None
    return judge.changes


def refuge_loss_signal(scores: Sequence, fused: Sequence, min_temp: float = 25.0,
                       max_diff: float = 1.0, max_activity_change: float = -0.4,
                       cohesion_drop: float = 0.3, lookback_h: float = 12.0) -> List[bool]:
    """R2(피난처 소실) 발동 여부 (SDD 6.3).

    환경: 표층 ≥ 25℃ 이고 표층−중층 < 1℃
    행동: 활동량 기준선 대비 −40% 이하, 또는 응집도 급락
          (응집도 급락 = 기준선 대비 응집도 변화가 직전 12시간 최고값보다 0.3 이상 떨어짐.
           밤에는 기준선 자체가 퍼져 있고 활동도 낮아 기준선 대비 값만으로는 잘 안 보인다)

    백엔드 원인 규칙 엔진(cause, #25)이 R2 결과를 주면 그것을 apply_levels에 넘기면 된다.
    이 함수는 데이터 파이프라인 단독 실행·검증용이다.
    """
    env = {f.ts: f.env for f in fused}
    out: List[bool] = []
    hist: List[tuple] = []          # (ts, cohesion_change)
    for s in scores:
        e = env.get(s.ts - timedelta(minutes=5), {}) or {}
        ev = s.evidence or {}
        coh = ev.get("cohesion_change")
        hist = [(t, c) for t, c in hist if s.ts - t <= timedelta(hours=lookback_h)]
        peak = max((c for _, c in hist), default=None)
        if coh is not None:
            hist.append((s.ts, coh))
        ts_, tm = e.get("temp_surface"), e.get("temp_mid")
        env_ok = ts_ is not None and tm is not None and ts_ >= min_temp and ts_ - tm < max_diff
        act = ev.get("activity_change")
        act_ok = act is not None and act <= max_activity_change
        coh_ok = coh is not None and peak is not None and peak - coh >= cohesion_drop
        out.append(bool(env_ok and (act_ok or coh_ok)))
    return out
