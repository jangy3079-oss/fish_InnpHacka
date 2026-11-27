"""M1: original-image quality. M2 never upgrades the confidence of an observation."""

from dataclasses import asdict, dataclass

import cv2
import numpy as np

from .config import VisionConfig


@dataclass(frozen=True)
class Quality:
    score: float
    contrast: float
    sharpness: float
    brightness: float

    def to_dict(self) -> dict:
        return asdict(self)


def validate_frame(frame: np.ndarray) -> None:
    if (not isinstance(frame, np.ndarray) or frame.dtype != np.uint8
            or frame.ndim != 3 or frame.shape[2] != 3 or min(frame.shape[:2]) < 2):
        raise ValueError("expected a nonempty uint8 BGR frame, at least 2x2")


def assess(frame: np.ndarray, config: VisionConfig) -> Quality:
    validate_frame(frame)
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    contrast = float(gray.std())
    sharpness = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    brightness = float(gray.mean())
    parts = (min(contrast / config.contrast_reference, 1),
             min(sharpness / config.sharpness_reference, 1),
             max(0, 1 - abs(brightness - config.brightness_target) / config.brightness_target))
    score = float(np.clip(np.dot(parts, config.quality_weights), 0, 1))
    return Quality(score, contrast, sharpness, brightness)


def enhance(frame: np.ndarray, q: float, config: VisionConfig) -> tuple[np.ndarray, bool]:
    # Half-open interval: q == 0.6 is already the camera-only quality tier.
    if not config.clahe_min <= q < config.clahe_max:
        return frame, False
    lab = cv2.cvtColor(frame, cv2.COLOR_BGR2LAB)
    clahe = cv2.createCLAHE(config.clahe_clip_limit,
                          (config.clahe_grid_size, config.clahe_grid_size))
    lab[:, :, 0] = clahe.apply(lab[:, :, 0])
    return cv2.cvtColor(lab, cv2.COLOR_LAB2BGR), True
