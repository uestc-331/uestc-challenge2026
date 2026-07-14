#!/usr/bin/env python3
"""Record RGB-D demo frames from ROS or render a YOLO-depth overlay video.

This script intentionally has two modes:

* record: run inside the ROS/Gazebo environment with system Python.
* render: run in the YOLO conda environment without importing ROS.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import time
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import cv2
import numpy as np


def normalize_depth_image(depth: np.ndarray) -> np.ndarray:
    arr = np.asarray(depth)
    if arr.dtype == np.uint16:
        return arr.astype(np.float32) / 1000.0
    return arr.astype(np.float32)


def camera_to_world(point_camera: np.ndarray, odom: Sequence[float]) -> np.ndarray:
    x, y, z, qx, qy, qz, qw = [float(v) for v in odom]
    body_point = np.array([point_camera[2] + 0.28, -point_camera[0], -point_camera[1] + 0.043], dtype=np.float32)
    yaw = math.atan2(2.0 * (qw * qz + qx * qy), 1.0 - 2.0 * (qy * qy + qz * qz))
    rot = np.array(
        [
            [math.cos(yaw), -math.sin(yaw), 0.0],
            [math.sin(yaw), math.cos(yaw), 0.0],
            [0.0, 0.0, 1.0],
        ],
        dtype=np.float32,
    )
    return np.array([x, y, z], dtype=np.float32) + rot @ body_point


def median_depth_in_box(depth_m: np.ndarray, box: Sequence[int]) -> Optional[float]:
    h, w = depth_m.shape[:2]
    x0, y0, x1, y1 = [int(v) for v in box]
    x0 = max(0, min(w - 1, x0))
    x1 = max(0, min(w - 1, x1))
    y0 = max(0, min(h - 1, y0))
    y1 = max(0, min(h - 1, y1))
    if x1 <= x0 or y1 <= y0:
        return None
    roi = depth_m[y0:y1 + 1, x0:x1 + 1]
    valid = roi[np.isfinite(roi) & (roi > 0.05) & (roi < 15.0)]
    if valid.size < 8:
        return None
    return float(np.median(valid))


def draw_label(image: np.ndarray, text: str, origin: Tuple[int, int], color: Tuple[int, int, int]) -> None:
    font = cv2.FONT_HERSHEY_SIMPLEX
    scale = 0.45
    thickness = 1
    (tw, th), baseline = cv2.getTextSize(text, font, scale, thickness)
    x, y = origin
    y = max(th + baseline + 3, y)
    cv2.rectangle(image, (x, y - th - baseline - 4), (x + tw + 6, y + 3), (20, 20, 20), -1)
    cv2.putText(image, text, (x + 3, y - 3), font, scale, color, thickness, cv2.LINE_AA)


def render_video(args: argparse.Namespace) -> None:
    from ultralytics import YOLO

    frames_dir = Path(args.frames_dir)
    output_video = Path(args.output_video)
    output_video.parent.mkdir(parents=True, exist_ok=True)
    frame_files = sorted(frames_dir.glob("frame_*.npz"))
    if not frame_files:
        raise RuntimeError(f"no frames found in {frames_dir}")

    model = YOLO(args.model)
    writer = None
    summary: List[Dict[str, object]] = []

    for idx, frame_file in enumerate(frame_files):
        data = np.load(str(frame_file))
        rgb = data["rgb"]
        depth = normalize_depth_image(data["depth"])
        k = data["k"].astype(np.float32)
        odom = data["odom"].astype(np.float32)
        bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
        h, w = bgr.shape[:2]
        if writer is None:
            fourcc = cv2.VideoWriter_fourcc(*"mp4v")
            writer = cv2.VideoWriter(str(output_video), fourcc, float(args.fps), (w, h))
            if not writer.isOpened():
                raise RuntimeError(f"failed to open video writer: {output_video}")

        results = model.predict(bgr, imgsz=args.imgsz, conf=args.conf, device=args.device, verbose=False)
        frame_detections: List[Dict[str, object]] = []
        boxes = results[0].boxes if results else None
        if boxes is not None:
            box_items = sorted(boxes, key=lambda b: float(b.conf[0].detach().cpu().item()), reverse=True)
            if args.max_boxes > 0:
                box_items = box_items[: args.max_boxes]
            for box_data in box_items:
                x0, y0, x1, y1 = [int(round(v)) for v in box_data.xyxy[0].detach().cpu().numpy().tolist()]
                conf = float(box_data.conf[0].detach().cpu().item())
                depth_m = median_depth_in_box(depth, (x0, y0, x1, y1))
                color = (0, 0, 255)
                cv2.rectangle(bgr, (x0, y0), (x1, y1), color, 2)
                if depth_m is None:
                    draw_label(bgr, f"red_sphere {conf:.2f} depth=N/A", (x0, y0 - 5), color)
                    continue

                u = 0.5 * (x0 + x1)
                v = 0.5 * (y0 + y1)
                fx, fy, cx, cy = float(k[0]), float(k[4]), float(k[2]), float(k[5])
                camera_point = np.array([(u - cx) * depth_m / fx, (v - cy) * depth_m / fy, depth_m], dtype=np.float32)
                world_point = camera_to_world(camera_point, odom)
                label1 = f"red_sphere {conf:.2f} D={depth_m:.2f}m"
                label2 = "cam=({:.2f},{:.2f},{:.2f}) world=({:.2f},{:.2f},{:.2f})".format(
                    camera_point[0],
                    camera_point[1],
                    camera_point[2],
                    world_point[0],
                    world_point[1],
                    world_point[2],
                )
                draw_label(bgr, label1, (x0, y0 - 22), color)
                draw_label(bgr, label2, (x0, y0 - 5), color)
                frame_detections.append(
                    {
                        "bbox": [x0, y0, x1, y1],
                        "confidence": round(conf, 4),
                        "distance_m": round(depth_m, 4),
                        "camera_position": [round(float(v), 4) for v in camera_point.tolist()],
                        "world_position": [round(float(v), 4) for v in world_point.tolist()],
                    }
                )

        cv2.putText(bgr, f"frame {idx + 1}/{len(frame_files)}", (10, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (255, 255, 255), 2, cv2.LINE_AA)
        writer.write(bgr)
        summary.append({"frame": idx, "stamp": float(np.asarray(data["stamp"]).reshape(-1)[0]), "detections": frame_detections})

    assert writer is not None
    writer.release()
    with open(output_video.with_suffix(".json"), "w", encoding="utf-8") as handle:
        json.dump({"video": str(output_video), "frames": summary}, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def record_frames(args: argparse.Namespace) -> None:
    import rospy
    from cv_bridge import CvBridge
    from gazebo_msgs.msg import ModelState
    from gazebo_msgs.msg import ModelStates
    from gazebo_msgs.srv import SetModelState
    from message_filters import ApproximateTimeSynchronizer, Subscriber
    from nav_msgs.msg import Odometry
    from sensor_msgs.msg import CameraInfo, Image

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    bridge = CvBridge()
    latest_odom = {"value": None}
    state = {"count": 0, "last_save": 0.0, "latest_rgb": None, "latest_depth": None, "latest_k": None, "latest_stamp": 0.0}

    def odom_cb(msg: Odometry) -> None:
        pose = msg.pose.pose
        latest_odom["value"] = np.array(
            [
                pose.position.x,
                pose.position.y,
                pose.position.z,
                pose.orientation.x,
                pose.orientation.y,
                pose.orientation.z,
                pose.orientation.w,
            ],
            dtype=np.float32,
        )

    def model_states_cb(msg: ModelStates) -> None:
        try:
            idx = msg.name.index(args.model_name)
        except ValueError:
            return
        pose = msg.pose[idx]
        latest_odom["value"] = np.array(
            [
                pose.position.x,
                pose.position.y,
                pose.position.z,
                pose.orientation.x,
                pose.orientation.y,
                pose.orientation.z,
                pose.orientation.w,
            ],
            dtype=np.float32,
        )

    def rgbd_cb(rgb_msg: Image, depth_msg: Image, camera_info: CameraInfo) -> None:
        now = time.time()
        if now - state["last_save"] < 1.0 / float(args.fps):
            return
        if latest_odom["value"] is None:
            return
        rgb = bridge.imgmsg_to_cv2(rgb_msg, desired_encoding="rgb8")
        depth = bridge.imgmsg_to_cv2(depth_msg, desired_encoding="passthrough")
        frame_path = output_dir / f"frame_{state['count']:05d}.npz"
        np.savez_compressed(
            str(frame_path),
            rgb=rgb,
            depth=depth,
            k=np.asarray(camera_info.K, dtype=np.float32),
            odom=latest_odom["value"],
            stamp=np.asarray([rgb_msg.header.stamp.to_sec()], dtype=np.float64),
        )
        state["count"] += 1
        state["last_save"] = now

    def orbit_robot() -> None:
        if args.no_orbit:
            return
        rospy.wait_for_service("/gazebo/set_model_state", timeout=20.0)
        set_state = rospy.ServiceProxy("/gazebo/set_model_state", SetModelState)
        start = time.time()
        rate = rospy.Rate(8)
        while not rospy.is_shutdown() and time.time() - start < args.duration:
            t = time.time() - start
            theta = args.start_yaw + 2.0 * math.pi * t / max(args.duration, 0.1)
            msg = ModelState()
            msg.model_name = args.model_name
            msg.pose.position.x = args.center_x + args.radius * math.cos(theta)
            msg.pose.position.y = args.center_y + args.radius * math.sin(theta)
            msg.pose.position.z = args.robot_z
            yaw = theta + math.pi
            msg.pose.orientation.z = math.sin(0.5 * yaw)
            msg.pose.orientation.w = math.cos(0.5 * yaw)
            msg.reference_frame = "world"
            try:
                set_state(msg)
            except Exception as exc:
                rospy.logwarn_throttle(2.0, "set_model_state failed: %s", exc)
            rate.sleep()

    rospy.init_node("record_yolo_depth_demo")
    rospy.Subscriber(args.odom_topic, Odometry, odom_cb, queue_size=10)
    rospy.Subscriber("/gazebo/model_states", ModelStates, model_states_cb, queue_size=10)
    sync = ApproximateTimeSynchronizer(
        [
            Subscriber(args.rgb_topic, Image),
            Subscriber(args.depth_topic, Image),
            Subscriber(args.camera_info_topic, CameraInfo),
        ],
        queue_size=10,
        slop=0.08,
    )
    sync.registerCallback(rgbd_cb)

    start = time.time()
    rate = rospy.Rate(20)
    import threading

    thread = threading.Thread(target=orbit_robot, daemon=True)
    thread.start()
    while not rospy.is_shutdown() and time.time() - start < args.duration:
        rate.sleep()

    manifest = {"frames": int(state["count"]), "duration_s": args.duration, "fps": args.fps}
    with open(output_dir / "recording_manifest.json", "w", encoding="utf-8") as handle:
        json.dump(manifest, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def parse_waypoints(value: str) -> List[Tuple[float, float]]:
    waypoints: List[Tuple[float, float]] = []
    for item in value.split(";"):
        item = item.strip()
        if not item:
            continue
        parts = [v.strip() for v in item.split(",")]
        if len(parts) != 2:
            raise ValueError(f"invalid waypoint '{item}', expected x,y")
        waypoints.append((float(parts[0]), float(parts[1])))
    if len(waypoints) < 2:
        raise ValueError("at least two waypoints are required")
    return waypoints


def yaw_from_quaternion(qx: float, qy: float, qz: float, qw: float) -> float:
    return math.atan2(2.0 * (qw * qz + qx * qy), 1.0 - 2.0 * (qy * qy + qz * qz))


def wrap_angle(angle: float) -> float:
    while angle > math.pi:
        angle -= 2.0 * math.pi
    while angle < -math.pi:
        angle += 2.0 * math.pi
    return angle


def record_route(args: argparse.Namespace) -> None:
    import rospy
    from cv_bridge import CvBridge
    from gazebo_msgs.msg import ModelStates
    from geometry_msgs.msg import Twist
    from message_filters import ApproximateTimeSynchronizer, Subscriber
    from nav_msgs.msg import Odometry
    from sensor_msgs.msg import CameraInfo, Image

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    waypoints = parse_waypoints(args.waypoints)
    bridge = CvBridge()
    latest_pose = {"value": None}
    state = {"count": 0, "last_save": 0.0, "waypoint": 0, "route_done": False}
    route_log: List[Dict[str, object]] = []

    def set_pose_from_values(x: float, y: float, z: float, qx: float, qy: float, qz: float, qw: float) -> None:
        latest_pose["value"] = np.array([x, y, z, qx, qy, qz, qw], dtype=np.float32)

    def odom_cb(msg: Odometry) -> None:
        pose = msg.pose.pose
        set_pose_from_values(
            pose.position.x,
            pose.position.y,
            pose.position.z,
            pose.orientation.x,
            pose.orientation.y,
            pose.orientation.z,
            pose.orientation.w,
        )

    def model_states_cb(msg: ModelStates) -> None:
        try:
            idx = msg.name.index(args.model_name)
        except ValueError:
            return
        pose = msg.pose[idx]
        set_pose_from_values(
            pose.position.x,
            pose.position.y,
            pose.position.z,
            pose.orientation.x,
            pose.orientation.y,
            pose.orientation.z,
            pose.orientation.w,
        )

    def rgbd_cb(rgb_msg: Image, depth_msg: Image, camera_info: CameraInfo) -> None:
        now = time.time()
        pose = latest_pose["value"]
        if pose is None or now - state["last_save"] < 1.0 / float(args.fps):
            return
        rgb = bridge.imgmsg_to_cv2(rgb_msg, desired_encoding="rgb8")
        depth = bridge.imgmsg_to_cv2(depth_msg, desired_encoding="passthrough")
        frame_path = output_dir / f"frame_{state['count']:05d}.npz"
        np.savez_compressed(
            str(frame_path),
            rgb=rgb,
            depth=depth,
            k=np.asarray(camera_info.K, dtype=np.float32),
            odom=pose,
            stamp=np.asarray([rgb_msg.header.stamp.to_sec()], dtype=np.float64),
        )
        state["count"] += 1
        state["last_save"] = now

    rospy.init_node("walk_record_yolo_depth_demo")
    cmd_pub = rospy.Publisher(args.cmd_topic, Twist, queue_size=1)
    rospy.Subscriber(args.odom_topic, Odometry, odom_cb, queue_size=10)
    rospy.Subscriber("/gazebo/model_states", ModelStates, model_states_cb, queue_size=10)
    sync = ApproximateTimeSynchronizer(
        [
            Subscriber(args.rgb_topic, Image),
            Subscriber(args.depth_topic, Image),
            Subscriber(args.camera_info_topic, CameraInfo),
        ],
        queue_size=10,
        slop=0.08,
    )
    sync.registerCallback(rgbd_cb)

    start = time.time()
    last_log = 0.0
    rate = rospy.Rate(args.control_rate)
    while not rospy.is_shutdown() and time.time() - start < args.duration:
        cmd = Twist()
        pose = latest_pose["value"]
        if pose is not None and not state["route_done"]:
            x, y, _z, qx, qy, qz, qw = [float(v) for v in pose]
            target = waypoints[min(state["waypoint"], len(waypoints) - 1)]
            dx = target[0] - x
            dy = target[1] - y
            distance = math.hypot(dx, dy)
            yaw = yaw_from_quaternion(qx, qy, qz, qw)
            target_yaw = math.atan2(dy, dx)
            yaw_error = wrap_angle(target_yaw - yaw)
            if distance < args.goal_tolerance:
                if state["waypoint"] + 1 < len(waypoints):
                    state["waypoint"] += 1
                else:
                    state["route_done"] = True
            elif abs(yaw_error) > args.turn_only_angle:
                cmd.angular.z = max(-args.max_wz, min(args.max_wz, args.k_yaw * yaw_error))
            else:
                speed = min(args.max_vx, max(args.min_vx, args.k_dist * distance))
                cmd.linear.x = speed * max(0.2, math.cos(yaw_error))
                cmd.angular.z = max(-args.max_wz, min(args.max_wz, args.k_yaw * yaw_error))

            now = time.time()
            if now - last_log > 1.0:
                route_log.append(
                    {
                        "t": round(now - start, 3),
                        "pose": [round(x, 4), round(y, 4), round(float(pose[2]), 4), round(yaw, 4)],
                        "target": [round(target[0], 4), round(target[1], 4)],
                        "distance": round(distance, 4),
                        "cmd": [round(cmd.linear.x, 4), round(cmd.angular.z, 4)],
                    }
                )
                last_log = now
        cmd_pub.publish(cmd)
        rate.sleep()

    stop = Twist()
    for _ in range(10):
        cmd_pub.publish(stop)
        time.sleep(0.05)

    manifest = {
        "frames": int(state["count"]),
        "duration_s": args.duration,
        "fps": args.fps,
        "waypoints": [[x, y] for x, y in waypoints],
        "route_done": bool(state["route_done"]),
        "last_waypoint_index": int(state["waypoint"]),
        "route_log": route_log,
    }
    with open(output_dir / "recording_manifest.json", "w", encoding="utf-8") as handle:
        json.dump(manifest, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="mode", required=True)

    record = sub.add_parser("record")
    record.add_argument("--output-dir", default="outputs/yolo_demo_frames")
    record.add_argument("--duration", type=float, default=45.0)
    record.add_argument("--fps", type=float, default=8.0)
    record.add_argument("--rgb-topic", default="/real_sense/rgb/image_raw")
    record.add_argument("--depth-topic", default="/real_sense/depth/image_raw")
    record.add_argument("--camera-info-topic", default="/real_sense/rgb/camera_info")
    record.add_argument("--odom-topic", default="/Odometry_gazebo")
    record.add_argument("--model-name", default="a1_gazebo")
    record.add_argument("--center-x", type=float, default=0.0)
    record.add_argument("--center-y", type=float, default=-2.2)
    record.add_argument("--radius", type=float, default=1.2)
    record.add_argument("--robot-z", type=float, default=0.6)
    record.add_argument("--start-yaw", type=float, default=0.0)
    record.add_argument("--no-orbit", action="store_true")

    walk = sub.add_parser("walk_record")
    walk.add_argument("--output-dir", default="outputs/yolo_demo_frames/walk_run")
    walk.add_argument("--duration", type=float, default=60.0)
    walk.add_argument("--fps", type=float, default=4.0)
    walk.add_argument("--rgb-topic", default="/real_sense/rgb/image_raw")
    walk.add_argument("--depth-topic", default="/real_sense/depth/image_raw")
    walk.add_argument("--camera-info-topic", default="/real_sense/rgb/camera_info")
    walk.add_argument("--odom-topic", default="/Odometry_gazebo")
    walk.add_argument("--cmd-topic", default="/cmd_vel")
    walk.add_argument("--model-name", default="a1_gazebo")
    walk.add_argument("--waypoints", default="0.0,-2.2;0.0,0.2;1.2,0.2;1.2,-2.2;0.0,-2.2")
    walk.add_argument("--control-rate", type=float, default=12.0)
    walk.add_argument("--goal-tolerance", type=float, default=0.35)
    walk.add_argument("--turn-only-angle", type=float, default=0.75)
    walk.add_argument("--max-vx", type=float, default=0.22)
    walk.add_argument("--min-vx", type=float, default=0.05)
    walk.add_argument("--max-wz", type=float, default=0.5)
    walk.add_argument("--k-dist", type=float, default=0.35)
    walk.add_argument("--k-yaw", type=float, default=0.9)

    render = sub.add_parser("render")
    render.add_argument("--frames-dir", default="outputs/yolo_demo_frames")
    render.add_argument("--model", default="outputs/yolo_runs/yolov10s_main/weights/best.pt")
    render.add_argument("--output-video", default="outputs/yolo_demo_videos/yolov10s_depth_demo.mp4")
    render.add_argument("--conf", type=float, default=0.18)
    render.add_argument("--imgsz", type=int, default=640)
    render.add_argument("--device", default="0")
    render.add_argument("--fps", type=float, default=8.0)
    render.add_argument("--max-boxes", type=int, default=5)
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    if args.mode == "record":
        record_frames(args)
    elif args.mode == "walk_record":
        record_route(args)
    elif args.mode == "render":
        render_video(args)
    else:
        raise ValueError(args.mode)


if __name__ == "__main__":
    main()
