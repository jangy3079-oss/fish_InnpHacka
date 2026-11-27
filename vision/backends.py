"""Adapters to the installed CFD RF-DETR and trackers APIs. No automatic downloads."""

from pathlib import Path

import cv2

from .config import VisionConfig


class FishDetector:
    def __init__(self, weights: Path | str, device: str = "cpu"):
        from rfdetr import from_checkpoint

        if not Path(weights).is_file():
            raise ValueError(f"model weights not found: {weights}")
        self.model = from_checkpoint(str(weights), device=device)

    def detect(self, frame, threshold):
        # RF-DETR ndarray input uses RGB; OpenCV video frames use BGR.
        return self.model.predict(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB), threshold=threshold)


class FishTracker:
    def __init__(self, config: VisionConfig, frame_rate: float):
        from trackers import ByteTrackTracker

        self.tracker = ByteTrackTracker(
            frame_rate=frame_rate,
            lost_track_buffer=config.lost_track_buffer,
            track_activation_threshold=config.track_activation_threshold,
            high_conf_det_threshold=config.high_conf_det_threshold,
            minimum_consecutive_frames=config.minimum_consecutive_frames,
        )

    def update(self, detections, seconds):
        return self.tracker.update(detections, timestamp=seconds)

    def reset(self):
        self.tracker.reset()
