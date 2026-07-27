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
        # FUEL/traj_server 输出的是无人机风格的 PositionCommand。
        # 四足狗控制端吃的是 /cmd_vel，因此这里是“规划语义 -> 地面速度控制”的核心适配层。
        self.lock = threading.Lock()
        self.last_cmd = None
        self.last_cmd_time = rospy.Time(0)
        self.seen_planned_cmd = False
        self.last_odom = None
        self.last_odom_time = rospy.Time(0)
        self.last_twist = Twist()
        self.last_publish_time = rospy.Time.now()

        # ------ controller gains ------
        # position_kp/kd 把期望位置和期望速度转换成世界系速度意图；
        # feedforward 用来保留 B 样条原始速度/加速度趋势，避免完全靠误差追踪。
        self.position_kp = rospy.get_param("~position_kp", 5.7)
        self.position_kd = rospy.get_param("~position_kd", 3.4)
        self.yaw_kp = rospy.get_param("~yaw_kp", 1.2)
        self.velocity_ff_gain = rospy.get_param("~velocity_ff_gain", 1.0)
        self.accel_ff_gain = rospy.get_param("~accel_ff_gain", 0.3)
        self.yaw_ff_gain = rospy.get_param("~yaw_ff_gain", 0.5)
        self.lookahead_time = rospy.get_param("~lookahead_time", 0.15)

        # ------ behaviour flags ------
        # align_yaw_to_velocity=true 时，让狗朝向实际运动方向，而不是强行跟随无人机轨迹 yaw。
        # disable_lateral=true 用来把横移压掉，更接近四足机器人稳定前进的行为。
        self.align_yaw_to_velocity = rospy.get_param("~align_yaw_to_velocity", True)
        self.forward_only = rospy.get_param("~forward_only", True)
        self.disable_lateral = rospy.get_param("~disable_lateral", True)
        self.ignore_unplanned_yaw = rospy.get_param("~ignore_unplanned_yaw", True)
        self.min_heading_speed = rospy.get_param("~min_heading_speed", 0.05)
        self.rotate_first = rospy.get_param("~rotate_first", False)
        self.rotate_first_enter_yaw = rospy.get_param("~rotate_first_enter_yaw", 0.785)
        self.rotate_first_exit_yaw = rospy.get_param("~rotate_first_exit_yaw", 0.262)
        self.rotate_first_min_dist = rospy.get_param("~rotate_first_min_dist", 0.4)
        self.rotate_first_active = False

        # ------ limits ------
        self.max_linear_speed = rospy.get_param("~max_linear_speed", 0.6)
        self.max_lateral_speed = rospy.get_param("~max_lateral_speed", self.max_linear_speed)
        self.max_angular_speed = rospy.get_param("~max_angular_speed", 0.8)
        self.linear_acc_limit = rospy.get_param("~linear_acc_limit", 0.8)
        self.angular_acc_limit = rospy.get_param("~angular_acc_limit", 1.2)

        # ------ timing ------
        # 两层/复杂场景 Gazebo RTF 降低时，command_timeout/odom_timeout 过小会导致误停。
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
            # trajectory_id > 0 表示已经收到真正规划轨迹，而不是 traj_server 初始化占位命令。
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

        # ---- controller gains -----------------
        # 当前固定使用 launch 中的增益，避免 PositionCommand 里无人机 PD 增益直接影响四足狗。
        kp_xy = self.position_kp
        kd_xy = self.position_kd
        # if len(cmd.kx) >= 2 and cmd.kx[0] > 0:
        #     kp_xy = cmd.kx[0]
        # if len(cmd.kv) >= 2 and cmd.kv[0] > 0:
        #     kd_xy = cmd.kv[0]

        yaw = self.yaw_from_odom(odom)
        odom_vx = odom.twist.twist.linear.x
        odom_vy = odom.twist.twist.linear.y

        # ---- look-ahead position ----------------------
        # 低 RTF 或转弯多时，适当 lookahead 可减少“追着旧点跑”的滞后。
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

        # 世界系速度意图：
        #   位置 P 项 + 轨迹速度前馈 + 速度误差阻尼 + 加速度前馈。
        # 变量名沿用 world_ax/world_ay，但后面实际作为速度命令输入 /cmd_vel。
        world_ax = kp_xy * err_x + self.velocity_ff_gain * des_vx + kd_xy * verr_x + self.accel_ff_gain * cmd.acceleration.x
        world_ay = kp_xy * err_y + self.velocity_ff_gain * des_vy + kd_xy * verr_y + self.accel_ff_gain * cmd.acceleration.y

        # ---- convert to body frame ----------------------------------------
        # /cmd_vel 对四足狗来说通常是机体系速度，因此要把 world 下的速度意图旋到 base 坐标系。
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
            # traj_server 启动时会发布 trajectory_id=0、yaw=0 的占位命令。
            # 如果直接执行，狗会在真正规划前原地转向 0rad；这里保持当前 yaw。
            target_yaw = yaw
            yaw_ff = 0.0
        elif self.align_yaw_to_velocity and world_speed > self.min_heading_speed:
            # 对地面四足狗更友好：朝向当前期望运动方向，而不是无人机轨迹的独立 yaw。
            target_yaw = math.atan2(world_ay, world_ax)
        else:
            # use trajectory yaw directly
            target_yaw = cmd.yaw

        goal_dx = target_x - odom.pose.pose.position.x
        goal_dy = target_y - odom.pose.pose.position.y
        goal_dist = math.hypot(goal_dx, goal_dy)
        goal_yaw_error = 0.0
        if goal_dist > self.rotate_first_min_dist:
            goal_heading = math.atan2(goal_dy, goal_dx)
            goal_yaw_error = wrap_pi(goal_heading - yaw)
            if (
                self.rotate_first
                and has_planned_traj
                and not self.rotate_first_active
                and abs(goal_yaw_error) > self.rotate_first_enter_yaw
            ):
                self.rotate_first_active = True
                rospy.logwarn_throttle(
                    1.0,
                    "fuel_poscmd_to_cmdvel: rotate-first active, yaw_error=%.2f",
                    goal_yaw_error,
                )

        if self.rotate_first_active:
            # rotate-first 模式用于“目标在视野外且直线可走”一类场景：
            # 先原地对准目标方向，再放开线速度，减少边转边绕圈。
            if goal_dist <= self.rotate_first_min_dist or abs(goal_yaw_error) < self.rotate_first_exit_yaw:
                self.rotate_first_active = False
            else:
                target_yaw = math.atan2(goal_dy, goal_dx)
                yaw_ff = 0.0

        yaw_error = wrap_pi(target_yaw - yaw)
        wz = yaw_ff + self.yaw_kp * yaw_error

        # ---- motion constraints -------------------------------------------
        # 这些约束把无人机式全向命令压成更适合四足狗执行的速度命令。
        if self.rotate_first_active:
            body_vx = 0.0
            body_vy = 0.0
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
        # 对最终 /cmd_vel 做加速度限制，避免规划命令突变直接冲击四足狗控制器。
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
