#!/usr/bin/env python3
import math
import os
import signal
import subprocess
import threading
import time

import rospy
import tf.transformations as tft
from building_generator_interfaces.srv import CallElevator, SetDoorState
from geometry_msgs.msg import Twist
from nav_msgs.msg import Odometry, Path
from std_msgs.msg import Empty


def clamp(value, low, high):
    return max(low, min(high, value))


def wrap_pi(value):
    return math.atan2(math.sin(value), math.cos(value))


class MultiFloorMission:
    def __init__(self):
        self.odom_topic = rospy.get_param("~odom_topic", "/Odometry_gazebo")
        self.cmd_vel_topic = rospy.get_param("~cmd_vel_topic", "/cmd_vel")
        self.finish_topic = rospy.get_param("~finish_topic", "/planning/finish")
        self.trigger_topic = rospy.get_param("~trigger_topic", "/waypoint_generator/waypoints")

        self.elevator_id = rospy.get_param("~elevator_id", "elevator_main")
        self.door_prefix = rospy.get_param("~door_prefix", "elevator_floor_")
        self.start_floor = int(rospy.get_param("~start_floor", 0))
        self.floor_change_min_z = rospy.get_param("~floor_change_min_z", 1.0)

        # Absolute world-frame navigation targets. Tune these for the generated building.
        self.elevator_inside_x = rospy.get_param("~elevator_inside_x", 0.0)
        self.elevator_inside_y = rospy.get_param("~elevator_inside_y", 3.0)
        self.elevator_inside_yaw = rospy.get_param("~elevator_inside_yaw", 0.0)
        self.floor_exit_x = rospy.get_param("~floor_exit_x", 0.8)
        self.floor_exit_y = rospy.get_param("~floor_exit_y", 2.62)
        self.floor_exit_yaw = rospy.get_param("~floor_exit_yaw", 0.0)
        self.floor0_final_x = rospy.get_param("~floor0_final_x", self.floor_exit_x)
        self.floor0_final_y = rospy.get_param("~floor0_final_y", self.floor_exit_y)
        self.floor0_final_yaw = rospy.get_param("~floor0_final_yaw", self.floor_exit_yaw)

        self.xy_tolerance = rospy.get_param("~xy_tolerance", 0.18)
        self.yaw_tolerance = rospy.get_param("~yaw_tolerance", 0.12)
        self.max_linear_speed = rospy.get_param("~max_linear_speed", 0.35)
        self.max_angular_speed = rospy.get_param("~max_angular_speed", 0.6)
        self.heading_kp = rospy.get_param("~heading_kp", 1.5)
        self.yaw_kp = rospy.get_param("~yaw_kp", 1.2)
        self.slow_radius = rospy.get_param("~slow_radius", 0.8)
        self.align_heading_threshold = rospy.get_param("~align_heading_threshold", 0.7)
        self.nav_timeout = rospy.get_param("~nav_timeout", 45.0)

        self.service_timeout = rospy.get_param("~service_timeout", 10.0)
        self.after_door_sleep = rospy.get_param("~after_door_sleep", 0.5)
        self.after_elevator_sleep = rospy.get_param("~after_elevator_sleep", 1.0)
        self.after_fuel_launch_sleep = rospy.get_param("~after_fuel_launch_sleep", 3.0)
        self.after_trigger_sleep = rospy.get_param("~after_trigger_sleep", 0.5)

        self.manage_fuel = rospy.get_param("~manage_fuel", True)
        self.auto_start_first_fuel = rospy.get_param("~auto_start_first_fuel", False)
        self.fuel_package = rospy.get_param("~fuel_package", "fuel_dog_adapter")
        self.fuel_launch = rospy.get_param("~fuel_launch", "fuel_dog_visual_exploration.launch")
        self.fuel_nodes = rospy.get_param(
            "~fuel_nodes",
            ["/exploration_node", "/traj_server", "/fuel_poscmd_to_cmdvel",
             "/odom_to_sensor_pose", "/dynamic_exploration_box"],
        )
        self.floor_z_box_low = rospy.get_param("~floor_z_box_low", 0.25)
        self.floor_z_box_high = rospy.get_param("~floor_z_box_high", 1.2)
        self.viewpoint_z_offset = rospy.get_param("~viewpoint_z_offset", 0.05)
        self.map_size_z = rospy.get_param("~map_size_z", 1.6)
        self.fuel_extra_args = rospy.get_param("~fuel_extra_args", "")

        self.odom = None
        self.finish_count = 0
        self.fuel_proc = None
        self.lock = threading.Lock()

        self.cmd_pub = rospy.Publisher(self.cmd_vel_topic, Twist, queue_size=1)
        self.trigger_pub = rospy.Publisher(self.trigger_topic, Path, queue_size=1, latch=True)
        rospy.Subscriber(self.odom_topic, Odometry, self.odom_callback, queue_size=1)
        rospy.Subscriber(self.finish_topic, Empty, self.finish_callback, queue_size=10)

        rospy.loginfo("multifloor mission: start_floor=%d, manage_fuel=%s", self.start_floor, self.manage_fuel)

    def odom_callback(self, msg):
        with self.lock:
            self.odom = msg

    def finish_callback(self, _msg):
        with self.lock:
            self.finish_count += 1
        rospy.logwarn("multifloor mission: received FUEL finish event")

    def get_pose(self):
        with self.lock:
            msg = self.odom
        if msg is None:
            return None
        q = msg.pose.pose.orientation
        yaw = tft.euler_from_quaternion([q.x, q.y, q.z, q.w])[2]
        p = msg.pose.pose.position
        return p.x, p.y, p.z, yaw

    def wait_odom(self):
        rate = rospy.Rate(20)
        while not rospy.is_shutdown() and self.get_pose() is None:
            rospy.logwarn_throttle(1.0, "multifloor mission: waiting for odometry")
            rate.sleep()

    def wait_fuel_finish(self):
        with self.lock:
            start_count = self.finish_count
        rate = rospy.Rate(5)
        rospy.logwarn("multifloor mission: waiting for current floor exploration to finish")
        while not rospy.is_shutdown():
            with self.lock:
                if self.finish_count > start_count:
                    return
            rate.sleep()

    def wait_service(self, name):
        rospy.loginfo("multifloor mission: waiting for service %s", name)
        rospy.wait_for_service(name, timeout=self.service_timeout)

    def set_door(self, floor, open_state):
        service = "/set_door_state"
        self.wait_service(service)
        proxy = rospy.ServiceProxy(service, SetDoorState)
        door_id = "%s%d" % (self.door_prefix, floor)
        action = "open" if open_state else "close"
        rospy.logwarn("multifloor mission: %s %s", action, door_id)
        resp = proxy(door_id, bool(open_state))
        if not resp.accepted:
            raise RuntimeError("door %s rejected: %s %s" % (door_id, resp.state, resp.message))
        rospy.sleep(self.after_door_sleep)
        return resp

    def call_elevator(self, target_floor):
        service = "/call_elevator"
        self.wait_service(service)
        proxy = rospy.ServiceProxy(service, CallElevator)
        rospy.logwarn("multifloor mission: call elevator %s to floor %d", self.elevator_id, target_floor)
        resp = proxy(self.elevator_id, int(target_floor), False)
        rospy.logwarn("multifloor mission: elevator response accepted=%s current_floor=%d state=%s msg=%s",
                      resp.accepted, resp.current_floor, resp.state, resp.message)
        rospy.sleep(self.after_elevator_sleep)
        return resp

    def publish_zero(self, duration=0.3):
        twist = Twist()
        end = rospy.Time.now() + rospy.Duration(duration)
        rate = rospy.Rate(20)
        while not rospy.is_shutdown() and rospy.Time.now() < end:
            self.cmd_pub.publish(twist)
            rate.sleep()

    def go_to_pose(self, x, y, yaw=None, label="target"):
        rospy.logwarn("multifloor mission: go to %s %.3f %.3f yaw=%s", label, x, y, str(yaw))
        start = rospy.Time.now()
        rate = rospy.Rate(30)
        while not rospy.is_shutdown():
            pose = self.get_pose()
            if pose is None:
                rate.sleep()
                continue
            px, py, _pz, pyaw = pose
            dx = x - px
            dy = y - py
            dist = math.hypot(dx, dy)
            heading = math.atan2(dy, dx)
            heading_error = wrap_pi(heading - pyaw)

            twist = Twist()
            if dist > self.xy_tolerance:
                if abs(heading_error) < self.align_heading_threshold:
                    speed_scale = clamp(dist / self.slow_radius, 0.25, 1.0)
                    twist.linear.x = self.max_linear_speed * speed_scale
                twist.angular.z = clamp(self.heading_kp * heading_error,
                                        -self.max_angular_speed, self.max_angular_speed)
            elif yaw is not None:
                yaw_error = wrap_pi(yaw - pyaw)
                if abs(yaw_error) <= self.yaw_tolerance:
                    self.publish_zero()
                    rospy.logwarn("multifloor mission: reached %s", label)
                    return True
                twist.angular.z = clamp(self.yaw_kp * yaw_error,
                                        -self.max_angular_speed, self.max_angular_speed)
            else:
                self.publish_zero()
                rospy.logwarn("multifloor mission: reached %s", label)
                return True

            self.cmd_pub.publish(twist)
            if (rospy.Time.now() - start).to_sec() > self.nav_timeout:
                self.publish_zero()
                rospy.logerr("multifloor mission: timeout going to %s", label)
                return False
            rate.sleep()
        return False

    def stop_fuel(self):
        if not self.manage_fuel:
            return
        rospy.logwarn("multifloor mission: stopping FUEL nodes")
        for node in self.fuel_nodes:
            subprocess.call(["rosnode", "kill", node], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if self.fuel_proc and self.fuel_proc.poll() is None:
            os.killpg(os.getpgid(self.fuel_proc.pid), signal.SIGTERM)
            try:
                self.fuel_proc.wait(timeout=5.0)
            except subprocess.TimeoutExpired:
                os.killpg(os.getpgid(self.fuel_proc.pid), signal.SIGKILL)
        self.fuel_proc = None
        self.publish_zero(0.5)

    def launch_fuel(self, floor):
        if not self.manage_fuel:
            rospy.logwarn("multifloor mission: manage_fuel=false, skip fuel relaunch")
            return
        pose = self.get_pose()
        if pose is None:
            raise RuntimeError("No odometry for FUEL launch")
        x, y, z, yaw = pose
        fixed_z = z
        viewpoint_z = z + self.viewpoint_z_offset
        ground_height = z - self.floor_z_box_low
        box_min_z = ground_height
        box_max_z = z + self.floor_z_box_high

        args = [
            "roslaunch", self.fuel_package, self.fuel_launch,
            "init_x:=%.3f" % x,
            "init_y:=%.3f" % y,
            "init_z:=%.3f" % fixed_z,
            "fixed_z:=%.3f" % fixed_z,
            "viewpoint_z:=%.3f" % viewpoint_z,
            "ground_height:=%.3f" % ground_height,
            "box_min_z:=%.3f" % box_min_z,
            "box_max_z:=%.3f" % box_max_z,
            "map_size_z:=%.3f" % self.map_size_z,
            "return_home_x:=%.3f" % self.floor_exit_x,
            "return_home_y:=%.3f" % self.floor_exit_y,
            "return_home_z:=%.3f" % fixed_z,
            "return_home_yaw:=%.3f" % self.floor_exit_yaw,
        ]
        if self.fuel_extra_args.strip():
            args.extend(self.fuel_extra_args.split())
        rospy.logwarn("multifloor mission: launch FUEL for floor %d: %s", floor, " ".join(args))
        self.fuel_proc = subprocess.Popen(args, preexec_fn=os.setsid)
        rospy.sleep(self.after_fuel_launch_sleep)

    def send_trigger(self):
        pose = self.get_pose()
        if pose is None:
            raise RuntimeError("No odometry for trigger")
        x, y, z, _yaw = pose
        msg = Path()
        msg.header.stamp = rospy.Time.now()
        msg.header.frame_id = "world"
        pose_msg = Odometry().pose.pose
        pose_msg.position.x = x
        pose_msg.position.y = y
        pose_msg.position.z = z
        pose_msg.orientation.w = 1.0
        from geometry_msgs.msg import PoseStamped
        ps = PoseStamped()
        ps.header = msg.header
        ps.pose = pose_msg
        msg.poses.append(ps)
        for _ in range(3):
            self.trigger_pub.publish(msg)
            rospy.sleep(0.1)
        rospy.sleep(self.after_trigger_sleep)
        rospy.logwarn("multifloor mission: sent FUEL trigger at %.3f %.3f %.3f", x, y, z)

    def try_next_floor(self, current_floor):
        before = self.get_pose()
        if before is None:
            return False, current_floor
        target = current_floor + 1
        resp = self.call_elevator(target)
        after = self.get_pose()
        if not resp.accepted:
            return False, current_floor
        if after is None:
            return False, current_floor
        dz = after[2] - before[2]
        if dz < self.floor_change_min_z:
            rospy.logwarn("multifloor mission: elevator accepted but z delta %.3f < %.3f, treat as top",
                          dz, self.floor_change_min_z)
            return False, current_floor
        return True, int(resp.current_floor if resp.current_floor >= 0 else target)

    def explore_current_floor(self, floor, first_floor=False):
        if not first_floor or self.auto_start_first_fuel:
            self.stop_fuel()
            self.launch_fuel(floor)
            self.send_trigger()
        self.wait_fuel_finish()
        self.stop_fuel()

    def run(self):
        self.wait_odom()
        current_floor = self.start_floor

        while not rospy.is_shutdown():
            self.explore_current_floor(current_floor, first_floor=(current_floor == self.start_floor))

            self.set_door(current_floor, True)
            if not self.go_to_pose(self.elevator_inside_x, self.elevator_inside_y,
                                   self.elevator_inside_yaw, "elevator inside"):
                raise RuntimeError("Failed to enter elevator")
            self.set_door(current_floor, False)

            moved_up, new_floor = self.try_next_floor(current_floor)
            if moved_up:
                current_floor = new_floor
                self.set_door(current_floor, True)
                if not self.go_to_pose(self.floor_exit_x, self.floor_exit_y,
                                       self.floor_exit_yaw, "floor %d elevator exit" % current_floor):
                    raise RuntimeError("Failed to exit elevator on floor %d" % current_floor)
                self.set_door(current_floor, False)
                continue

            rospy.logwarn("multifloor mission: top floor detected at floor %d, returning to floor 0", current_floor)
            resp = self.call_elevator(0)
            if not resp.accepted:
                raise RuntimeError("Failed to return elevator to floor 0: %s" % resp.message)
            current_floor = 0
            self.set_door(0, True)
            if not self.go_to_pose(self.floor0_final_x, self.floor0_final_y,
                                   self.floor0_final_yaw, "floor 0 final exit"):
                raise RuntimeError("Failed to exit elevator on floor 0")
            self.set_door(0, False)
            self.publish_zero()
            rospy.logwarn("multifloor mission: DONE")
            return


if __name__ == "__main__":
    rospy.init_node("fuel_multifloor_mission")
    mission = MultiFloorMission()
    try:
        mission.run()
    except Exception as exc:
        rospy.logerr("multifloor mission failed: %s", exc)
        mission.publish_zero(1.0)
        raise
