"""Frame processing with explicit missing observations and calibrated measurements."""

from dataclasses import dataclass
from datetime import datetime
from typing import Callable

import numpy as np

from common import MetricRecord
from common.layers import layer_fractions

from .config import VisionConfig
from .quality import assess, enhance


@dataclass
class FrameResult:
    records: list[MetricRecord]
    diagnostics: dict


class CameraPipeline:
    def __init__(self, cage_id: str, config: VisionConfig, detector_factory: Callable,
                 tracker_factory: Callable):
        self.cage_id = cage_id
        self.config = config
        self.detector_factory = detector_factory
        self.tracker_factory = tracker_factory
        self.detector = None
        self.tracker = None
        self.previous = {}
        self.last_ts = None

    def process(self, frame: np.ndarray, ts: datetime) -> FrameResult:
        # Validate identity/time even on low-quality frames, before changing state.
        identity = MetricRecord(cage_id=self.cage_id, ts=ts, source="camera",
                                metric="quality", value=0.0, confidence=0.0)
        ts = identity.ts
        if self.last_ts is not None and ts <= self.last_ts:
            raise ValueError("frame timestamps must strictly increase")
        q = assess(frame, self.config)
        self.last_ts = ts
        records = [identity.model_copy(update={"value": q.score, "confidence": q.score})]
        diag = {"cage_id": self.cage_id, "ts": ts.isoformat(), "quality": q.to_dict(),
                "enhanced": False, "state": "low_quality", "detection_count": None,
                "tracked_count": None, "speed_px_s": None, "speed_m_s": None,
                "depth_mean_m": None, "tracks": []}
        if q.score < self.config.gate_low:
            self.previous.clear()
            if self.tracker is not None:
                self.tracker.reset()
            return FrameResult(records, diag)

        image, diag["enhanced"] = enhance(frame, q.score, self.config)
        if self.detector is None:
            self.detector = self.detector_factory()
        if self.tracker is None:
            self.tracker = self.tracker_factory()
        detections = self.detector.detect(image, self.config.detection_threshold)
        diag["detection_count"] = len(detections)
        tracked = self.tracker.update(detections, ts.timestamp())
        # -1 identifies provisional detections; never group them into one fish.
        ids = tracked.tracker_id
        valid = np.zeros(len(tracked), dtype=bool) if ids is None else ids >= 0
        boxes = tracked.xyxy[valid]
        ids = np.array([], dtype=int) if ids is None else ids[valid]
        centers = (boxes[:, :2] + boxes[:, 2:]) / 2
        diag["tracked_count"] = len(ids)
        diag["state"] = "tracked" if len(ids) else ("no_detections" if not len(detections) else "warming_up")

        speeds = []
        reliable_speeds = []
        current = {}
        for track_id, center in zip(ids, centers):
            key = int(track_id)
            previous = self.previous.get(key)
            speed = None
            if previous is not None:
                point, old_ts, old_quality = previous
                speed = float(np.linalg.norm(center - point) / (ts - old_ts).total_seconds())
                speeds.append(speed)
                if min(old_quality, q.score) >= self.config.camera_only_min:
                    reliable_speeds.append(speed)
            current[key] = (center, ts, q.score)
            diag["tracks"].append({"id": key, "center_px": center.tolist(), "speed_px_s": speed})
        # Compare only consecutive observations; never bridge missing/low quality periods.
        self.previous = current

        def emit(metric, value):
            records.append(MetricRecord(cage_id=self.cage_id, ts=ts, source="camera",
                                        metric=metric, value=value, confidence=q.score))

        if speeds:
            speed = float(np.mean(speeds))
            diag["speed_px_s"] = speed
            if self.config.meters_per_pixel is not None:
                diag["speed_m_s"] = speed * self.config.meters_per_pixel
            if self.config.activity_reference_px_s is not None:
                emit("activity", speed / self.config.activity_reference_px_s)
            if reliable_speeds:
                emit("still_ratio", float(np.mean(np.asarray(reliable_speeds) <= self.config.still_speed_px_s)))
        if len(centers) and self.config.depth_top_m is not None:
            # Linear y→depth mapping is an explicit installation assumption, never inferred.
            depths = (self.config.depth_top_m + np.clip(centers[:, 1] / frame.shape[0], 0, 1)
                      * (self.config.depth_bottom_m - self.config.depth_top_m))
            diag["depth_mean_m"] = float(np.mean(depths))
            emit("vertical_dist", layer_fractions(depths, self.config.net_depth_m).tolist())
        return FrameResult(records, diag)
