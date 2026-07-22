#!/usr/bin/env python3
import math

import rospy
from geometry_msgs.msg import TwistStamped


class BsplineStartVelocityMonitor:
    def __init__(self):
        self.current = None
        self.bspline = None
        self.print_rate = rospy.get_param("~print_rate", 5.0)
        self.last_print = rospy.Time(0)

        rospy.Subscriber(
            "/planning/debug/current_start_velocity",
            TwistStamped,
            self.current_callback,
            queue_size=1,
        )
        rospy.Subscriber(
            "/planning/debug/bspline_start_velocity",
            TwistStamped,
            self.bspline_callback,
            queue_size=1,
        )

        rospy.loginfo(
            "bspline_start_velocity_monitor: waiting for planning velocity debug topics"
        )

    def current_callback(self, msg):
        self.current = msg
        self.print_pair()

    def bspline_callback(self, msg):
        self.bspline = msg
        self.print_pair()

    def print_pair(self):
        if self.current is None or self.bspline is None:
            return
        if abs((self.current.header.stamp - self.bspline.header.stamp).to_sec()) > 1e-3:
            return

        now = rospy.Time.now()
        if self.print_rate > 0.0 and (now - self.last_print).to_sec() < 1.0 / self.print_rate:
            return
        self.last_print = now

        current = self.current.twist.linear
        bspline = self.bspline.twist.linear
        current_speed = math.hypot(current.x, current.y)
        bspline_speed = math.hypot(bspline.x, bspline.y)
        current_yaw = math.atan2(current.y, current.x) if current_speed > 1e-6 else 0.0
        bspline_yaw = math.atan2(bspline.y, bspline.x) if bspline_speed > 1e-6 else 0.0
        if current_speed > 1e-3 and bspline_speed > 1e-3:
            angle_diff = math.degrees(
                math.atan2(
                    math.sin(bspline_yaw - current_yaw),
                    math.cos(bspline_yaw - current_yaw),
                )
            )
            angle_text = "%.1fdeg" % angle_diff
        else:
            angle_text = "N/A"

        rospy.logwarn(
            "bspline start | current=(%.3f, %.3f) bspline=(%.3f, %.3f) "
            "speed=%.3f->%.3f dir_diff=%s",
            current.x,
            current.y,
            bspline.x,
            bspline.y,
            current_speed,
            bspline_speed,
            angle_text,
        )


if __name__ == "__main__":
    rospy.init_node("bspline_start_velocity_monitor")
    BsplineStartVelocityMonitor()
    rospy.spin()
