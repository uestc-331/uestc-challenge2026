#!/usr/bin/env python3
import math

import rospy
import tf.transformations as tft
from geometry_msgs.msg import Twist
from nav_msgs.msg import Odometry


class DogVelocityMonitor:
    def __init__(self):
        self.odom_topic = rospy.get_param("~odom_topic", "/Odometry_gazebo")
        self.cmd_vel_topic = rospy.get_param("~cmd_vel_topic", "/cmd_vel")
        self.print_rate = rospy.get_param("~print_rate", 5.0)
        self.print_cmd_vel = rospy.get_param("~print_cmd_vel", True)

        self.last_odom = None
        self.last_cmd = None
        self.last_print = rospy.Time(0)

        rospy.Subscriber(self.odom_topic, Odometry, self.odom_callback, queue_size=1)
        rospy.Subscriber(self.cmd_vel_topic, Twist, self.cmd_callback, queue_size=1)

        rospy.loginfo(
            "dog_velocity_monitor: odom=%s, cmd_vel=%s, print_rate=%.2f Hz",
            self.odom_topic,
            self.cmd_vel_topic,
            self.print_rate,
        )

    def odom_callback(self, msg):
        self.last_odom = msg
        self.maybe_print()

    def cmd_callback(self, msg):
        self.last_cmd = msg

    def maybe_print(self):
        now = rospy.Time.now()
        if self.print_rate > 0.0 and (now - self.last_print).to_sec() < 1.0 / self.print_rate:
            return
        self.last_print = now

        if self.last_odom is None:
            return

        odom_twist = self.last_odom.twist.twist
        odom_linear = odom_twist.linear
        odom_angular = odom_twist.angular
        yaw = self.yaw_from_odom(self.last_odom)
        cos_yaw = math.cos(yaw)
        sin_yaw = math.sin(yaw)
        odom_body_vx = cos_yaw * odom_linear.x + sin_yaw * odom_linear.y
        odom_body_vy = -sin_yaw * odom_linear.x + cos_yaw * odom_linear.y

        if self.print_cmd_vel and self.last_cmd is not None:
            rospy.logwarn(
                "vel | odom_body v=%.3f w=%.3f | cmd v=%.3f w=%.3f",
                odom_body_vx,
                odom_angular.z,
                self.last_cmd.linear.x,
                self.last_cmd.angular.z,
            )
        else:
            rospy.logwarn(
                "vel | odom_body v=%.3f vy=%.3f w=%.3f",
                odom_body_vx,
                odom_body_vy,
                odom_angular.z,
            )

    @staticmethod
    def yaw_from_odom(odom):
        q = odom.pose.pose.orientation
        _, _, yaw = tft.euler_from_quaternion([q.x, q.y, q.z, q.w])
        return yaw


if __name__ == "__main__":
    rospy.init_node("dog_velocity_monitor")
    DogVelocityMonitor()
    rospy.spin()
