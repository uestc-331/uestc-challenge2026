#!/usr/bin/env python3
"""Draw red-sphere candidate boxes on an offline image dataset."""

from __future__ import annotations

import argparse
import csv
import os
from pathlib import Path
from typing import Iterable

import cv2

from danger_detector.vision import VisionConfig, find_image_candidates, red_mask


def iter_images(input_dir: Path) -> Iterable[Path]:
    for suffix in ("*.jpg", "*.jpeg", "*.png"):
        yield from sorted(input_dir.rglob(suffix))


def draw_candidates(image_bgr, candidates):
    out = image_bgr.copy()
    for idx, candidate in enumerate(candidates):
        x0, y0, x1, y1 = candidate.bbox
        cv2.rectangle(out, (x0, y0), (x1, y1), (0, 255, 255), 2)
        label = f"red_ball {candidate.score:.2f}"
        cv2.putText(out, label, (x0, max(14, y0 - 5)), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 255, 255), 1)
        cx, cy = candidate.centroid_px
        cv2.circle(out, (int(round(cx)), int(round(cy))), 3, (255, 255, 255), -1)
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--min-area", type=int, default=40)
    parser.add_argument("--max-images", type=int, default=0, help="0 means all images")
    parser.add_argument("--summary-file", type=Path, default=None)
    parser.add_argument("--label-dir", type=Path, default=None, help="optional YOLO txt label output directory")
    args = parser.parse_args()

    config = VisionConfig(min_component_area=args.min_area)
    image_paths = list(iter_images(args.input_dir))
    if args.max_images > 0:
        image_paths = image_paths[: args.max_images]

    args.output_dir.mkdir(parents=True, exist_ok=True)
    summary_file = args.summary_file or args.output_dir / "red_ball_boxes.csv"
    summary_file.parent.mkdir(parents=True, exist_ok=True)

    rows = []
    total_boxes = 0
    for image_path in image_paths:
        image_bgr = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
        if image_bgr is None:
            continue

        image_rgb = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB)
        candidates = find_image_candidates(red_mask(image_rgb, config), config)
        total_boxes += len(candidates)

        rel_path = image_path.relative_to(args.input_dir)
        output_path = args.output_dir / rel_path
        output_path.parent.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(output_path), draw_candidates(image_bgr, candidates))

        if args.label_dir is not None:
            label_path = args.label_dir / rel_path.with_suffix(".txt")
            label_path.parent.mkdir(parents=True, exist_ok=True)
            height, width = image_bgr.shape[:2]
            with open(label_path, "w", encoding="utf-8") as handle:
                for candidate in candidates:
                    x0, y0, x1, y1 = candidate.bbox
                    x_center = ((x0 + x1) / 2.0) / width
                    y_center = ((y0 + y1) / 2.0) / height
                    box_width = (x1 - x0 + 1) / width
                    box_height = (y1 - y0 + 1) / height
                    handle.write(f"0 {x_center:.6f} {y_center:.6f} {box_width:.6f} {box_height:.6f}\n")

        for candidate in candidates:
            x0, y0, x1, y1 = candidate.bbox
            rows.append(
                {
                    "image": rel_path.as_posix(),
                    "x0": x0,
                    "y0": y0,
                    "x1": x1,
                    "y1": y1,
                    "score": f"{candidate.score:.6f}",
                    "area": candidate.area,
                    "circularity": f"{candidate.circularity:.6f}",
                    "extent": f"{candidate.extent:.6f}",
                    "aspect_ratio": f"{candidate.aspect_ratio:.6f}",
                }
            )

    with open(summary_file, "w", newline="", encoding="utf-8") as handle:
        fieldnames = ["image", "x0", "y0", "x1", "y1", "score", "area", "circularity", "extent", "aspect_ratio"]
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(f"processed_images={len(image_paths)}")
    print(f"total_boxes={total_boxes}")
    print(f"output_dir={args.output_dir}")
    print(f"summary_file={summary_file}")
    if args.label_dir is not None:
        print(f"label_dir={args.label_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
