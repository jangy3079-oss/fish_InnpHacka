"""Camera settings are fixed for one video run (config policy discussion: #90)."""

from pathlib import Path
from typing import Annotated, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from common.config_loader import ConfigLoader

ROOT = Path(__file__).resolve().parents[1]
Positive = Annotated[float, Field(gt=0, allow_inf_nan=False)]
Ratio = Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]


class VisionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    contrast_reference: Positive
    sharpness_reference: Positive
    brightness_target: Positive
    quality_weights: tuple[Ratio, Ratio, Ratio]
    clahe_clip_limit: Positive
    clahe_grid_size: Annotated[int, Field(strict=True, ge=1)]
    detection_threshold: Ratio
    track_activation_threshold: Ratio
    high_conf_det_threshold: Ratio
    minimum_consecutive_frames: Annotated[int, Field(strict=True, ge=1)]
    lost_track_buffer: Annotated[int, Field(strict=True, ge=1)]
    still_speed_px_s: Positive
    activity_reference_px_s: Positive | None
    meters_per_pixel: Positive | None
    depth_top_m: Annotated[float, Field(ge=0, allow_inf_nan=False)] | None
    depth_bottom_m: Positive | None
    net_depth_m: Positive | None
    gate_low: Ratio
    clahe_min: Ratio
    clahe_max: Ratio
    camera_only_min: Ratio

    @model_validator(mode="after")
    def consistent(self) -> Self:
        if abs(sum(self.quality_weights) - 1) > 1e-6:
            raise ValueError("quality_weights must sum to 1")
        if not self.gate_low <= self.clahe_min < self.clahe_max <= self.camera_only_min:
            raise ValueError("invalid camera quality gates")
        if self.brightness_target > 255:
            raise ValueError("brightness_target must be at most 255")
        depths = (self.depth_top_m, self.depth_bottom_m, self.net_depth_m)
        if any(d is not None for d in depths):
            if any(d is None for d in depths):
                raise ValueError("depth calibration requires top, bottom and net depth")
            if not 0 <= self.depth_top_m < self.depth_bottom_m <= self.net_depth_m:
                raise ValueError("require 0 <= top < bottom <= net depth")
        return self


def load_config(path: Path | str = ROOT / "config/vision.yaml") -> VisionConfig:
    settings = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(settings, dict):
        raise ValueError("vision config must be a mapping")
    thresholds = ConfigLoader().snapshot()["thresholds"]
    return VisionConfig.model_validate({
        **settings,
        "gate_low": thresholds["fusion"]["gate_low"],
        **{key: thresholds["quality"][key]
           for key in ("clahe_min", "clahe_max", "camera_only_min")},
    })
