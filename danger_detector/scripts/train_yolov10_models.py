#!/usr/bin/env python3
"""Train YOLOv10s as the main model and YOLOv10m as a comparison model."""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Iterable, List, Optional


def count_label_lines(label_root: Path) -> int:
    total = 0
    for label_file in label_root.rglob("*.txt"):
        with open(label_file, "r", encoding="utf-8") as handle:
            total += sum(1 for line in handle if line.strip())
    return total


def run_command(command: List[str], dry_run: bool) -> None:
    print(" ".join(command), flush=True)
    if not dry_run:
        subprocess.run(command, check=True)


def parse_args(argv: Optional[Iterable[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", default="outputs/yolo_dataset/dataset.yaml")
    parser.add_argument("--project", default="outputs/yolo_runs")
    parser.add_argument("--imgsz", type=int, default=640)
    parser.add_argument("--epochs", type=int, default=100)
    parser.add_argument("--device", default="0")
    parser.add_argument("--v10s-batch", type=int, default=16)
    parser.add_argument("--v10m-batch", type=int, default=8)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--yolo-bin", default="")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--skip-v10m", action="store_true")
    return parser.parse_args(argv)


def main() -> int:
    args = parse_args()
    data_file = Path(args.data)
    dataset_root = data_file.parent
    label_count = count_label_lines(dataset_root / "labels")
    if not data_file.exists():
        raise FileNotFoundError(f"dataset yaml not found: {data_file}")
    if label_count <= 0:
        raise RuntimeError(f"no positive YOLO labels found under {dataset_root / 'labels'}")

    default_yolo = Path(sys.executable).resolve().parent / "yolo"
    yolo_bin = args.yolo_bin or (str(default_yolo) if default_yolo.exists() else shutil.which("yolo") or "yolo")
    common = [
        yolo_bin,
        "detect",
        "train",
        f"data={data_file}",
        f"imgsz={args.imgsz}",
        f"epochs={args.epochs}",
        f"device={args.device}",
        f"project={args.project}",
        f"workers={args.workers}",
    ]
    run_command(common + ["model=yolov10s.pt", f"batch={args.v10s_batch}", "name=yolov10s_main"], args.dry_run)
    if not args.skip_v10m:
        run_command(common + ["model=yolov10m.pt", f"batch={args.v10m_batch}", "name=yolov10m_compare"], args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
