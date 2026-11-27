"""VC-07 dataset preparation and one-to-one box matching. No model imports."""

from __future__ import annotations

import hashlib
import json
import math
import zipfile
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
from scipy.optimize import linear_sum_assignment


def file_hash(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def checked_boxes(boxes, width=None, height=None) -> np.ndarray:
    values = np.asarray(boxes, dtype=float)
    if values.size == 0:
        return np.empty((0, 4), dtype=float)
    if values.ndim != 2 or values.shape[1] != 4 or not np.isfinite(values).all():
        raise ValueError("boxes must contain finite [x1,y1,x2,y2] rows")
    if (values[:, :2] < 0).any() or (values[:, 2:] <= values[:, :2]).any():
        raise ValueError("boxes must have nonnegative origins and positive area")
    if width is not None and ((values[:, 2] > width).any() or (values[:, 3] > height).any()):
        raise ValueError("box extends beyond the image")
    return values


def box_iou(truth, predictions) -> np.ndarray:
    truth, predictions = checked_boxes(truth), checked_boxes(predictions)
    left = np.maximum(truth[:, None, :2], predictions[None, :, :2])
    right = np.minimum(truth[:, None, 2:], predictions[None, :, 2:])
    intersection = np.maximum(right - left, 0).prod(axis=2)
    areas_a = (truth[:, 2:] - truth[:, :2]).prod(axis=1)
    areas_b = (predictions[:, 2:] - predictions[:, :2]).prod(axis=1)
    return intersection / (areas_a[:, None] + areas_b[None, :] - intersection)


def match_iou(ious: np.ndarray, threshold: float) -> list[dict]:
    """Maximum number of valid matches first, total IoU second; never reuse a box."""
    if not 0 < threshold <= 1 or not math.isfinite(threshold):
        raise ValueError("IoU threshold must be in (0,1]")
    ious = np.asarray(ious, dtype=float)
    if ious.ndim != 2 or not np.isfinite(ious).all() or ((ious < 0) | (ious > 1)).any():
        raise ValueError("invalid IoU matrix")
    if not ious.size:
        return []
    valid = ious >= threshold
    # This bonus dominates every possible difference in summed IoU.
    bonus = min(ious.shape) + 1
    rows, columns = linear_sum_assignment(-np.where(valid, bonus + ious, 0))
    return [{"truth_index": int(i), "prediction_index": int(j), "iou": float(ious[i, j])}
            for i, j in zip(rows, columns) if valid[i, j]]


def compare_boxes(truth, predictions, threshold: float) -> dict:
    truth, predictions = checked_boxes(truth), checked_boxes(predictions)
    matches = match_iou(box_iou(truth, predictions), threshold)
    matched_truth = {m["truth_index"] for m in matches}
    matched_predictions = {m["prediction_index"] for m in matches}
    missed = sorted(set(range(len(truth))) - matched_truth)
    extra = sorted(set(range(len(predictions))) - matched_predictions)
    return {"true_positive": len(matches), "false_negative": len(missed),
            "false_positive": len(extra), "matches": matches,
            "missed_truth_indices": missed, "extra_prediction_indices": extra}


def summarize(rows: list[dict]) -> list[dict]:
    groups = defaultdict(list)
    for row in rows:
        groups[(row["mode"], row["confidence_threshold"])].append(row)
    totals = []
    for (mode, threshold), group in sorted(groups.items()):
        counts = {key: sum(row[key] for row in group)
                  for key in ("true_positive", "false_positive", "false_negative")}
        tp, fp, fn = (counts[key] for key in ("true_positive", "false_positive", "false_negative"))
        totals.append({"mode": mode, "confidence_threshold": threshold,
                       "images": len(group), **counts,
                       "quality_skipped_images": sum(row["quality_skipped"] for row in group),
                       "precision": tp / (tp + fp) if tp + fp else None,
                       "recall": tp / (tp + fn) if tp + fn else None})
    return totals


def resolve_image(root: Path, filename: str, index: dict[str, list[Path]]) -> Path:
    normalized = filename.replace("\\", "/")
    relative = Path(normalized)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError(f"image filename must be relative: {filename}")
    direct = root / relative
    if direct.is_file():
        return direct
    found = index.get(relative.name, [])
    if len(found) != 1:
        raise ValueError(f"image missing or ambiguous: {filename} ({len(found)} matches)")
    return found[0]


def extract_candidates(archive: Path, labels: Path, categories: list[str], count: int, out: Path) -> dict:
    """Extract a balanced light-condition pool without unpacking the whole dataset."""
    if not 1 <= count <= 1000:
        raise ValueError("candidate count must be between 1 and 1000")
    data = json.loads(labels.read_text(encoding="utf-8-sig"))
    target_ids = {str(c["id"]) for c in data["categories"] if c["name"] in categories}
    if not target_ids:
        raise ValueError("requested categories not found in source labels")
    annotations = defaultdict(list)
    for annotation in data["annotations"]:
        annotations[str(annotation["image_id"])].append(annotation)
    groups = defaultdict(list)
    for image in data["images"]:
        observed = annotations[str(image["id"])]
        if observed and all(str(a["category_id"]) in target_ids for a in observed):
            groups[str(image.get("light", "unknown"))].append(image)
    for group in groups.values():
        group.sort(key=lambda image: (str(image.get("date_captured", "")), image["file_name"]))
    available = sum(map(len, groups.values()))
    if available < count:
        raise ValueError(f"need {count} candidates, found {available}")
    quotas = dict.fromkeys(sorted(groups), 0)
    remaining = count
    while remaining:
        for key in quotas:
            if quotas[key] < len(groups[key]) and remaining:
                quotas[key] += 1
                remaining -= 1
    selected = []
    for key, size in quotas.items():
        selected.extend(groups[key][int(i)] for i in np.linspace(0, len(groups[key]) - 1, size, dtype=int))
    if len({str(image["id"]) for image in selected}) != len(selected):
        raise ValueError("duplicate source image IDs")
    out.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as zipped:
        members = defaultdict(list)
        for member in zipped.infolist():
            if not member.is_dir():
                members[Path(member.filename).name].append(member)
        for image in selected:
            relative = Path(image["file_name"].replace("\\", "/"))
            if relative.is_absolute() or ".." in relative.parts:
                raise ValueError("source image filenames must stay within the extraction directory")
            found = members.get(relative.name, [])
            if len(found) != 1:
                raise ValueError(f"archive image missing or ambiguous: {relative}")
            destination = out / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(zipped.read(found[0]))  # also verifies the selected entry's CRC
    ids = {str(image["id"]) for image in selected}
    provenance = {"source_labels": labels.name, "source_labels_sha256": file_hash(labels),
                  "source_archive": archive.name, "source_archive_bytes": archive.stat().st_size,
                  "eligible_images": available, "candidate_images": len(selected),
                  "selection": "balanced light quotas, evenly spaced date/filename ranks within each light",
                  "source_light_counts": {key: len(group) for key, group in sorted(groups.items())},
                  "candidate_light_counts": quotas}
    subset = {"categories": data["categories"], "images": selected,
              "annotations": [a for a in data["annotations"] if str(a["image_id"]) in ids],
              "source_provenance": provenance}
    (out / "labels.json").write_text(json.dumps(subset, ensure_ascii=False, indent=2), encoding="utf-8")
    return provenance


def prepare_dataset(labels: Path, images_root: Path, categories: list[str], count: int,
                    source_url: str, camera_config) -> dict:
    """Read AI Hub's published COCO-style xywh schema; select quality quantiles."""
    import cv2
    from .quality import assess

    if not 1 <= count <= 20:
        raise ValueError("sample count must be between 1 and 20")
    if not categories or not source_url.startswith(("https://", "http://")):
        raise ValueError("explicit category names and source URL are required")
    paths = [labels] if labels.is_file() else sorted(labels.rglob("*.json"))
    if not paths:
        raise ValueError("no annotation JSON files found")
    image_index = defaultdict(list)
    for path in sorted(images_root.rglob("*")):
        if path.is_file() and path.suffix.lower() in {".jpg", ".jpeg", ".png", ".bmp"}:
            image_index[path.name].append(path)
    candidates, seen_pixels, seen_images = [], set(), set()
    source_provenance = []
    skipped = Counter()
    found_names = set()
    for label_path in paths:
        data = json.loads(label_path.read_text(encoding="utf-8-sig"))
        if not isinstance(data, dict) or not all(key in data for key in ("images", "annotations", "categories")):
            raise ValueError(f"expected COCO-style AI Hub annotation: {label_path.name}")
        if data.get("source_provenance") and data["source_provenance"] not in source_provenance:
            source_provenance.append(data["source_provenance"])
        names = {str(item["id"]): item["name"] for item in data["categories"]}
        found_names.update(names.values())
        target_ids = {key for key, name in names.items() if name in categories}
        if not target_ids:
            skipped["other_category_documents"] += 1
            continue
        annotation_hash = file_hash(label_path)
        by_image = defaultdict(list)
        images = {str(item["id"]): item for item in data["images"]}
        if len(images) != len(data["images"]):
            raise ValueError(f"duplicate image IDs: {label_path.name}")
        for annotation in data["annotations"]:
            image_id, category_id = str(annotation["image_id"]), str(annotation["category_id"])
            if image_id not in images or category_id not in names:
                raise ValueError(f"unknown annotation image/category ID: {label_path.name}")
            by_image[image_id].append(annotation)
        for image_id, meta in images.items():
            annotations = by_image[image_id]
            if not any(str(a["category_id"]) in target_ids for a in annotations):
                continue
            if any(str(a["category_id"]) not in target_ids for a in annotations):
                skipped["mixed_category_images"] += 1
                continue
            path = resolve_image(images_root, meta["file_name"], image_index)
            if path in seen_images:
                raise ValueError(f"image has multiple annotation records: {meta['file_name']}")
            seen_images.add(path)
            frame = cv2.imread(str(path))
            if frame is None or frame.shape[:2] != (meta["height"], meta["width"]):
                raise ValueError(f"unreadable image or label dimensions mismatch: {path.name}")
            xywh = np.asarray([a["bbox"] for a in annotations], dtype=float)
            if xywh.ndim != 2 or xywh.shape[1] != 4:
                raise ValueError(f"expected xywh bounding boxes: {label_path.name}")
            boxes = xywh.copy()
            boxes[:, 2:] += boxes[:, :2]
            checked_boxes(boxes, meta["width"], meta["height"])
            if len(np.unique(boxes, axis=0)) != len(boxes):
                raise ValueError(f"duplicate ground-truth boxes require review: {label_path.name}")
            pixel_hash = hashlib.sha256(str(frame.shape).encode() + frame.tobytes()).hexdigest()
            if pixel_hash in seen_pixels:
                skipped["duplicate_pixels"] += 1
                continue
            seen_pixels.add(pixel_hash)
            candidates.append({"image": path.relative_to(images_root).as_posix(),
                "image_sha256": file_hash(path), "pixel_sha256": pixel_hash,
                "annotation_file": (label_path.relative_to(labels).as_posix()
                                    if labels.is_dir() else label_path.name),
                "annotation_sha256": annotation_hash, "source_image_id": image_id,
                "light": meta.get("light"), "date_captured": meta.get("date_captured"),
                "width": meta["width"], "height": meta["height"],
                "boxes_xyxy": boxes.tolist(), "annotation_ids": [a["id"] for a in annotations],
                "publisher_iscrowd": [a.get("iscrowd") for a in annotations],
                "quality": assess(frame, camera_config).to_dict()})
    if len(candidates) < count:
        raise ValueError(f"need {count} unique images, found {len(candidates)}; categories={sorted(found_names)}")
    candidates.sort(key=lambda row: (row["quality"]["score"], row["image"]))
    indices = np.linspace(0, len(candidates) - 1, count, dtype=int)
    selected = [candidates[int(i)] for i in indices]
    return {"schema_version": 1, "source_url": source_url, "categories": categories,
            "camera_config": camera_config.model_dump(mode="json"),
            "source_provenance": source_provenance,
            "selection": "evenly spaced ranks of original quality score, no detection-based selection",
            "available_unique_images": len(candidates), "excluded": dict(skipped),
            "bbox_source_format": "xywh", "iscrowd_policy": "each published annotation box is one object; review overlays",
            "images": selected}
