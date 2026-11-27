"""수직 분포 3구간 정의 (MetricRecord vertical_dist 순서 = [저층, 중층, 표층]).

MVP는 그물 깊이를 3등분한다. 9m 그물: 표층 0~3m, 중층 3~6m, 저층 6~9m.
simulator(카메라 참값)와 sonar(M4)가 같은 경계를 쓴다.
"""

from typing import Tuple

import numpy as np

LAYER_ORDER: Tuple[str, str, str] = ("bottom", "mid", "surface")


def layer_edges(depth_m: float) -> Tuple[float, float]:
    """(표층/중층 경계, 중층/저층 경계) 수심(m)."""
    return depth_m / 3.0, 2.0 * depth_m / 3.0


def layer_index(z: np.ndarray, depth_m: float) -> np.ndarray:
    """수심 → vertical_dist 배열 인덱스 (0=저층, 1=중층, 2=표층)."""
    a, b = layer_edges(depth_m)
    return np.where(z < a, 2, np.where(z < b, 1, 0))


def layer_fractions(z: np.ndarray, depth_m: float, weights: np.ndarray | None = None) -> np.ndarray:
    """수심 값(또는 가중치)으로 [저층, 중층, 표층] 비율 계산."""
    idx = layer_index(np.asarray(z), depth_m)
    w = np.ones_like(idx, dtype=float) if weights is None else np.asarray(weights, dtype=float)
    out = np.bincount(idx.ravel(), weights=w.ravel(), minlength=3).astype(float)
    s = out.sum()
    return out / s if s > 0 else np.full(3, 1 / 3)
