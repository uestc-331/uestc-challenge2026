#!/usr/bin/env python3
import rospy
from nav_msgs.msg import Odometry
from std_msgs.msg import Empty


class DynamicExplorationBox:
    def __init__(self):
        # 这个节点把探索边界从“固定参数”变成“随任务阶段变化的参数”。
        # 主要用于厂房门/走廊场景：进门前限制 x，进门后放开 x，并在回家阶段恢复可达范围。
        self.odom_topic = rospy.get_param("~odom_topic", "/Odometry_gazebo")
        self.return_home_start_topic = rospy.get_param("~return_home_start_topic", "/planning/return_home_start")
        self.expand_y = rospy.get_param("~expand_y", rospy.get_param("~trigger_y", 9.0))
        self.shrink_y = rospy.get_param("~shrink_y", self.expand_y - 0.2)
        self.gate_min_x = rospy.get_param("~gate_min_x", -0.9)
        self.gate_max_x = rospy.get_param("~gate_max_x", 0.9)
        self.box_min_x_before = rospy.get_param("~box_min_x_before", -2.0)
        self.box_max_x_before = rospy.get_param("~box_max_x_before", 2.0)
        self.box_min_y_before = rospy.get_param("~box_min_y_before", -2.5)
        self.box_max_y_before = rospy.get_param("~box_max_y_before", 50.0)
        self.box_min_x_after = rospy.get_param("~box_min_x_after", -25.0)
        self.box_max_x_after = rospy.get_param("~box_max_x_after", 25.0)
        self.box_min_y_after = rospy.get_param("~box_min_y_after", self.shrink_y)
        self.box_max_y_after = rospy.get_param("~box_max_y_after", self.box_max_y_before)
        self.target_namespace = rospy.get_param("~target_namespace", "/exploration_node")
        self.expanded = False
        self.returning_home = False

        ns = self.target_namespace.rstrip("/")
        # exploration_node 读取的是私有参数，因此这里直接写 /exploration_node/sdf_map/...。
        # FUEL 的后续地图边界判断会使用这些更新后的参数。
        self.box_min_x_param = ns + "/sdf_map/box_min_x"
        self.box_max_x_param = ns + "/sdf_map/box_max_x"
        self.box_min_y_param = ns + "/sdf_map/box_min_y"
        self.box_max_y_param = ns + "/sdf_map/box_max_y"
        self.set_box(
            self.box_min_x_before,
            self.box_max_x_before,
            self.box_min_y_before,
            self.box_max_y_before,
            "initial narrow",
        )

        rospy.Subscriber(self.odom_topic, Odometry, self.odom_callback, queue_size=1)
        rospy.Subscriber(self.return_home_start_topic, Empty, self.return_home_start_callback, queue_size=1)
        rospy.loginfo(
            "dynamic_exploration_box: before box x [%.3f, %.3f], y [%.3f, %.3f]; after box x [%.3f, %.3f], y [%.3f, %.3f]; expand y >= %.3f, shrink y <= %.3f; trigger only if robot x in [%.3f, %.3f]",
            self.box_min_x_before,
            self.box_max_x_before,
            self.box_min_y_before,
            self.box_max_y_before,
            self.box_min_x_after,
            self.box_max_x_after,
            self.box_min_y_after,
            self.box_max_y_after,
            self.expand_y,
            self.shrink_y,
            self.gate_min_x,
            self.gate_max_x,
        )
        rospy.loginfo(
            "dynamic_exploration_box: watching %s and %s, writing %s/%s and %s/%s",
            self.odom_topic,
            self.return_home_start_topic,
            self.box_min_x_param,
            self.box_max_x_param,
            self.box_min_y_param,
            self.box_max_y_param,
        )

    def odom_callback(self, msg):
        x = msg.pose.pose.position.x
        y = msg.pose.pose.position.y

        # 只有机器人位于门洞附近的 x 范围内，才允许触发 box 切换。
        # 这样可以避免机器人在房间两侧或回绕过程中误触发探索范围变化。
        if x < self.gate_min_x or x > self.gate_max_x:
            return

        if self.returning_home:
            # return-home 刚开始时先放宽 x/y，保证 A* 能从房间里规划回门口。
            # 等机器人真正穿过门洞回到 shrink_y 以下，再恢复初始窄范围。
            if y <= self.shrink_y:
                self.set_box(
                    self.box_min_x_before,
                    self.box_max_x_before,
                    self.box_min_y_before,
                    self.box_max_y_before,
                    "return-home gate shrink",
                )
                self.expanded = False
                self.returning_home = False
            return

        if not self.expanded and y >= self.expand_y:
            # 正向进入厂房/房间后扩大探索区域，允许探索左右房间。
            self.set_box(
                self.box_min_x_after,
                self.box_max_x_after,
                self.box_min_y_after,
                self.box_max_y_after,
                "expanded",
            )
            self.expanded = True
            return
        if self.expanded and y <= self.shrink_y:
            # 非 return-home 情况下，如果机器人退回门外，则恢复窄范围。
            self.set_box(
                self.box_min_x_before,
                self.box_max_x_before,
                self.box_min_y_before,
                self.box_max_y_before,
                "shrunk",
            )
            self.expanded = False
            return

    def return_home_start_callback(self, _msg):
        # FUEL 宣布开始 return-home 时，机器人可能还在宽 x 房间区域。
        # 这里先临时恢复完整 y 范围并保持宽 x，避免回家路径被 box 裁掉。
        self.set_box(
            self.box_min_x_after,
            self.box_max_x_after,
            self.box_min_y_before,
            self.box_max_y_before,
            "return-home wide-x full-y",
        )
        self.expanded = True
        self.returning_home = True

    def set_box(self, min_x, max_x, min_y, max_y, reason):
        rospy.set_param(self.box_min_x_param, min_x)
        rospy.set_param(self.box_max_x_param, max_x)
        rospy.set_param(self.box_min_y_param, min_y)
        rospy.set_param(self.box_max_y_param, max_y)
        rospy.logwarn(
            "dynamic_exploration_box: %s exploration box to x [%.3f, %.3f], y [%.3f, %.3f]",
            reason,
            min_x,
            max_x,
            min_y,
            max_y,
        )


if __name__ == "__main__":
    rospy.init_node("dynamic_exploration_box")
    DynamicExplorationBox()
    rospy.spin()
