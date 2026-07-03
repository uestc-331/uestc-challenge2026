# FUEL 源码解析

本文档说明当前工程中 `/src/fuel` 下 FUEL 相关代码的结构、每个功能包的作用、核心代码位置，以及它们在探索流程中的数据流。

## 1. 总体理解

FUEL 是一个面向无人机的主动探索系统。它的核心任务是：

1. 接收传感器数据，构建局部占据地图和 ESDF（一张“每个位置离最近障碍物有多远”的地图。）。
2. 从已知自由空间和未知空间的边界中提取 frontier。
3. 为 frontier 生成候选观察点 viewpoint。
4. 用 TSP/局部图搜索决定下一个最值得去的观察点。
5. 用 A*/kinodynamic/topological planner 生成路径。
6. 用 B 样条优化生成平滑轨迹。
7. 通过 `traj_server` 把轨迹采样成 `/position_cmd`。

在原版 FUEL 中，`/position_cmd` 是给无人机控制器用的。当前工程中新增了 `fuel_dog_adapter`，把 `/position_cmd` 转成四足狗可用的 `/cmd_vel`。

## 2. 当前 FUEL 数据流

视觉四足狗探索的数据流是：

```text
/real_sense/depth/image_raw
        +
/fuel_dog_adapter/sensor_pose
        |
        v
plan_env::MapROS
        |
        v
plan_env::SDFMap / EDTEnvironment
        |
        v
active_perception::FrontierFinder
        |
        v
exploration_manager::FastExplorationManager
        |
        v
plan_manage::FastPlannerManager
        |
        v
bspline / bspline_opt
        |
        v
/planning/bspline
        |
        v
traj_server
        |
        v
/position_cmd
        |
        v
fuel_dog_adapter
        |
        v
/cmd_vel
```

如果使用点云模式，入口从 `/map_ros/depth` 换成 `/map_ros/cloud`。

## 3. exploration_manager

路径：

```text
src/fuel/fuel_planner/exploration_manager
```

作用：FUEL 探索任务的最高层调度器。它决定什么时候规划、什么时候发布轨迹、什么时候认为探索结束。

核心文件：

```text
src/exploration_node.cpp
src/fast_exploration_fsm.cpp
src/fast_exploration_manager.cpp
launch/algorithm.xml
```

### exploration_node.cpp

入口节点：

```cpp
FastExplorationFSM expl_fsm;
expl_fsm.init(nh);
```

它只负责启动 FSM，真正逻辑在 `FastExplorationFSM`。

### fast_exploration_fsm.cpp

核心状态机。主要状态：

```text
INIT
WAIT_TRIGGER
PLAN_TRAJ
PUB_TRAJ
EXEC_TRAJ
FINISH
```

典型日志：

```text
[FSM]: state: WAIT_TRIGGER
[FSM]: state: PLAN_TRAJ
[FSM]: state: EXEC_TRAJ
[FSM]: state: FINISH
```

关键逻辑：

- `WAIT_TRIGGER`：等待 `/waypoint_generator/waypoints` 触发。
- `PLAN_TRAJ`：调用探索规划器。
- `PUB_TRAJ`：发布 `/planning/bspline`。
- `EXEC_TRAJ`：等待轨迹执行，并根据 frontier 是否变化重规划。
- `FINISH`：没有可访问 frontier，探索结束。

如果终端出现：

```text
No coverable frontier.
finish exploration.
```

说明 frontier 检测阶段没有可访问目标了。

### fast_exploration_manager.cpp

探索核心管理器。关键函数：

```cpp
FastExplorationManager::planExploreMotion(...)
```

它完成：

1. 调用 `frontier_finder_->searchFrontiers()` 搜索 frontier。
2. 调用 `computeFrontiersToVisit()` 生成可访问 frontier。
3. 选择候选 viewpoint。
4. 求全局 tour 和局部 refined tour。
5. 选出 `Next view`。
6. 调用 planner 生成到 next viewpoint 的轨迹。

常见日志：

```text
Frontier: N
Next view: x y z yaw
No path to next viewpoint
plan fail
```

### algorithm.xml

FUEL 探索算法参数集中配置文件。包含：

- 地图参数：`sdf_map/*`
- 深度图参数：`map_ros/*`
- frontier 参数：`frontier/*`
- 探索参数：`exploration/*`
- 轨迹规划参数：`manager/*`
- 搜索参数：`search/*`、`astar/*`
- B 样条优化参数：`optimization/*`、`bspline/*`

当前为四足狗视觉探索增加了这些可调参数：

```text
force_viewpoint_z
viewpoint_z
frontier_cluster_min
frontier_cluster_size_z
frontier_min_candidate_dist
frontier_min_candidate_clearance
frontier_candidate_rmin
frontier_candidate_rmax
frontier_min_visib_num
```

这些参数由 `fuel_dog_visual_exploration.launch` 传入。

## 4. active_perception

路径：

```text
src/fuel/fuel_planner/active_perception
```

作用：主动感知模块。负责 frontier 提取、候选观察点生成、视野判断、可见性检查、方向规划。

核心文件：

```text
src/frontier_finder.cpp
src/perception_utils.cpp
src/graph_node.cpp
src/heading_planner.cpp
src/traj_visibility.cpp
```

### frontier_finder.cpp

这是 FUEL 探索中非常核心的文件。

主要实现：

- 搜索 frontier：已知自由空间旁边的未知空间边界。
- 聚类 frontier。
- 给每个 frontier 生成候选 viewpoint。
- 计算每个 viewpoint 能看到多少未知区域。
- 维护 frontier 的 cost matrix。

关键函数：

```cpp
searchFrontiers()
expandFrontier()
splitLargeFrontiers()
computeFrontiersToVisit()
sampleViewpoints()
countVisibleCells()
updateFrontierCostMatrix()
```

运行中常见打印：

```text
Before remove
After remove
new num
to visit
cost mat size before remove
cost mat size final
```

这些都来自 frontier 管理和代价矩阵更新。

当前为四足狗增加的小补丁：

```text
frontier/force_viewpoint_z
frontier/viewpoint_z
```

作用：让候选 viewpoint 的 z 固定在四足狗相机高度附近，例如 `0.45m`，避免生成无人机式空中观察点。

### perception_utils.cpp

作用：相机视场模型和 FOV 判断。

它根据相机位姿和 yaw 判断某个点是否在视野内，并生成视锥体可视化。

相关参数：

```text
perception_utils/top_angle
perception_utils/left_angle
perception_utils/right_angle
perception_utils/max_dist
perception_utils/vis_dist
```

### graph_node.cpp

作用：给 viewpoint 之间构建图结构，并搜索 viewpoint 间路径。

在局部 tour refine 时会用到。

如果出现：

```text
Local tour graph
Node: ...
edge: ...
```

说明正在构建局部 viewpoint 图。

### heading_planner.cpp

作用：更细的朝向规划和信息增益评估。当前主探索流程里主要还是通过 frontier viewpoint yaw 和 B 样条 yaw 规划使用方向信息。

### traj_visibility.cpp

作用：轨迹可见性约束相关工具，用于判断轨迹上的点对目标/未知区域的可见性。

## 5. plan_env

路径：

```text
src/fuel/fuel_planner/plan_env
```

作用：环境建图和距离场。它是 FUEL 规划的地图基础。

核心文件：

```text
src/map_ros.cpp
src/sdf_map.cpp
src/edt_environment.cpp
src/raycast.cpp
src/obj_generator.cpp
src/obj_predictor.cpp
```

### map_ros.cpp

ROS 传感器接口层。

订阅：

```text
/map_ros/depth
/map_ros/cloud
/map_ros/pose
```

发布：

```text
/sdf_map/occupancy_all
/sdf_map/occupancy_local
/sdf_map/occupancy_local_inflate
/sdf_map/unknown
/sdf_map/esdf
/sdf_map/update_range
/sdf_map/depth_cloud
```

视觉探索时，`depthPoseCallback()` 会把深度图按相机内参投影成三维点：

```cpp
pt_cur(0) = (u - cx_) * depth / fx_;
pt_cur(1) = (v - cy_) * depth / fy_;
pt_cur(2) = depth;
pt_world = camera_r * pt_cur + camera_pos_;
```

所以视觉模式必须提供：

- 深度图
- 相机位姿
- 正确的 `fx/fy/cx/cy`
- 正确的相机光学坐标系

### sdf_map.cpp

占据地图和 ESDF 核心。

主要实现：

- 地图体素初始化。
- log-odds 占据概率更新。
- raycasting 融合传感器点云。
- 障碍物膨胀。
- ESDF 距离场更新。
- 地图边界和 box 限制。

关键参数：

```text
sdf_map/resolution
sdf_map/map_size_x
sdf_map/map_size_y
sdf_map/map_size_z
sdf_map/obstacles_inflation
sdf_map/p_hit
sdf_map/p_miss
sdf_map/p_occ
sdf_map/max_ray_length
sdf_map/box_min_x/y/z
sdf_map/box_max_x/y/z
```

### edt_environment.cpp

对 `SDFMap` 的封装，给规划器提供距离查询接口。

路径搜索和轨迹优化会频繁查询：

```text
某点是否碰撞
某点到障碍物距离
距离梯度
```

### raycast.cpp

射线遍历工具。用于：

- 深度/点云融合时从相机到测量点更新 free/occupied。
- frontier 可见性检测。
- 判断观察点到 frontier cell 是否被遮挡。

### obj_generator.cpp / obj_predictor.cpp

动态障碍物测试相关工具。当前四足狗探索主流程基本不用。

## 6. path_searching

路径：

```text
src/fuel/fuel_planner/path_searching
```

作用：路径搜索模块。

核心文件：

```text
src/astar.cpp
src/astar2.cpp
src/kinodynamic_astar.cpp
src/topo_prm.cpp
```

### astar.cpp / astar2.cpp

几何 A* 搜索。用于在占据地图中寻找无碰撞路径。

`astar2.cpp` 常用于拓扑路径和几何路径搜索。

### kinodynamic_astar.cpp

考虑动力学约束的 A*。无人机版本会考虑速度、加速度、时间步。

对于四足狗来说，这部分仍然带有无人机基因。当前我们没有重写它，只是通过参数降低速度、加速度，并用适配器把输出转成 `/cmd_vel`。

### topo_prm.cpp

拓扑 PRM 路径搜索。用于生成多条拓扑不同的候选路径，给后续 B 样条优化选择。

当 FUEL 尝试绕障、找不同通路时，会用到这个模块。

## 7. plan_manage

路径：

```text
src/fuel/fuel_planner/plan_manage
```

作用：轨迹规划管理和轨迹服务器。

核心文件：

```text
src/planner_manager.cpp
src/kino_replan_fsm.cpp
src/topo_replan_fsm.cpp
src/fast_planner_node.cpp
src/traj_server.cpp
launch/kino_algorithm.xml
launch/topo_algorithm.xml
```

### planner_manager.cpp

轨迹规划核心调度器。

主要职责：

- 调用 path_searching 找初始路径。
- 调用 bspline_opt 优化 B 样条。
- 做碰撞检查。
- 规划 yaw。
- 生成最终轨迹。

探索管理器选出 `Next view` 后，真正把它变成轨迹的是这里。

### traj_server.cpp

轨迹执行服务器。

订阅：

```text
planning/bspline
planning/replan
planning/new
/odom_world
```

发布：

```text
/position_cmd
planning/position_cmd_vis
planning/travel_traj
```

它会定时采样 B 样条轨迹，生成 `quadrotor_msgs/PositionCommand`。

原本这是给无人机控制器用的。当前四足狗接入时：

```text
/position_cmd -> fuel_poscmd_to_cmdvel.py -> /cmd_vel
```

### kino_replan_fsm.cpp / topo_replan_fsm.cpp

这是 FUEL/FAST-Planner 原始的非探索重规划 FSM。当前主要探索入口用的是 `exploration_manager`，但这些文件仍然提供局部重规划和测试功能。

## 8. bspline

路径：

```text
src/fuel/fuel_planner/bspline
```

作用：B 样条轨迹数学表示。

核心文件：

```text
src/non_uniform_bspline.cpp
```

实现功能：

- 非均匀 B 样条轨迹表示。
- 轨迹求值。
- 求导得到速度、加速度。
- 时间重分配。
- 控制点管理。

FUEL 最终轨迹不是普通折线，而是 B 样条曲线。

## 9. bspline_opt

路径：

```text
src/fuel/fuel_planner/bspline_opt
```

作用：B 样条轨迹优化。

核心文件：

```text
src/bspline_optimizer.cpp
```

优化目标一般包括：

- 平滑性。
- 避障距离。
- 动力学可行性。
- 时间代价。
- 引导路径贴合。
- 终点约束。

如果轨迹绕障失败、控制点贴近障碍物，通常和这里的优化代价、地图 ESDF、初始路径质量有关。

## 10. poly_traj

路径：

```text
src/fuel/fuel_planner/poly_traj
```

作用：多项式轨迹工具。属于原始 FAST-Planner/FUEL 工具链的一部分。

核心文件：

```text
src/polynomial_traj.cpp
src/traj_generator.cpp
```

它可以生成 minimum jerk 等多项式轨迹。当前探索主流程主要使用 B 样条，`poly_traj` 更多是工具和示例性质。

## 11. traj_utils

路径：

```text
src/fuel/fuel_planner/traj_utils
```

作用：轨迹可视化和辅助工具。

核心文件：

```text
src/planning_visualization.cpp
src/process_msg.cpp
```

`planning_visualization.cpp` 发布 RViz Marker：

```text
/planning_vis/trajectory
/planning_vis/topo_path
/planning_vis/prediction
/planning_vis/visib_constraint
/planning_vis/frontier
/planning_vis/yaw
/planning_vis/viewpoints
```

你在 RViz 里看到的 frontier、候选点、轨迹线，大多来自这个包。

## 12. utils/lkh_tsp_solver

路径：

```text
src/fuel/fuel_planner/utils/lkh_tsp_solver
```

作用：TSP 求解器。

FUEL 会把多个 frontier/viewpoint 的访问顺序近似成 TSP 问题：

```text
当前点 -> frontier A -> frontier B -> frontier C ...
```

然后调用 LKH 求一个访问代价较低的顺序。

终端里的：

```text
Cost mat
TSP
Local tour graph
```

就和这个访问顺序优化有关。

## 13. fuel_dog_adapter

路径：

```text
src/fuel/fuel_dog_adapter
```

这是为本工程新增的四足狗适配包，不属于原版 FUEL 核心。

核心文件：

```text
scripts/fuel_poscmd_to_cmdvel.py
scripts/odom_to_sensor_pose.py
scripts/cmd_vel_marker.py
scripts/fuel_path_visualizer.py
launch/fuel_dog_visual_exploration.launch
launch/fuel_dog_exploration.launch
launch/rviz_fuel_dog.launch
config/fuel_dog_exploration.rviz
```

### fuel_poscmd_to_cmdvel.py

作用：把 FUEL 的无人机位置命令转换成四足狗速度命令。

订阅：

```text
/position_cmd
/Odometry_gazebo
```

发布：

```text
/cmd_vel
```

主要逻辑：

- 根据当前位置和 FUEL 期望位置算 XY 误差。
- 加上 FUEL 给的期望速度前馈。
- 转到四足狗 body 坐标系。
- 输出 `Twist.linear.x/y` 和 `Twist.angular.z`。
- 限速、限加速度。
- 命令超时自动发零速度。
- 当前已支持让狗尽量朝运动方向走：

```text
align_yaw_to_velocity
forward_only
disable_lateral
```

### odom_to_sensor_pose.py

作用：把四足狗里程计转换成 FUEL 建图需要的相机位姿。

订阅：

```text
/Odometry_gazebo
```

发布：

```text
/fuel_dog_adapter/sensor_pose
```

视觉探索时，它会输出相机光学坐标系位姿，因为 FUEL 深度图投影假设：

```text
camera x: right
camera y: down
camera z: forward
```

### fuel_dog_visual_exploration.launch

视觉深度图探索启动文件。

默认接入：

```text
/real_sense/depth/image_raw
/Odometry_gazebo
/cmd_vel
```

重点参数：

```text
init_z
viewpoint_z
box_min_z
box_max_z
frontier_cluster_min
frontier_min_visib_num
frontier_min_candidate_clearance
max_vel
max_acc
```

### rviz_fuel_dog.launch

启动 RViz 和辅助可视化节点。

可视化内容：

```text
/sdf_map/depth_cloud
/sdf_map/occupancy_local
/planning_vis/frontier
/planning_vis/viewpoints
/planning_vis/trajectory
/fuel_dog_adapter/generated_path
/fuel_dog_adapter/executed_path
/fuel_dog_adapter/cmd_vel_marker
```

## 14. 运行时关键话题

输入类：

```text
/real_sense/depth/image_raw
/real_sense/depth/points
/Odometry_gazebo
/fuel_dog_adapter/sensor_pose
/waypoint_generator/waypoints
```

地图类：

```text
/sdf_map/occupancy_local
/sdf_map/occupancy_local_inflate
/sdf_map/depth_cloud
/sdf_map/esdf
/sdf_map/unknown
```

探索可视化：

```text
/planning_vis/frontier
/planning_vis/viewpoints
/planning_vis/trajectory
/planning_vis/topo_path
/planning_vis/yaw
```

轨迹和控制：

```text
/planning/bspline
/position_cmd
/cmd_vel
/planning/travel_traj
/planning/position_cmd_vis
```

## 15. 重要日志解释

### wait for trigger

```text
wait for trigger.
```

FSM 在等待 `/waypoint_generator/waypoints` 触发。

### to visit

```text
to visit: 7, dormant: 0
```

当前有 7 个可访问 frontier，0 个暂时不可访问 frontier。

### cost mat

```text
cost mat size before remove
cost mat size final
```

frontier 之间的访问代价矩阵更新。

### Next view

```text
Next view: x y z, yaw
```

FUEL 选中的下一个观察点。

### No path to next viewpoint

说明有目标，但路径搜索或轨迹规划到不了。

### FINISH

```text
[FSM]: state: FINISH
finish exploration.
to visit: 0, dormant: 0
```

说明 FUEL 认为没有可探索 frontier。

## 16. 当前移植状态总结

目前对 FUEL 的核心算法没有大改。

已做的是：

- 新增四足狗适配包 `fuel_dog_adapter`。
- 接入深度图视觉探索。
- 接入 `/cmd_vel`。
- 增加 RViz 可视化。
- 增加固定 viewpoint 高度的小补丁。
- 调整四足狗低高度探索参数。

还没有做的是：

- 没有把 FUEL 完全重写成地面机器人 planner。
- 没有重写 frontier 算法。
- 没有重写 kinodynamic A*。
- 没有去掉无人机 z 维轨迹优化。
- 没有加入门、电梯、楼层语义策略。

因此当前方案本质是：

```text
保留 FUEL 核心探索能力
+ 低高度视觉参数
+ 固定观察点高度
+ 四足狗 cmd_vel 适配
```

