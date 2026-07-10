#!/usr/bin/env python3
import rospy
from nav_msgs.msg import Odometry


class DynamicExplorationBox:
    def __init__(self):
        self.odom_topic = rospy.get_param("~odom_topic", "/Odometry_gazebo")
        self.expand_y = rospy.get_param("~expand_y", rospy.get_param("~trigger_y", 9.0))
        self.shrink_y = rospy.get_param("~shrink_y", self.expand_y - 0.2)
        self.gate_min_x = rospy.get_param("~gate_min_x", -0.9)
        self.gate_max_x = rospy.get_param("~gate_max_x", 0.9)
        self.box_min_x_before = rospy.get_param("~box_min_x_before", -2.0)
        self.box_max_x_before = rospy.get_param("~box_max_x_before", 2.0)
        self.box_min_x_after = rospy.get_param("~box_min_x_after", -25.0)
        self.box_max_x_after = rospy.get_param("~box_max_x_after", 25.0)
        self.target_namespace = rospy.get_param("~target_namespace", "/exploration_node")
        self.expanded = False

        self.box_min_param = self.target_namespace.rstrip("/") + "/sdf_map/box_min_x"
        self.box_max_param = self.target_namespace.rstrip("/") + "/sdf_map/box_max_x"
        self.set_box(self.box_min_x_before, self.box_max_x_before, "initial narrow")

        rospy.Subscriber(self.odom_topic, Odometry, self.odom_callback, queue_size=1)
        rospy.loginfo(
            "dynamic_exploration_box: narrow x [%.3f, %.3f] when y <= %.3f; wide x [%.3f, %.3f] when y >= %.3f; trigger only if robot x in [%.3f, %.3f]",
            self.box_min_x_before,
            self.box_max_x_before,
            self.shrink_y,
            self.box_min_x_after,
            self.box_max_x_after,
            self.expand_y,
            self.gate_min_x,
            self.gate_max_x,
        )
        rospy.loginfo(
            "dynamic_exploration_box: watching %s, writing %s and %s",
            self.odom_topic,
            self.box_min_param,
            self.box_max_param,
        )

    def odom_callback(self, msg):
        x = msg.pose.pose.position.x
        y = msg.pose.pose.position.y
        if x < self.gate_min_x or x > self.gate_max_x:
            return
        if not self.expanded and y >= self.expand_y:
            self.set_box(self.box_min_x_after, self.box_max_x_after, "expanded")
            self.expanded = True
            return
        if self.expanded and y <= self.shrink_y:
            self.set_box(self.box_min_x_before, self.box_max_x_before, "shrunk")
            self.expanded = False
            return

    def set_box(self, min_x, max_x, reason):
        rospy.set_param(self.box_min_param, min_x)
        rospy.set_param(self.box_max_param, max_x)
        rospy.logwarn(
            "dynamic_exploration_box: %s exploration x box to [%.3f, %.3f]",
            reason,
            min_x,
            max_x,
        )


if __name__ == "__main__":
    rospy.init_node("dynamic_exploration_box")
    DynamicExplorationBox()
    rospy.spin()
