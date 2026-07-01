#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import curses
import os
import queue
import threading
import time

import rospy
import yaml
from geometry_msgs.msg import Twist
from sensor_msgs.msg import Image

from building_generator_interfaces.srv import CallElevator, SetDoorState


DEFAULT_DOOR_CONFIG = "/home/uestc/SimEnv/generated_building/door_config.yaml"
DEFAULT_ELEVATOR_CONFIG = "/home/uestc/SimEnv/generated_building/elevator_config.yaml"
DEFAULT_IMAGE_TOPICS = (
    "/camera/image_raw",
    "/camera/rgb/image_raw",
    "/real_sense/rgb/image_raw",
    "/real_sense/depth/image_raw",
)


class SharedState:
    def __init__(self, command_timeout):
        self.lock = threading.Lock()
        self.vx = 0.0
        self.vy = 0.0
        self.wz = 0.0
        self.status = "Ready"
        self.last_service = ""
        self.running = True
        self.command_timeout = command_timeout
        self.last_motion_command = 0.0

    def set_status(self, text):
        with self.lock:
            self.status = text

    def snapshot(self):
        with self.lock:
            return self.vx, self.vy, self.wz, self.status, self.last_service

    def set_velocity(self, vx=None, vy=None, wz=None):
        with self.lock:
            if vx is not None:
                self.vx = vx
            if vy is not None:
                self.vy = vy
            if wz is not None:
                self.wz = wz
            self.last_motion_command = time.monotonic()

    def adjust_velocity(self, dvx=0.0, dvy=0.0, dwz=0.0, max_vx=0.0, max_vy=0.0, max_wz=0.0):
        with self.lock:
            self.vx = clamp(self.vx + dvx, -max_vx, max_vx)
            self.vy = clamp(self.vy + dvy, -max_vy, max_vy)
            self.wz = clamp(self.wz + dwz, -max_wz, max_wz)
            self.last_motion_command = time.monotonic()

    def stop(self):
        self.set_velocity(0.0, 0.0, 0.0)

    def apply_motion_timeout(self):
        if self.command_timeout <= 0:
            return
        with self.lock:
            if self.last_motion_command and time.monotonic() - self.last_motion_command > self.command_timeout:
                self.vx = 0.0
                self.vy = 0.0
                self.wz = 0.0
                self.last_motion_command = 0.0


def load_yaml(path):
    if not path or not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as stream:
        return yaml.safe_load(stream) or {}


def load_doors(path):
    data = load_yaml(path)
    doors = []
    for item in data.get("doors", []):
        if item.get("dynamic", False):
            doors.append(
                {
                    "id": item.get("id", ""),
                    "kind": item.get("kind", ""),
                    "floor": item.get("floor_index", "?"),
                    "open": bool(item.get("initial_open", False)),
                    "busy": False,
                }
            )
    doors = [door for door in doors if door["id"]]
    return sorted(doors, key=_door_sort_key)


def clamp(value, min_value, max_value):
    return max(min_value, min(max_value, value))


def _door_sort_key(door):
    if door["id"] == "main_entrance":
        return (0, -1, door["id"])
    if door["id"].startswith("elevator_floor_"):
        try:
            return (1, int(str(door["id"]).rsplit("_", 1)[-1]), door["id"])
        except ValueError:
            pass
    return (2, int(door["floor"]) if str(door["floor"]).isdigit() else 999, door["id"])


def load_elevator(path):
    data = load_yaml(path)
    elevators = data.get("elevators", [])
    if not elevators:
        return {"id": "elevator_main", "floors": [0, 1, 2], "busy": False}
    elevator = elevators[0]
    floors = elevator.get("served_floors") or sorted(int(k) for k in elevator.get("floor_poses", {}).keys())
    return {"id": elevator.get("id", "elevator_main"), "floors": floors, "busy": False}


def call_in_thread(name, fn, state):
    def runner():
        try:
            state.set_status("%s ..." % name)
            ok, message = fn()
            suffix = "OK" if ok else "FAILED"
            with state.lock:
                state.last_service = "%s: %s %s" % (name, suffix, message)
                state.status = state.last_service
        except Exception as exc:
            with state.lock:
                state.last_service = "%s: ERROR %s" % (name, exc)
                state.status = state.last_service

    thread = threading.Thread(target=runner, daemon=True)
    thread.start()


class ServiceController:
    def __init__(self, doors, elevator, state):
        self.doors = doors
        self.elevator = elevator
        self.state = state
        self.door_proxy = rospy.ServiceProxy("/set_door_state", SetDoorState)
        self.elevator_proxy = rospy.ServiceProxy("/call_elevator", CallElevator)

    def toggle_door(self, index):
        if index < 0 or index >= len(self.doors):
            self.state.set_status("No door bound to key %d" % (index + 1))
            return
        door = self.doors[index]
        if door["busy"]:
            self.state.set_status("%s is still moving" % door["id"])
            return

        target_open = not door["open"]
        door["busy"] = True

        def work():
            try:
                resp = self.door_proxy(door["id"], target_open)
                if resp.accepted:
                    door["open"] = target_open
                return bool(resp.accepted), "%s %s" % (resp.state, resp.message)
            finally:
                door["busy"] = False

        action = "open" if target_open else "close"
        call_in_thread("door %s %s" % (door["id"], action), work, self.state)

    def call_elevator(self, floor):
        if self.elevator["busy"]:
            self.state.set_status("%s is still moving" % self.elevator["id"])
            return
        if floor not in self.elevator["floors"]:
            self.state.set_status("Elevator floor %s is not available" % floor)
            return

        self.elevator["busy"] = True

        def work():
            try:
                resp = self.elevator_proxy(self.elevator["id"], int(floor), False)
                return bool(resp.accepted), "floor=%s %s %s" % (resp.current_floor, resp.state, resp.message)
            finally:
                self.elevator["busy"] = False

        call_in_thread("call elevator to floor %s" % floor, work, self.state)


class CameraViewer:
    def __init__(self, topic, auto_topic, state):
        self.topic = topic
        self.auto_topic = auto_topic
        self.state = state
        self.bridge = None
        self.cv2 = None
        self.latest = None
        self.lock = threading.Lock()
        self.sub = None
        self.thread = None

    def start(self):
        try:
            import cv2
            from cv_bridge import CvBridge
        except Exception as exc:
            self.state.set_status("Camera disabled: %s" % exc)
            return

        self.cv2 = cv2
        self.bridge = CvBridge()
        topic = self.topic
        if self.auto_topic:
            topic = find_image_topic() or topic
        if not topic:
            self.state.set_status("Camera disabled: no sensor_msgs/Image topic found")
            return
        self.topic = topic
        self.sub = rospy.Subscriber(topic, Image, self._image_cb, queue_size=1)
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()
        self.state.set_status("Camera view: %s" % topic)

    def _image_cb(self, msg):
        try:
            if msg.encoding in ("32FC1", "16UC1", "mono16"):
                img = self.bridge.imgmsg_to_cv2(msg, desired_encoding="passthrough")
                img = self.cv2.normalize(img, None, 0, 255, self.cv2.NORM_MINMAX)
                img = img.astype("uint8")
                img = self.cv2.applyColorMap(img, self.cv2.COLORMAP_TURBO)
            else:
                img = self.bridge.imgmsg_to_cv2(msg, desired_encoding="bgr8")
            with self.lock:
                self.latest = img
        except Exception as exc:
            self.state.set_status("Camera frame error: %s" % exc)

    def _loop(self):
        window = "competition camera"
        while not rospy.is_shutdown() and self.state.running:
            with self.lock:
                img = None if self.latest is None else self.latest.copy()
            if img is not None:
                self.cv2.imshow(window, img)
            self.cv2.waitKey(20)
        try:
            self.cv2.destroyWindow(window)
        except Exception:
            pass


def find_image_topic():
    try:
        topics = rospy.get_published_topics()
    except Exception:
        return None
    images = [name for name, msg_type in topics if msg_type == "sensor_msgs/Image"]
    for preferred in DEFAULT_IMAGE_TOPICS:
        if preferred in images:
            return preferred
    for name in images:
        if "rgb" in name or "image_raw" in name:
            return name
    return images[0] if images else None


def publish_loop(pub, state, rate_hz):
    rate = rospy.Rate(rate_hz)
    while not rospy.is_shutdown() and state.running:
        state.apply_motion_timeout()
        vx, vy, wz, _, _ = state.snapshot()
        msg = Twist()
        msg.linear.x = vx
        msg.linear.y = vy
        msg.angular.z = wz
        pub.publish(msg)
        rate.sleep()


def draw_screen(stdscr, args, state, doors, elevator, camera_topic):
    stdscr.erase()
    height, width = stdscr.getmaxyx()
    vx, vy, wz, status, last_service = state.snapshot()

    lines = [
        "Competition keyboard control",
        "",
        "Motion: W/S forward/back  A/D left/right  Q/E turn  SPACE stop  X zero all",
        "Step:   +/- linear %.2f m/s   [/ ] angular %.2f rad/s" % (args.linear_step, args.angular_step),
        "Publish: %s  vx=%.2f vy=%.2f wz=%.2f" % (args.cmd_vel_topic, vx, vy, wz),
        "",
        "Doors: number keys toggle configured dynamic doors",
    ]
    for i, door in enumerate(doors[:9], start=1):
        state_text = "moving" if door["busy"] else ("open" if door["open"] else "closed")
        lines.append("  %d: %-22s floor=%s kind=%s state=%s" % (i, door["id"], door["floor"], door["kind"], state_text))

    floors = ", ".join("%d(F%d)" % (f, f + 1) for f in elevator["floors"])
    lines.extend(
        [
            "",
            "Elevator: F1/F2/F3... call %s to floor index: %s" % (elevator["id"], floors),
            "Camera: %s" % (camera_topic or "disabled"),
            "",
            "Status: %s" % status,
            "Last service: %s" % last_service,
            "",
            "Press H for help refresh, ESC or Ctrl-C to quit.",
        ]
    )

    for row, line in enumerate(lines[: height - 1]):
        stdscr.addnstr(row, 0, line, max(1, width - 1))
    stdscr.refresh()


def curses_main(stdscr, args, state, doors, elevator, camera_topic):
    curses.curs_set(0)
    stdscr.nodelay(True)
    stdscr.keypad(True)
    services = ServiceController(doors, elevator, state)

    last_draw = 0.0
    while not rospy.is_shutdown() and state.running:
        key = stdscr.getch()
        if key != -1:
            if key in (27, 3):
                state.running = False
                break
            elif key in (ord("w"), ord("W"), curses.KEY_UP):
                state.adjust_velocity(dvx=args.linear_step, max_vx=args.max_linear_speed, max_vy=args.max_linear_speed, max_wz=args.max_angular_speed)
            elif key in (ord("s"), ord("S"), curses.KEY_DOWN):
                state.adjust_velocity(dvx=-args.linear_step, max_vx=args.max_linear_speed, max_vy=args.max_linear_speed, max_wz=args.max_angular_speed)
            elif key in (ord("a"), ord("A"), curses.KEY_LEFT):
                state.adjust_velocity(dvy=args.linear_step, max_vx=args.max_linear_speed, max_vy=args.max_linear_speed, max_wz=args.max_angular_speed)
            elif key in (ord("d"), ord("D"), curses.KEY_RIGHT):
                state.adjust_velocity(dvy=-args.linear_step, max_vx=args.max_linear_speed, max_vy=args.max_linear_speed, max_wz=args.max_angular_speed)
            elif key in (ord("q"), ord("Q")):
                state.adjust_velocity(dwz=args.angular_step, max_vx=args.max_linear_speed, max_vy=args.max_linear_speed, max_wz=args.max_angular_speed)
            elif key in (ord("e"), ord("E")):
                state.adjust_velocity(dwz=-args.angular_step, max_vx=args.max_linear_speed, max_vy=args.max_linear_speed, max_wz=args.max_angular_speed)
            elif key in (ord(" "), ord("x"), ord("X")):
                state.stop()
            elif key in (ord("+"), ord("=")):
                args.linear_step = min(args.linear_step + 0.05, args.max_linear_speed)
            elif key in (ord("-"), ord("_")):
                args.linear_step = max(args.linear_step - 0.05, 0.05)
            elif key == ord("["):
                args.angular_step = max(args.angular_step - 0.05, 0.05)
            elif key == ord("]"):
                args.angular_step = min(args.angular_step + 0.05, args.max_angular_speed)
            elif ord("1") <= key <= ord("9"):
                services.toggle_door(key - ord("1"))
            elif curses.KEY_F1 <= key <= curses.KEY_F12:
                floor_index = key - curses.KEY_F1
                services.call_elevator(floor_index)

        now = time.time()
        if now - last_draw > 0.1:
            draw_screen(stdscr, args, state, doors, elevator, camera_topic)
            last_draw = now
        time.sleep(0.02)


def parse_args():
    parser = argparse.ArgumentParser(description="Keyboard competition control for A1 simulation.")
    parser.add_argument("--cmd-vel-topic", default="/cmd_vel")
    parser.add_argument("--door-config", default=DEFAULT_DOOR_CONFIG)
    parser.add_argument("--elevator-config", default=DEFAULT_ELEVATOR_CONFIG)
    parser.add_argument("--linear-step", type=float, default=0.2)
    parser.add_argument("--angular-step", type=float, default=0.3)
    parser.add_argument("--linear-speed", dest="linear_step", type=float, help=argparse.SUPPRESS)
    parser.add_argument("--angular-speed", dest="angular_step", type=float, help=argparse.SUPPRESS)
    parser.add_argument("--max-linear-speed", type=float, default=2.5)
    parser.add_argument("--max-angular-speed", type=float, default=3.0)
    parser.add_argument(
        "--command-timeout",
        type=float,
        default=0.0,
        help="Seconds before motion command returns to zero if no movement key is repeated. Default 0 keeps the command latched until changed or stopped.",
    )
    parser.add_argument("--rate", type=float, default=20.0)
    parser.add_argument("--show-camera", action="store_true", help="Open an OpenCV window for the robot camera.")
    parser.add_argument("--image-topic", default="", help="Camera image topic. Empty means auto-detect when --show-camera is used.")
    return parser.parse_args(rospy.myargv()[1:])


def main():
    args = parse_args()
    rospy.init_node("keyboard_competition_control", anonymous=False)

    state = SharedState(args.command_timeout)
    doors = load_doors(args.door_config)
    elevator = load_elevator(args.elevator_config)
    pub = rospy.Publisher(args.cmd_vel_topic, Twist, queue_size=1)

    publisher = threading.Thread(target=publish_loop, args=(pub, state, args.rate), daemon=True)
    publisher.start()

    viewer = None
    camera_topic = ""
    if args.show_camera:
        viewer = CameraViewer(args.image_topic, not bool(args.image_topic), state)
        viewer.start()
        camera_topic = viewer.topic

    try:
        curses.wrapper(curses_main, args, state, doors, elevator, camera_topic)
    finally:
        state.running = False
        state.stop()
        zero = Twist()
        for _ in range(5):
            pub.publish(zero)
            time.sleep(0.03)


if __name__ == "__main__":
    main()
