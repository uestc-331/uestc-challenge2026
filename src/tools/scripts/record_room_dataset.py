#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import os
import pty
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import rospy
from cv_bridge import CvBridge
from geometry_msgs.msg import Twist
from nav_msgs.msg import Odometry
from sensor_msgs.msg import Image

from building_generator_interfaces.srv import SetDoorState


WORKSPACE = Path("/home/uestc/SimEnv")
DEFAULT_DATASET_DIR = WORKSPACE / "datasets" / "room_objects"
DEFAULT_CAMERA_TOPIC = "/camera/image_raw"


def angle_wrap(value: float) -> float:
    return math.atan2(math.sin(value), math.cos(value))


def yaw_from_quaternion(q) -> float:
    siny_cosp = 2.0 * (q.w * q.z + q.x * q.y)
    cosy_cosp = 1.0 - 2.0 * (q.y * q.y + q.z * q.z)
    return math.atan2(siny_cosp, cosy_cosp)


def clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


def append_log(log_path: Path, message: str) -> None:
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    with log_path.open("a", encoding="utf-8") as f:
        f.write(f"[{timestamp}] {message}\n")
        f.flush()


class AutoProcess:
    def __init__(self, workspace: Path, log_path: Path, env: dict[str, str]):
        self.workspace = workspace
        self.log_path = log_path
        self.env = env
        self.proc: subprocess.Popen | None = None
        self.master_fd: int | None = None
        self.reader_thread: threading.Thread | None = None
        self._stop_reader = threading.Event()

    def start(self) -> None:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self.log_path.write_text("", encoding="utf-8")
        master_fd, slave_fd = pty.openpty()
        self.master_fd = master_fd
        command = "source /opt/ros/noetic/setup.bash && source devel/setup.bash && ./auto.sh"
        self.proc = subprocess.Popen(
            ["bash", "-lc", command],
            cwd=str(self.workspace),
            env={**os.environ, **self.env},
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            preexec_fn=os.setsid,
            close_fds=True,
        )
        os.close(slave_fd)
        self.reader_thread = threading.Thread(target=self._reader, daemon=True)
        self.reader_thread.start()

    def send_text(self, text: str) -> None:
        if self.master_fd is None:
            return
        os.write(self.master_fd, text.encode("utf-8"))

    def stop(self) -> None:
        self._stop_reader.set()
        if self.proc and self.proc.poll() is None:
            try:
                os.killpg(os.getpgid(self.proc.pid), signal.SIGINT)
                self.proc.wait(timeout=8.0)
            except Exception:
                try:
                    os.killpg(os.getpgid(self.proc.pid), signal.SIGTERM)
                    self.proc.wait(timeout=5.0)
                except Exception:
                    try:
                        os.killpg(os.getpgid(self.proc.pid), signal.SIGKILL)
                    except Exception:
                        pass
        if self.master_fd is not None:
            try:
                os.close(self.master_fd)
            except OSError:
                pass
            self.master_fd = None
        cleanup_simulation_processes()

    def _reader(self) -> None:
        with self.log_path.open("ab") as log_file:
            while not self._stop_reader.is_set():
                try:
                    data = os.read(self.master_fd, 4096) if self.master_fd is not None else b""
                except OSError:
                    break
                if not data:
                    break
                log_file.write(data)
                log_file.flush()

    def wait_for_log_text(self, needle: str, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                if needle in self.log_path.read_text(encoding="utf-8", errors="ignore"):
                    return True
            except Exception:
                pass
            time.sleep(0.2)
        return False

    def read_log_text(self) -> str:
        try:
            return self.log_path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            return ""


class RoscoreProcess:
    def __init__(self, log_path: Path):
        self.log_path = log_path
        self.proc: subprocess.Popen | None = None

    def ensure(self, timeout: float = 30.0) -> None:
        if master_is_available():
            return
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        log_file = self.log_path.open("ab")
        self.proc = subprocess.Popen(["roscore"], stdout=log_file, stderr=log_file, preexec_fn=os.setsid)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if master_is_available():
                return
            time.sleep(0.2)
        raise TimeoutError("roscore did not become available")

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            try:
                os.killpg(os.getpgid(self.proc.pid), signal.SIGINT)
                self.proc.wait(timeout=5.0)
            except Exception:
                try:
                    os.killpg(os.getpgid(self.proc.pid), signal.SIGTERM)
                except Exception:
                    pass


def master_is_available() -> bool:
    try:
        import rosgraph

        rosgraph.Master("/record_room_dataset_probe").getPid()
        return True
    except Exception:
        return False


def wait_for_master(timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if master_is_available():
            return
        time.sleep(0.2)
    raise TimeoutError("ROS master did not become available")


def cleanup_simulation_processes() -> None:
    patterns = [
        "[r]oslaunch unitree_guide multi_floor_gazeboSim.launch",
        "[b]uilding_generator_classic_control",
        "[j]unior_ctrl",
        "[s]tate_from_gazebo",
        "[p]ointcloud2livox.py",
        "[r]obot_state_publisher",
        "[s]pawner joint_state_controller",
    ]
    names = ["gzserver", "gzclient", "gazebo"]
    for pattern in patterns:
        subprocess.run(["pkill", "-f", pattern], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for name in names:
        subprocess.run(["pkill", "-x", name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


class OdomTracker:
    def __init__(self, topic: str):
        self.lock = threading.Lock()
        self.msg: Odometry | None = None
        self.sub = rospy.Subscriber(topic, Odometry, self._callback, queue_size=1)

    def _callback(self, msg: Odometry) -> None:
        with self.lock:
            self.msg = msg

    def get_pose(self) -> tuple[float, float, float] | None:
        with self.lock:
            msg = self.msg
        if msg is None:
            return None
        p = msg.pose.pose.position
        yaw = yaw_from_quaternion(msg.pose.pose.orientation)
        return float(p.x), float(p.y), yaw

    def wait(self, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        while not rospy.is_shutdown() and time.monotonic() < deadline:
            if self.get_pose() is not None:
                return True
            rospy.sleep(0.1)
        return False


class CmdVelDriver:
    def __init__(self, topic: str, rate_hz: float):
        self.pub = rospy.Publisher(topic, Twist, queue_size=1)
        self.lock = threading.Lock()
        self.cmd = Twist()
        self.running = True
        self.period = 1.0 / rate_hz
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()

    def set(self, vx: float, vy: float, wz: float) -> None:
        msg = Twist()
        msg.linear.x = vx
        msg.linear.y = vy
        msg.angular.z = wz
        with self.lock:
            self.cmd = msg

    def stop(self) -> None:
        self.set(0.0, 0.0, 0.0)
        for _ in range(8):
            self.pub.publish(Twist())
            rospy.sleep(0.03)

    def shutdown(self) -> None:
        self.running = False
        self.stop()

    def _loop(self) -> None:
        while not rospy.is_shutdown() and self.running:
            with self.lock:
                msg = self.cmd
            self.pub.publish(msg)
            time.sleep(self.period)


class CameraRecorder:
    def __init__(self, topic: str, jpeg_quality: int):
        self.bridge = CvBridge()
        self.topic = topic
        self.jpeg_quality = jpeg_quality
        self.lock = threading.Lock()
        self.recording = False
        self.output_dir: Path | None = None
        self.frame_index = 0
        self.saved_frames: list[dict[str, object]] = []
        self.cv2 = None
        self.sub = rospy.Subscriber(topic, Image, self._callback, queue_size=1)

    def start(self, output_dir: Path) -> None:
        import cv2

        output_dir.mkdir(parents=True, exist_ok=True)
        with self.lock:
            self.cv2 = cv2
            self.output_dir = output_dir
            self.frame_index = 0
            self.saved_frames = []
            self.recording = True

    def stop(self) -> list[dict[str, object]]:
        with self.lock:
            self.recording = False
            return list(self.saved_frames)

    def _callback(self, msg: Image) -> None:
        with self.lock:
            if not self.recording or self.output_dir is None or self.cv2 is None:
                return
            output_dir = self.output_dir
            frame_index = self.frame_index
            self.frame_index += 1
            cv2 = self.cv2
        try:
            image = self.bridge.imgmsg_to_cv2(msg, desired_encoding="bgr8")
            filename = f"frame_{frame_index:06d}.jpg"
            path = output_dir / filename
            cv2.imwrite(str(path), image, [int(cv2.IMWRITE_JPEG_QUALITY), self.jpeg_quality])
            entry = {
                "file": filename,
                "stamp": float(msg.header.stamp.to_sec()),
                "frame_id": msg.header.frame_id,
            }
            with self.lock:
                self.saved_frames.append(entry)
        except Exception as exc:
            rospy.logwarn("failed to save camera frame: %s", exc)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Record room-object camera dataset from generated SimEnv rooms.")
    parser.add_argument("--runs", type=int, required=True, help="How many full simulation/data-recording runs to execute.")
    parser.add_argument("--output-dir", default=str(DEFAULT_DATASET_DIR), help="Dataset output directory.")
    parser.add_argument("--workspace", default=str(WORKSPACE), help="SimEnv workspace path.")
    parser.add_argument("--seed-base", type=int, default=1000, help="Seed base; run index is added to it.")
    parser.add_argument("--camera-topic", default=DEFAULT_CAMERA_TOPIC)
    parser.add_argument("--odom-topic", default="/Odometry_gazebo")
    parser.add_argument("--cmd-vel-topic", default="/cmd_vel")
    parser.add_argument("--room-count", type=int, default=10)
    parser.add_argument("--gui", dest="gui", action="store_true", default=True, help="Start Gazebo with GUI. Enabled by default.")
    parser.add_argument("--no-gui", dest="gui", action="store_false", help="Start Gazebo headless.")
    parser.add_argument("--approach-offset", type=float, default=0.75, help="Meters from room door into corridor.")
    parser.add_argument("--position-tolerance", type=float, default=0.35)
    parser.add_argument("--yaw-tolerance", type=float, default=0.18)
    parser.add_argument("--max-linear-speed", type=float, default=1.00)
    parser.add_argument("--max-lateral-speed", type=float, default=0.60)
    parser.add_argument("--max-angular-speed", type=float, default=1.40)
    parser.add_argument("--goal-timeout", type=float, default=180.0)
    parser.add_argument("--startup-timeout", type=float, default=240.0)
    parser.add_argument("--sweep-deg", type=float, default=80.0)
    parser.add_argument("--settle-time", type=float, default=0.8)
    parser.add_argument("--jpeg-quality", type=int, default=92)
    return parser.parse_args(rospy.myargv()[1:])


def simulation_env(args, run_index: int) -> dict[str, str]:
    return {
        "SEED": str(args.seed_base + run_index - 1),
        "FLOOR_COUNT": "1",
        "ROOMS_PER_FLOOR": str(args.room_count),
        "GUI": "true" if args.gui else "false",
        "PAUSED": "false",
        "SIM_FAST": "1",
        "ENABLE_REALSENSE": "true",
        "ENABLE_LIVOX": "true",
        "ENABLE_LIVOX_CONVERTER": "1",
        "ENABLE_CAMERA": "true",
        "DANGER_COUNT": "30:50",
        "DISTRACTOR_COUNT": "30:50",
        "START_CONTROLLER": "1",
        "CONTROLLER_FOREGROUND": "1",
        "START_BUILDING_CONTROL": "1",
        "START_JOY_NODE": "0",
        "START_VIRTUAL_JOY": "0",
        "RESET_ROS_MASTER": "0",
        "ROBOT_X": "0.0",
        "ROBOT_Y": "-1.5",
        "ROBOT_Z": "0.6",
        "ROBOT_YAW": "1.5708",
    }


def wait_for_ros_ready(args, odom: OdomTracker) -> None:
    deadline = time.monotonic() + args.startup_timeout
    while not rospy.is_shutdown() and time.monotonic() < deadline:
        try:
            rospy.wait_for_service("/set_door_state", timeout=2.0)
            if odom.wait(timeout=2.0):
                return
        except Exception:
            pass
        rospy.sleep(0.5)
    raise TimeoutError("simulation did not expose /set_door_state and odometry in time")


def load_floor_rooms(workspace: Path, expected_count: int) -> list[dict[str, object]]:
    path = workspace / "generated_building" / "layout_metadata.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    rooms = []
    for floor in data.get("floors", []):
        if int(floor.get("floor_index", -1)) != 0:
            continue
        rooms.extend(floor.get("rooms", []))
    rooms.sort(key=lambda item: (float(item["door_pose"][1]), 0 if item.get("side") == "left" else 1))
    if len(rooms) < expected_count:
        raise RuntimeError(f"expected at least {expected_count} rooms on floor 0, got {len(rooms)}")
    return rooms[:expected_count]


def room_goal(room: dict[str, object], approach_offset: float) -> tuple[float, float, float]:
    door_pose = room["door_pose"]
    door_x = float(door_pose[0])
    door_y = float(door_pose[1])
    side = str(room.get("side", "left"))
    if side == "left":
        return door_x + approach_offset, door_y, math.pi
    return door_x - approach_offset, door_y, 0.0


def corridor_goal_y(room: dict[str, object]) -> float:
    return float(room["door_pose"][1])


def drive_forward_to_y(args, driver: CmdVelDriver, odom: OdomTracker, target_y: float, heading_yaw: float) -> None:
    deadline = time.monotonic() + args.goal_timeout
    stable_since = None
    while not rospy.is_shutdown() and time.monotonic() < deadline:
        pose = odom.get_pose()
        if pose is None:
            rospy.sleep(0.05)
            continue
        _, y, yaw = pose
        dy = target_y - y
        yaw_error = angle_wrap(heading_yaw - yaw)
        if abs(dy) <= args.position_tolerance:
            if stable_since is None:
                stable_since = time.monotonic()
            if time.monotonic() - stable_since >= 0.3:
                driver.stop()
                return
        else:
            stable_since = None

        world_vy = clamp(0.85 * dy, -args.max_linear_speed, args.max_linear_speed)
        if abs(dy) < 0.8:
            world_vy = clamp(world_vy, -0.25, 0.25)
        cos_yaw = math.cos(yaw)
        sin_yaw = math.sin(yaw)
        vx = sin_yaw * world_vy
        vy = cos_yaw * world_vy
        wz = clamp(1.2 * yaw_error, -args.max_angular_speed, args.max_angular_speed)
        driver.set(vx, vy, wz)
        rospy.sleep(0.05)
    driver.stop()
    raise TimeoutError(f"failed to drive along corridor to y={target_y:.2f}")


def drive_x_with_forward_motion(
    args,
    driver: CmdVelDriver,
    odom: OdomTracker,
    target_x: float,
    final_yaw: float,
) -> None:
    """Align x by driving forward/backward instead of relying on body-y motion.

    The RL policy is noticeably more reliable with body-frame linear.x than
    with a pure body-frame linear.y command.  In the corridor, first rotate
    toward +/- world X, drive the measured X error, then restore the corridor
    heading.
    """
    pose = odom.get_pose()
    if pose is None:
        raise TimeoutError("odometry not available before x alignment")

    current_x = pose[0]
    dx = target_x - current_x
    rospy.loginfo("x alignment start: current_x=%.3f target_x=%.3f error=%.3f", current_x, target_x, dx)
    if abs(dx) <= args.position_tolerance:
        rotate_to(args, driver, odom, final_yaw)
        return

    travel_yaw = 0.0 if dx > 0.0 else math.pi
    rotate_to(args, driver, odom, travel_yaw)
    drive_along_heading_distance(
        args,
        driver,
        odom,
        travel_yaw,
        abs(dx),
        timeout=args.goal_timeout,
        speed_limit=min(args.max_linear_speed, 0.6),
    )

    pose = odom.get_pose()
    if pose is not None:
        rospy.loginfo(
            "x alignment finished: current_x=%.3f target_x=%.3f error=%.3f",
            pose[0], target_x, target_x - pose[0],
        )
    rotate_to(args, driver, odom, final_yaw)


def drive_along_heading_distance(
    args,
    driver: CmdVelDriver,
    odom: OdomTracker,
    heading_yaw: float,
    distance_m: float,
    *,
    timeout: float | None = None,
    speed_limit: float | None = None,
) -> None:
    deadline = time.monotonic() + (timeout if timeout is not None else args.goal_timeout)
    pose = odom.get_pose()
    if pose is None:
        raise TimeoutError("odometry not available for heading-distance drive")

    start_x, start_y, _ = pose
    target_distance = float(distance_m)
    speed_cap = speed_limit if speed_limit is not None else args.max_linear_speed
    stable_since = None

    while not rospy.is_shutdown() and time.monotonic() < deadline:
        pose = odom.get_pose()
        if pose is None:
            rospy.sleep(0.05)
            continue

        x, y, yaw = pose
        travel = (x - start_x) * math.cos(heading_yaw) + (y - start_y) * math.sin(heading_yaw)
        remaining = target_distance - travel
        yaw_error = angle_wrap(heading_yaw - yaw)

        if abs(remaining) <= args.position_tolerance:
            if stable_since is None:
                stable_since = time.monotonic()
            if time.monotonic() - stable_since >= 0.3:
                driver.stop()
                return
        else:
            stable_since = None

        vx = clamp(1.1 * remaining, -speed_cap, speed_cap)
        if abs(remaining) < 0.5:
            vx = clamp(vx, -0.28, 0.28)
        wz = clamp(1.4 * yaw_error, -args.max_angular_speed, args.max_angular_speed)
        driver.set(vx, 0.0, wz)
        rospy.sleep(0.05)

    driver.stop()
    raise TimeoutError(f"failed to move {distance_m:.2f}m along heading {heading_yaw:.2f}")


def drive_to_goal(args, driver: CmdVelDriver, odom: OdomTracker, gx: float, gy: float, gyaw: float) -> None:
    deadline = time.monotonic() + args.goal_timeout
    stable_since = None
    while not rospy.is_shutdown() and time.monotonic() < deadline:
        pose = odom.get_pose()
        if pose is None:
            rospy.sleep(0.05)
            continue
        x, y, yaw = pose
        dx = gx - x
        dy = gy - y
        distance = math.hypot(dx, dy)
        yaw_error = angle_wrap(gyaw - yaw)
        if distance <= args.position_tolerance and abs(yaw_error) <= args.yaw_tolerance:
            if stable_since is None:
                stable_since = time.monotonic()
            if time.monotonic() - stable_since >= 0.4:
                driver.stop()
                return
        else:
            stable_since = None

        cos_yaw = math.cos(yaw)
        sin_yaw = math.sin(yaw)
        body_x = cos_yaw * dx + sin_yaw * dy
        body_y = -sin_yaw * dx + cos_yaw * dy
        vx = clamp(0.75 * body_x, -args.max_linear_speed, args.max_linear_speed)
        vy = clamp(0.75 * body_y, -args.max_lateral_speed, args.max_lateral_speed)
        wz = clamp(1.4 * yaw_error, -args.max_angular_speed, args.max_angular_speed)
        if distance < 0.6:
            vx = clamp(vx, -0.25, 0.25)
            vy = clamp(vy, -0.20, 0.20)
        driver.set(vx, vy, wz)
        rospy.sleep(0.05)
    driver.stop()
    raise TimeoutError(f"failed to reach goal x={gx:.2f} y={gy:.2f} yaw={gyaw:.2f}")


def rotate_to(args, driver: CmdVelDriver, odom: OdomTracker, target_yaw: float, timeout: float = 20.0) -> None:
    deadline = time.monotonic() + timeout
    stable_since = None
    while not rospy.is_shutdown() and time.monotonic() < deadline:
        pose = odom.get_pose()
        if pose is None:
            rospy.sleep(0.05)
            continue
        error = angle_wrap(target_yaw - pose[2])
        if abs(error) <= args.yaw_tolerance:
            if stable_since is None:
                stable_since = time.monotonic()
            if time.monotonic() - stable_since >= 0.25:
                driver.stop()
                return
        else:
            stable_since = None
        driver.set(0.0, 0.0, clamp(1.8 * error, -args.max_angular_speed, args.max_angular_speed))
        rospy.sleep(0.05)
    driver.stop()
    raise TimeoutError(f"failed to rotate to yaw={target_yaw:.2f}")


def open_main_door() -> None:
    rospy.wait_for_service("/set_door_state", timeout=20.0)
    proxy = rospy.ServiceProxy("/set_door_state", SetDoorState)
    response = proxy("main_entrance", True)
    if not response.accepted:
        raise RuntimeError(f"main door open rejected: {response.state} {response.message}")


def enter_rl_mode(args, proc: AutoProcess) -> None:
    if not proc.wait_for_log_text("load model is successed!", timeout=args.startup_timeout):
        raise TimeoutError("junior_ctrl did not report model load completion")

    proc.send_text("2")
    if not proc.wait_for_log_text("Switched from passive to fixed stand", timeout=20.0):
        raise TimeoutError("controller did not switch from passive to fixed stand")

    last_log = proc.read_log_text()
    for attempt in range(1, 6):
        proc.send_text("6")
        deadline = time.monotonic() + 12.0
        while time.monotonic() < deadline:
            current_log = proc.read_log_text()
            if "Switched from fixed stand to RL" in current_log:
                time.sleep(1.0)
                return
            if "stop the controller" in current_log and "stop the controller" not in last_log:
                raise RuntimeError("junior_ctrl exited while switching to RL mode")
            time.sleep(0.25)
        last_log = proc.read_log_text()
        print(f"[RL retry] attempt {attempt}/5 did not switch to RL yet, retrying 6")

    raise TimeoutError("controller did not switch from fixed stand to RL")


def record_room(
    args,
    driver: CmdVelDriver,
    odom: OdomTracker,
    camera: CameraRecorder,
    room,
    room_dir: Path,
    log_path: Path,
) -> dict[str, object]:
    gx, gy, base_yaw = room_goal(room, args.approach_offset)
    room_id = room.get("id", "unknown")
    append_log(log_path, f"[room {room_id}] approach corridor y={gy:.2f}")
    rotate_to(args, driver, odom, math.pi / 2.0)
    drive_forward_to_y(args, driver, odom, corridor_goal_y(room), math.pi / 2.0)
    append_log(log_path, f"[room {room_id}] rotate to base yaw={base_yaw:.2f}")
    rotate_to(args, driver, odom, base_yaw)
    append_log(log_path, f"[room {room_id}] drive into room 2.0m")
    drive_along_heading_distance(args, driver, odom, base_yaw, 2.0, speed_limit=args.max_linear_speed)
    rospy.sleep(args.settle_time)

    sweep = math.radians(args.sweep_deg)
    append_log(log_path, f"[room {room_id}] start camera sweep")
    camera.start(room_dir)
    started_at = time.time()
    for yaw in (base_yaw - sweep, base_yaw + sweep, base_yaw):
        rotate_to(args, driver, odom, yaw)
        rospy.sleep(0.25)
    frames = camera.stop()
    append_log(log_path, f"[room {room_id}] retreat from room 2.0m")
    drive_along_heading_distance(args, driver, odom, base_yaw, -2.0, speed_limit=min(args.max_linear_speed, 0.6))
    append_log(log_path, f"[room {room_id}] reposition to record goal x={gx:.2f} y={gy:.2f}")
    rotate_to(args, driver, odom, math.pi / 2.0)
    drive_x_with_forward_motion(args, driver, odom, gx, math.pi / 2.0)
    ended_at = time.time()
    metadata = {
        "room_id": room.get("id"),
        "room_type": room.get("room_type"),
        "side": room.get("side"),
        "door_pose": room.get("door_pose"),
        "record_goal": [gx, gy, base_yaw],
        "sweep_deg": args.sweep_deg,
        "started_at": started_at,
        "ended_at": ended_at,
        "frame_count": len(frames),
        "frames": frames,
    }
    (room_dir / "metadata.json").write_text(json.dumps(metadata, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return metadata


class RosComms:
    def __init__(self, args):
        if not rospy.core.is_initialized():
            rospy.init_node("record_room_dataset", anonymous=False, disable_signals=True)
        self.odom = OdomTracker(args.odom_topic)
        self.driver = CmdVelDriver(args.cmd_vel_topic, rate_hz=20.0)
        self.camera = CameraRecorder(args.camera_topic, args.jpeg_quality)

    def shutdown(self) -> None:
        self.driver.shutdown()


def run_once(args, run_index: int, comms: RosComms) -> dict[str, object]:
    workspace = Path(args.workspace)
    output_dir = Path(args.output_dir)
    run_dir = output_dir / f"run_{run_index:03d}"
    if run_dir.exists():
        subprocess.run(["rm", "-rf", str(run_dir)], check=False)
    run_dir.mkdir(parents=True, exist_ok=True)
    auto_log_path = run_dir / "auto.log"
    proc = AutoProcess(workspace, auto_log_path, simulation_env(args, run_index))
    room_results = []
    try:
        append_log(auto_log_path, f"[run {run_index}/{args.runs}] starting simulation")
        proc.start()
        wait_for_master(args.startup_timeout)
        wait_for_ros_ready(args, comms.odom)
        append_log(auto_log_path, f"[run {run_index}/{args.runs}] waiting for junior_ctrl ready and entering RL mode")
        enter_rl_mode(args, proc)
        open_main_door()
        rooms = load_floor_rooms(workspace, args.room_count)
        append_log(auto_log_path, f"[run {run_index}/{args.runs}] main door open, recording {len(rooms)} rooms")
        for index, room in enumerate(rooms, start=1):
            room_dir = run_dir / f"room_{index:02d}_{room.get('id', 'unknown')}"
            append_log(auto_log_path, f"[run {run_index}/{args.runs}] recording room {index}/{len(rooms)}: {room.get('id')}")
            result = record_room(args, comms.driver, comms.odom, comms.camera, room, room_dir, auto_log_path)
            room_results.append(result)
            append_log(
                auto_log_path,
                f"[run {run_index}/{args.runs}] finished room {index}/{len(rooms)}: "
                f"{room.get('id')} frames={result['frame_count']}",
            )
        summary = {
            "run_index": run_index,
            "seed": args.seed_base + run_index - 1,
            "room_count": len(room_results),
            "rooms": room_results,
        }
        (run_dir / "run_summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        append_log(auto_log_path, f"[run {run_index}/{args.runs}] completed")
        return summary
    except Exception as exc:
        (run_dir / "error.log").write_text(f"{type(exc).__name__}: {exc}\n", encoding="utf-8")
        raise
    finally:
        comms.driver.stop()
        proc.stop()


def main() -> int:
    args = build_parser()
    if args.runs <= 0:
        raise SystemExit("--runs must be positive")
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    cleanup_simulation_processes()
    roscore = RoscoreProcess(output_dir / "roscore.log")
    roscore.ensure()
    comms = RosComms(args)
    summaries = []
    try:
        for run_index in range(1, args.runs + 1):
            summaries.append(run_once(args, run_index, comms))
            print(f"已完成 {run_index}/{args.runs} 次录制")
    finally:
        comms.shutdown()
        roscore.stop()
    summary_path = output_dir / "dataset_summary.json"
    summary_path.write_text(json.dumps({"runs": summaries}, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"全部完成，数据保存在: {Path(args.output_dir).resolve()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
