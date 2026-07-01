from __future__ import annotations

import argparse
import math
from pathlib import Path
import time

from building_generator_classic.control_runtime import BuildingControlRuntime
import yaml

DEFAULT_DOOR_ANIMATION_RATE_HZ = 10.0


def main(argv: list[str] | None = None) -> int:
    import rospy
    from gazebo_msgs.srv import GetModelState, SetLinkState, SetModelState
    from building_generator_interfaces.srv import (
        CallElevator,
        CallElevatorResponse,
        SetDoorState,
        SetDoorStateResponse,
    )

    args = _build_parser().parse_args(rospy.myargv(argv=argv)[1:])
    runtime = _load_runtime(args.door_config, args.elevator_config)

    rospy.init_node("building_generator_classic_control")
    robot_model_name = rospy.get_param("~robot_model_name", args.robot_model_name)
    rospy.wait_for_service("/gazebo/set_model_state")
    rospy.wait_for_service("/gazebo/set_link_state")
    rospy.wait_for_service("/gazebo/get_model_state")
    set_model_state = rospy.ServiceProxy("/gazebo/set_model_state", SetModelState)
    set_link_state = rospy.ServiceProxy("/gazebo/set_link_state", SetLinkState)
    get_model_state = rospy.ServiceProxy("/gazebo/get_model_state", GetModelState)
    rospy.Service(
        "call_elevator",
        CallElevator,
        lambda request: _handle_call_elevator(
            runtime,
            request,
            CallElevatorResponse,
            set_model_state,
            get_model_state,
            robot_model_name,
        ),
    )
    rospy.Service(
        "set_door_state",
        SetDoorState,
        lambda request: _handle_set_door_state(runtime, request, SetDoorStateResponse, set_model_state, set_link_state),
    )
    rospy.loginfo("building_generator_classic_control ready")
    rospy.spin()
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Building generator Gazebo Classic control server")
    parser.add_argument("--door-config", required=True)
    parser.add_argument("--elevator-config", required=True)
    parser.add_argument("--robot-model-name", default="a1_gazebo")
    return parser


def _load_runtime(door_config_path: str, elevator_config_path: str) -> BuildingControlRuntime:
    door_config = yaml.safe_load(Path(door_config_path).read_text()) or {}
    elevator_config = yaml.safe_load(Path(elevator_config_path).read_text()) or {}
    return BuildingControlRuntime(
        door_specs=door_config.get("doors", []),
        elevator_specs=elevator_config.get("elevators", []),
    )


def _handle_call_elevator(
    runtime: BuildingControlRuntime,
    request,
    response_type,
    set_model_state,
    get_model_state,
    robot_model_name: str,
):
    result = runtime.call_elevator(
        request.elevator_id,
        request.target_floor,
        request.open_doors,
    )
    if result.get("accepted") and result.get("target_pose"):
        _apply_model_pose(set_model_state, result["model_name"], result["target_pose"])
        _carry_robot_with_elevator(
            get_model_state,
            set_model_state,
            robot_model_name,
            result.get("previous_pose"),
            result["target_pose"],
            result.get("car_size"),
        )
    return response_type(
        accepted=bool(result["accepted"]),
        current_floor=int(result["current_floor"]),
        state=str(result["state"]),
        message=str(result["message"]),
    )


def _handle_set_door_state(runtime: BuildingControlRuntime, request, response_type, set_model_state, set_link_state):
    result = runtime.set_door_state(request.door_id, request.open)
    if result.get("accepted") and result.get("panel_poses"):
        _apply_model_pose(set_model_state, result["model_name"], result["model_pose"])
        motion_duration = float(result.get("motion_duration", 0.0) or 0.0)
        if motion_duration > 0.0:
            _animate_panel_poses(
                set_link_state,
                result["model_name"],
                result["model_pose"],
                result.get("start_panel_poses", {}) or {},
                result["panel_poses"],
                motion_duration,
            )
        else:
            _apply_panel_poses(
                set_link_state,
                result["model_name"],
                result["model_pose"],
                result["panel_poses"],
            )
    elif result.get("accepted") and result.get("target_pose"):
        _apply_model_pose(set_model_state, result["model_name"], result["target_pose"])
    return response_type(
        accepted=bool(result["accepted"]),
        state=str(result["state"]),
        message=str(result["message"]),
    )


def _apply_panel_poses(
    set_link_state,
    model_name: str,
    model_pose: list[float],
    panel_poses: dict[str, list[float] | None],
) -> None:
    for link_name, pose_values in panel_poses.items():
        if pose_values:
            _apply_link_pose(
                set_link_state,
                model_name,
                link_name,
                _compose_world_pose(model_pose, pose_values),
            )


def _animate_panel_poses(
    set_link_state,
    model_name: str,
    model_pose: list[float],
    start_panel_poses: dict[str, list[float] | None],
    target_panel_poses: dict[str, list[float] | None],
    duration: float,
) -> None:
    import rospy

    duration = max(0.0, float(duration))
    if duration <= 0.0:
        _apply_panel_poses(set_link_state, model_name, model_pose, target_panel_poses)
        return

    step_count = max(1, int(math.ceil(duration * DEFAULT_DOOR_ANIMATION_RATE_HZ)))
    step_period = duration / step_count
    link_names = sorted(target_panel_poses.keys())
    start_time = time.monotonic()
    rospy.loginfo("Animating %s door panels over %.1f seconds", model_name, duration)

    for step in range(step_count + 1):
        if rospy.is_shutdown():
            return
        ratio = step / step_count
        current_poses: dict[str, list[float]] = {}
        for link_name in link_names:
            target_pose = target_panel_poses.get(link_name)
            if not target_pose:
                continue
            start_pose = start_panel_poses.get(link_name) or target_pose
            current_poses[link_name] = _interpolate_pose(start_pose, target_pose, ratio)
        _apply_panel_poses(set_link_state, model_name, model_pose, current_poses)

        next_time = start_time + (step + 1) * step_period
        sleep_time = next_time - time.monotonic()
        if step < step_count and sleep_time > 0.0:
            rospy.sleep(sleep_time)


def _interpolate_pose(start_pose: list[float], target_pose: list[float], ratio: float) -> list[float]:
    ratio = max(0.0, min(1.0, float(ratio)))
    return [
        float(start_value) + (float(target_value) - float(start_value)) * ratio
        for start_value, target_value in zip(start_pose, target_pose)
    ]


def _apply_model_pose(set_model_state, model_name: str, pose_values: list[float]) -> None:
    from gazebo_msgs.msg import ModelState
    from geometry_msgs.msg import Pose
    from tf.transformations import quaternion_from_euler

    model_state = ModelState()
    model_state.model_name = model_name
    model_state.reference_frame = "world"
    model_state.pose = Pose()
    model_state.pose.position.x = float(pose_values[0])
    model_state.pose.position.y = float(pose_values[1])
    model_state.pose.position.z = float(pose_values[2])
    qx, qy, qz, qw = quaternion_from_euler(
        float(pose_values[3]),
        float(pose_values[4]),
        float(pose_values[5]),
    )
    model_state.pose.orientation.x = qx
    model_state.pose.orientation.y = qy
    model_state.pose.orientation.z = qz
    model_state.pose.orientation.w = qw
    set_model_state(model_state)


def _carry_robot_with_elevator(
    get_model_state,
    set_model_state,
    robot_model_name: str,
    previous_elevator_pose: list[float] | None,
    target_elevator_pose: list[float] | None,
    car_size: list[float] | None,
) -> None:
    if not previous_elevator_pose or not target_elevator_pose or not car_size:
        return

    robot_pose = _get_model_pose(get_model_state, robot_model_name)
    if robot_pose is None:
        return
    if not _pose_is_inside_elevator(robot_pose, previous_elevator_pose, car_size):
        return

    carried_pose = list(robot_pose)
    carried_pose[0] += float(target_elevator_pose[0]) - float(previous_elevator_pose[0])
    carried_pose[1] += float(target_elevator_pose[1]) - float(previous_elevator_pose[1])
    carried_pose[2] += float(target_elevator_pose[2]) - float(previous_elevator_pose[2])
    _apply_model_pose(set_model_state, robot_model_name, carried_pose)


def _get_model_pose(get_model_state, model_name: str) -> list[float] | None:
    from tf.transformations import euler_from_quaternion

    response = get_model_state(model_name, "world")
    if not getattr(response, "success", False):
        return None

    pose = response.pose
    roll, pitch, yaw = euler_from_quaternion(
        [
            pose.orientation.x,
            pose.orientation.y,
            pose.orientation.z,
            pose.orientation.w,
        ]
    )
    return [
        pose.position.x,
        pose.position.y,
        pose.position.z,
        roll,
        pitch,
        yaw,
    ]


def _pose_is_inside_elevator(
    robot_pose: list[float],
    elevator_pose: list[float],
    car_size: list[float],
) -> bool:
    margin_xy = 0.25
    margin_z = 1.3
    dx = float(robot_pose[0]) - float(elevator_pose[0])
    dy = float(robot_pose[1]) - float(elevator_pose[1])
    yaw = float(elevator_pose[5])
    cos_yaw = math.cos(-yaw)
    sin_yaw = math.sin(-yaw)
    local_x = dx * cos_yaw - dy * sin_yaw
    local_y = dx * sin_yaw + dy * cos_yaw

    half_x = float(car_size[0]) / 2.0 + margin_xy
    half_y = float(car_size[1]) / 2.0 + margin_xy
    if abs(local_x) > half_x or abs(local_y) > half_y:
        return False

    floor_z = float(elevator_pose[2]) - float(car_size[2]) / 2.0
    return floor_z - 0.2 <= float(robot_pose[2]) <= floor_z + margin_z


def _apply_link_pose(set_link_state, model_name: str, link_name: str, pose_values: list[float]) -> None:
    from gazebo_msgs.msg import LinkState
    from geometry_msgs.msg import Pose
    from tf.transformations import quaternion_from_euler

    link_state = LinkState()
    link_state.link_name = f"{model_name}::{link_name}"
    link_state.reference_frame = "world"
    link_state.pose = Pose()
    link_state.pose.position.x = float(pose_values[0])
    link_state.pose.position.y = float(pose_values[1])
    link_state.pose.position.z = float(pose_values[2])
    qx, qy, qz, qw = quaternion_from_euler(
        float(pose_values[3]),
        float(pose_values[4]),
        float(pose_values[5]),
    )
    link_state.pose.orientation.x = qx
    link_state.pose.orientation.y = qy
    link_state.pose.orientation.z = qz
    link_state.pose.orientation.w = qw
    set_link_state(link_state)


def _compose_world_pose(base_pose: list[float], local_pose: list[float]) -> list[float]:
    yaw = float(base_pose[5])
    cos_yaw = math.cos(yaw)
    sin_yaw = math.sin(yaw)
    local_x = float(local_pose[0])
    local_y = float(local_pose[1])
    return [
        float(base_pose[0]) + local_x * cos_yaw - local_y * sin_yaw,
        float(base_pose[1]) + local_x * sin_yaw + local_y * cos_yaw,
        float(base_pose[2]) + float(local_pose[2]),
        float(base_pose[3]) + float(local_pose[3]),
        float(base_pose[4]) + float(local_pose[4]),
        float(base_pose[5]) + float(local_pose[5]),
    ]

__all__ = ["main"]
