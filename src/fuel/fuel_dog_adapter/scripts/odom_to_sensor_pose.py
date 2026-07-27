#!/usr/bin/env python3
from __future__ import annotations

import rospy
import tf.transformations as tft
from geometry_msgs.msg import PoseStamped
from nav_msgs.msg import Odometry


class OdomToSensorPose:
    def __init__(self) -> None:
        # FUEL 建图需要的是“传感器在 world 下的位姿”，而四足狗仿真直接给的是机体 odom。
        # 这里用机体 odom + 相机安装偏移，实时换算出深度相机 pose。
        odom_topic = rospy.get_param("~odom_topic", "/Odometry_gazebo")
        pose_topic = rospy.get_param("~pose_topic", "/fuel_dog_adapter/sensor_pose")
        self.frame_id = rospy.get_param("~frame_id", "world")
        self.sensor_offset_x = rospy.get_param("~sensor_offset_x", 0.0)
        self.sensor_offset_y = rospy.get_param("~sensor_offset_y", 0.0)
        self.sensor_offset_z = rospy.get_param("~sensor_offset_z", 0.45)
        self.use_optical_frame = rospy.get_param("~use_optical_frame", False)
        self.sensor_roll = rospy.get_param("~sensor_roll", 0.0)
        self.sensor_pitch = rospy.get_param("~sensor_pitch", 0.0)
        self.sensor_yaw = rospy.get_param("~sensor_yaw", 0.0)

        # FUEL 深度投影按相机光学坐标理解：x 向右、y 向下、z 向前。
        # 四足狗 base 坐标通常是 x 前、y 左、z 上，因此需要这一步固定旋转。
        self.base_to_optical_q = tft.quaternion_from_matrix([
            [0.0, 0.0, 1.0, 0.0],
            [-1.0, 0.0, 0.0, 0.0],
            [0.0, -1.0, 0.0, 0.0],
            [0.0, 0.0, 0.0, 1.0],
        ])
        self.sensor_offset_q = tft.quaternion_from_euler(
            self.sensor_roll, self.sensor_pitch, self.sensor_yaw
        )

        self.pub = rospy.Publisher(pose_topic, PoseStamped, queue_size=1)
        self.sub = rospy.Subscriber(odom_topic, Odometry, self.odom_callback, queue_size=1)
        rospy.loginfo("odom_to_sensor_pose: %s -> %s", odom_topic, pose_topic)

    def odom_callback(self, msg: Odometry) -> None:
        q = msg.pose.pose.orientation
        base_q = [q.x, q.y, q.z, q.w]
        base_rot = tft.quaternion_matrix(base_q)

        # 相机安装偏移定义在机体系下，需要先随机器人姿态旋转到 world 系，
        # 再叠加到 odom 位置上，得到真实传感器位置。
        offset_world_x = (
            base_rot[0][0] * self.sensor_offset_x
            + base_rot[0][1] * self.sensor_offset_y
            + base_rot[0][2] * self.sensor_offset_z
        )
        offset_world_y = (
            base_rot[1][0] * self.sensor_offset_x
            + base_rot[1][1] * self.sensor_offset_y
            + base_rot[1][2] * self.sensor_offset_z
        )
        offset_world_z = (
            base_rot[2][0] * self.sensor_offset_x
            + base_rot[2][1] * self.sensor_offset_y
            + base_rot[2][2] * self.sensor_offset_z
        )

        pose = PoseStamped()
        pose.header.stamp = msg.header.stamp if msg.header.stamp != rospy.Time(0) else rospy.Time.now()
        pose.header.frame_id = self.frame_id
        pose.pose.position.x = msg.pose.pose.position.x + offset_world_x
        pose.pose.position.y = msg.pose.pose.position.y + offset_world_y
        pose.pose.position.z = msg.pose.pose.position.z + offset_world_z

        # 传感器姿态 = 机体姿态 * 安装角偏差 * 可选光学坐标修正。
        # use_optical_frame=true 时，发布的是更适合深度相机投影的 optical frame。
        sensor_q = tft.quaternion_multiply(base_q, self.sensor_offset_q)
        if self.use_optical_frame:
            sensor_q = tft.quaternion_multiply(sensor_q, self.base_to_optical_q)
        pose.pose.orientation.x = sensor_q[0]
        pose.pose.orientation.y = sensor_q[1]
        pose.pose.orientation.z = sensor_q[2]
        pose.pose.orientation.w = sensor_q[3]
        self.pub.publish(pose)


if __name__ == "__main__":
    rospy.init_node("odom_to_sensor_pose")
    OdomToSensorPose()
    rospy.spin()
