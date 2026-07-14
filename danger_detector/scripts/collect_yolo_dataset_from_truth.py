#!/usr/bin/env python3
"""Collect YOLO labels by projecting offline truth into live ROS camera frames."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

import cv2
import numpy as np

import rospy
import tf2_ros
from cv_bridge import CvBridge
from gazebo_msgs.msg import ModelStates
from message_filters import ApproximateTimeSynchronizer, Subscriber
from sensor_msgs.msg import CameraInfo, Image


def normalize_depth_image(depth: np.ndarray) -> np.ndarray:
    arr = np.asarray(depth)
    if arr.dtype == np.uint16:
        return arr.astype(np.float32) / 1000.0
    return arr.astype(np.float32)


def load_danger_sources(truth_file: Path) -> List[Dict[str, object]]:
    with open(truth_file, "r", encoding="utf-8") as handle:
        payload = json.load(handle)
    sources = []
    for source in payload.get("danger_sources", []):
        if source.get("is_danger", True) and source.get("shape") == "sphere":
            sources.append(source)
    if not sources:
        raise ValueError(f"no danger sphere sources found in {truth_file}")
    return sources


def split_for_key(key: str, val_fraction: float) -> str:
    digest = hashlib.sha1(key.encode("utf-8")).hexdigest()
    value = int(digest[:8], 16) / float(0xFFFFFFFF)
    return "val" if value < val_fraction else "train"


def clamp_box(x0: float, y0: float, x1: float, y1: float, width: int, height: int) -> Optional[Tuple[int, int, int, int]]:
    ix0 = max(0, int(np.floor(x0)))
    iy0 = max(0, int(np.floor(y0)))
    ix1 = min(width - 1, int(np.ceil(x1)))
    iy1 = min(height - 1, int(np.ceil(y1)))
    if ix1 <= ix0 or iy1 <= iy0:
        return None
    return ix0, iy0, ix1, iy1


def yolo_line_for_box(box: Tuple[int, int, int, int], width: int, height: int) -> str:
    x0, y0, x1, y1 = box
    x_center = ((x0 + x1) / 2.0) / width
    y_center = ((y0 + y1) / 2.0) / height
    box_width = (x1 - x0 + 1) / width
    box_height = (y1 - y0 + 1) / height
    return f"0 {x_center:.6f} {y_center:.6f} {box_width:.6f} {box_height:.6f}"


class TruthYoloCollector:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.output_dir = Path(args.output_dir)
        self.sources = load_danger_sources(Path(args.truth_file))
        self.bridge = CvBridge()
        self.tf_buffer = tf2_ros.Buffer(cache_time=rospy.Duration(30.0))
        self.tf_listener = tf2_ros.TransformListener(self.tf_buffer)
        self.latest_model_states: Optional[ModelStates] = None
        self.frame_index = 0
        self.saved_images = 0
        self.saved_labels = 0
        self.skipped_empty = 0

        self._write_dataset_yaml()

        subscribers = [
            Subscriber(args.rgb_topic, Image),
            Subscriber(args.depth_topic, Image),
            Subscriber(args.camera_info_topic, CameraInfo),
        ]
        self.sync = ApproximateTimeSynchronizer(subscribers, queue_size=8, slop=args.sync_slop)
        self.sync.registerCallback(self._callback)
        rospy.Subscriber(args.model_states_topic, ModelStates, self._model_states_cb, queue_size=2)
        rospy.loginfo(
            "collecting YOLO labels from %s using %s, %s, %s",
            args.truth_file,
            args.rgb_topic,
            args.depth_topic,
            args.camera_info_topic,
        )

    def _model_states_cb(self, msg: ModelStates) -> None:
        self.latest_model_states = msg

    def _write_dataset_yaml(self) -> None:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        dataset_yaml = self.output_dir / "dataset.yaml"
        dataset_path = self.output_dir.resolve()
        dataset_yaml.write_text(
            "\n".join(
                [
                    f"path: {dataset_path}",
                    "train: images/train",
                    "val: images/val",
                    "names:",
                    "  0: red_sphere",
                    "",
                ]
            ),
            encoding="utf-8",
        )

    def _callback(self, rgb_msg: Image, depth_msg: Image, camera_info: CameraInfo) -> None:
        if self.frame_index % max(1, self.args.sample_every_n) != 0:
            self.frame_index += 1
            return

        try:
            image_rgb = self.bridge.imgmsg_to_cv2(rgb_msg, desired_encoding="rgb8")
            depth = normalize_depth_image(self.bridge.imgmsg_to_cv2(depth_msg, desired_encoding="passthrough"))
        except Exception as exc:
            rospy.logwarn_throttle(5.0, "failed to convert image/depth: %s", exc)
            self.frame_index += 1
            return

        height, width = image_rgb.shape[:2]
        frame_id = rgb_msg.header.frame_id or camera_info.header.frame_id or self.args.camera_frame
        labels = self._project_labels(frame_id, rgb_msg.header.stamp, camera_info, depth, width, height)

        if not labels and not self.args.keep_empty:
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
        if self.saved_images % 25 == 0:
            rospy.loginfo(
                "saved_images=%d saved_labels=%d skipped_empty=%d",
                self.saved_images,
                self.saved_labels,
                self.skipped_empty,
            )
        if self.args.max_images > 0 and self.saved_images >= self.args.max_images:
            rospy.signal_shutdown("max images collected")
        self.frame_index += 1

    def _project_labels(
        self,
        camera_frame: str,
        stamp: rospy.Time,
        camera_info: CameraInfo,
        depth: np.ndarray,
        width: int,
        height: int,
    ) -> List[str]:
        fx, fy, cx, cy = camera_info.K[0], camera_info.K[4], camera_info.K[2], camera_info.K[5]
        try:
            transform = self.tf_buffer.lookup_transform(camera_frame, self.args.world_frame, stamp, rospy.Duration(0.15))
            transform_source = "tf"
        except Exception as exc:
            rospy.logwarn_throttle(5.0, "TF lookup %s <- %s failed: %s", camera_frame, self.args.world_frame, exc)
            transform = self._fallback_world_to_camera_transform()
            transform_source = "model_states"
            if transform is None:
                return []

        labels = []
        for source in self.sources:
            position = source.get("position")
            if not isinstance(position, list) or len(position) != 3:
                continue
            radius = float(source.get("radius", self.args.sphere_radius_m))
            world_point = np.asarray(position, dtype=np.float64)
            if transform_source == "tf":
                point = self._transform_point(world_point, transform)
            else:
                point = transform(world_point)
            x, y, z = point.tolist()
            if z <= self.args.min_depth_m or z >= self.args.max_depth_m:
                continue

            u = fx * x / z + cx
            v = fy * y / z + cy
            if not (0 <= u < width and 0 <= v < height):
                continue

            half_w = self.args.box_margin * fx * radius / z
            half_h = self.args.box_margin * fy * radius / z
            box = clamp_box(u - half_w, v - half_h, u + half_w, v + half_h, width, height)
            if box is None:
                continue
            if not self._passes_depth_visibility(depth, box, z):
                continue
            labels.append(yolo_line_for_box(box, width, height))
        return labels

    def _fallback_world_to_camera_transform(self):
        if self.latest_model_states is None:
            return None
        if self.args.robot_model_name not in self.latest_model_states.name:
            return None
        index = self.latest_model_states.name.index(self.args.robot_model_name)
        pose = self.latest_model_states.pose[index]
        q = pose.orientation
        rotation_world_base = quaternion_to_matrix(q.x, q.y, q.z, q.w)
        translation_world_base = np.array([pose.position.x, pose.position.y, pose.position.z], dtype=np.float64)
        camera_in_base = np.array(
            [self.args.camera_x, self.args.camera_y, self.args.camera_z],
            dtype=np.float64,
        )

        def world_to_camera(world_point: np.ndarray) -> np.ndarray:
            base_point = rotation_world_base.T @ (world_point - translation_world_base)
            relative = base_point - camera_in_base
            # Gazebo OpenNI image coordinates use optical convention:
            # X right, Y down, Z forward. Base is X forward, Y left, Z up.
            return np.array([-relative[1], -relative[2], relative[0]], dtype=np.float64)

        return world_to_camera

    def _passes_depth_visibility(self, depth: np.ndarray, box: Tuple[int, int, int, int], expected_z: float) -> bool:
        x0, y0, x1, y1 = box
        cx0 = int(x0 + 0.3 * (x1 - x0))
        cx1 = int(x0 + 0.7 * (x1 - x0))
        cy0 = int(y0 + 0.3 * (y1 - y0))
        cy1 = int(y0 + 0.7 * (y1 - y0))
        patch = depth[max(0, cy0) : max(cy0 + 1, cy1 + 1), max(0, cx0) : max(cx0 + 1, cx1 + 1)]
        valid = patch[np.isfinite(patch) & (patch >= self.args.min_depth_m) & (patch <= self.args.max_depth_m)]
        if valid.size < self.args.min_depth_pixels:
            return False
        median_depth = float(np.median(valid))
        return abs(median_depth - expected_z) <= self.args.depth_tolerance_m

    @staticmethod
    def _transform_point(point: np.ndarray, transform) -> np.ndarray:
        t = transform.transform.translation
        q = transform.transform.rotation
        rotation = quaternion_to_matrix(q.x, q.y, q.z, q.w)
        translation = np.array([t.x, t.y, t.z], dtype=np.float64)
        return rotation @ point + translation


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


def parse_args(argv: Optional[Iterable[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--truth-file", default="results/danger_truth.json")
    parser.add_argument("--output-dir", default="outputs/yolo_dataset")
    parser.add_argument("--run-name", default="simenv")
    parser.add_argument("--rgb-topic", default="/real_sense/rgb/image_raw")
    parser.add_argument("--depth-topic", default="/real_sense/depth/image_raw")
    parser.add_argument("--camera-info-topic", default="/real_sense/rgb/camera_info")
    parser.add_argument("--model-states-topic", default="/gazebo/model_states")
    parser.add_argument("--world-frame", default="world")
    parser.add_argument("--camera-frame", default="real_sense")
    parser.add_argument("--robot-model-name", default="a1_gazebo")
    parser.add_argument("--camera-x", type=float, default=0.28)
    parser.add_argument("--camera-y", type=float, default=0.0)
    parser.add_argument("--camera-z", type=float, default=0.043)
    parser.add_argument("--sample-every-n", type=int, default=5)
    parser.add_argument("--max-images", type=int, default=0)
    parser.add_argument("--val-fraction", type=float, default=0.2)
    parser.add_argument("--sphere-radius-m", type=float, default=0.15)
    parser.add_argument("--box-margin", type=float, default=1.15)
    parser.add_argument("--min-depth-m", type=float, default=0.05)
    parser.add_argument("--max-depth-m", type=float, default=8.0)
    parser.add_argument("--depth-tolerance-m", type=float, default=0.25)
    parser.add_argument("--min-depth-pixels", type=int, default=8)
    parser.add_argument("--sync-slop", type=float, default=0.08)
    parser.add_argument("--jpeg-quality", type=int, default=95)
    parser.add_argument("--keep-empty", action="store_true", help="keep frames without visible red spheres as hard negatives")
    return parser.parse_args(argv)


def main() -> None:
    args = parse_args()
    rospy.init_node("truth_yolo_dataset_collector")
    TruthYoloCollector(args)
    rospy.spin()


if __name__ == "__main__":
    main()
