"""VC-07: annotation integrity, distinct images, one-to-one matching and quality gates."""

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from vision.evaluation import box_iou, checked_boxes, compare_boxes, match_iou, prepare_dataset, summarize

HAS_CV = importlib.util.find_spec("cv2") is not None
if HAS_CV:
    import cv2
    from vision.config import load_config
    from vision.evaluate import EvalConfig, run_evaluation


class BoxMatchingTests(unittest.TestCase):
    def test_iou_overlap_and_touching_edges(self):
        matrix = box_iou([[0, 0, 10, 10]], [[5, 0, 15, 10], [10, 0, 20, 10]])
        np.testing.assert_allclose(matrix, [[1 / 3, 0.]])

    def test_maximum_cardinality_avoids_greedy_missed_fish(self):
        matches = match_iou(np.array([[.9, .8], [.8, .1]]), .5)
        self.assertEqual({(x["truth_index"], x["prediction_index"]) for x in matches}, {(0, 1), (1, 0)})

    def test_duplicate_detections_cannot_reuse_one_truth_box(self):
        result = compare_boxes([[0, 0, 10, 10]], [[0, 0, 10, 10], [0, 0, 10, 10]], .5)
        self.assertEqual((result["true_positive"], result["false_positive"], result["false_negative"]), (1, 1, 0))

    def test_empty_detections_are_misses_and_empty_truth_has_false_positives(self):
        missing = compare_boxes([[0, 0, 10, 10]], [], .5)
        extra = compare_boxes([], [[0, 0, 10, 10]], .5)
        self.assertEqual(missing["false_negative"], 1)
        self.assertEqual(extra["false_positive"], 1)
        self.assertEqual(compare_boxes([], [], .5)["true_positive"], 0)

    def test_bad_boxes_and_iou_thresholds_rejected(self):
        for boxes in ([[1, 2, 0, 3]], [[-1, 0, 10, 10]], [[0, 0, float("nan"), 2]], [[0, 0, 2]]):
            with self.assertRaises(ValueError):
                checked_boxes(boxes)
        with self.assertRaises(ValueError):
            checked_boxes([[0, 0, 11, 10]], 10, 10)
        for threshold in (0, 2, float("nan")):
            with self.assertRaises(ValueError):
                match_iou(np.ones((1, 1)), threshold)

    def test_totals_use_object_counts_and_undefined_precision_is_null(self):
        rows = [{"mode": "raw", "confidence_threshold": .3, "true_positive": 1,
                 "false_positive": 0, "false_negative": 3, "quality_skipped": False},
                {"mode": "raw", "confidence_threshold": .3, "true_positive": 1,
                 "false_positive": 1, "false_negative": 0, "quality_skipped": False}]
        total = summarize(rows)[0]
        self.assertAlmostEqual(total["precision"], 2 / 3)
        self.assertAlmostEqual(total["recall"], 2 / 5)
        total = summarize([{**rows[0], "true_positive": 0, "quality_skipped": True}])[0]
        self.assertIsNone(total["precision"])
        self.assertEqual(total["quality_skipped_images"], 1)


@unittest.skipUnless(HAS_CV, "optional requirements-vision.txt")
class EvaluationDatasetTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.images = self.root / "images"
        self.images.mkdir()
        self.labels = self.root / "labels.json"
        self.config = load_config()
        self.data = {"categories": [{"id": "1", "name": "조피볼락"}],
                     "images": [{"id": "a", "file_name": "fish.png", "width": 20, "height": 20}],
                     "annotations": [{"id": 7, "image_id": "a", "category_id": 1,
                                      "bbox": [2, 2, 6, 6], "iscrowd": 1}]}
        cv2.imwrite(str(self.images / "fish.png"), np.zeros((20, 20, 3), dtype=np.uint8))

    def prepare(self, count=1):
        self.labels.write_text(json.dumps(self.data, ensure_ascii=False))
        return prepare_dataset(self.labels, self.images, ["조피볼락"], count,
                               "https://www.aihub.or.kr/aihubdata/data/view.do?dataSetSn=155", self.config)

    def test_coco_xywh_and_string_numeric_ids_preserve_publisher_crowd_metadata(self):
        result = self.prepare()
        self.assertEqual(result["images"][0]["boxes_xyxy"], [[2., 2., 8., 8.]])
        self.assertEqual(result["images"][0]["publisher_iscrowd"], [1])
        self.assertEqual(len(result["images"][0]["image_sha256"]), 64)

    def test_same_pixels_do_not_count_as_two_test_images(self):
        (self.images / "copy.png").write_bytes((self.images / "fish.png").read_bytes())
        self.data["images"].append({**self.data["images"][0], "id": "b", "file_name": "copy.png"})
        self.data["annotations"].append({**self.data["annotations"][0], "id": 8, "image_id": "b"})
        with self.assertRaisesRegex(ValueError, "found 1"):
            self.prepare(2)
        self.assertEqual(self.prepare()["excluded"]["duplicate_pixels"], 1)

    def test_mixed_species_image_is_not_a_species_specific_trial(self):
        self.data["categories"].append({"id": 2, "name": "참돔"})
        self.data["annotations"].append({**self.data["annotations"][0], "id": 8, "category_id": 2})
        with self.assertRaisesRegex(ValueError, "found 0"):
            self.prepare()

    def test_missing_or_wrong_species_is_not_silently_relabelled(self):
        self.data["categories"][0]["name"] = "rockfish-other-species"
        with self.assertRaisesRegex(ValueError, "found 0"):
            self.prepare()

    def test_duplicate_truth_boxes_require_review_instead_of_double_counting(self):
        self.data["annotations"].append({**self.data["annotations"][0], "id": 8})
        with self.assertRaisesRegex(ValueError, "duplicate ground-truth"):
            self.prepare()

    def test_unknown_ids_and_wrong_dimensions_rejected(self):
        self.data["annotations"][0]["image_id"] = "unknown"
        with self.assertRaisesRegex(ValueError, "unknown annotation"):
            self.prepare()
        self.data["annotations"][0]["image_id"] = "a"
        self.data["images"][0]["width"] = 21
        with self.assertRaisesRegex(ValueError, "dimensions mismatch"):
            self.prepare()

    def test_path_traversal_and_ambiguous_filename_rejected(self):
        self.data["images"][0]["file_name"] = "../fish.png"
        with self.assertRaisesRegex(ValueError, "relative"):
            self.prepare()
        self.data["images"][0]["file_name"] = "missing/fish.png"
        for name in ("one", "two"):
            directory = self.images / name
            directory.mkdir()
            (directory / "fish.png").write_bytes((self.images / "fish.png").read_bytes())
        with self.assertRaisesRegex(ValueError, "ambiguous"):
            self.prepare()

    def test_invalid_eval_conditions_rejected(self):
        for thresholds in ([], [.3, .3], [0], [float("nan")]):
            with self.assertRaises(ValueError):
                EvalConfig(confidence_thresholds=thresholds, iou_threshold=.5, sample_count=20)

    def test_evaluator_reports_threshold_recovery_and_production_quality_skip(self):
        dataset = self.prepare()
        manifest = self.root / "manifest.json"
        manifest.write_text(json.dumps(dataset))
        weights = self.root / "weights.pth"
        weights.write_bytes(b"mock weights, not a model accuracy trial")
        detector = SimpleNamespace(detect=lambda image, threshold: SimpleNamespace(
            xyxy=np.array([[2, 2, 8, 8], [10, 10, 19, 19]]), confidence=np.array([.2, .15])))
        report = run_evaluation(manifest, self.images, weights, self.root / "out", self.config,
                                EvalConfig(confidence_thresholds=[.3, .1], iou_threshold=.5, sample_count=1),
                                detector_factory=lambda: detector)
        totals = {(x["mode"], x["confidence_threshold"]): x for x in report["totals"]}
        self.assertEqual(totals[("raw", .3)]["false_negative"], 1)
        self.assertEqual(totals[("raw", .1)]["true_positive"], 1)
        self.assertEqual(totals[("raw", .1)]["false_positive"], 1)
        self.assertEqual(totals[("pipeline", .1)]["quality_skipped_images"], 1)
        self.assertEqual(totals[("pipeline", .1)]["false_negative"], 1)
        self.assertEqual(len(list((self.root / "out").glob("*.jpg"))), 4)
        self.assertTrue((self.root / "out/counts.csv").is_file())

    def test_changed_image_is_rejected_before_model_loading(self):
        dataset = self.prepare()
        manifest = self.root / "manifest.json"
        manifest.write_text(json.dumps(dataset))
        (self.images / "fish.png").write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "hash changed"):
            run_evaluation(manifest, self.images, self.root / "missing.pth", self.root / "out",
                           self.config, EvalConfig(confidence_thresholds=[.3], iou_threshold=.5, sample_count=1),
                           detector_factory=lambda: self.fail("model must not load"))


if __name__ == "__main__":
    unittest.main()
