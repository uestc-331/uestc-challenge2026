#!/usr/bin/env python3
from __future__ import annotations

import math

import rospy
from geometry_msgs.msg import PoseStamped
from nav_msgs.msg import Odometry, Path
from quadrotor_msgs.msg import PositionCommand


class FuelPathVisualizer:
    def __init__(self) -> None:
        self.frame_id = rospy.get_param("~frame_id", "world")
        self.odom_topic = rospy.get_param("~odom_topic", "/Odometry_gazebo")
        self.position_cmd_topic = rospy.get_param("~position_cmd_topic", "/position_cmd")
        self.executed_path_topic = rospy.get_param("~executed_path_topic", "/fuel_dog_adapter/executed_path")
        self.generated_path_topic = rospy.get_param("~generated_path_topic", "/fuel_dog_adapter/generated_path")
        self.max_points = rospy.get_param("~max_points", 2000)
        self.min_distance = rospy.get_param("~min_distance", 0.03)

        self.executed_path = Path()
        self.generated_path = Path()
        self.executed_path.header.frame_id = self.frame_id
        self.generated_path.header.frame_id = self.frame_id

        self.last_odom_pose = None
        self.last_cmd_pose = None

        self.executed_pub = rospy.Publisher(self.executed_path_topic, Path, queue_size=1, latch=True)
        self.generated_pub = rospy.Publisher(self.generated_path_topic, Path, queue_size=1, latch=True)
        self.odom_sub = rospy.Subscriber(self.odom_topic, Odometry, self.odom_callback, queue_size=1)
        self.cmd_sub = rospy.Subscriber(
            self.position_cmd_topic, PositionCommand, self.position_cmd_callback, queue_size=1
        )

        rospy.loginfo(
            "fuel_path_visualizer: %s -> %s, %s -> %s",
            self.odom_topic,
            self.executed_path_topic,
            self.position_cmd_topic,
            self.generated_path_topic,
        )

    def odom_callback(self, msg: Odometry) -> None:
        pose = PoseStamped()
        pose.header.stamp = msg.header.stamp if msg.header.stamp != rospy.Time(0) else rospy.Time.now()
        pose.header.frame_id = self.frame_id
        pose.pose = msg.pose.pose
        if self._append_pose(self.executed_path, pose, "last_odom_pose"):
            self.executed_path.header.stamp = pose.header.stamp
            self.executed_pub.publish(self.executed_path)

    def position_cmd_callback(self, msg: PositionCommand) -> None:
        pose = PoseStamped()
        pose.header.stamp = rospy.Time.now()
        pose.header.frame_id = self.frame_id
        pose.pose.position.x = msg.position.x
        pose.pose.position.y = msg.position.y
        pose.pose.position.z = msg.position.z
        pose.pose.orientation.w = 1.0
        if self._append_pose(self.generated_path, pose, "last_cmd_pose"):
            self.generated_path.header.stamp = pose.header.stamp
            self.generated_pub.publish(self.generated_path)

    def _append_pose(self, path: Path, pose: PoseStamped, last_attr: str) -> bool:
        last_pose = getattr(self, last_attr)
        if last_pose is not None:
            dx = pose.pose.position.x - last_pose.pose.position.x
            dy = pose.pose.position.y - last_pose.pose.position.y
            dz = pose.pose.position.z - last_pose.pose.position.z
            if math.sqrt(dx * dx + dy * dy + dz * dz) < self.min_distance:
                return False

        path.poses.append(pose)
        if len(path.poses) > self.max_points:
            path.poses = path.poses[-self.max_points :]
        setattr(self, last_attr, pose)
        return True


if __name__ == "__main__":
    rospy.init_node("fuel_path_visualizer")
    FuelPathVisualizer()
    rospy.spin()
