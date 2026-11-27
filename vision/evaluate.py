"""Prepare and run VC-07 on local, annotated rockfish images: --help."""

from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from pathlib import Path
from typing import Annotated, Self

import cv2
import numpy as np
import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from common.config_loader import ConfigLoader

from .backends import FishDetector
from .config import ROOT, load_config
from .evaluation import checked_boxes, compare_boxes, extract_candidates, file_hash, prepare_dataset, summarize
from .quality import assess, enhance


class EvalConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    confidence_thresholds: tuple[Annotated[float, Field(gt=0, le=1, allow_inf_nan=False)], ...]
    iou_threshold: Annotated[float, Field(gt=0, le=1, allow_inf_nan=False)]
    sample_count: Annotated[int, Field(strict=True, ge=1, le=20)]

    @model_validator(mode="after")
    def distinct_thresholds(self) -> Self:
        if not self.confidence_thresholds or len(set(self.confidence_thresholds)) != len(self.confidence_thresholds):
            raise ValueError("confidence thresholds must be nonempty and distinct")
        return self


def preview(frame, truth, predictions, scores, result, path):
    canvas = frame.copy()
    missed = set(result["missed_truth_indices"])
    extra = set(result["extra_prediction_indices"])
    for index, box in enumerate(truth):
        x1, y1, x2, y2 = map(round, box)
        color = (255, 0, 255) if index in missed else (0, 255, 0)
        cv2.rectangle(canvas, (x1, y1), (x2, y2), color, 2)
        cv2.putText(canvas, f"GT {index}", (x1, max(18, y1 - 4)),
                    cv2.FONT_HERSHEY_SIMPLEX, .6, color, 2)
    for index, (box, score) in enumerate(zip(predictions, scores)):
        x1, y1, x2, y2 = map(round, box)
        color = (0, 0, 255) if index in extra else (255, 255, 0)
        cv2.rectangle(canvas, (x1, y1), (x2, y2), color, 2)
        cv2.putText(canvas, f"P {index} {score:.2f}", (x1, min(canvas.shape[0] - 5, y2 + 18)),
                    cv2.FONT_HERSHEY_SIMPLEX, .6, color, 2)
    if not cv2.imwrite(str(path), canvas):
        raise ValueError(f"could not write preview: {path.name}")


def run_evaluation(manifest: Path, images_root: Path, weights: Path, out: Path,
                   camera_config, eval_config: EvalConfig, device="cpu", detector_factory=None):
    config_revision = ConfigLoader().snapshot().revision
    dataset = json.loads(manifest.read_text(encoding="utf-8"))
    if dataset.get("schema_version") != 1 or not dataset.get("images"):
        raise ValueError("expected a nonempty version 1 evaluation manifest")
    if dataset.get("camera_config") != camera_config.model_dump(mode="json"):
        raise ValueError("camera config changed after selection; prepare the manifest again")
    # Validate all inputs before loading the model or producing partial comparisons.
    verified = []
    seen = set()
    root = images_root.resolve()
    for entry in dataset["images"]:
        path = (root / entry["image"]).resolve()
        if not path.is_relative_to(root) or path in seen:
            raise ValueError("manifest contains outside-root or duplicate image paths")
        seen.add(path)
        if file_hash(path) != entry["image_sha256"]:
            raise ValueError(f"image hash changed: {entry['image']}")
        frame = cv2.imread(str(path))
        if frame is None or frame.shape[:2] != (entry["height"], entry["width"]):
            raise ValueError(f"image dimensions changed: {entry['image']}")
        truth = checked_boxes(entry["boxes_xyxy"], entry["width"], entry["height"])
        verified.append((entry, frame, truth))
    out.mkdir(parents=True, exist_ok=True)
    report_path = out / "results.json"
    report_path.write_text('{"status":"processing"}', encoding="utf-8")
    started = time.perf_counter()
    detector = detector_factory() if detector_factory else FishDetector(weights, device)
    rows = []
    for index, (entry, frame, truth) in enumerate(verified):
        quality = assess(frame, camera_config)
        pipeline_frame, enhanced = enhance(frame, quality.score, camera_config)
        for mode, image in (("raw", frame), ("pipeline", pipeline_frame)):
            skipped = mode == "pipeline" and quality.score < camera_config.gate_low
            if skipped:
                boxes, scores = np.empty((0, 4)), np.empty(0)
            else:
                detections = detector.detect(image, min(eval_config.confidence_thresholds))
                boxes, scores = np.asarray(detections.xyxy, dtype=float).copy(), np.asarray(detections.confidence)
                if (boxes.shape != (len(scores), 4) or not np.isfinite(boxes).all()
                        or not np.isfinite(scores).all() or ((scores < 0) | (scores > 1)).any()):
                    raise ValueError("detector returned invalid boxes or confidences")
                boxes[:, [0, 2]] = np.clip(boxes[:, [0, 2]], 0, entry["width"])
                boxes[:, [1, 3]] = np.clip(boxes[:, [1, 3]], 0, entry["height"])
                positive = (boxes[:, 2:] > boxes[:, :2]).all(axis=1)
                boxes, scores = boxes[positive], scores[positive]
            for threshold in eval_config.confidence_thresholds:
                selected = scores >= threshold
                predictions, confidence = boxes[selected], scores[selected]
                comparison = compare_boxes(truth, predictions, eval_config.iou_threshold)
                preview_name = f"{index:02d}-{mode}-conf{threshold:g}.jpg"
                row = {"image": entry["image"], "image_sha256": entry["image_sha256"],
                       "mode": mode, "confidence_threshold": threshold,
                       "quality": quality.score, "quality_skipped": skipped,
                       "enhanced": mode == "pipeline" and enhanced and not skipped,
                       "truth_count": len(truth), "prediction_count": len(predictions),
                       "predictions_xyxy": predictions.tolist(), "confidences": confidence.tolist(),
                       "preview": preview_name, **comparison}
                rows.append(row)
                preview(frame, truth, predictions, confidence, comparison, out / preview_name)
    report = {"status": "complete", "manifest_sha256": file_hash(manifest),
              "config_revision": config_revision,
              "counting_note": "false_positive means unmatched against published labels; non-exhaustive labels require visual review before interpreting real false alarms",
              "weights_sha256": file_hash(weights), "source_url": dataset["source_url"],
              "source_provenance": dataset.get("source_provenance", []),
              "categories": dataset["categories"], "device": device,
              "seconds": round(time.perf_counter() - started, 3),
              "camera_config": camera_config.model_dump(mode="json"),
              "evaluation_config": eval_config.model_dump(mode="json"),
              "totals": summarize(rows), "images": dataset["images"], "results": rows}
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    columns = ("image", "mode", "confidence_threshold", "quality", "quality_skipped", "enhanced",
               "truth_count", "prediction_count", "true_positive", "false_positive", "false_negative", "preview")
    with (out / "counts.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--camera-config", type=Path, default=ROOT / "config/vision.yaml")
    parser.add_argument("--eval-config", type=Path, default=ROOT / "config/vision_eval.yaml")
    subcommands = parser.add_subparsers(dest="command", required=True)
    extract = subcommands.add_parser("extract", help="extract a light-balanced candidate pool from a large source ZIP")
    extract.add_argument("--archive", type=Path, required=True)
    extract.add_argument("--labels", type=Path, required=True)
    extract.add_argument("--category", action="append", required=True)
    extract.add_argument("--candidates", type=int, required=True)
    extract.add_argument("--out", type=Path, required=True)
    prepare = subcommands.add_parser("prepare", help="read AI Hub COCO-style labels and select unique frames")
    prepare.add_argument("--labels", type=Path, required=True)
    prepare.add_argument("--images-root", type=Path, required=True)
    prepare.add_argument("--category", action="append", required=True, help="exact category name in the source JSON")
    prepare.add_argument("--source-url", required=True)
    prepare.add_argument("--out", type=Path, required=True)
    run = subcommands.add_parser("run", help="compare raw detector and production quality/CLAHE path")
    run.add_argument("--manifest", type=Path, required=True)
    run.add_argument("--images-root", type=Path, required=True)
    run.add_argument("--weights", type=Path, required=True)
    run.add_argument("--out", type=Path, required=True)
    run.add_argument("--device", choices=("cpu", "mps", "cuda"), default="cpu")
    args = parser.parse_args()
    try:
        camera_config = load_config(args.camera_config)
        eval_config = EvalConfig.model_validate(yaml.safe_load(args.eval_config.read_text(encoding="utf-8")))
        if args.command == "extract":
            provenance = extract_candidates(args.archive, args.labels, args.category, args.candidates, args.out)
            print(json.dumps(provenance, ensure_ascii=False))
        elif args.command == "prepare":
            dataset = prepare_dataset(args.labels, args.images_root, args.category,
                                      eval_config.sample_count, args.source_url, camera_config)
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(json.dumps(dataset, ensure_ascii=False, indent=2), encoding="utf-8")
            print(json.dumps({"manifest": str(args.out), "images": len(dataset["images"])}))
        else:
            report = run_evaluation(args.manifest, args.images_root, args.weights, args.out,
                                    camera_config, eval_config, args.device)
            print(json.dumps(report["totals"], ensure_ascii=False))
    except (ValueError, KeyError, OSError, RuntimeError) as exc:
        print(f"vision.evaluate: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
