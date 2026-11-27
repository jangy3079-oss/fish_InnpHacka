"""#18: quality gates, missing observations, calibration, tracking and clip timestamps."""

import importlib.util
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

HAS_CV = importlib.util.find_spec("cv2") is not None
if HAS_CV:
    import cv2
    from vision.config import VisionConfig, load_config
    from vision.pipeline import CameraPipeline
    from vision.quality import Quality, assess, enhance


class Detections:
    def __init__(self, boxes=(), ids=None):
        self.xyxy = np.array(boxes, dtype=float).reshape(-1, 4)
        self.tracker_id = None if ids is None else np.asarray(ids)

    def __len__(self):
        return len(self.xyxy)


class Backend:
    def __init__(self, output):
        self.output = output
        self.calls = 0
        self.resets = 0

    def detect(self, frame, threshold):
        self.calls += 1
        return self.output

    def update(self, detections, seconds):
        return detections

    def reset(self):
        self.resets += 1


@unittest.skipUnless(HAS_CV, "optional camera dependencies: requirements-vision.txt")
class VisionTests(unittest.TestCase):
    def setUp(self):
        self.config = load_config()
        self.frame = np.random.default_rng(18).integers(0, 256, (90, 120, 3), dtype=np.uint8)
        self.ts = datetime(2026, 11, 27, tzinfo=timezone.utc)
        self.backend = Backend(Detections([[0, 0, 20, 20]], [1]))

    def pipeline(self, **changes):
        config = VisionConfig.model_validate({**self.config.model_dump(), **changes})
        return CameraPipeline("cage-003", config, lambda: self.backend, lambda: self.backend)

    @staticmethod
    def values(result):
        return {r.metric.value: r.value for r in result.records}

    def test_quality_is_finite_and_black_white_blur_score_lower(self):
        sharp = assess(self.frame, self.config).score
        for frame in (np.zeros_like(self.frame), np.full_like(self.frame, 255),
                      cv2.GaussianBlur(self.frame, (31, 31), 10)):
            q = assess(frame, self.config).score
            self.assertTrue(0 <= q < sharp <= 1)

    def test_invalid_frames_rejected(self):
        for frame in (np.zeros((0, 0, 3), np.uint8), np.zeros((5, 5)),
                      np.zeros((4, 4, 3), np.float32)):
            with self.assertRaises(ValueError):
                assess(frame, self.config)

    def test_clahe_boundaries_and_original_unchanged(self):
        original = self.frame.copy()
        for q, expected in ((0.299, False), (0.3, True), (0.599, True), (0.6, False)):
            image, applied = enhance(self.frame, q, self.config)
            self.assertEqual(applied, expected)
            self.assertEqual(image.shape, self.frame.shape)
            self.assertEqual(image.dtype, np.uint8)
        np.testing.assert_array_equal(self.frame, original)

    def test_low_quality_is_quality_only_and_model_never_loaded(self):
        def fail():
            self.fail("low-quality frame must skip model loading")
        pipeline = CameraPipeline("3", self.config, fail, fail)
        result = pipeline.process(np.zeros_like(self.frame), self.ts)
        self.assertEqual(self.values(result), {"quality": 0.0})
        self.assertIsNone(result.diagnostics["detection_count"])
        self.assertEqual(result.diagnostics["state"], "low_quality")

    def test_no_detections_never_means_zero_behavior(self):
        self.backend.output = Detections()
        result = self.pipeline().process(self.frame, self.ts)
        self.assertEqual(set(self.values(result)), {"quality"})
        self.assertEqual(result.diagnostics["state"], "no_detections")

    def test_unconfirmed_ids_are_excluded(self):
        self.backend.output = Detections([[0, 0, 20, 20], [40, 40, 60, 60]], [-1, -1])
        result = self.pipeline().process(self.frame, self.ts)
        self.assertEqual(result.diagnostics["tracked_count"], 0)
        self.assertEqual(result.diagnostics["state"], "warming_up")
        self.assertEqual(set(self.values(result)), {"quality"})

    def test_calibrated_speed_activity_depth_and_layer_order(self):
        pipeline = self.pipeline(activity_reference_px_s=10., meters_per_pixel=.1,
                                 depth_top_m=0., depth_bottom_m=9., net_depth_m=9.)
        first = pipeline.process(self.frame, self.ts)
        self.assertNotIn("activity", self.values(first))
        self.backend.output = Detections([[10, 0, 30, 20]], [1])
        result = pipeline.process(self.frame, self.ts + timedelta(seconds=2))
        self.assertEqual(result.diagnostics["speed_px_s"], 5.)
        self.assertEqual(result.diagnostics["speed_m_s"], .5)
        self.assertEqual(result.diagnostics["depth_mean_m"], 1.)
        self.assertEqual(self.values(result)["activity"], .5)
        self.assertEqual(self.values(result)["vertical_dist"], [0., 0., 1.])
        self.assertEqual(self.values(result)["still_ratio"], 1.)
        for record in result.records:
            self.assertEqual(record.source.value, "camera")
            self.assertEqual(record.confidence, result.diagnostics["quality"]["score"])

    def test_no_calibration_omits_activity_and_physical_depth(self):
        pipeline = self.pipeline()
        pipeline.process(self.frame, self.ts)
        result = pipeline.process(self.frame, self.ts + timedelta(seconds=1))
        self.assertEqual(set(self.values(result)), {"quality", "still_ratio"})
        self.assertIsNone(result.diagnostics["depth_mean_m"])
        self.assertIsNone(result.diagnostics["speed_m_s"])

    def test_clahe_does_not_promote_confidence_or_still_ratio(self):
        pipeline = self.pipeline()
        with patch("vision.pipeline.assess", return_value=Quality(.4, 20., 10., 100.)):
            pipeline.process(self.frame, self.ts)
            result = pipeline.process(self.frame, self.ts + timedelta(seconds=1))
        self.assertTrue(result.diagnostics["enhanced"])
        self.assertNotIn("still_ratio", self.values(result))
        self.assertTrue(all(r.confidence == .4 for r in result.records))

    def test_low_quality_gap_resets_motion_history(self):
        pipeline = self.pipeline()
        pipeline.process(self.frame, self.ts)
        pipeline.process(np.zeros_like(self.frame), self.ts + timedelta(seconds=1))
        result = pipeline.process(self.frame, self.ts + timedelta(seconds=2))
        self.assertEqual(self.backend.resets, 1)
        self.assertNotIn("still_ratio", self.values(result))
        self.assertIsNone(result.diagnostics["speed_px_s"])

    def test_still_ratio_requires_two_high_quality_observations(self):
        pipeline = self.pipeline()
        with patch("vision.pipeline.assess", side_effect=[Quality(.4, 0., 0., 0.),
                                                         Quality(.8, 0., 0., 0.),
                                                         Quality(.8, 0., 0., 0.)]):
            pipeline.process(self.frame, self.ts)
            first_clear = pipeline.process(self.frame, self.ts + timedelta(seconds=1))
            second_clear = pipeline.process(self.frame, self.ts + timedelta(seconds=2))
        self.assertNotIn("still_ratio", self.values(first_clear))
        self.assertEqual(self.values(second_clear)["still_ratio"], 1.)

    def test_missing_track_gap_does_not_make_stationary_fish(self):
        pipeline = self.pipeline()
        pipeline.process(self.frame, self.ts)
        self.backend.output = Detections()
        pipeline.process(self.frame, self.ts + timedelta(seconds=1))
        self.backend.output = Detections([[0, 0, 20, 20]], [1])
        result = pipeline.process(self.frame, self.ts + timedelta(seconds=2))
        self.assertIsNone(result.diagnostics["speed_px_s"])

    def test_duplicate_out_of_order_and_naive_timestamps_rejected(self):
        pipeline = self.pipeline()
        pipeline.process(self.frame, self.ts)
        for ts in (self.ts, self.ts - timedelta(seconds=1), self.ts.replace(tzinfo=None)):
            with self.assertRaises(ValueError):
                pipeline.process(self.frame, ts)

    def test_invalid_configuration_fails_before_processing(self):
        for changes in ({"quality_weights": [.1, .1, .1]}, {"activity_reference_px_s": 0},
                        {"depth_top_m": 0.}, {"contrast_reference": float("nan")},
                        {"depth_top_m": 5., "depth_bottom_m": 2., "net_depth_m": 9.}):
            with self.assertRaises(ValueError):
                VisionConfig.model_validate({**self.config.model_dump(), **changes})

    def test_clip_sampling_preserves_elapsed_time_and_jsonl_contract(self):
        from vision.__main__ import run
        from common import MetricRecord

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            video = root / "black.avi"
            writer = cv2.VideoWriter(str(video), cv2.VideoWriter_fourcc(*"MJPG"), 10., (120, 90))
            self.assertTrue(writer.isOpened())
            for _ in range(6):
                writer.write(np.zeros_like(self.frame))
            writer.release()
            weights = root / "unused.pth"
            weights.write_bytes(b"never loaded on low-quality video")
            args = SimpleNamespace(config=Path(__file__).resolve().parents[1] / "config/vision.yaml",
                video=video, weights=weights, out=root / "out", cage_id="3", start=self.ts,
                stride=2, max_frames=None, device="cpu")
            summary = run(args)
            rows = [MetricRecord.model_validate_json(line) for line in
                    (args.out / "records.jsonl").read_text().splitlines()]
            self.assertEqual(summary["frames"], 3)
            self.assertEqual(summary["states"], {"low_quality": 3})
            self.assertEqual([r.ts for r in rows], [self.ts + timedelta(seconds=x) for x in (0, .2, .4)])
            self.assertEqual(json.loads((args.out / "summary.json").read_text())["status"], "complete")

    @unittest.skipUnless(importlib.util.find_spec("trackers"), "optional trackers dependency")
    def test_real_bytetrack_confirms_id_and_resets(self):
        import supervision as sv
        from vision.backends import FishTracker

        tracker = FishTracker(self.config, 10.)
        def detection():
            return sv.Detections(xyxy=np.array([[0., 0., 20., 20.]]),
                                 confidence=np.array([.9]), class_id=np.array([0]))
        outputs = [tracker.update(detection(), i / 10) for i in range(4)]
        self.assertTrue(np.all(outputs[-1].tracker_id >= 0))
        self.assertEqual(outputs[-1].tracker_id.tolist(), outputs[-2].tracker_id.tolist())
        tracker.reset()
        self.assertEqual(tracker.update(detection(), 1.).tracker_id.tolist(), [-1])


if __name__ == "__main__":
    unittest.main()
