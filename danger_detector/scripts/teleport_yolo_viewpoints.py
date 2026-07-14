#!/usr/bin/env python3
"""Teleport the simulated robot around truth objects for offline YOLO data capture."""

from __future__ import annotations

import argparse
import json
import math
import time
from pathlib import Path
from typing import Iterable, List, Optional

import rospy
from gazebo_msgs.msg import ModelState
from gazebo_msgs.srv import SetModelState
from geometry_msgs.msg import Pose, Quaternion


def yaw_to_quaternion(yaw: float) -> Quaternion:
    q = Quaternion()
    q.z = math.sin(yaw / 2.0)
    q.w = math.cos(yaw / 2.0)
    return q


def load_targets(truth_file: Path, include_distractors: bool) -> List[dict]:
    with open(truth_file, "r", encoding="utf-8") as handle:
        payload = json.load(handle)
    targets = list(payload.get("danger_sources", []))
    if include_distractors:
        targets.extend(payload.get("distraction_sources", []))
    return [t for t in targets if isinstance(t.get("position"), list) and len(t["position"]) == 3]


def parse_distances(value: str) -> List[float]:
    return [float(part) for part in value.split(",") if part.strip()]


def parse_args(argv: Optional[Iterable[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--truth-file", default="results/danger_truth.json")
    parser.add_argument("--robot-model-name", default="a1_gazebo")
    parser.add_argument("--views-per-target", type=int, default=8)
    parser.add_argument("--distances", default="1.2,1.8,2.6")
    parser.add_argument("--robot-z", type=float, default=0.36)
    parser.add_argument("--settle-s", type=float, default=0.35)
    parser.add_argument("--hold-s", type=float, default=0.8)
    parser.add_argument("--include-distractors", action="store_true")
    parser.add_argument("--start-angle-deg", type=float, default=0.0)
    return parser.parse_args(argv)


def main() -> int:
    args = parse_args()
    rospy.init_node("teleport_yolo_viewpoints")
    rospy.wait_for_service("/gazebo/set_model_state", timeout=20.0)
    set_model_state = rospy.ServiceProxy("/gazebo/set_model_state", SetModelState)
    targets = load_targets(Path(args.truth_file), args.include_distractors)
    distances = parse_distances(args.distances)
    if not targets:
        raise RuntimeError(f"no targets found in {args.truth_file}")

    total = 0
    for target in targets:
        tx, ty, _tz = [float(v) for v in target["position"]]
        for distance in distances:
            for view_idx in range(args.views_per_target):
                angle = math.radians(args.start_angle_deg) + (2.0 * math.pi * view_idx / args.views_per_target)
                rx = tx - math.cos(angle) * distance
                ry = ty - math.sin(angle) * distance
                yaw = math.atan2(ty - ry, tx - rx)

                state = ModelState()
                state.model_name = args.robot_model_name
                state.pose = Pose()
                state.pose.position.x = rx
                state.pose.position.y = ry
                state.pose.position.z = args.robot_z
                state.pose.orientation = yaw_to_quaternion(yaw)
                response = set_model_state(state)
                if not response.success:
                    rospy.logwarn("set_model_state failed: %s", response.status_message)
                    continue
                total += 1
                rospy.loginfo(
                    "view %d: target=%s shape=%s danger=%s robot=(%.2f, %.2f, %.2f) yaw=%.2f",
                    total,
                    target.get("model_name", target.get("id", "?")),
                    target.get("shape", "?"),
                    target.get("is_danger", "?"),
                    rx,
                    ry,
                    args.robot_z,
                    yaw,
                )
                time.sleep(args.settle_s + args.hold_s)
    rospy.loginfo("teleport viewpoint generation completed: %d views", total)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
