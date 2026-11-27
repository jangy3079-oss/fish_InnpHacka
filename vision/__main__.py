"""Run an offline camera clip: python -m vision --help."""

import argparse
import hashlib
import json
import math
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import cv2
import numpy as np

from .backends import FishDetector, FishTracker
from .config import ROOT, load_config
from .pipeline import CameraPipeline


def positive_int(value):
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return number


def aware_time(value):
    try:
        ts = datetime.fromisoformat(value)
        if ts.utcoffset() is None:
            raise ValueError
        return ts
    except ValueError:
        raise argparse.ArgumentTypeError("use an ISO 8601 timestamp with timezone") from None


def sha256(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def run(args):
    config = load_config(args.config)
    if not args.video.is_file() or not args.weights.is_file():
        raise ValueError("video and model weights must be existing local files")
    capture = cv2.VideoCapture(str(args.video))
    try:
        if not capture.isOpened():
            raise ValueError("cannot open video")
        fps = capture.get(cv2.CAP_PROP_FPS)
        if not math.isfinite(fps) or fps <= 0:
            raise ValueError("video has no usable frame rate")
        processor = CameraPipeline(args.cage_id, config,
            lambda: FishDetector(args.weights, args.device),
            lambda: FishTracker(config, fps / args.stride))
        args.out.mkdir(parents=True, exist_ok=True)
        # An interrupted rerun must not leave a previous successful summary in place.
        (args.out / "summary.json").write_text('{"status": "processing"}', encoding="utf-8")
        stats = {"frames": 0, "records": 0, "enhanced": 0, "states": {},
                 "max_detections": 0, "max_tracked": 0}
        qualities = []
        preview_saved = False
        started = time.perf_counter()
        frame_index = 0
        with (args.out / "records.jsonl").open("w", encoding="utf-8") as metrics, \
             (args.out / "frames.jsonl").open("w", encoding="utf-8") as frames:
            while args.max_frames is None or stats["frames"] < args.max_frames:
                ok, frame = capture.read()
                if not ok:
                    break
                index = frame_index
                frame_index += 1
                if index % args.stride:
                    continue
                # Fixed-rate local clips: preserve elapsed source time when sampling.
                result = processor.process(frame, args.start + timedelta(seconds=index / fps))
                diag = result.diagnostics
                diag["frame_index"] = index
                frames.write(json.dumps(diag, ensure_ascii=False, allow_nan=False) + "\n")
                for record in result.records:
                    metrics.write(record.model_dump_json() + "\n")
                stats["frames"] += 1
                stats["records"] += len(result.records)
                stats["enhanced"] += int(diag["enhanced"])
                state = diag["state"]
                stats["states"][state] = stats["states"].get(state, 0) + 1
                stats["max_detections"] = max(stats["max_detections"], diag["detection_count"] or 0)
                stats["max_tracked"] = max(stats["max_tracked"], diag["tracked_count"] or 0)
                qualities.append(diag["quality"]["score"])
                if not preview_saved and diag["tracked_count"]:
                    preview = frame.copy()
                    for track in diag["tracks"]:
                        x, y = map(round, track["center_px"])
                        cv2.circle(preview, (x, y), 6, (0, 255, 255), 2)
                        cv2.putText(preview, str(track["id"]), (x, y), cv2.FONT_HERSHEY_SIMPLEX,
                                    0.7, (0, 255, 255), 2)
                    if not cv2.imwrite(str(args.out / "preview.jpg"), preview):
                        raise ValueError("could not write preview")
                    preview_saved = True
        if stats["frames"] == 0:
            raise ValueError("video contains no readable frames")
        elapsed = time.perf_counter() - started
        summary = {**stats, "status": "complete", "seconds": round(elapsed, 3),
                   "preview_written": preview_saved,
                   "processed_fps_including_model_load": round(stats["frames"] / elapsed, 3),
                   "quality_min": min(qualities), "quality_mean": float(np.mean(qualities)),
                   "quality_max": max(qualities), "source_fps": fps, "stride": args.stride,
                   "start": args.start.isoformat(), "device": args.device,
                   "video_sha256": sha256(args.video), "weights_sha256": sha256(args.weights),
                   "config": config.model_dump(mode="json")}
        (args.out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2),
                                                encoding="utf-8")
        return summary
    finally:
        capture.release()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("video", type=Path)
    parser.add_argument("--weights", type=Path, required=True,
                        help="CFD RF-DETR checkpoint; downloaded separately")
    parser.add_argument("--cage-id", required=True)
    parser.add_argument("--start", required=True, type=aware_time,
                        help="clip start time (or explicitly chosen replay time), timezone required")
    parser.add_argument("--out", type=Path, default=ROOT / "data/out/vision")
    parser.add_argument("--config", type=Path, default=ROOT / "config/vision.yaml")
    parser.add_argument("--stride", type=positive_int, default=1)
    parser.add_argument("--max-frames", type=positive_int)
    parser.add_argument("--device", choices=("cpu", "mps", "cuda"), default="cpu")
    args = parser.parse_args()
    try:
        print(json.dumps(run(args), ensure_ascii=False))
    except (ValueError, OSError, RuntimeError) as exc:
        print(f"vision: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
