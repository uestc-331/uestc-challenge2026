# FUEL 接入四足狗修改说明

本文记录将 FUEL 探索规划算法接入 `/home/uestc/tzb/uestc-challenge2026` 四足狗仿真的改动。

## 改动概览

这次接入采用“FUEL 负责探索与轨迹生成，适配层负责转换四足狗速度命令”的方式：

- FUEL 输出 `quadrotor_msgs/PositionCommand`。
- 新增适配节点将 FUEL 的位置/速度/yaw 命令转换为四足狗使用的 `/cmd_vel`。
- 仿真里程计 `/Odometry_gazebo` 同时提供给 FUEL 和适配器。
- 默认使用 `/livox/Pointcloud2` 作为 FUEL 建图点云输入。
- 探索按 2.5D 单楼层处理，固定在狗身/雷达高度附近规划。

## 新增文件

### `src/fuel/fuel_dog_adapter`

新增 ROS 包 `fuel_dog_adapter`，用于放四足狗接 FUEL 的桥接节点和启动文件。

### `src/fuel/fuel_dog_adapter/scripts/fuel_poscmd_to_cmdvel.py`

作用：

- 订阅 `/position_cmd`，类型为 `quadrotor_msgs/PositionCommand`。
- 订阅 `/Odometry_gazebo`，类型为 `nav_msgs/Odometry`。
- 发布 `/cmd_vel`，类型为 `geometry_msgs/Twist`。

主要逻辑：

- 读取 FUEL 给出的目标位置、目标速度和目标 yaw。
- 根据当前里程计计算 XY 位置误差。
- 将世界坐标系速度转换到四足狗机体系。
- 输出 `linear.x`、`linear.y`、`angular.z`。
- 对线速度、横向速度、角速度做限幅。
- 对速度变化做加速度限制。
- 如果 `/position_cmd` 或 `/Odometry_gazebo` 超时，则自动发布零速度。

默认关键参数：

- `max_linear_speed`: `0.6`
- `max_lateral_speed`: `0.6`
- `max_angular_speed`: `0.8`
- `linear_acc_limit`: `0.8`
- `command_timeout`: `0.5`
- `odom_timeout`: `0.5`

### `src/fuel/fuel_dog_adapter/scripts/odom_to_sensor_pose.py`

作用：

- 订阅 `/Odometry_gazebo`。
- 发布 `/fuel_dog_adapter/sensor_pose`。
- 给 FUEL 的 `map_ros` 提供传感器位姿。

默认传感器高度偏移：

- `sensor_offset_z`: `0.45`

### `src/fuel/fuel_dog_adapter/launch/fuel_dog_exploration.launch`

四足狗探索启动文件。

启动节点：

- `exploration_node`
- `traj_server`
- `odom_to_sensor_pose`
- `fuel_poscmd_to_cmdvel`

默认话题：

- 里程计：`/Odometry_gazebo`
- 点云：`/livox/Pointcloud2`
- FUEL 传感器位姿：`/fuel_dog_adapter/sensor_pose`
- 四足狗速度命令：`/cmd_vel`

默认 2.5D 参数：

- `init_z`: `0.75`
- `map_size_z`: `2.5`
- `box_min_z`: `0.0`
- `box_max_z`: `2.0`
- `max_vel`: `0.6`
- `max_acc`: `0.8`

## 修改文件

### `src/fuel/fuel_planner/poly_traj/package.xml`

删除了不存在的 `swarmtal_msgs` 依赖。

原因：

- 当前工程和 FUEL 目录里都没有 `swarmtal_msgs` 包。
- 四足狗接入不使用原无人机 `swarmtal_msgs/drone_onboard_command` 命令链路。
- 保留该依赖会导致 catkin 配置失败。

### `src/fuel/fuel_planner/bspline_opt/CMakeLists.txt`

将原来写死的 NLOPT 路径：

```cmake
set(NLOPT_INCLUDE_DIR "/usr/local/include")
set(NLOPT_LIBRARY "/usr/local/lib/libnlopt.so")
```

改为自动查找：

```cmake
find_path(NLOPT_INCLUDE_DIR nlopt.hpp)
find_library(NLOPT_LIBRARY nlopt)
```

原因：

- 当前机器实际库位置是 `/usr/lib/x86_64-linux-gnu/libnlopt.so`。
- 写死 `/usr/local/lib/libnlopt.so` 会导致 `bspline_opt` 链接失败。

### `src/unitree_guide/unitree_guide/unitree_guide/CMakeLists.txt`

将：

```cmake
set(MOVE_BASE OFF)
```

改为：

```cmake
set(MOVE_BASE ON)
```

原因：

- 需要编译 `State_move_base`。
- `State_move_base` 会订阅 `/cmd_vel`。
- FUEL 适配器最终就是通过 `/cmd_vel` 控制四足狗。

### `src/unitree_guide/unitree_guide/unitree_guide/src/state_from_gazebo.cpp`

修复里程计速度赋值错误。

原来代码把三维线速度都写到了 `linear.x`，角速度都写到了 `angular.x`。

现在改为：

```cpp
Odom.twist.twist.linear.x = transformed_linear_vel.x();
Odom.twist.twist.linear.y = transformed_linear_vel.y();
Odom.twist.twist.linear.z = transformed_linear_vel.z();

Odom.twist.twist.angular.x = transformed_angular_vel.x();
Odom.twist.twist.angular.y = transformed_angular_vel.y();
Odom.twist.twist.angular.z = transformed_angular_vel.z();
```

原因：

- FUEL 和速度适配器都依赖 `/Odometry_gazebo`。
- 速度分量错误会影响轨迹跟踪和状态估计。

## 启动方式

### 终端 1：启动四足狗仿真

```bash
cd /home/uestc/tzb/uestc-challenge2026
source devel/setup.bash
roslaunch unitree_guide gazeboSim.launch
```

如果使用多楼层仿真：

```bash
roslaunch unitree_guide multi_floor_gazeboSim.launch
```

启动后在 `junior_ctrl` 控制窗口中进入可接收 `/cmd_vel` 的运动状态。

### 终端 2：启动 FUEL 四足狗探索

视觉深度图探索使用：

```bash
cd /home/uestc/tzb/uestc-challenge2026
source devel/setup.bash
roslaunch fuel_dog_adapter fuel_dog_visual_exploration.launch
// 触发探索
rostopic pub -1 /waypoint_generator/waypoints nav_msgs/Path "header:
  frame_id: 'world'
poses:
- header:
    frame_id: 'world'
  pose:
    position:
      x: 0.0
      y: 0.0
      z: 1.0
    orientation:
      w: 1.0"
```

该模式订阅 `/real_sense/depth/image_raw`，通过 `/fuel_dog_adapter/sensor_pose` 提供相机光学坐标系位姿，适合使用 Gazebo 里 A1 模型自带的 RealSense/Kinect 深度相机。

点云探索使用：

```bash
cd /home/uestc/tzb/uestc-challenge2026
source devel/setup.bash
roslaunch fuel_dog_adapter fuel_dog_exploration.launch
```

如果点云话题不是 `/livox/Pointcloud2`，可指定：

```bash
roslaunch fuel_dog_adapter fuel_dog_exploration.launch cloud_topic:=/你的点云话题
```

## 验证命令

检查里程计：

```bash
rostopic hz /Odometry_gazebo
```

检查点云：

```bash
rostopic hz /livox/Pointcloud2
```

检查视觉深度图：

```bash
rostopic hz /real_sense/depth/image_raw
```

检查 FUEL 输出：

```bash
rostopic echo /position_cmd
```

检查四足狗速度命令：

```bash
rostopic echo /cmd_vel
```

检查 FUEL 建图：

```bash
rostopic hz /sdf_map/occupancy_local
```

## RViz 可视化

单独开一个终端启动 RViz：

```bash
cd /home/uestc/tzb/uestc-challenge2026
source devel/setup.bash
roslaunch fuel_dog_adapter rviz_fuel_dog.launch
```

这套 RViz 默认显示：

- `/sdf_map/occupancy_local`：FUEL 当前局部占据地图。
- `/sdf_map/occupancy_local_inflate`：膨胀后的障碍物范围，用来看规划安全距离。
- `/sdf_map/depth_cloud`：深度图投影出来的点云，视觉探索时最重要。
- `/planning_vis/frontier`：当前检测到的未知边界。
- `/planning_vis/viewpoints`：FUEL 选择/评估的观察点。
- `/planning_vis/trajectory`：FUEL 规划出的 B 样条轨迹。
- `/fuel_dog_adapter/generated_path`：由 `/position_cmd` 累计出的生成路径。
- `/planning/travel_traj`：轨迹服务器执行轨迹。
- `/fuel_dog_adapter/executed_path`：由 `/Odometry_gazebo` 累计出的实际执行路径。
- `/planning/position_cmd_vis`：FUEL 当前输出的位置命令。
- `/Odometry_gazebo`：四足狗里程计箭头。
- `/fuel_dog_adapter/sensor_pose`：送给 FUEL 的相机位姿。
- `/fuel_dog_adapter/cmd_vel_marker`：适配器输出的 `/cmd_vel` 速度箭头。

如果 RViz 中没有地图或轨迹，优先检查：

```bash
rostopic hz /sdf_map/occupancy_local
rostopic echo -n1 /planning_vis/frontier
rostopic echo -n1 /position_cmd
```

## 四足狗高度参数

如果通过下面命令看到仿真中四足狗 base 高度约为 `0.4m`：

```bash
rostopic echo -n1 /Odometry_gazebo/pose/pose/position
```

视觉探索版 FUEL 已按四足狗高度调整：

- `fuel_dog_visual_exploration.launch` 中 `init_z=0.45`：轨迹服务器初始规划高度。
- `fuel_dog_visual_exploration.launch` 中 `viewpoint_z=0.45`：FUEL 生成候选观察点的固定高度。
- `fuel_dog_visual_exploration.launch` 中 `box_min_z=0.15`、`box_max_z=1.2`：限制探索/建图关注的高度范围。
- `fuel_dog_visual_exploration.launch` 中 `map_size_z=1.4`：降低 FUEL 地图 z 尺寸。
- `algorithm.xml` 中 `frontier/cluster_size_z=0.5`：减少把高低差很大的 frontier 混成同一组。
- `frontier/force_viewpoint_z=true` 只在视觉四足狗 launch 中打开，FUEL 原始/点云入口默认不强制固定高度。

如果 RViz 里 `/sdf_map/depth_cloud` 和实际相机高度不一致，优先微调：

```bash
roslaunch fuel_dog_adapter fuel_dog_visual_exploration.launch viewpoint_z:=0.45 init_z:=0.45
```

经验范围：

- base 高度约 `0.4m`：`viewpoint_z=0.42~0.50`
- 低头看地面太多：适当增大 `viewpoint_z`
- 候选点又飘到空中：确认 `force_viewpoint_z=true`

## 走几步就 FINISH 的处理

如果终端出现：

```text
[FSM]: state: FINISH
finish exploration.
to visit: 0, dormant: 0
```

这表示 FUEL 认为“没有可访问 frontier 了”，不是 `/cmd_vel` 控制器主动停。视觉四足狗低高度探索时，frontier 容易被原版无人机阈值过滤掉，因此视觉 launch 已放宽这些参数：

- `frontier_cluster_min=20`：原版 `100`，小门口/窄缝 frontier 也能保留。
- `frontier_min_visib_num=3`：原版 `15`，低视角下少量可见未知也能生成观察点。
- `frontier_min_candidate_dist=0.45`：候选点可以离当前位姿更近。
- `frontier_min_candidate_clearance=0.12`：降低候选点附近未知/障碍过滤强度。
- `frontier_candidate_rmin=0.8`、`frontier_candidate_rmax=1.8`：候选观察点距离更适合四足狗和室内门口。
- `box_min_z=0.05`、`box_max_z=1.5`：保留四足狗低视角能看到的门、墙、障碍高度。

如果仍然提前 FINISH，可以继续放宽：

```bash
roslaunch fuel_dog_adapter fuel_dog_visual_exploration.launch \
  frontier_cluster_min:=10 \
  frontier_min_visib_num:=1 \
  frontier_min_candidate_clearance:=0.08
```

如果 frontier 太碎、规划来回抖，再把这些值调回大一些。

## 编译验证

已执行：

```bash
cd /home/uestc/tzb/uestc-challenge2026
source /opt/ros/noetic/setup.bash
catkin_make
```

结果：

- 编译通过。
- `exploration_node`、`traj_server`、`junior_ctrl`、`state_from_gazebo`、`fuel_dog_adapter` 均成功构建。
- 编译过程中有 FUEL 原始代码和 PCL/VTK 的 warning，但没有阻塞编译。

## 注意事项

- 当前版本优先支持 Gazebo 仿真，不建议直接上真机。
- 当前版本按 2.5D 单楼层探索处理，没有接入电梯、多楼层切换或楼梯策略。
- `/livox/Pointcloud2` 需要由工程已有的 `pointcloud2livox.py` 正常发布。
- 如果 `/cmd_vel` 有输出但狗不动，需要确认 `junior_ctrl` 是否已进入接收速度命令的控制状态。
