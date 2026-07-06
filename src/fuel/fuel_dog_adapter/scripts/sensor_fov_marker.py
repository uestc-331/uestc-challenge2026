#!/usr/bin/env python3
from __future__ import annotations

import rospy
import tf.transformations as tft
from geometry_msgs.msg import Point, PoseStamped
from visualization_msgs.msg import Marker


class SensorFovMarker:
    def __init__(self) -> None:
        self.pose_topic = rospy.get_param("~pose_topic", "/fuel_dog_adapter/sensor_pose")
        self.marker_topic = rospy.get_param("~marker_topic", "/fuel_dog_adapter/sensor_fov_marker")
        self.frame_id = rospy.get_param("~frame_id", "world")
        self.fx = rospy.get_param("~fx", 554.256)
        self.fy = rospy.get_param("~fy", 554.256)
        self.cx = rospy.get_param("~cx", 320.0)
        self.cy = rospy.get_param("~cy", 240.0)
        self.width = rospy.get_param("~width", 640.0)
        self.height = rospy.get_param("~height", 480.0)
        self.max_dist = rospy.get_param("~max_dist", 5.0)

        self.pub = rospy.Publisher(self.marker_topic, Marker, queue_size=1)
        self.sub = rospy.Subscriber(self.pose_topic, PoseStamped, self.pose_callback, queue_size=1)
        rospy.loginfo("sensor_fov_marker: %s -> %s", self.pose_topic, self.marker_topic)

    def pose_callback(self, msg: PoseStamped) -> None:
        marker = Marker()
        marker.header.stamp = msg.header.stamp if msg.header.stamp != rospy.Time(0) else rospy.Time.now()
        marker.header.frame_id = self.frame_id
        marker.ns = "actual_sensor_fov"
        marker.id = 1
        marker.type = Marker.LINE_LIST
        marker.action = Marker.ADD
        marker.scale.x = 0.035
        marker.color.r = 0.0
        marker.color.g = 0.9
        marker.color.b = 1.0
        marker.color.a = 1.0
        marker.lifetime = rospy.Duration(0.3)

        q = msg.pose.orientation
        rot = tft.quaternion_matrix([q.x, q.y, q.z, q.w])
        origin = [msg.pose.position.x, msg.pose.position.y, msg.pose.position.z]
        corners_cam = [
            self.project(0.0, 0.0),
            self.project(self.width, 0.0),
            self.project(self.width, self.height),
            self.project(0.0, self.height),
        ]
        corners_world = [self.transform_point(rot, origin, p) for p in corners_cam]
        origin_pt = self.to_point(origin)

        for corner in corners_world:
            marker.points.append(origin_pt)
            marker.points.append(self.to_point(corner))
        for i in range(4):
            marker.points.append(self.to_point(corners_world[i]))
            marker.points.append(self.to_point(corners_world[(i + 1) % 4]))

        self.pub.publish(marker)

    def project(self, u: float, v: float):
        z = self.max_dist
        return [(u - self.cx) * z / self.fx, (v - self.cy) * z / self.fy, z]

    @staticmethod
    def transform_point(rot, origin, point):
        return [
            origin[0] + rot[0][0] * point[0] + rot[0][1] * point[1] + rot[0][2] * point[2],
            origin[1] + rot[1][0] * point[0] + rot[1][1] * point[1] + rot[1][2] * point[2],
            origin[2] + rot[2][0] * point[0] + rot[2][1] * point[1] + rot[2][2] * point[2],
        ]

    @staticmethod
    def to_point(values):
        pt = Point()
        pt.x = values[0]
        pt.y = values[1]
        pt.z = values[2]
        return pt


if __name__ == "__main__":
    rospy.init_node("sensor_fov_marker")
    SensorFovMarker()
    rospy.spin()
