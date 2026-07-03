#!/usr/bin/env python3
from __future__ import annotations

import math

import rospy
import tf.transformations as tft
from geometry_msgs.msg import Point, Twist
from nav_msgs.msg import Odometry
from visualization_msgs.msg import Marker


class CmdVelMarker:
    def __init__(self) -> None:
        self.frame_id = rospy.get_param("~frame_id", "world")
        self.odom_topic = rospy.get_param("~odom_topic", "/Odometry_gazebo")
        self.cmd_vel_topic = rospy.get_param("~cmd_vel_topic", "/cmd_vel")
        self.marker_topic = rospy.get_param("~marker_topic", "/fuel_dog_adapter/cmd_vel_marker")
        self.timeout = rospy.get_param("~timeout", 0.5)
        self.scale = rospy.get_param("~scale", 1.0)

        self.odom = None
        self.cmd = Twist()
        self.last_cmd_time = rospy.Time(0)

        self.pub = rospy.Publisher(self.marker_topic, Marker, queue_size=1)
        self.odom_sub = rospy.Subscriber(self.odom_topic, Odometry, self.odom_callback, queue_size=1)
        self.cmd_sub = rospy.Subscriber(self.cmd_vel_topic, Twist, self.cmd_callback, queue_size=1)
        self.timer = rospy.Timer(rospy.Duration(0.1), self.publish_marker)

        rospy.loginfo("cmd_vel_marker: %s + %s -> %s", self.odom_topic, self.cmd_vel_topic, self.marker_topic)

    def odom_callback(self, msg: Odometry) -> None:
        self.odom = msg

    def cmd_callback(self, msg: Twist) -> None:
        self.cmd = msg
        self.last_cmd_time = rospy.Time.now()

    def publish_marker(self, _event) -> None:
        marker = Marker()
        marker.header.stamp = rospy.Time.now()
        marker.header.frame_id = self.frame_id
        marker.ns = "fuel_dog_adapter"
        marker.id = 1
        marker.type = Marker.ARROW
        marker.action = Marker.ADD
        marker.scale.x = 0.06
        marker.scale.y = 0.16
        marker.scale.z = 0.18
        marker.color.r = 1.0
        marker.color.g = 0.18
        marker.color.b = 0.08
        marker.color.a = 0.9
        marker.lifetime = rospy.Duration(0.3)

        if self.odom is None or rospy.Time.now() - self.last_cmd_time > rospy.Duration(self.timeout):
            marker.action = Marker.DELETE
            self.pub.publish(marker)
            return

        q = self.odom.pose.pose.orientation
        _, _, yaw = tft.euler_from_quaternion([q.x, q.y, q.z, q.w])
        cos_yaw = math.cos(yaw)
        sin_yaw = math.sin(yaw)
        vx_world = cos_yaw * self.cmd.linear.x - sin_yaw * self.cmd.linear.y
        vy_world = sin_yaw * self.cmd.linear.x + cos_yaw * self.cmd.linear.y
        speed = math.hypot(vx_world, vy_world)

        start = Point()
        start.x = self.odom.pose.pose.position.x
        start.y = self.odom.pose.pose.position.y
        start.z = self.odom.pose.pose.position.z + 0.45

        end = Point()
        if speed < 1e-3:
            end.x = start.x + 0.05 * math.cos(yaw)
            end.y = start.y + 0.05 * math.sin(yaw)
        else:
            end.x = start.x + vx_world * self.scale
            end.y = start.y + vy_world * self.scale
        end.z = start.z

        marker.points = [start, end]
        self.pub.publish(marker)


if __name__ == "__main__":
    rospy.init_node("cmd_vel_marker")
    CmdVelMarker()
    rospy.spin()
