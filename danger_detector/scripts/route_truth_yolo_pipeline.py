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
import time
import zipfile
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np


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


def yaw_from_quaternion(x: float, y: float, z: float, w: float) -> float:
    return math.atan2(2.0 * (w * z + x * y), 1.0 - 2.0 * (y * y + z * z))


def wrap_angle(angle: float) -> float:
    while angle > math.pi:
        angle -= 2.0 * math.pi
    while angle < -math.pi:
        angle += 2.0 * math.pi
    return angle


def load_route(route_file: Path) -> List[Tuple[float, float]]:
    with route_file.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    raw_waypoints = payload.get("waypoints") if isinstance(payload, dict) else None
    if not isinstance(raw_waypoints, list) or len(raw_waypoints) < 2:
        raise ValueError(f"route must contain at least two waypoints: {route_file}")
    waypoints: List[Tuple[float, float]] = []
    for index, point in enumerate(raw_waypoints):
        if not isinstance(point, list) or len(point) != 2:
            raise ValueError(f"invalid waypoint {index} in {route_file}; expected [x, y]")
        waypoints.append((float(point[0]), float(point[1])))
    return waypoints


def route_command(
    pose: Tuple[float, float, float],
    target: Tuple[float, float],
    args: argparse.Namespace,
) -> Tuple[float, float, bool, float]:
    x, y, yaw = pose
    dx = target[0] - x
    dy = target[1] - y
    distance = math.hypot(dx, dy)
    if distance <= args.goal_tolerance:
        return 0.0, 0.0, True, distance
    yaw_error = wrap_angle(math.atan2(dy, dx) - yaw)
    angular_z = max(-args.max_wz, min(args.max_wz, args.k_yaw * yaw_error))
    if abs(yaw_error) > args.turn_only_angle:
        return 0.0, math.copysign(args.max_wz, yaw_error), False, distance
    linear_x = min(args.max_vx, max(args.min_vx, args.k_dist * distance))
    linear_x *= max(0.2, math.cos(yaw_error))
    linear_x = min(args.max_vx, max(args.min_vx, linear_x))
    return linear_x, angular_z, False, distance


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

        write_dataset_yaml(self.output_dir, args.danger_only)

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
        import cv2

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

        image_bgr = cv2.cvtColor(image_rgb, cv2.COLOR_RGB2BGR)
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

    def write_summary(self, extra: Optional[Dict[str, object]] = None) -> None:
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
        if extra:
            summary.update(extra)
        (self.output_dir / "collection_summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )


def run_collect(args: argparse.Namespace) -> Path:
    import rospy

    rospy.init_node("route_truth_yolo_collector", anonymous=True)
    collector = RouteTruthCollector(args)
    rospy.loginfo("collecting dataset into %s", args.output_dir)
    rospy.spin()
    collector.write_summary()
    print(f"dataset: {Path(args.output_dir) / 'dataset.yaml'}")
    print(f"saved_images={collector.saved_images} saved_labels={collector.saved_labels} saved_empty={collector.saved_empty}")
    return Path(args.output_dir) / "dataset.yaml"


def run_collect_route(args: argparse.Namespace) -> Path:
    import rospy
    from geometry_msgs.msg import Twist

    rospy.init_node("route_truth_yolo_collector", anonymous=True)
    collector = RouteTruthCollector(args)
    waypoints = load_route(Path(args.route_file))
    command_publisher = rospy.Publisher(args.cmd_topic, Twist, queue_size=1)
    route_index = 0
    route_done = False
    route_log: List[Dict[str, object]] = []
    start_time = time.time()
    last_log_time = 0.0
    rate = rospy.Rate(args.control_rate)

    rospy.loginfo("collecting dataset into %s along %d waypoints", args.output_dir, len(waypoints))
    try:
        while not rospy.is_shutdown():
            elapsed = time.time() - start_time
            if args.duration > 0 and elapsed >= args.duration:
                break

            command = Twist()
            states = collector.latest_model_states
            pose_values: Optional[Tuple[float, float, float]] = None
            if states is not None:
                try:
                    model_index = states.name.index(args.robot_model_name)
                except ValueError:
                    model_index = -1
                if model_index >= 0:
                    pose = states.pose[model_index]
                    pose_values = (
                        float(pose.position.x),
                        float(pose.position.y),
                        yaw_from_quaternion(
                            pose.orientation.x,
                            pose.orientation.y,
                            pose.orientation.z,
                            pose.orientation.w,
                        ),
                    )

            if pose_values is not None and elapsed >= args.route_start_delay:
                while route_index < len(waypoints):
                    linear_x, angular_z, reached, distance = route_command(
                        pose_values, waypoints[route_index], args
                    )
                    if not reached:
                        command.linear.x = linear_x
                        command.angular.z = angular_z
                        break
                    route_index += 1
                if route_index >= len(waypoints):
                    route_done = True

                now = time.time()
                if now - last_log_time >= 1.0:
                    route_log.append(
                        {
                            "elapsed_s": round(elapsed, 3),
                            "pose": [round(value, 4) for value in pose_values],
                            "waypoint_index": min(route_index, len(waypoints) - 1),
                            "distance_m": round(distance if route_index < len(waypoints) else 0.0, 4),
                            "command": [round(command.linear.x, 4), round(command.angular.z, 4)],
                        }
                    )
                    last_log_time = now

            command_publisher.publish(command)
            if route_done:
                break
            rate.sleep()
    finally:
        stop = Twist()
        for _ in range(10):
            command_publisher.publish(stop)
            time.sleep(0.05)

    collector.write_summary(
        {
            "route_file": str(Path(args.route_file)),
            "route_completed": route_done,
            "last_waypoint_index": route_index,
            "waypoint_count": len(waypoints),
            "route_elapsed_s": round(time.time() - start_time, 3),
            "route_log": route_log,
        }
    )
    print(f"dataset: {Path(args.output_dir) / 'dataset.yaml'}")
    print(
        f"route_completed={route_done} saved_images={collector.saved_images} "
        f"saved_labels={collector.saved_labels} saved_empty={collector.saved_empty}"
    )
    return Path(args.output_dir) / "dataset.yaml"


def count_label_lines(label_root: Path) -> int:
    total = 0
    for label_file in label_root.rglob("*.txt"):
        with label_file.open("r", encoding="utf-8") as handle:
            total += sum(1 for line in handle if line.strip())
    return total


def prepare_training_data(data_file: Path) -> Path:
    import yaml

    with data_file.open("r", encoding="utf-8") as handle:
        payload = yaml.safe_load(handle)
    if not isinstance(payload, dict):
        raise ValueError(f"invalid dataset yaml: {data_file}")

    configured_root = payload.get("path")
    if configured_root and Path(str(configured_root)).expanduser().exists():
        return data_file
    local_root = data_file.parent.resolve()
    if not (local_root / "images").is_dir() or not (local_root / "labels").is_dir():
        return data_file

    payload["path"] = str(local_root)
    local_data_file = data_file.with_name(f"{data_file.stem}.local{data_file.suffix}")
    with local_data_file.open("w", encoding="utf-8") as handle:
        yaml.safe_dump(payload, handle, sort_keys=False, allow_unicode=True)
    print(f"dataset path relocated: {configured_root} -> {local_root}", flush=True)
    return local_data_file


def validate_model_file(model: str) -> None:
    model_file = Path(model)
    if not model_file.is_file() or model_file.suffix.lower() != ".pt":
        return
    with model_file.open("rb") as handle:
        has_zip_header = handle.read(4) == b"PK\x03\x04"
    if not has_zip_header:
        return
    try:
        with zipfile.ZipFile(model_file) as archive:
            if archive.testzip() is not None:
                raise zipfile.BadZipFile("checkpoint contains a damaged member")
    except zipfile.BadZipFile as exc:
        raise RuntimeError(
            f"model checkpoint is incomplete or corrupted: {model_file} "
            f"({model_file.stat().st_size} bytes). Remove it and download it again, "
            "or pass a valid checkpoint with --model."
        ) from exc


def run_train(args: argparse.Namespace) -> None:
    from ultralytics import YOLO

    data_file = Path(args.data)
    if not data_file.exists():
        raise FileNotFoundError(f"dataset yaml not found: {data_file}")
    data_file = prepare_training_data(data_file)
    validate_model_file(args.model)
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


def add_route_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--route-file", default="config/fixed_floor0_route.json")
    parser.add_argument("--cmd-topic", default="/cmd_vel")
    parser.add_argument("--route-start-delay", type=float, default=2.0)
    parser.add_argument("--control-rate", type=float, default=12.0)
    parser.add_argument("--goal-tolerance", type=float, default=0.35)
    parser.add_argument("--turn-only-angle", type=float, default=0.2)
    parser.add_argument("--max-vx", type=float, default=0.5)
    parser.add_argument("--min-vx", type=float, default=0.5)
    parser.add_argument("--max-wz", type=float, default=1.0)
    parser.add_argument("--k-dist", type=float, default=0.35)
    parser.add_argument("--k-yaw", type=float, default=0.9)


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

    collect_route = sub.add_parser("collect-route", help="follow a fixed route and collect a YOLO dataset")
    add_collect_args(collect_route)
    add_route_args(collect_route)

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
    elif args.mode == "collect-route":
        run_collect_route(args)
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
