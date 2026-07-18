#!/usr/bin/env python3
"""Run live YOLO red-sphere detection while following a fixed Gazebo route."""

from __future__ import annotations

import argparse
from collections import Counter
import json
import math
import os
from pathlib import Path
import struct
import subprocess
import sys
import threading
import time
from typing import Dict, List, Optional, Sequence, Tuple

import cv2
import numpy as np

from danger_detector.vision import (
    LandmarkConfig,
    Observation3D,
    YoloDepthConfig,
    cluster_landmarks,
    localize_yolo_sphere,
)
from route_truth_yolo_pipeline import load_route, quaternion_to_matrix, route_command


def write_packet(stream, payload: bytes) -> None:
    stream.write(struct.pack("!I", len(payload)))
    stream.write(payload)
    stream.flush()


def read_packet(stream) -> Optional[bytes]:
    header = _read_exact(stream, 4)
    if not header:
        return None
    size = struct.unpack("!I", header)[0]
    return _read_exact(stream, size)


def _read_exact(stream, size: int) -> bytes:
    chunks = []
    remaining = size
    while remaining:
        chunk = stream.read(remaining)
        if not chunk:
            if remaining == size:
                return b""
            raise RuntimeError("incomplete YOLO worker packet")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def normalize_depth_image(depth: np.ndarray) -> np.ndarray:
    array = np.asarray(depth)
    if array.dtype == np.uint16:
        return array.astype(np.float32) / 1000.0
    return array.astype(np.float32)


def median_depth_in_box(depth_m: np.ndarray, box: Sequence[int]) -> Optional[float]:
    height, width = depth_m.shape[:2]
    x0, y0, x1, y1 = [int(value) for value in box]
    margin_x = max(1, int((x1 - x0) * 0.25))
    margin_y = max(1, int((y1 - y0) * 0.25))
    x0 = max(0, min(width - 1, x0 + margin_x))
    x1 = max(0, min(width - 1, x1 - margin_x))
    y0 = max(0, min(height - 1, y0 + margin_y))
    y1 = max(0, min(height - 1, y1 - margin_y))
    if x1 <= x0 or y1 <= y0:
        return None
    values = depth_m[y0 : y1 + 1, x0 : x1 + 1]
    valid = values[np.isfinite(values) & (values >= 0.1) & (values <= 12.0)]
    if valid.size < 6:
        return None
    return float(np.median(valid))


def camera_to_world(
    point_camera: Sequence[float],
    robot_pose: Sequence[float],
    camera_offset: Sequence[float],
) -> np.ndarray:
    px, py, pz = [float(value) for value in point_camera]
    x, y, z, qx, qy, qz, qw = [float(value) for value in robot_pose]
    rotation = quaternion_to_matrix(qx, qy, qz, qw)
    camera_x, camera_y, camera_z = [float(value) for value in camera_offset]
    point_base = np.array(
        [pz + camera_x, -px + camera_y, -py + camera_z], dtype=np.float64
    )
    return (np.array([x, y, z], dtype=np.float64) + rotation @ point_base).astype(np.float32)


def world_to_camera(
    point_world: Sequence[float],
    robot_pose: Sequence[float],
    camera_offset: Sequence[float],
) -> np.ndarray:
    x, y, z, qx, qy, qz, qw = [float(value) for value in robot_pose]
    rotation = quaternion_to_matrix(qx, qy, qz, qw)
    point_base = rotation.T @ (np.asarray(point_world, dtype=np.float64) - np.array([x, y, z]))
    relative = point_base - np.asarray(camera_offset, dtype=np.float64)
    return np.array([-relative[1], -relative[2], relative[0]], dtype=np.float32)


def box_iou(first: Sequence[int], second: Sequence[int]) -> float:
    ax0, ay0, ax1, ay1 = [float(value) for value in first]
    bx0, by0, bx1, by1 = [float(value) for value in second]
    intersection = max(0.0, min(ax1, bx1) - max(ax0, bx0)) * max(
        0.0, min(ay1, by1) - max(ay0, by0)
    )
    first_area = max(0.0, ax1 - ax0) * max(0.0, ay1 - ay0)
    second_area = max(0.0, bx1 - bx0) * max(0.0, by1 - by0)
    return intersection / max(first_area + second_area - intersection, 1e-9)


def deduplicate_detections(
    detections: Sequence[Dict[str, object]], iou_threshold: float = 0.9
) -> List[Dict[str, object]]:
    kept = []
    for detection in sorted(
        detections, key=lambda item: float(item["confidence"]), reverse=True
    ):
        if any(
            box_iou(detection["bbox"], existing["bbox"]) >= iou_threshold
            for existing in kept
        ):
            continue
        kept.append(detection)
    return kept


def greedy_box_matches(
    detections: Sequence[Sequence[int]], truths: Sequence[Sequence[int]], threshold: float
) -> List[Tuple[int, int, float]]:
    candidates = [
        (box_iou(detection, truth), detection_index, truth_index)
        for detection_index, detection in enumerate(detections)
        for truth_index, truth in enumerate(truths)
    ]
    candidates.sort(reverse=True)
    used_detections = set()
    used_truths = set()
    matches = []
    for iou, detection_index, truth_index in candidates:
        if iou < threshold or detection_index in used_detections or truth_index in used_truths:
            continue
        used_detections.add(detection_index)
        used_truths.add(truth_index)
        matches.append((detection_index, truth_index, iou))
    return matches


def cluster_points(
    points: Sequence[Sequence[float]], radius_m: float, min_observations: int
) -> List[np.ndarray]:
    observations = [
        Observation3D(
            position=np.asarray(point, dtype=np.float32),
            stamp=float(index),
            confidence=1.0,
            quality="strong",
            depth_metrics={},
        )
        for index, point in enumerate(points)
    ]
    config = LandmarkConfig(
        cluster_radius_m=radius_m,
        min_total_observations=min_observations,
        min_strong_observations=min_observations,
    )
    return [cluster.center for cluster in cluster_landmarks(observations, config)]


def greedy_position_matches(
    truths: Sequence[Sequence[float]], detections: Sequence[Sequence[float]], threshold_m: float
) -> List[Dict[str, object]]:
    candidates = []
    for truth_index, truth in enumerate(truths):
        for detection_index, detection in enumerate(detections):
            distance = float(np.linalg.norm(np.asarray(truth) - np.asarray(detection)))
            if distance <= threshold_m:
                candidates.append((distance, truth_index, detection_index))
    candidates.sort()
    used_truths = set()
    used_detections = set()
    matches = []
    for distance, truth_index, detection_index in candidates:
        if truth_index in used_truths or detection_index in used_detections:
            continue
        used_truths.add(truth_index)
        used_detections.add(detection_index)
        matches.append(
            {
                "truth_index": truth_index,
                "detection_index": detection_index,
                "distance_m": round(distance, 4),
            }
        )
    return matches


def load_truth_positions(path: Path) -> List[np.ndarray]:
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    return [
        np.asarray(source["position"], dtype=np.float32)
        for source in payload.get("danger_sources", [])
        if source.get("is_danger", True)
        and str(source.get("color", "red")).lower() == "red"
        and str(source.get("shape", "sphere")).lower() == "sphere"
    ]


class YoloWorker:
    def __init__(self, args: argparse.Namespace) -> None:
        command = [
            args.yolo_python,
            str(Path(__file__).resolve()),
            "worker",
            "--model",
            str(Path(args.model).resolve()),
            "--conf",
            str(args.conf),
            "--imgsz",
            str(args.imgsz),
            "--device",
            args.device,
        ]
        worker_environment = os.environ.copy()
        worker_environment["USER"] = worker_environment.get("USER") or "simenv"
        worker_environment["LOGNAME"] = worker_environment.get("LOGNAME") or worker_environment["USER"]
        worker_environment.setdefault("YOLO_CONFIG_DIR", "/tmp/ultralytics")
        worker_environment.setdefault("TORCHINDUCTOR_CACHE_DIR", "/tmp/torchinductor")
        worker_environment.setdefault("MPLCONFIGDIR", "/tmp/matplotlib")
        for key in ("YOLO_CONFIG_DIR", "TORCHINDUCTOR_CACHE_DIR", "MPLCONFIGDIR"):
            Path(worker_environment[key]).mkdir(parents=True, exist_ok=True)
        self.process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=None,
            bufsize=0,
            env=worker_environment,
        )
        assert self.process.stdin is not None and self.process.stdout is not None
        ready_payload = read_packet(self.process.stdout)
        if ready_payload is None:
            raise RuntimeError("YOLO worker exited before initialization")
        ready = json.loads(ready_payload.decode("utf-8"))
        if not ready.get("ready"):
            raise RuntimeError(f"YOLO worker initialization failed: {ready}")
        self.names = ready["names"]

    def predict(self, image_bgr: np.ndarray) -> List[Dict[str, object]]:
        assert self.process.stdin is not None and self.process.stdout is not None
        ok, encoded = cv2.imencode(".jpg", image_bgr, [int(cv2.IMWRITE_JPEG_QUALITY), 92])
        if not ok:
            raise RuntimeError("failed to encode camera frame")
        write_packet(self.process.stdin, encoded.tobytes())
        response = read_packet(self.process.stdout)
        if response is None:
            raise RuntimeError(f"YOLO worker exited with code {self.process.poll()}")
        return json.loads(response.decode("utf-8"))["detections"]

    def close(self) -> None:
        if self.process.poll() is None:
            try:
                assert self.process.stdin is not None
                write_packet(self.process.stdin, b"")
                self.process.wait(timeout=5)
            except Exception:
                self.process.terminate()
                self.process.wait(timeout=5)


def worker_main(args: argparse.Namespace) -> int:
    protocol_output = os.fdopen(os.dup(sys.stdout.fileno()), "wb", buffering=0)
    os.dup2(sys.stderr.fileno(), sys.stdout.fileno())
    from ultralytics import YOLO

    model = YOLO(args.model)
    names = {int(key): str(value) for key, value in model.names.items()}
    red_classes = [index for index, name in names.items() if name == "red_sphere"]
    if not red_classes:
        write_packet(protocol_output, json.dumps({"ready": False, "names": names}).encode("utf-8"))
        return 2
    red_class = red_classes[0]
    write_packet(
        protocol_output,
        json.dumps({"ready": True, "names": names, "red_class": red_class}).encode("utf-8"),
    )
    while True:
        payload = read_packet(sys.stdin.buffer)
        if payload is None or not payload:
            break
        image = cv2.imdecode(np.frombuffer(payload, dtype=np.uint8), cv2.IMREAD_COLOR)
        results = model.predict(
            image,
            conf=args.conf,
            imgsz=args.imgsz,
            device=args.device,
            classes=[red_class],
            max_det=20,
            verbose=False,
        )
        detections = []
        boxes = results[0].boxes if results else None
        if boxes is not None:
            for box in boxes:
                detections.append(
                    {
                        "bbox": [int(round(value)) for value in box.xyxy[0].detach().cpu().tolist()],
                        "confidence": round(float(box.conf[0].detach().cpu().item()), 6),
                    }
                )
        detections = deduplicate_detections(detections)
        write_packet(protocol_output, json.dumps({"detections": detections}).encode("utf-8"))
    return 0


class LiveRouteEvaluator:
    def __init__(self, args: argparse.Namespace) -> None:
        import rospy
        from cv_bridge import CvBridge
        from gazebo_msgs.msg import ModelStates
        from geometry_msgs.msg import Twist
        from message_filters import ApproximateTimeSynchronizer, Subscriber
        from sensor_msgs.msg import CameraInfo, Image

        self.rospy = rospy
        self.args = args
        self.bridge = CvBridge()
        self.worker = YoloWorker(args)
        self.waypoints = load_route(Path(args.route_file))
        self.truth_positions = load_truth_positions(Path(args.truth_file))
        self.camera_offset = (args.camera_x, args.camera_y, args.camera_z)
        self.output_dir = Path(args.output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.detected_file = Path(args.detected_file)
        self.detected_file.parent.mkdir(parents=True, exist_ok=True)
        self.frame_log = (self.output_dir / "frames.jsonl").open("w", encoding="utf-8")
        self.video_path = self.output_dir / "overlay.mp4"
        self.video_writer = None
        self.latest_robot_pose: Optional[Tuple[float, ...]] = None
        self.pose_lock = threading.Lock()
        self.callback_lock = threading.Lock()
        self.depth_config = YoloDepthConfig(sphere_radius_m=args.sphere_radius)
        self.landmark_config = LandmarkConfig(
            cluster_radius_m=args.cluster_radius,
            min_total_observations=args.min_cluster_observations,
            min_strong_observations=args.min_strong_observations,
        )
        self.observations: List[Observation3D] = []
        self.depth_status_counts: Counter = Counter()
        self.depth_reason_counts: Counter = Counter()
        self.depth_localization_times_ms: List[float] = []
        self.diagnostics_dir = self.output_dir / "depth_diagnostics"
        if args.save_depth_diagnostics:
            self.diagnostics_dir.mkdir(parents=True, exist_ok=True)
        self.frame_count = 0
        self.raw_detection_count = 0
        self.frame_tp = 0
        self.frame_fp = 0
        self.visible_truth_count = 0
        self.last_inference_time = 0.0
        self.started_wall_time = time.time()
        self.started_sim_time = rospy.get_time()
        self.stopping = False
        self.failure: Optional[str] = None

        self.command_publisher = rospy.Publisher(args.cmd_topic, Twist, queue_size=1)
        self.overlay_publisher = rospy.Publisher(args.overlay_topic, Image, queue_size=1)
        rospy.Subscriber(args.model_states_topic, ModelStates, self._model_states_callback, queue_size=2)
        subscribers = [
            Subscriber(args.rgb_topic, Image),
            Subscriber(args.depth_topic, Image),
            Subscriber(args.camera_info_topic, CameraInfo),
        ]
        self.synchronizer = ApproximateTimeSynchronizer(subscribers, queue_size=6, slop=0.08)
        self.synchronizer.registerCallback(self._guarded_image_callback)

    def _model_states_callback(self, message) -> None:
        try:
            index = message.name.index(self.args.robot_model_name)
        except ValueError:
            return
        pose = message.pose[index]
        values = (
            pose.position.x,
            pose.position.y,
            pose.position.z,
            pose.orientation.x,
            pose.orientation.y,
            pose.orientation.z,
            pose.orientation.w,
        )
        with self.pose_lock:
            self.latest_robot_pose = values

    def _pose(self) -> Optional[Tuple[float, ...]]:
        with self.pose_lock:
            return self.latest_robot_pose

    def _sim_elapsed(self) -> float:
        return max(0.0, self.rospy.get_time() - self.started_sim_time)

    def _guarded_image_callback(self, rgb_message, depth_message, camera_info) -> None:
        with self.callback_lock:
            self._image_callback(rgb_message, depth_message, camera_info)

    def _visible_truth_boxes(
        self,
        depth: np.ndarray,
        intrinsics: Sequence[float],
        robot_pose: Sequence[float],
        width: int,
        height: int,
    ) -> List[Tuple[int, int, int, int]]:
        fx, fy, cx, cy = [float(value) for value in intrinsics]
        boxes = []
        for position in self.truth_positions:
            point = world_to_camera(position, robot_pose, self.camera_offset)
            x, y, z = [float(value) for value in point]
            if z <= 0.1 or z >= 12.0:
                continue
            u = fx * x / z + cx
            v = fy * y / z + cy
            radius_x = fx * self.args.sphere_radius / z
            radius_y = fy * self.args.sphere_radius / z
            box = (
                max(0, int(u - radius_x)),
                max(0, int(v - radius_y)),
                min(width - 1, int(u + radius_x)),
                min(height - 1, int(v + radius_y)),
            )
            if box[2] <= box[0] or box[3] <= box[1]:
                continue
            measured_depth = median_depth_in_box(depth, box)
            expected_surface = max(0.1, z - self.args.sphere_radius)
            if measured_depth is None or min(abs(measured_depth - z), abs(measured_depth - expected_surface)) > 0.6:
                continue
            boxes.append(box)
        return boxes

    @staticmethod
    def _draw_label(image: np.ndarray, text: str, x: int, y: int, color: Tuple[int, int, int]) -> None:
        y = max(18, y)
        cv2.putText(image, text, (x, y), cv2.FONT_HERSHEY_SIMPLEX, 0.45, color, 1, cv2.LINE_AA)

    def _save_depth_diagnostic(
        self,
        rgb: np.ndarray,
        depth: np.ndarray,
        box: Sequence[int],
        observation,
        detection_index: int,
    ) -> None:
        if not self.args.save_depth_diagnostics or observation.quality == "strong":
            return
        height, width = depth.shape
        x0, y0, x1, y1 = [int(round(value)) for value in box]
        x0 = max(0, min(width - 1, x0))
        x1 = max(0, min(width - 1, x1))
        y0 = max(0, min(height - 1, y0))
        y1 = max(0, min(height - 1, y1))
        if x1 <= x0 or y1 <= y0:
            return
        prefix = "frame_{:06d}_det_{:02d}_{}_{}".format(
            self.frame_count + 1,
            detection_index,
            observation.quality,
            observation.reason,
        )
        rgb_crop = cv2.cvtColor(rgb[y0 : y1 + 1, x0 : x1 + 1], cv2.COLOR_RGB2BGR)
        cv2.imwrite(str(self.diagnostics_dir / f"{prefix}_rgb.jpg"), rgb_crop)
        if observation.mask_roi is not None:
            cv2.imwrite(
                str(self.diagnostics_dir / f"{prefix}_mask.png"),
                observation.mask_roi.astype(np.uint8) * 255,
            )
        depth_crop = depth[y0 : y1 + 1, x0 : x1 + 1]
        valid = np.isfinite(depth_crop) & (depth_crop >= 0.1) & (depth_crop <= 12.0)
        depth_image = np.zeros(depth_crop.shape, dtype=np.uint8)
        if np.any(valid):
            low, high = np.percentile(depth_crop[valid], [2.0, 98.0])
            if high <= low:
                high = low + 0.01
            depth_image[valid] = np.clip(
                (depth_crop[valid] - low) * 255.0 / (high - low), 0.0, 255.0
            ).astype(np.uint8)
        depth_color = cv2.applyColorMap(depth_image, cv2.COLORMAP_TURBO)
        depth_color[~valid] = 0
        cv2.imwrite(str(self.diagnostics_dir / f"{prefix}_depth.png"), depth_color)

    def _image_callback(self, rgb_message, depth_message, camera_info) -> None:
        if self.stopping:
            return
        now = time.time()
        if now - self.last_inference_time < 1.0 / max(self.args.inference_fps, 0.1):
            return
        robot_pose = self._pose()
        if robot_pose is None:
            return
        self.last_inference_time = now
        try:
            rgb = self.bridge.imgmsg_to_cv2(rgb_message, desired_encoding="rgb8")
            depth = normalize_depth_image(
                self.bridge.imgmsg_to_cv2(depth_message, desired_encoding="passthrough")
            )
            image = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
            predictions = self.worker.predict(image)
        except Exception as exc:
            self.failure = str(exc)
            self.rospy.logerr("live YOLO inference failed: %s", exc)
            return

        intrinsics = (camera_info.K[0], camera_info.K[4], camera_info.K[2], camera_info.K[5])
        height, width = image.shape[:2]
        truth_boxes = self._visible_truth_boxes(depth, intrinsics, robot_pose, width, height)
        detection_boxes = [prediction["bbox"] for prediction in predictions]
        matches = greedy_box_matches(detection_boxes, truth_boxes, self.args.iou_threshold)
        matched_detection_indexes = {match[0] for match in matches}

        for box in truth_boxes:
            x0, y0, x1, y1 = box
            cv2.rectangle(image, (x0, y0), (x1, y1), (0, 255, 0), 1)
            self._draw_label(image, "GT red_sphere", x0, y0 - 4, (0, 255, 0))

        frame_detections = []
        for index, prediction in enumerate(predictions):
            box = prediction["bbox"]
            confidence = float(prediction["confidence"])
            x0, y0, x1, y1 = box
            localization_started = time.perf_counter()
            depth_observation = localize_yolo_sphere(
                rgb, depth, box, intrinsics, self.depth_config
            )
            self.depth_localization_times_ms.append(
                (time.perf_counter() - localization_started) * 1000.0
            )
            self.depth_status_counts[depth_observation.quality] += 1
            self.depth_reason_counts[depth_observation.reason] += 1
            depth_z = depth_observation.surface_depth_m
            world_position = None
            if depth_observation.position_camera is not None:
                point_world = camera_to_world(
                    depth_observation.position_camera, robot_pose, self.camera_offset
                )
                self.observations.append(
                    Observation3D(
                        position=point_world,
                        stamp=float(rgb_message.header.stamp.to_sec()),
                        confidence=confidence,
                        quality=depth_observation.quality,
                        depth_metrics=depth_observation.metrics(),
                    )
                )
                world_position = [round(float(value), 4) for value in point_world]
            self._save_depth_diagnostic(rgb, depth, box, depth_observation, index)
            is_frame_tp = index in matched_detection_indexes
            color = (0, 220, 255) if is_frame_tp else (255, 0, 255)
            cv2.rectangle(image, (x0, y0), (x1, y1), color, 2)
            label = f"red_sphere {confidence:.2f} [{depth_observation.quality[0].upper()}]"
            if depth_z is not None:
                label += f" D={depth_z:.2f}m"
            self._draw_label(image, label, x0, y0 - 20, color)
            if world_position is not None:
                self._draw_label(
                    image,
                    "W=({:.2f},{:.2f},{:.2f})".format(*world_position),
                    x0,
                    y0 - 4,
                    color,
                )
            frame_detections.append(
                {
                    "bbox": box,
                    "confidence": confidence,
                    "depth_m": None if depth_z is None else round(depth_z, 4),
                    "world_position": world_position,
                    "matched_visible_truth": is_frame_tp,
                    "depth_status": depth_observation.quality,
                    "depth_reason": depth_observation.reason,
                    "depth_metrics": depth_observation.metrics(),
                }
            )

        self.frame_count += 1
        self.raw_detection_count += len(predictions)
        self.frame_tp += len(matches)
        self.frame_fp += len(predictions) - len(matches)
        self.visible_truth_count += len(truth_boxes)
        elapsed = self._sim_elapsed()
        cv2.putText(
            image,
            f"t={elapsed:.1f}s frame={self.frame_count} det={len(predictions)} GT={len(truth_boxes)}",
            (10, 24),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.6,
            (255, 255, 255),
            2,
            cv2.LINE_AA,
        )

        if self.video_writer is None:
            fourcc = cv2.VideoWriter_fourcc(*"mp4v")
            self.video_writer = cv2.VideoWriter(
                str(self.video_path), fourcc, float(self.args.inference_fps), (width, height)
            )
            if not self.video_writer.isOpened():
                raise RuntimeError(f"failed to open video output: {self.video_path}")
        self.video_writer.write(image)
        self.frame_log.write(
            json.dumps(
                {
                    "frame": self.frame_count,
                    "stamp": rgb_message.header.stamp.to_sec(),
                    "visible_truth_count": len(truth_boxes),
                    "frame_matches": len(matches),
                    "detections": frame_detections,
                },
                ensure_ascii=False,
            )
            + "\n"
        )
        self.frame_log.flush()

        overlay_message = self.bridge.cv2_to_imgmsg(image, encoding="bgr8")
        overlay_message.header = rgb_message.header
        self.overlay_publisher.publish(overlay_message)
        if self.args.show:
            cv2.imshow("YOLO red sphere route evaluation", image)
            cv2.waitKey(1)

    def run(self) -> Dict[str, object]:
        from geometry_msgs.msg import Twist

        route_index = 0
        route_completed = False
        rate = self.rospy.Rate(self.args.control_rate)
        while not self.rospy.is_shutdown():
            elapsed = self._sim_elapsed()
            if self.failure or (self.args.duration > 0 and elapsed >= self.args.duration):
                break
            command = Twist()
            pose = self._pose()
            if pose is not None and elapsed >= self.args.route_start_delay:
                yaw = math.atan2(
                    2.0 * (pose[6] * pose[5] + pose[3] * pose[4]),
                    1.0 - 2.0 * (pose[4] * pose[4] + pose[5] * pose[5]),
                )
                route_pose = (pose[0], pose[1], yaw)
                while route_index < len(self.waypoints):
                    linear_x, angular_z, reached, _ = route_command(
                        route_pose, self.waypoints[route_index], self.args
                    )
                    if not reached:
                        command.linear.x = linear_x
                        command.angular.z = angular_z
                        break
                    route_index += 1
                route_completed = route_index >= len(self.waypoints)
            self.command_publisher.publish(command)
            if route_completed:
                break
            try:
                rate.sleep()
            except self.rospy.ROSInterruptException:
                break

        self.stopping = True
        stop = Twist()
        for _ in range(10):
            self.command_publisher.publish(stop)
            time.sleep(0.05)
        return self.finalize(route_completed, route_index)

    def finalize(self, route_completed: bool, route_index: int) -> Dict[str, object]:
        elapsed = self._sim_elapsed()
        wall_elapsed = time.time() - self.started_wall_time
        with self.callback_lock:
            if self.video_writer is not None:
                self.video_writer.release()
            self.frame_log.close()
            if self.args.show:
                cv2.destroyAllWindows()
            self.worker.close()

        landmarks = cluster_landmarks(self.observations, self.landmark_config)
        centers = [landmark.center for landmark in landmarks]
        detected_payload = {
            "exploration_time": round(elapsed, 3),
            "detected_danger_sources": [
                {"position": [round(float(value), 3) for value in center]} for center in centers
            ],
        }
        self.detected_file.write_text(
            json.dumps(detected_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

        matches = greedy_position_matches(
            self.truth_positions, centers, self.args.position_threshold
        )
        correct = len(matches)
        missed = len(self.truth_positions) - correct
        false_alarms = len(centers) - correct
        detail = {
            "model": str(Path(self.args.model).resolve()),
            "red_sphere_only": True,
            "truth_used_for_detection": False,
            "route_completed": route_completed,
            "last_waypoint_index": route_index,
            "waypoint_count": len(self.waypoints),
            "exploration_time_s": round(elapsed, 3),
            "wall_time_s": round(wall_elapsed, 3),
            "frames": self.frame_count,
            "raw_detections": self.raw_detection_count,
            "raw_3d_observations": len(self.observations),
            "clustered_detections": len(centers),
            "truth_count": len(self.truth_positions),
            "correct": correct,
            "missed": missed,
            "false_alarms": false_alarms,
            "recall": round(correct / len(self.truth_positions), 4) if self.truth_positions else 0.0,
            "false_alarm_rate": round(false_alarms / len(centers), 4) if centers else 0.0,
            "frame_level": {
                "visible_truth_instances": self.visible_truth_count,
                "matched_detections": self.frame_tp,
                "unmatched_detections": self.frame_fp,
            },
            "depth_observations": {
                "strong": self.depth_status_counts["strong"],
                "weak": self.depth_status_counts["weak"],
                "rejected": self.depth_status_counts["rejected"],
                "reasons": dict(sorted(self.depth_reason_counts.items())),
            },
            "depth_localization_ms": {
                "mean": round(float(np.mean(self.depth_localization_times_ms)), 4)
                if self.depth_localization_times_ms
                else 0.0,
                "p95": round(float(np.percentile(self.depth_localization_times_ms, 95)), 4)
                if self.depth_localization_times_ms
                else 0.0,
            },
            "clusters": [
                {
                    "position": [round(float(value), 4) for value in landmark.center],
                    "total_support": landmark.total_support,
                    "strong_support": landmark.strong_support,
                    "mean_confidence": round(landmark.mean_confidence, 4),
                    "spread_m": round(landmark.spread_m, 4),
                }
                for landmark in landmarks
            ],
            "matches": matches,
            "detected_positions": [[round(float(value), 4) for value in center] for center in centers],
            "truth_positions": [[round(float(value), 4) for value in point] for point in self.truth_positions],
            "failure": self.failure,
            "video": str(self.video_path),
            "detected_file": str(self.detected_file),
        }
        (self.output_dir / "evaluation_detail.json").write_text(
            json.dumps(detail, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        return detail


def run_main(args: argparse.Namespace) -> int:
    import rospy

    rospy.init_node("live_yolo_route_eval", anonymous=True)
    evaluator = LiveRouteEvaluator(args)
    detail = evaluator.run()
    print(json.dumps(detail, ensure_ascii=False, indent=2))
    return 1 if detail["failure"] else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="mode", required=True)

    worker = subparsers.add_parser("worker")
    worker.add_argument("--model", required=True)
    worker.add_argument("--conf", type=float, default=0.25)
    worker.add_argument("--imgsz", type=int, default=960)
    worker.add_argument("--device", default="0")

    run = subparsers.add_parser("run")
    run.add_argument("--model", required=True)
    run.add_argument("--yolo-python", required=True)
    run.add_argument("--truth-file", required=True)
    run.add_argument("--route-file", default="config/fixed_floor0_route.json")
    run.add_argument("--output-dir", default="outputs/yolo_route_eval/latest")
    run.add_argument("--detected-file", default="outputs/yolo_route_eval/latest/detected_danger.json")
    run.add_argument("--rgb-topic", default="/real_sense/rgb/image_raw")
    run.add_argument("--depth-topic", default="/real_sense/depth/image_raw")
    run.add_argument("--camera-info-topic", default="/real_sense/rgb/camera_info")
    run.add_argument("--model-states-topic", default="/gazebo/model_states")
    run.add_argument("--robot-model-name", default="a1_gazebo")
    run.add_argument("--cmd-topic", default="/cmd_vel")
    run.add_argument("--overlay-topic", default="/vision/yolo_eval_overlay")
    run.add_argument("--camera-x", type=float, default=0.28)
    run.add_argument("--camera-y", type=float, default=0.0)
    run.add_argument("--camera-z", type=float, default=0.043)
    run.add_argument("--sphere-radius", type=float, default=0.15)
    run.add_argument("--conf", type=float, default=0.25)
    run.add_argument("--imgsz", type=int, default=960)
    run.add_argument("--device", default="0")
    run.add_argument("--inference-fps", type=float, default=5.0)
    run.add_argument("--duration", type=float, default=1200.0)
    run.add_argument("--iou-threshold", type=float, default=0.3)
    run.add_argument("--position-threshold", type=float, default=1.0)
    run.add_argument("--cluster-radius", type=float, default=0.35)
    run.add_argument("--min-cluster-observations", type=int, default=5)
    run.add_argument("--min-strong-observations", type=int, default=2)
    run.add_argument("--save-depth-diagnostics", action="store_true")
    run.add_argument("--show", action="store_true")
    run.add_argument("--route-start-delay", type=float, default=2.0)
    run.add_argument("--control-rate", type=float, default=12.0)
    run.add_argument("--goal-tolerance", type=float, default=0.35)
    run.add_argument("--turn-only-angle", type=float, default=0.2)
    run.add_argument("--max-vx", type=float, default=0.5)
    run.add_argument("--min-vx", type=float, default=0.5)
    run.add_argument("--max-wz", type=float, default=1.0)
    run.add_argument("--k-dist", type=float, default=0.35)
    run.add_argument("--k-yaw", type=float, default=0.9)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    if args.mode == "worker":
        return worker_main(args)
    return run_main(args)


if __name__ == "__main__":
    raise SystemExit(main())
