"""조피볼락 개체 기반 행동 모델.

각 개체는 수심 z(m)를 가지며, 핑(기본 1분)마다 Metropolis 무작위 걷기로 움직인다.
목표 수심 분포 P(z)는 밤낮 기본 선호 × 수온 회피 × 저산소 상승으로 정하고,
걸음 크기는 활동 지수에 비례한다. 그래서 수온·층간 수온 차·DO가 바뀌면
수직 분포·활동량·응집도가 저절로 바뀐다 (FR-MEA-03).

수온 반응 기준 (SRS 6.1 시뮬레이터 기준값 표)
  23℃ 이상 : 급이 반응 저하                       → feeding_response()
  25℃      : 활동 감소, 저·중층 쏠림               → heat_activity(), 표층 수온 회피
  28℃      : 활동 추가 감소                        → heat_activity()
  30℃      : 정지 개체 증가, 분포 붕괴              → still_fraction(), disorder()
  층간 차 소멸 : 피난처 소실 → 쏠림 붕괴·활동 급감   → disorder()
여기 있는 배수·폭은 설계 가정값이며 2·3단계 실증에서 다시 맞춘다.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

# ---------------------------------------------------------------- 가정값

T_COMFORT = 24.0         # 이 수온까지는 수온 회피 없음 (℃)
T_AVOID_SD = 1.0         # 회피 강도: 24℃ 초과분의 가우시안 폭
DAY_CENTER, DAY_SD = 3.0, 2.0        # 낮: 표층~중층 사이에 모여 있음 (m)
NIGHT_CENTER, NIGHT_SD = 4.5, 2.4    # 밤: 조금 깊고 넓게 퍼짐
NIGHT_ACTIVITY = 0.6     # 밤 활동 배율
STEP_SD = 0.45           # 활동 1.0일 때 1핑 사이 제안 걸음 표준편차 (m)
DO_LOW = 4.5             # 이 DO(mg/L) 아래부터 표층으로 올라옴 (값 확인 필요, SDD 6.3 R3)


def _interp(x, xp, fp):
    return np.interp(x, xp, fp)


def heat_activity(temp_surface):
    """표층 수온에 따른 활동 배율. 25℃부터 감소, 28℃ 추가 감소, 30℃ 크게 감소."""
    return _interp(temp_surface, [24.0, 25.0, 28.0, 30.0, 32.0], [1.0, 0.75, 0.6, 0.4, 0.3])


def feeding_response(temp_surface):
    """급이 반응 지수 0~1. 23℃ 진입부터 감소 (#39, UC-04)."""
    return _interp(temp_surface, [22.5, 23.0, 24.0, 25.0, 28.0, 30.0], [0.9, 0.8, 0.6, 0.45, 0.25, 0.1])


def disorder(temp_coolest):
    """피난처 소실 정도 0~1. 물기둥에서 가장 찬 물도 25℃ 근처 이상이면 쏠림이 무너진다."""
    return np.clip((temp_coolest - 24.5) / 2.0, 0.0, 1.0)


def still_fraction(temp_surface, day):
    """정지 개체 비율. 밤에 조금 높고, 28℃부터 늘고 30℃에서 크게 는다."""
    base = 0.03 * day + 0.08 * (1 - day)
    return base + _interp(temp_surface, [27.0, 28.0, 30.0, 32.0], [0.0, 0.05, 0.35, 0.5])


def temperature_profile(z, temp_surface, temp_mid):
    """수심별 수온. 0~2.5m 표층 혼합층, 2.5~4.5m 수온약층, 그 아래 중층 수온(약간 더 찬)."""
    k = np.clip((z - 2.5) / 2.0, 0.0, 1.0)
    t = (1 - k) * temp_surface + k * temp_mid
    return t - 0.1 * np.clip(z - 4.5, 0.0, None)


@dataclass
class Drivers:
    """한 주기 동안의 행동 결정 요인 (환경 + 이벤트)."""
    temp_surface: float
    temp_mid: float
    do: float
    day: float            # 낮 정도 0~1
    disease: float = 0.0  # 질병 진행도 0~1
    parasite: float = 0.0 # 기생충 감염 정도 0~1
    red_tide: float = 0.0 # 적조 노출 정도 0~1 (#174)
    work: float = 0.0     # 망갈이·선별 직후 스트레스 0~1, 시간이 지나며 회복 (#174)


@dataclass
class Behavior:
    """Drivers로 계산한 이번 주기 행동 상태 (참값)."""
    pdf: np.ndarray       # 깊이 격자 위 목표 분포
    activity: float       # 활동 배율 (정상 낮 = 1)
    still: float          # 정지 개체 비율
    feeding: float        # 급이 반응 0~1
    rubbing_mult: float   # 비비기 배율
    disorder: float


def behavior(dr: Drivers, grid: np.ndarray, rubbing_multiplier: float = 4.0) -> Behavior:
    center = DAY_CENTER * dr.day + NIGHT_CENTER * (1 - dr.day)
    sd = DAY_SD * dr.day + NIGHT_SD * (1 - dr.day)
    # 질병이 진행되면 힘이 빠져 아래로 가라앉는다
    center += 2.5 * dr.disease
    # 작업 직후: 놀라서 바닥 쪽으로 몰린다. 적조: 아가미 자극으로 표층 쪽으로 오른다 (가정값, #174)
    center += 2.0 * dr.work - 1.0 * dr.red_tide
    base = np.exp(-0.5 * ((grid - center) / sd) ** 2)

    temps = temperature_profile(grid, dr.temp_surface, dr.temp_mid)
    thermal = np.exp(-0.5 * (np.clip(temps - T_COMFORT, 0, None) / T_AVOID_SD) ** 2)

    hyp = np.clip((DO_LOW - dr.do) / 2.0, 0.0, 1.0)
    oxygen = np.exp(-1.2 * hyp * grid)

    pref = base * thermal * oxygen + 1e-9
    pref /= pref.sum()
    d = float(disorder(temps.min()))
    pdf = (1 - d) * pref + d / grid.size

    act = (1.0 * dr.day + NIGHT_ACTIVITY * (1 - dr.day))
    act *= float(heat_activity(dr.temp_surface)) * (1 - 0.5 * d)
    act *= 1 - 0.5 * dr.disease
    act *= 1 + 0.15 * dr.parasite      # 몸 비비기·급회전으로 조금 더 움직임
    act *= 1 + 1.2 * dr.red_tide       # 적조: 아가미 자극으로 급하게 돌아다님 (가정값)
    act *= 1 - 0.5 * dr.work           # 작업 직후: 움츠러들어 덜 움직임 (가정값)

    still = float(still_fraction(dr.temp_surface, dr.day)) + 0.25 * dr.disease + 0.2 * dr.work
    feed = float(feeding_response(dr.temp_surface)) * (1 - 0.7 * dr.disease) * (1 - 0.6 * dr.red_tide) \
        * (1 - 0.7 * dr.work)
    rub = 1 + (rubbing_multiplier - 1) * dr.parasite
    return Behavior(pdf=pdf, activity=act, still=min(still, 0.9), feeding=feed,
                    rubbing_mult=rub, disorder=d)


class FishSchool:
    """N 마리 개체의 수심과 반사 강도(TS)를 들고 핑마다 움직인다."""

    def __init__(self, n: int, depth_m: float, rng: np.random.Generator, grid_step: float = 0.1):
        self.rng = rng
        self.depth_m = depth_m
        self.grid = np.arange(0.25, depth_m - 0.25 + 1e-9, grid_step)
        self.z = rng.uniform(1.0, depth_m - 1.0, n)
        # 개체마다 크기가 달라 반사 강도가 다름 (선형 σ_bs, 로그정규)
        self.ts_lin = rng.lognormal(mean=0.0, sigma=0.35, size=n)

    def warm_start(self, pdf: np.ndarray):
        cdf = np.cumsum(pdf)
        idx = np.searchsorted(cdf, self.rng.uniform(0, cdf[-1], self.z.size))
        self.z = self.grid[np.clip(idx, 0, self.grid.size - 1)]

    def step(self, b: Behavior) -> np.ndarray:
        n = self.z.size
        # 활동 지수는 '움직이는 개체 비율'과 '걸음 크기' 둘 다에 반영한다
        p_move = (1.0 - b.still) * min(1.0, b.activity)
        moving = self.rng.random(n) < p_move
        prop = self.z + self.rng.normal(0.0, STEP_SD * (0.5 + 0.5 * b.activity), n)
        lo, hi = self.grid[0], self.grid[-1]
        prop = np.where(prop < lo, 2 * lo - prop, prop)       # 경계 반사
        prop = np.where(prop > hi, 2 * hi - prop, prop)
        prop = np.clip(prop, lo, hi)
        p_now = np.interp(self.z, self.grid, b.pdf)
        p_new = np.interp(prop, self.grid, b.pdf)
        accept = self.rng.random(n) < np.minimum(1.0, p_new / np.maximum(p_now, 1e-12))
        self.z = np.where(moving & accept, prop, self.z)
        return self.z
