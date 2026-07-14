#!/usr/bin/env python3
from __future__ import annotations

import math
import threading

import rospy
import tf.transformations as tft
from geometry_msgs.msg import Twist
from nav_msgs.msg import Odometry
from quadrotor_msgs.msg import PositionCommand


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def wrap_pi(angle: float) -> float:
    while angle > math.pi:
        angle -= 2.0 * math.pi
    while angle < -math.pi:
        angle += 2.0 * math.pi
    return angle


class FuelPosCmdToCmdVel:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.last_cmd = None
        self.last_cmd_time = rospy.Time(0)
        self.seen_planned_cmd = False
        self.last_odom = None
        self.last_odom_time = rospy.Time(0)
        self.last_twist = Twist()
        self.last_publish_time = rospy.Time.now()

        # ------ controller gains (fallbacks, overridden by PositionCommand.kx/kv) ------
        self.position_kp = rospy.get_param("~position_kp", 5.7)
        self.position_kd = rospy.get_param("~position_kd", 3.4)
        self.yaw_kp = rospy.get_param("~yaw_kp", 1.2)
        self.velocity_ff_gain = rospy.get_param("~velocity_ff_gain", 1.0)
        self.accel_ff_gain = rospy.get_param("~accel_ff_gain", 0.3)
        self.yaw_ff_gain = rospy.get_param("~yaw_ff_gain", 0.5)
        self.lookahead_time = rospy.get_param("~lookahead_time", 0.15)

        # ------ behaviour flags ------
        self.align_yaw_to_velocity = rospy.get_param("~align_yaw_to_velocity", True)
        self.forward_only = rospy.get_param("~forward_only", True)
        self.disable_lateral = rospy.get_param("~disable_lateral", True)
        self.ignore_unplanned_yaw = rospy.get_param("~ignore_unplanned_yaw", True)
        self.min_heading_speed = rospy.get_param("~min_heading_speed", 0.05)

        # ------ limits ------
        self.max_linear_speed = rospy.get_param("~max_linear_speed", 0.6)
        self.max_lateral_speed = rospy.get_param("~max_lateral_speed", self.max_linear_speed)
        self.max_angular_speed = rospy.get_param("~max_angular_speed", 0.8)
        self.linear_acc_limit = rospy.get_param("~linear_acc_limit", 0.8)
        self.angular_acc_limit = rospy.get_param("~angular_acc_limit", 1.2)

        # ------ timing ------
        self.command_timeout = rospy.Duration(rospy.get_param("~command_timeout", 0.5))
        self.odom_timeout = rospy.Duration(rospy.get_param("~odom_timeout", 0.5))
        self.control_rate = rospy.get_param("~control_rate", 30.0)
        self.publish_zero_before_first_cmd = rospy.get_param("~publish_zero_before_first_cmd", False)

        # ------ topics ------
        position_cmd_topic = rospy.get_param("~position_cmd_topic", "/position_cmd")
        odom_topic = rospy.get_param("~odom_topic", "/Odometry_gazebo")
        cmd_vel_topic = rospy.get_param("~cmd_vel_topic", "/cmd_vel")

        self.pub = rospy.Publisher(cmd_vel_topic, Twist, queue_size=1)
        self.cmd_sub = rospy.Subscriber(position_cmd_topic, PositionCommand, self.cmd_callback, queue_size=1)
        self.odom_sub = rospy.Subscriber(odom_topic, Odometry, self.odom_callback, queue_size=1)
        self.timer = rospy.Timer(rospy.Duration(1.0 / self.control_rate), self.timer_callback)

        rospy.loginfo(
            "fuel_poscmd_to_cmdvel: %s + %s -> %s",
            position_cmd_topic, odom_topic, cmd_vel_topic,
        )
        rospy.loginfo(
            "fuel_poscmd_to_cmdvel: kp=%.2f kd=%.2f yaw_kp=%.2f lookahead=%.2fs",
            self.position_kp, self.position_kd, self.yaw_kp, self.lookahead_time,
        )

    # ------------------------------------------------------------------
    def cmd_callback(self, msg: PositionCommand) -> None:
        with self.lock:
            self.last_cmd = msg
            self.last_cmd_time = rospy.Time.now()
            if msg.trajectory_id > 0:
                self.seen_planned_cmd = True

    def odom_callback(self, msg: Odometry) -> None:
        with self.lock:
            self.last_odom = msg
            self.last_odom_time = rospy.Time.now()

    # ------------------------------------------------------------------
    def timer_callback(self, _event: rospy.timer.TimerEvent) -> None:
        now = rospy.Time.now()
        with self.lock:
            cmd = self.last_cmd
            seen_planned_cmd = self.seen_planned_cmd
            odom = self.last_odom
            cmd_age = now - self.last_cmd_time
            odom_age = now - self.last_odom_time

        if cmd is None or cmd_age > self.command_timeout:
            rospy.logwarn_throttle(2.0, "fuel_poscmd_to_cmdvel: waiting for fresh /position_cmd")
            if not seen_planned_cmd and not self.publish_zero_before_first_cmd:
                return
            self.publish_zero(now)
            return
        if odom is None or odom_age > self.odom_timeout:
            rospy.logwarn_throttle(2.0, "fuel_poscmd_to_cmdvel: waiting for fresh odometry")
            self.publish_zero(now)
            return

        # ---- use trajectory-supplied gains when available -----------------
        kp_xy = self.position_kp
        kd_xy = self.position_kd
        if len(cmd.kx) >= 2 and cmd.kx[0] > 0:
            kp_xy = cmd.kx[0]
        if len(cmd.kv) >= 2 and cmd.kv[0] > 0:
            kd_xy = cmd.kv[0]

        yaw = self.yaw_from_odom(odom)
        odom_vx = odom.twist.twist.linear.x
        odom_vy = odom.twist.twist.linear.y

        # ---- look-ahead position (anticipate corners) ----------------------
        la_time = clamp(self.lookahead_time, 0.0, 0.5)
        target_x = cmd.position.x + cmd.velocity.x * la_time
        target_y = cmd.position.y + cmd.velocity.y * la_time

        # ---- position error + velocity error (PD) -------------------------
        err_x = target_x - odom.pose.pose.position.x
        err_y = target_y - odom.pose.pose.position.y

        # velocity error (D-term) — desired trajectory vel vs actual odom vel
        des_vx = cmd.velocity.x
        des_vy = cmd.velocity.y
        verr_x = des_vx - odom_vx
        verr_y = des_vy - odom_vy

        # world-frame acceleration command
        #   position P  +  velocity FF  +  velocity D  +  acceleration FF
        world_ax = kp_xy * err_x + self.velocity_ff_gain * des_vx + kd_xy * verr_x + self.accel_ff_gain * cmd.acceleration.x
        world_ay = kp_xy * err_y + self.velocity_ff_gain * des_vy + kd_xy * verr_y + self.accel_ff_gain * cmd.acceleration.y

        # ---- convert to body frame ----------------------------------------
        cos_yaw = math.cos(yaw)
        sin_yaw = math.sin(yaw)
        body_vx = cos_yaw * world_ax + sin_yaw * world_ay
        body_vy = -sin_yaw * world_ax + cos_yaw * world_ay

        # ---- yaw control --------------------------------------------------
        world_speed = math.hypot(world_ax, world_ay)
        yaw_ff = self.yaw_ff_gain * cmd.yaw_dot
        has_planned_traj = cmd.trajectory_id > 0

        if not has_planned_traj and not self.publish_zero_before_first_cmd:
            return

        if self.ignore_unplanned_yaw and not has_planned_traj:
            # traj_server publishes an initial UAV-style command with yaw=0 and
            # trajectory_id=0. Keep the current yaw so the dog does not rotate
            # before FUEL has produced a real exploration trajectory.
            target_yaw = yaw
            yaw_ff = 0.0
        elif self.align_yaw_to_velocity and world_speed > self.min_heading_speed:
            # use velocity direction for target yaw (good for non-holonomic)
            target_yaw = math.atan2(world_ay, world_ax)
        else:
            # use trajectory yaw directly
            target_yaw = cmd.yaw

        yaw_error = wrap_pi(target_yaw - yaw)
        wz = yaw_ff + self.yaw_kp * yaw_error

        # ---- motion constraints -------------------------------------------
        if self.disable_lateral:
            body_vy = 0.0
        if self.forward_only:
            body_vx = max(0.0, body_vx)

        # ---- saturate & publish -------------------------------------------
        desired = Twist()
        desired.linear.x = clamp(body_vx, -self.max_linear_speed, self.max_linear_speed)
        desired.linear.y = clamp(body_vy, -self.max_lateral_speed, self.max_lateral_speed)
        desired.angular.z = clamp(wz, -self.max_angular_speed, self.max_angular_speed)

        self.publish_limited(desired, now)

    # ------------------------------------------------------------------
    def publish_zero(self, now: rospy.Time) -> None:
        self.publish_limited(Twist(), now)

    def publish_limited(self, desired: Twist, now: rospy.Time) -> None:
        dt = max((now - self.last_publish_time).to_sec(), 1.0 / max(self.control_rate, 1.0))

        limited = Twist()
        max_dv = self.linear_acc_limit * dt
        max_dw = self.angular_acc_limit * dt
        limited.linear.x = self.step_toward(self.last_twist.linear.x, desired.linear.x, max_dv)
        limited.linear.y = self.step_toward(self.last_twist.linear.y, desired.linear.y, max_dv)
        limited.angular.z = self.step_toward(self.last_twist.angular.z, desired.angular.z, max_dw)

        self.pub.publish(limited)
        self.last_twist = limited
        self.last_publish_time = now

    @staticmethod
    def step_toward(current: float, target: float, max_delta: float) -> float:
        return current + clamp(target - current, -max_delta, max_delta)

    @staticmethod
    def yaw_from_odom(odom: Odometry) -> float:
        q = odom.pose.pose.orientation
        _, _, yaw = tft.euler_from_quaternion([q.x, q.y, q.z, q.w])
        return yaw


if __name__ == "__main__":
    rospy.init_node("fuel_poscmd_to_cmdvel")
    FuelPosCmdToCmdVel()
    rospy.spin()
