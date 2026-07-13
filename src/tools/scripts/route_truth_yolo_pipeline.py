#!/usr/bin/env python3
"""Collect route-time YOLO labels from SimEnv truth and train YOLO models.

The script is intentionally self-contained:

* collect: run inside the ROS/Gazebo environment while the robot is moving.
* train: run inside the YOLO/conda environment after collection.
* all: collect first, then train in the same environment if both ROS and
  Ultralytics are available.

Truth files are used only for dataset generation, not for competition runtime
detection.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import shutil
import sys
import threading
import time
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np
import cv2


DEFAULT_NAMES = {
    0: "red_sphere",
    1: "red_box",
    2: "green_sphere",
}


def normalize_depth_image(depth: np.ndarray) -> np.ndarray:
    arr = np.asarray(depth)
    if arr.dtype == np.uint16:
        return arr.astype(np.float32) / 1000.0
    return arr.astype(np.float32)


def quaternion_to_matrix(x: float, y: float, z: float, w: float) -> np.ndarray:
    norm = x * x + y * y + z * z + w * w
    if norm < 1e-12:
        return np.eye(3, dtype=np.float64)
    s = 2.0 / norm
    xx, yy, zz = x * x * s, y * y * s, z * z * s
    xy, xz, yz = x * y * s, x * z * s, y * z * s
    wx, wy, wz = w * x * s, w * y * s, w * z * s
    return np.array(
        [
            [1.0 - yy - zz, xy - wz, xz + wy],
            [xy + wz, 1.0 - xx - zz, yz - wx],
            [xz - wy, yz + wx, 1.0 - xx - yy],
        ],
        dtype=np.float64,
    )


def split_for_key(key: str, val_fraction: float) -> str:
    digest = hashlib.sha1(key.encode("utf-8")).hexdigest()
    value = int(digest[:8], 16) / float(0xFFFFFFFF)
    return "val" if value < val_fraction else "train"


def class_id_for_source(source: Dict[str, object], danger_only: bool) -> Optional[int]:
    color = str(source.get("color", "")).lower()
    shape = str(source.get("shape", "")).lower()
    is_danger = bool(source.get("is_danger", False))
    if is_danger and color == "red" and shape == "sphere":
        return 0
    if danger_only:
        return None
    if color == "red" and shape == "box":
        return 1
    if color == "green" and shape == "sphere":
        return 2
    return None


def load_sources(truth_file: Path, danger_only: bool) -> List[Dict[str, object]]:
    with truth_file.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    sources: List[Dict[str, object]] = []
    for key in ("danger_sources", "distraction_sources"):
        for source in payload.get(key, []):
            if not isinstance(source, dict):
                continue
            class_id = class_id_for_source(source, danger_only)
            position = source.get("position")
            if class_id is None or not isinstance(position, list) or len(position) != 3:
                continue
            item = dict(source)
            item["_class_id"] = class_id
            sources.append(item)
    if not sources:
        raise RuntimeError(f"no usable truth objects found in {truth_file}")
    return sources


def dataset_names(danger_only: bool) -> Dict[int, str]:
    if danger_only:
        return {0: DEFAULT_NAMES[0]}
    return dict(DEFAULT_NAMES)


def write_dataset_yaml(output_dir: Path, danger_only: bool) -> None:
    lines = [
        f"path: {output_dir.resolve()}",
        "train: images/train",
        "val: images/val",
        "names:",
    ]
    for class_id, name in dataset_names(danger_only).items():
        lines.append(f"  {class_id}: {name}")
    lines.append("")
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "dataset.yaml").write_text("\n".join(lines), encoding="utf-8")


def clamp_box(x0: float, y0: float, x1: float, y1: float, width: int, height: int) -> Optional[Tuple[int, int, int, int]]:
    ix0 = max(0, int(math.floor(x0)))
    iy0 = max(0, int(math.floor(y0)))
    ix1 = min(width - 1, int(math.ceil(x1)))
    iy1 = min(height - 1, int(math.ceil(y1)))
    if ix1 <= ix0 or iy1 <= iy0:
        return None
    return ix0, iy0, ix1, iy1


def yolo_line(class_id: int, box: Tuple[int, int, int, int], width: int, height: int) -> str:
    x0, y0, x1, y1 = box
    x_center = ((x0 + x1) / 2.0) / width
    y_center = ((y0 + y1) / 2.0) / height
    box_width = (x1 - x0 + 1) / width
    box_height = (y1 - y0 + 1) / height
    return f"{class_id} {x_center:.6f} {y_center:.6f} {box_width:.6f} {box_height:.6f}"


def draw_preview(image_bgr: np.ndarray, labels: List[str], names: Dict[int, str]) -> np.ndarray:
    """Draw normalized YOLO labels on a live collection preview."""
    image = image_bgr.copy()
    height, width = image.shape[:2]
    colors = [(0, 0, 255), (0, 165, 255), (0, 180, 0)]
    for line in labels:
        fields = line.split()
        if len(fields) < 5:
            continue
        class_id = int(fields[0])
        x_center, y_center, box_width, box_height = map(float, fields[1:5])
        x0 = max(0, int((x_center - box_width / 2) * width))
        y0 = max(0, int((y_center - box_height / 2) * height))
        x1 = min(width - 1, int((x_center + box_width / 2) * width))
        y1 = min(height - 1, int((y_center + box_height / 2) * height))
        color = colors[class_id % len(colors)]
        text = names.get(class_id, f"class_{class_id}")
        cv2.rectangle(image, (x0, y0), (x1, y1), color, 2)
        cv2.putText(image, text, (x0, max(20, y0 - 6)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2, cv2.LINE_AA)
    return image


def source_radius(source: Dict[str, object], default_radius: float) -> float:
    if "radius" in source:
        return float(source["radius"])
    size = source.get("size")
    if isinstance(size, list) and size:
        return 0.5 * max(float(v) for v in size)
    return default_radius


def passes_depth_visibility(
    depth: np.ndarray,
    box: Tuple[int, int, int, int],
    expected_z: float,
    radius: float,
    args: argparse.Namespace,
) -> bool:
    x0, y0, x1, y1 = box
    cx0 = int(x0 + 0.25 * (x1 - x0))
    cx1 = int(x0 + 0.75 * (x1 - x0))
    cy0 = int(y0 + 0.25 * (y1 - y0))
    cy1 = int(y0 + 0.75 * (y1 - y0))
    patch = depth[max(0, cy0) : max(cy0 + 1, cy1 + 1), max(0, cx0) : max(cx0 + 1, cx1 + 1)]
    valid = patch[np.isfinite(patch) & (patch >= args.min_depth_m) & (patch <= args.max_depth_m)]
    if valid.size < args.min_depth_pixels:
        return False
    median_depth = float(np.median(valid))
    surface_z = max(args.min_depth_m, expected_z - radius)
    return min(abs(median_depth - expected_z), abs(median_depth - surface_z)) <= args.depth_tolerance_m


class RouteTruthCollector:
    def __init__(self, args: argparse.Namespace) -> None:
        import rospy
        from cv_bridge import CvBridge
        from gazebo_msgs.msg import ModelStates
        from message_filters import ApproximateTimeSynchronizer, Subscriber
        from sensor_msgs.msg import CameraInfo, Image

        self.rospy = rospy
        self.args = args
        self.output_dir = Path(args.output_dir)
        self.sources = load_sources(Path(args.truth_file), args.danger_only)
        self.bridge = CvBridge()
        self.latest_model_states: Optional[ModelStates] = None
        self.frame_index = 0
        self.saved_images = 0
        self.saved_labels = 0
        self.saved_empty = 0
        self.skipped_empty = 0
        self.skipped_unlabeled = 0
        self.last_save_wall_time = 0.0
        self.started_wall_time = time.time()
        self.preview_lock = threading.Lock()
        self.preview_image: Optional[np.ndarray] = None
        self.preview_labels: List[str] = []

        write_dataset_yaml(self.output_dir, args.danger_only)
        self.preview_names = dataset_names(args.danger_only)

        subscribers = [
            Subscriber(args.rgb_topic, Image),
            Subscriber(args.depth_topic, Image),
            Subscriber(args.camera_info_topic, CameraInfo),
        ]
        self.sync = ApproximateTimeSynchronizer(subscribers, queue_size=8, slop=args.sync_slop)
        self.sync.registerCallback(self._callback)
        rospy.Subscriber(args.model_states_topic, ModelStates, self._model_states_cb, queue_size=2)

    def _model_states_cb(self, msg) -> None:
        self.latest_model_states = msg

    def _world_to_camera_transform(self):
        if self.latest_model_states is None:
            return None
        try:
            index = self.latest_model_states.name.index(self.args.robot_model_name)
        except ValueError:
            return None
        pose = self.latest_model_states.pose[index]
        q = pose.orientation
        rotation_world_base = quaternion_to_matrix(q.x, q.y, q.z, q.w)
        translation_world_base = np.array([pose.position.x, pose.position.y, pose.position.z], dtype=np.float64)
        camera_in_base = np.array([self.args.camera_x, self.args.camera_y, self.args.camera_z], dtype=np.float64)

        def convert(world_point: np.ndarray) -> np.ndarray:
            base_point = rotation_world_base.T @ (world_point - translation_world_base)
            relative = base_point - camera_in_base
            # Optical frame: X right, Y down, Z forward. Robot base: X forward, Y left, Z up.
            return np.array([-relative[1], -relative[2], relative[0]], dtype=np.float64)

        return convert

    def _project_labels(self, camera_info, depth: np.ndarray, width: int, height: int) -> List[str]:
        world_to_camera = self._world_to_camera_transform()
        if world_to_camera is None:
            return []
        fx, fy, cx, cy = camera_info.K[0], camera_info.K[4], camera_info.K[2], camera_info.K[5]
        labels: List[str] = []
        for source in self.sources:
            world_point = np.asarray(source["position"], dtype=np.float64)
            point = world_to_camera(world_point)
            x, y, z = point.tolist()
            if z <= self.args.min_depth_m or z >= self.args.max_depth_m:
                continue
            u = fx * x / z + cx
            v = fy * y / z + cy
            radius = source_radius(source, self.args.default_radius_m)
            half_w = self.args.box_margin * fx * radius / z
            half_h = self.args.box_margin * fy * radius / z
            box = clamp_box(u - half_w, v - half_h, u + half_w, v + half_h, width, height)
            if box is None:
                continue
            if not passes_depth_visibility(depth, box, z, radius, self.args):
                continue
            labels.append(yolo_line(int(source["_class_id"]), box, width, height))
        return labels

    def _should_save_empty(self) -> bool:
        if self.args.empty_ratio < 0:
            return True
        allowed = self.args.min_empty + int(self.args.empty_ratio * max(1, self.saved_images - self.saved_empty))
        return self.saved_empty < allowed

    def _callback(self, rgb_msg, depth_msg, camera_info) -> None:
        now = time.time()
        if now - self.last_save_wall_time < 1.0 / max(0.1, self.args.fps):
            return
        if self.args.duration > 0 and now - self.started_wall_time >= self.args.duration:
            self.rospy.signal_shutdown("collection duration reached")
            return

        try:
            image_rgb = self.bridge.imgmsg_to_cv2(rgb_msg, desired_encoding="rgb8")
            depth = normalize_depth_image(self.bridge.imgmsg_to_cv2(depth_msg, desired_encoding="passthrough"))
        except Exception as exc:
            self.rospy.logwarn_throttle(5.0, "image conversion failed: %s", exc)
            return

        height, width = image_rgb.shape[:2]
        labels = self._project_labels(camera_info, depth, width, height)
        image_bgr = cv2.cvtColor(image_rgb, cv2.COLOR_RGB2BGR)
        if self.args.preview:
            # ROS callbacks can run outside the main thread.  Only publish the
            # latest frame here; OpenCV window operations are done by run_collect.
            with self.preview_lock:
                self.preview_image = image_bgr
                self.preview_labels = list(labels)
        if not labels and not self._should_save_empty():
            self.skipped_empty += 1
            self.frame_index += 1
            return

        split = split_for_key(f"{self.args.run_name}:{self.frame_index}", self.args.val_fraction)
        stem = f"{self.args.run_name}_{self.frame_index:06d}"
        image_path = self.output_dir / "images" / split / f"{stem}.jpg"
        label_path = self.output_dir / "labels" / split / f"{stem}.txt"
        image_path.parent.mkdir(parents=True, exist_ok=True)
        label_path.parent.mkdir(parents=True, exist_ok=True)

        cv2.imwrite(str(image_path), image_bgr, [int(cv2.IMWRITE_JPEG_QUALITY), self.args.jpeg_quality])
        label_path.write_text("\n".join(labels) + ("\n" if labels else ""), encoding="utf-8")

        self.saved_images += 1
        self.saved_labels += len(labels)
        if not labels:
            self.saved_empty += 1
        self.last_save_wall_time = now
        self.frame_index += 1

        if self.saved_images % self.args.log_every == 0:
            self.rospy.loginfo(
                "saved_images=%d saved_labels=%d saved_empty=%d skipped_empty=%d",
                self.saved_images,
                self.saved_labels,
                self.saved_empty,
                self.skipped_empty,
            )
        if self.args.max_images > 0 and self.saved_images >= self.args.max_images:
            self.rospy.signal_shutdown("max images reached")

    def write_summary(self) -> None:
        summary = {
            "output_dir": str(self.output_dir),
            "truth_file": self.args.truth_file,
            "run_name": self.args.run_name,
            "saved_images": self.saved_images,
            "saved_labels": self.saved_labels,
            "saved_empty": self.saved_empty,
            "skipped_empty": self.skipped_empty,
            "class_names": dataset_names(self.args.danger_only),
        }
        (self.output_dir / "collection_summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        if self.args.preview:
            cv2.destroyAllWindows()

    def update_preview(self) -> None:
        """Refresh the OpenCV window from the main thread."""
        with self.preview_lock:
            if self.preview_image is None:
                return
            image = self.preview_image.copy()
            labels = list(self.preview_labels)
        preview = draw_preview(image, labels, self.preview_names)
        status = f"labels={len(labels)} saved={self.saved_images}  q/ESC: stop"
        cv2.putText(preview, status, (10, 25), cv2.FONT_HERSHEY_SIMPLEX,
                    0.65, (255, 255, 255), 2, cv2.LINE_AA)
        cv2.imshow("YOLO collection preview", preview)
        key = cv2.waitKey(max(1, self.args.preview_wait_ms)) & 0xFF
        if key in (ord("q"), 27):
            self.rospy.signal_shutdown("preview window requested stop")


def run_collect(args: argparse.Namespace) -> Path:
    import rospy

    rospy.init_node("route_truth_yolo_collector", anonymous=True)
    collector = RouteTruthCollector(args)
    rospy.loginfo("collecting dataset into %s", args.output_dir)
    if args.preview:
        # Keep GUI event handling in the main thread.  ROS subscriber callbacks
        # only update the latest preview frame.
        while not rospy.is_shutdown():
            collector.update_preview()
            rospy.sleep(0.01)
    else:
        rospy.spin()
    collector.write_summary()
    print(f"dataset: {Path(args.output_dir) / 'dataset.yaml'}")
    print(f"saved_images={collector.saved_images} saved_labels={collector.saved_labels} saved_empty={collector.saved_empty}")
    return Path(args.output_dir) / "dataset.yaml"


def count_label_lines(label_root: Path) -> int:
    total = 0
    for label_file in label_root.rglob("*.txt"):
        with label_file.open("r", encoding="utf-8") as handle:
            total += sum(1 for line in handle if line.strip())
    return total


def run_train(args: argparse.Namespace) -> None:
    from ultralytics import YOLO

    data_file = Path(args.data)
    if not data_file.exists():
        raise FileNotFoundError(f"dataset yaml not found: {data_file}")
    label_count = count_label_lines(data_file.parent / "labels")
    if label_count <= 0:
        raise RuntimeError(f"no positive labels found under {data_file.parent / 'labels'}")

    train_kwargs = {
        "data": str(data_file),
        "epochs": args.epochs,
        "imgsz": args.imgsz,
        "batch": args.batch,
        "device": args.device,
        "workers": args.workers,
        "project": args.project,
        "name": args.name,
        "exist_ok": args.exist_ok,
        "pretrained": True,
        "mosaic": args.mosaic,
        "close_mosaic": args.close_mosaic,
        "erasing": args.erasing,
        "hsv_h": args.hsv_h,
        "hsv_s": args.hsv_s,
        "hsv_v": args.hsv_v,
        "multi_scale": args.multi_scale,
        "patience": args.patience,
    }
    print("training", args.model, "labels", label_count, flush=True)
    YOLO(args.model).train(**train_kwargs)

    if args.compare_model:
        compare_kwargs = dict(train_kwargs)
        compare_kwargs["batch"] = args.compare_batch
        compare_kwargs["name"] = args.compare_name
        print("training", args.compare_model, "labels", label_count, flush=True)
        YOLO(args.compare_model).train(**compare_kwargs)


def add_collect_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--truth-file", default="results/danger_truth.json")
    parser.add_argument("--output-dir", default="outputs/yolo_dataset_route")
    parser.add_argument("--run-name", default=time.strftime("route_%Y%m%d_%H%M%S"))
    parser.add_argument("--duration", type=float, default=600.0)
    parser.add_argument("--max-images", type=int, default=0)
    parser.add_argument("--fps", type=float, default=3.0)
    parser.add_argument("--val-fraction", type=float, default=0.2)
    parser.add_argument("--empty-ratio", type=float, default=0.5, help="negative images per positive image; -1 saves all empty frames")
    parser.add_argument("--min-empty", type=int, default=30)
    parser.add_argument("--danger-only", action="store_true", help="only label red danger spheres")
    parser.add_argument("--rgb-topic", default="/real_sense/rgb/image_raw")
    parser.add_argument("--depth-topic", default="/real_sense/depth/image_raw")
    parser.add_argument("--camera-info-topic", default="/real_sense/rgb/camera_info")
    parser.add_argument("--model-states-topic", default="/gazebo/model_states")
    parser.add_argument("--robot-model-name", default="a1_gazebo")
    parser.add_argument("--camera-x", type=float, default=0.28)
    parser.add_argument("--camera-y", type=float, default=0.0)
    parser.add_argument("--camera-z", type=float, default=0.043)
    parser.add_argument("--default-radius-m", type=float, default=0.15)
    parser.add_argument("--box-margin", type=float, default=1.25)
    parser.add_argument("--min-depth-m", type=float, default=0.15)
    parser.add_argument("--max-depth-m", type=float, default=12.0)
    parser.add_argument("--depth-tolerance-m", type=float, default=0.75)
    parser.add_argument("--min-depth-pixels", type=int, default=6)
    parser.add_argument("--sync-slop", type=float, default=0.08)
    parser.add_argument("--jpeg-quality", type=int, default=92)
    parser.add_argument("--log-every", type=int, default=25)
    parser.add_argument("--preview", action="store_true", help="show live RGB images with generated YOLO boxes")
    parser.add_argument("--preview-wait-ms", type=int, default=1, help="OpenCV preview wait time per frame")


def add_train_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--data", default="outputs/yolo_dataset_route/dataset.yaml")
    parser.add_argument("--model", default="yolov10s.pt")
    parser.add_argument("--project", default="outputs/yolo_runs_route")
    parser.add_argument("--name", default="yolov10s_route")
    parser.add_argument("--imgsz", type=int, default=960)
    parser.add_argument("--epochs", type=int, default=200)
    parser.add_argument("--batch", type=int, default=8)
    parser.add_argument("--device", default="0")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--patience", type=int, default=80)
    parser.add_argument("--mosaic", type=float, default=0.3)
    parser.add_argument("--close-mosaic", type=int, default=30)
    parser.add_argument("--erasing", type=float, default=0.0)
    parser.add_argument("--hsv-h", type=float, default=0.01)
    parser.add_argument("--hsv-s", type=float, default=0.4)
    parser.add_argument("--hsv-v", type=float, default=0.25)
    parser.add_argument("--multi-scale", action="store_true")
    parser.add_argument("--exist-ok", action="store_true")
    parser.add_argument("--compare-model", default="", help="optional comparison model, e.g. yolov10m.pt")
    parser.add_argument("--compare-name", default="yolov10m_route_compare")
    parser.add_argument("--compare-batch", type=int, default=4)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="mode", required=True)

    collect = sub.add_parser("collect", help="collect a YOLO dataset while the robot is running")
    add_collect_args(collect)

    train = sub.add_parser("train", help="train YOLO from a collected dataset")
    add_train_args(train)

    all_cmd = sub.add_parser("all", help="collect then train in one environment")
    add_collect_args(all_cmd)
    add_train_args(all_cmd)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    if args.mode == "collect":
        run_collect(args)
    elif args.mode == "train":
        run_train(args)
    elif args.mode == "all":
        dataset_yaml = run_collect(args)
        args.data = str(dataset_yaml)
        run_train(args)
    else:
        raise ValueError(args.mode)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
