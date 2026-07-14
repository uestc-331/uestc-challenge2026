#!/usr/bin/env python3
"""ROS node for detecting red spherical danger sources from RealSense RGB-D."""

from __future__ import annotations

import json
import os
import time
from typing import List, Optional

import numpy as np

import rospy
import tf2_ros
import tf2_geometry_msgs  # noqa: F401 - registers geometry conversions
from cv_bridge import CvBridge
from geometry_msgs.msg import Point, PointStamped
from message_filters import ApproximateTimeSynchronizer, Subscriber
from nav_msgs.msg import Odometry
from sensor_msgs.msg import CameraInfo, Image
from std_msgs.msg import Header
from visualization_msgs.msg import Marker, MarkerArray

from danger_detector.vision import VisionConfig, cluster_world_points, detections_from_rgbd, normalize_depth_image


class DangerDetectorNode:
    def __init__(self) -> None:
        self.bridge = CvBridge()
        self.config = VisionConfig(
            min_cluster_observations=rospy.get_param("~min_cluster_observations", 2),
            cluster_radius_m=rospy.get_param("~cluster_radius_m", 0.75),
            max_depth_m=rospy.get_param("~max_depth_m", 8.0),
        )
        self.output_file = rospy.get_param("~output_file", self._default_output_file())
        self.world_frame = rospy.get_param("~world_frame", "world")
        self.camera_frame = rospy.get_param("~camera_frame", "")
        self.process_every_n = max(1, int(rospy.get_param("~process_every_n", 1)))
        self.write_period_s = float(rospy.get_param("~write_period_s", 2.0))
        self.max_observations = int(rospy.get_param("~max_observations", 5000))

        self.tf_buffer = tf2_ros.Buffer(cache_time=rospy.Duration(10.0))
        self.tf_listener = tf2_ros.TransformListener(self.tf_buffer)
        self.latest_odom: Optional[Odometry] = None
        self.world_points: List[np.ndarray] = []
        self.frame_count = 0
        self.started_at = time.time()
        self.last_write = 0.0

        self.marker_pub = rospy.Publisher("/vision/danger_candidates", MarkerArray, queue_size=1, latch=True)
        rospy.Subscriber("/Odometry_gazebo", Odometry, self._odom_cb, queue_size=5)

        rgb_topic = rospy.get_param("~rgb_topic", "/real_sense/rgb/image_raw")
        depth_topic = rospy.get_param("~depth_topic", "/real_sense/depth/image_raw")
        camera_info_topic = rospy.get_param("~camera_info_topic", "/real_sense/rgb/camera_info")

        self.sync = ApproximateTimeSynchronizer(
            [
                Subscriber(rgb_topic, Image),
                Subscriber(depth_topic, Image),
                Subscriber(camera_info_topic, CameraInfo),
            ],
            queue_size=8,
            slop=0.08,
        )
        self.sync.registerCallback(self._rgbd_cb)
        rospy.on_shutdown(self._write_results)

        rospy.loginfo("danger_detector listening on %s, %s, %s", rgb_topic, depth_topic, camera_info_topic)

    def _default_output_file(self) -> str:
        package_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
        return os.path.join(package_root, "results", "detected_danger.json")

    def _odom_cb(self, msg: Odometry) -> None:
        self.latest_odom = msg

    def _rgbd_cb(self, rgb_msg: Image, depth_msg: Image, camera_info: CameraInfo) -> None:
        self.frame_count += 1
        if self.frame_count % self.process_every_n != 0:
            return

        try:
            rgb = self.bridge.imgmsg_to_cv2(rgb_msg, desired_encoding="rgb8")
            depth = normalize_depth_image(self.bridge.imgmsg_to_cv2(depth_msg, desired_encoding="passthrough"))
        except Exception as exc:
            rospy.logwarn_throttle(5.0, "failed to convert RGB-D images: %s", exc)
            return

        intrinsics = (camera_info.K[0], camera_info.K[4], camera_info.K[2], camera_info.K[5])
        detections = detections_from_rgbd(rgb, depth, intrinsics, self.config)
        if not detections:
            self._periodic_write()
            return

        frame_id = self.camera_frame or rgb_msg.header.frame_id or camera_info.header.frame_id
        for detection in detections:
            world_point = self._camera_to_world(detection.position_camera, frame_id, rgb_msg.header.stamp)
            if world_point is None:
                continue
            self.world_points.append(world_point)

        if len(self.world_points) > self.max_observations:
            self.world_points = self.world_points[-self.max_observations :]

        self._publish_markers()
        self._periodic_write()

    def _camera_to_world(self, point_camera: np.ndarray, frame_id: str, stamp: rospy.Time) -> Optional[np.ndarray]:
        point = PointStamped()
        point.header = Header(stamp=stamp, frame_id=frame_id)
        point.point = Point(float(point_camera[0]), float(point_camera[1]), float(point_camera[2]))

        try:
            transformed = self.tf_buffer.transform(point, self.world_frame, rospy.Duration(0.05))
            return np.array([transformed.point.x, transformed.point.y, transformed.point.z], dtype=np.float32)
        except Exception as exc:
            rospy.logwarn_throttle(5.0, "TF transform %s -> %s failed: %s", frame_id, self.world_frame, exc)
            return self._fallback_camera_to_world(point_camera)

    def _fallback_camera_to_world(self, point_camera: np.ndarray) -> Optional[np.ndarray]:
        if self.latest_odom is None:
            return None

        # Fallback assumes camera optical Z is robot forward, X is robot right,
        # and Y is down. TF is preferred whenever available.
        body_point = np.array([point_camera[2] + 0.28, -point_camera[0], -point_camera[1] + 0.043], dtype=np.float32)
        pose = self.latest_odom.pose.pose
        q = pose.orientation
        yaw = np.arctan2(2.0 * (q.w * q.z + q.x * q.y), 1.0 - 2.0 * (q.y * q.y + q.z * q.z))
        rot = np.array(
            [
                [np.cos(yaw), -np.sin(yaw), 0.0],
                [np.sin(yaw), np.cos(yaw), 0.0],
                [0.0, 0.0, 1.0],
            ],
            dtype=np.float32,
        )
        base = np.array([pose.position.x, pose.position.y, pose.position.z], dtype=np.float32)
        return base + rot @ body_point

    def _clustered_points(self) -> List[np.ndarray]:
        return cluster_world_points(self.world_points, self.config)

    def _publish_markers(self) -> None:
        marker_array = MarkerArray()
        now = rospy.Time.now()
        for idx, point in enumerate(self._clustered_points()):
            marker = Marker()
            marker.header.frame_id = self.world_frame
            marker.header.stamp = now
            marker.ns = "danger_detector"
            marker.id = idx
            marker.type = Marker.SPHERE
            marker.action = Marker.ADD
            marker.pose.position.x = float(point[0])
            marker.pose.position.y = float(point[1])
            marker.pose.position.z = float(point[2])
            marker.pose.orientation.w = 1.0
            marker.scale.x = marker.scale.y = marker.scale.z = 0.3
            marker.color.r = 1.0
            marker.color.g = 0.0
            marker.color.b = 0.0
            marker.color.a = 0.8
            marker_array.markers.append(marker)
        self.marker_pub.publish(marker_array)

    def _periodic_write(self) -> None:
        now = time.time()
        if now - self.last_write >= self.write_period_s:
            self._write_results()
            self.last_write = now

    def _write_results(self) -> None:
        os.makedirs(os.path.dirname(self.output_file), exist_ok=True)
        detections = [{"position": [round(float(v), 3) for v in p.tolist()]} for p in self._clustered_points()]
        payload = {
            "exploration_time": round(time.time() - self.started_at, 3),
            "detected_danger_sources": detections,
        }
        tmp_file = self.output_file + ".tmp"
        with open(tmp_file, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(tmp_file, self.output_file)


def main() -> None:
    rospy.init_node("danger_detector")
    DangerDetectorNode()
    rospy.spin()


if __name__ == "__main__":
    main()
