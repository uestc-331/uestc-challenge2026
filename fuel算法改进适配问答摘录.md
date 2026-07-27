# FUEL 算法改进/适配问答摘录

来源: `tzbai聊天记录_用户助手.md`
仅保留 7 类主题的实质性问题，共 92 组；已排除 FUEL 接入方案、启动/编译问题、纯日志/命令查询、RViz 纯显示问题和红球视觉识别适配。

## 1. [探索范围] FUEL算法是通过给点吗，我以为是给他限制范围让它自主探索

**问题**

FUEL算法是通过给点吗，我以为是给他限制范围让它自主探索

**回答**

对，你理解得基本对：**FUEL 是自主探索算法，不是传统“给一个目标点然后导航过去”**。但它启动时需要一个“开始探索”的触发信号。

现在你看到的：

```text
[FSM]: state: WAIT_TRIGGER
wait for trigger.
fuel_poscmd_to_cmdvel: waiting for fresh /position_cmd
```

意思是：

- FUEL 已经在建图、找 frontier 了；
- 但探索 FSM 还没正式开始规划轨迹；
- 所以 `traj_server` 还没有发布 `/position_cmd`；
- 所以我的适配器也只能等 `/position_cmd`。

FUEL 的逻辑大概是：

```text
传感器点云 + 里程计
        ↓
构建未知/已知/障碍地图
        ↓
找 frontier，也就是“已知区域和未知区域的边界”
        ↓
选择最值得探索的 frontier
        ↓
生成 B-spline 轨迹
        ↓
发布 /position_cmd
        ↓
适配器转成 /cmd_vel
```

你说的“给它限制范围让它自主探索”对应的是 launch 里的这些参数：

```xml
box_min_x
box_min_y
box_min_z
box_max_x
box_max_y
box_max_z
```

也就是探索边界。这个已经在 `fuel_dog_exploration.launch` 里设置了。

但是 FUEL 还需要一个“开始信号”。原版 FUEL 是通过 RViz 的 `2D Nav Goal` 或 waypoint generator 发 `/waypoint_generator/waypoints` 来触发。

你现在可以手动触发一次：

```bash
rostopic pub -1 /waypoint_generator/waypoints nav_msgs/Path "header:
  frame_id: 'world'
poses:
- header:
    frame_id: 'world'
  pose:
    position:
      x: 0.0
      y: 0.0
      z: 0.75
    orientation:
      w: 1.0"
```

发完后如果正常，终端会从：

```text
WAIT_TRIGGER
```

变成：

```text
PLAN_TRAJ
PUB_TRAJ
EXEC_TRAJ
```

然后 `/position_cmd` 才会出来，狗才会开始动。

更好的做法是我给你加一个自动触发节点或 launch 参数，让启动 FUEL 后自动发一次 trigger，这样就符合你说的“给范围后自主探索”。现在还差的就是这个自动 trigger。


---

## 2. [视觉深度建图] FUEL的原版是视觉探索，为什么你移植后需要点云输入

**问题**

FUEL的原版是视觉探索，为什么你移植后需要点云输入

**回答**

因为 FUEL 原版其实**不是只能吃视觉图像**，它的建图模块本来就支持两种输入：

1. 深度图 `depth image`
2. 点云 `PointCloud2`

在原版 launch 里就有这两行：

```xml
<arg name="depth_topic" value="/pcl_render_node/depth"/>
<arg name="cloud_topic" value="/pcl_render_node/cloud"/>
```

并且注释写了类似：

```xml
<!-- subscribe ONLY TO ONE of the two topics -->
```

也就是说，FUEL 的“视觉探索”核心不是必须 RGB 图像，而是需要**带空间信息的感知输入**，用来构建占据地图/ESDF：

```text
深度图 或 点云
    ↓
占据地图 / ESDF
    ↓
frontier 检测
    ↓
选择下一个探索视点
    ↓
轨迹规划
```

我这次移植选择点云，是因为你的四足狗仿真里已经有 Mid360/Livox 相关输出：

```text
/scan
/livox/Pointcloud2
```

而 FUEL 的 `map_ros` 直接支持 `sensor_msgs/PointCloud2`，所以接点云是最直接、改动最少的路线。

如果用视觉深度图，也可以，但需要保证同时有：

```text
/map_ros/depth   sensor_msgs/Image
/map_ros/pose    geometry_msgs/PoseStamped
```

还要配置相机内参：

```xml
cx
cy
fx
fy
```

以及深度缩放：

```xml
k_depth_scaling_factor
```

相比之下，点云路线不需要相机内参，直接拿 `/livox/Pointcloud2` 建图，更适合你现在这个四足狗 + Mid360 仿真。

所以准确说：

> FUEL 原版是主动感知/视觉探索框架，但底层建图既能用深度图，也能用点云。移植到四足狗时，我选了你工程里最现成的点云输入。


---

## 3. [视觉深度建图 / 探索范围] ①我希望让相机跟前进的方向保持一致 ②我发现路径往后走，没有进前面的门，把探索范围给调整一下，并告诉我需要调哪里的参数

**问题**

①我希望让相机跟前进的方向保持一致
②我发现路径往后走，没有进前面的门，把探索范围给调整一下，并告诉我需要调哪里的参数

**回答**

我理解你的两个目标：相机视线始终跟狗头/前进方向一致，同时把探索从“往后找 frontier”压到门前方向。这里一半是控制适配器问题，一半是 FUEL 搜索边界/初始方向问题；我先看一下当前适配器和 launch 参数，然后直接把可调开关补上。

当前代码里相机位姿已经跟随狗身 yaw，但速度适配器允许 `linear.y` 平移，也允许 `linear.x` 为负，所以狗可能“侧着走/倒着走”，相机自然就不会总朝运动方向。我要把适配器改成默认更像四足狗：先转头朝目标方向，再向前走，禁止倒车，横向速度默认压小/关掉。


---

## 4. [视觉深度建图] 简单解释一下ESDF

**问题**

简单解释一下ESDF

**回答**

ESDF 全称是 **Euclidean Signed Distance Field**，可以简单理解成：

**一张“每个位置离最近障碍物有多远”的地图。**

比如在地图里的某个点：

```text
距离墙 1.2m  -> ESDF = 1.2
距离桌子 0.3m -> ESDF = 0.3
已经在障碍物里面 -> ESDF 可能是负数
```

在 FUEL 里，ESDF 主要给轨迹规划和优化用：

```text
离障碍物越近，代价越高
离障碍物太近，轨迹要被推开
```

所以流程大概是：

```text
深度图/点云
  -> 占据地图 occupancy map
  -> 膨胀障碍物
  -> ESDF
  -> 轨迹避障优化
```

占据地图只告诉你：

```text
这里是障碍 / 空闲 / 未知
```

ESDF 进一步告诉你：

```text
这里离障碍物还有 0.8m
这里离障碍物只有 0.1m，很危险
```

对四足狗来说，ESDF 就是 FUEL 判断“这条路径离墙够不够远”的重要依据。


---

## 5. [frontier/viewpoint] frontier是自由空间和未知空间的边界吗

**问题**

frontier是自由空间和未知空间的边界吗

**回答**

对，**frontier 就是自由空间和未知空间的边界**。

更具体一点：

```text
自由空间：传感器已经看过，并确认没有障碍，可以走
未知空间：传感器还没看过，不知道有没有障碍
frontier：自由空间旁边紧挨着未知空间的那一圈边界
```

比如狗站在门口，门内房间还没看清：

```text
走廊地面 = 自由空间
门后房间 = 未知空间
门口边缘 = frontier
```

FUEL 的探索逻辑就是：

```text
找到 frontier
-> 生成能看到 frontier 后方未知区域的观察点
-> 选一个收益高、代价低的观察点
-> 规划过去
-> 传感器看到更多区域
-> 地图更新
-> 继续找新的 frontier
```

所以 frontier 本质上就是“下一步该去哪里看”的候选线索。


---

## 6. [视觉深度建图] 我有个疑惑，这里的建图和使用fastlivo算法建图的区别是什么

**问题**

我有个疑惑，这里的建图和使用fastlivo算法建图的区别是什么

**回答**

区别很大。这里 FUEL 的建图更像 **规划用局部占据建图**，FastLIVO 更像 **定位建图/SLAM**。

简单对比：

| 项目 | FUEL 里的建图 | FastLIVO 建图 |
|---|---|---|
| 核心目的 | 给探索和避障用 | 给定位和三维重建用 |
| 输入 | 深度图/点云 + 已知位姿 | LiDAR + IMU + 视觉 |
| 是否估计自身位姿 | 通常不负责 | 负责，核心功能之一 |
| 地图类型 | Occupancy Map / ESDF | 稠密或半稠密点云地图 |
| 关心内容 | 哪些地方空闲、障碍、未知 | 环境几何结构和位姿轨迹 |
| 输出给谁 | FUEL frontier、A*、轨迹优化 | 定位、建图、导航、回环等 |
| 是否区分未知空间 | 强烈依赖未知空间 | 通常不把“未知”作为探索目标 |
| 是否适合 frontier 探索 | 是 | 不是直接做 frontier 探索 |

FUEL 这里的建图流程大概是：

```text
深度图/点云 + 里程计位姿
-> raycasting
-> 占据栅格地图
-> 自由 / 障碍 / 未知
-> frontier
-> ESDF
-> 轨迹规划
```

它最关心的是：

```text
哪里能走？
哪里是障碍？
哪里还未知？
离障碍物多远？
```

而 FastLIVO 更像：

```text
LiDAR + IMU + Camera
-> 状态估计
-> 点云/视觉特征配准
-> 位姿优化
-> 构建全局几何地图
```

它最关心的是：

```text
我在哪里？
我走过的轨迹是否准确？
环境三维结构长什么样？
点云地图是否一致？
```

最关键的一句话：

**FUEL 的建图是给“探索决策和避障”服务的；FastLIVO 的建图是给“定位和重建”服务的。**

实际系统里二者可以配合：

```text
FastLIVO 负责提供准确 odom / map
FUEL 使用 FastLIVO 的位姿和点云，构建自己的 occupancy/ESDF 地图
FUEL 再根据 frontier 做探索规划
```

也就是说，FastLIVO 的地图不能直接替代 FUEL 的地图，除非你再做一层转换，把 FastLIVO 的点云/局部地图转换成 FUEL 需要的：

```text
自由空间
障碍空间
未知空间
ESDF
```

FUEL 特别需要“未知空间”，这是普通 SLAM 点云地图通常不会直接表达的。


---

## 7. [视觉深度建图] 所以我要设置相机实际能看多远，是设置perception_max_dist:吗？在哪个文件设置

**问题**

所以我要设置相机实际能看多远，是设置perception_max_dist:吗？在哪个文件设置

**回答**

对，**如果你想设置 FUEL 算法认为“相机实际能看多远”**，主要调：

```xml
perception_max_dist
```

位置在：

[fuel_dog_visual_exploration.launch](/home/uestc/tzb/uestc-challenge2026/src/fuel/fuel_dog_adapter/launch/fuel_dog_visual_exploration.launch:42)

现在是：

```xml
<arg name="perception_max_dist" default="4.5"/>
```

它会传给 FUEL 的感知模块：

```xml
<arg name="perception_max_dist" value="$(arg perception_max_dist)"/>
```

以及 `traj_server` 的视场模型：

```xml
<param name="perception_utils/max_dist" value="$(arg perception_max_dist)" type="double"/>
```

你可以直接改文件默认值，比如改成：

```xml
<arg name="perception_max_dist" default="3.0"/>
```

也可以启动时临时覆盖：

```bash
roslaunch fuel_dog_adapter fuel_dog_visual_exploration.launch perception_max_dist:=3.0
```

另外有个容易混的参数：

```xml
perception_vis_dist
```

它主要控制 RViz 里红色锥形画多长，不等于算法实际看的距离。  
简单记：

```text
perception_max_dist  = 算法认为能看多远
perception_vis_dist  = RViz 里画出来多长
```


---

## 8. [视觉深度建图] /home/uestc/tzb/uestc-challenge2026/src/unitree_guide/unitree_ros/robots/a1_description/xacro/gazebo

**问题**

/home/uestc/tzb/uestc-challenge2026/src/unitree_guide/unitree_ros/robots/a1_description/xacro/gazebo.xacro
这里的相机最多能看多远

**回答**

我看一下这个 xacro 里 RealSense / depth camera 的 Gazebo 参数，重点找 `clip far`、`range`、`depth_camera` 这些字段。

我找到了：这个文件里有两个相机，普通单目 `front_camera` 和深度 `real_sense`，你 FUEL 现在用的是后者的深度图/点云。

这个文件里 **FUEL 用的 RealSense 深度相机最多能看 8.0 米**。

位置在：

[gazebo.xacro](/home/uestc/tzb/uestc-challenge2026/src/unitree_guide/unitree_ros/robots/a1_description/xacro/gazebo.xacro:451)

关键参数是：

```xml
<sensor type="depth" name="real_sense">
  ...
  <clip>
    <near>0.05</near>
    <far>8.0</far>
  </clip>
```

含义：

```text
near = 0.05 m   最近可见距离
far  = 8.0 m    最远可见距离
```

另外还有：

```xml
<pointCloudCutoff>0.4</pointCloudCutoff>
```

这个表示点云中过近的点会被裁掉，通常可以理解为 **0.4 m 以内的点云不发布/不可靠**。

所以现在仿真 RealSense 的有效深度范围大致是：

```text
0.4 m ~ 8.0 m
```

但 FUEL 里你现在设置的：

```xml
<arg name="perception_max_dist" default="4.5"/>
```

表示 **FUEL 算法只按 4.5 m 来计算可见性/信息增益**。也就是说：

```text
Gazebo 相机最多能看 8.0 m
FUEL 当前只用 4.5 m
```

如果你想让 FUEL 更接近真实相机极限，可以在：

[fuel_dog_visual_exploration.launch](/home/uestc/tzb/uestc-challenge2026/src/fuel/fuel_dog_adapter/launch/fuel_dog_visual_exploration.launch:42)

改成比如：

```xml
<arg name="perception_max_dist" default="6.0"/>
```

不建议一上来直接设 8.0，四足狗低视角下远距离深度会让 frontier 更激进，先试 `5.0~6.0` 比较稳。


---

## 9. [frontier/viewpoint] 我需要先确认一下，viewpoints到底是生成在哪里，和frointier的位置关系

**问题**

我需要先确认一下，viewpoints到底是生成在哪里，和frointier的位置关系

**回答**

可以，先把概念捋清楚。

**frontier 是什么**

`frontier` 是 FUEL 在地图里找到的：

```text
已知自由空间 free space 和未知空间 unknown space 的边界
```

也就是“从这里往外还没探索过”的边界。

它通常贴在：

```text
门口
走廊尽头
墙边开口
当前视野边缘
未知区域边缘
```

**viewpoint 是什么**

`viewpoint` 不是 frontier 本身，而是 FUEL 为了观察 frontier 生成的“拍摄/观察位置”。

也就是说：

```text
frontier = 要被观察的目标区域
viewpoint = 机器人/相机应该站在哪里看它
```

它们的关系大概是：

```text
机器人当前位置 ----> viewpoint ----看向----> frontier
```

所以 viewpoint 一般不会落在 frontier 上，而是会在 frontier 前方一段距离，位于 FUEL 认为可通行、视野能覆盖 frontier 的地方。

**在源码里大致是这样生成的**

FUEL 先聚类 frontier：

```text
未知/自由边界体素 -> frontier cluster
```

然后对每个 frontier cluster 采样候选 viewpoint：

```text
围绕 frontier 中心，在一定半径范围内采样多个位置
```

这些半径就是你 launch 里的：

```xml
<arg name="frontier_candidate_rmin" default="0.8"/>
<arg name="frontier_candidate_rmax" default="1.8"/>
```

含义：

```text
viewpoint 距离 frontier 至少 0.8m
viewpoint 距离 frontier 最多 1.8m
```

所以如果一个 frontier 在门口附近，FUEL 会尝试在离它 `0.8~1.8m` 的位置生成若干 viewpoint，然后挑一个信息增益高、代价低的。

**高度关系**

你现在还有：

```xml
<arg name="viewpoint_z" default="0.45"/>
```

这表示我们改过后，让 FUEL 候选 viewpoint 的高度固定在 `z=0.45` 附近。

所以当前 viewpoint 的生成形式可以理解为：

```text
x/y：围绕 frontier 采样
z：固定为 viewpoint_z
yaw：朝向 frontier
```

你的日志：

```text
Next view: -0.805284 7.05667 0.45, 2.98079
```

就是一个最终选中的 viewpoint：

```text
viewpoint position = (-0.805, 7.057, 0.45)
viewpoint yaw      = 2.981 rad
```

它不是 frontier 坐标，而是“站在这里、朝这个方向看 frontier”。

**RViz 里怎么看它们的关系**

建议同时开这几个显示：

```text
/planning_vis/frontier
/planning_vis/viewpoints
/planning/position_cmd_vis
/fuel_dog_adapter/generated_path
/fuel_dog_adapter/executed_path
/sdf_map/occupancy_local
```

你要看的是：

```text
1. frontier 是否在未知边界上
2. viewpoint 是否在 frontier 前方 0.8~1.8m
3. viewpoint 到狗当前位置之间是否有连续 free space
4. viewpoint 的朝向是否指向 frontier
```

如果你看到：

```text
frontier 在门后/墙后
viewpoint 在门框另一侧或 unknown 区域边缘
当前位置到 viewpoint 中间不是 free space
```

那就会出现：

```text
No path to next viewpoint
```

**一句话总结**

```text
frontier 是“要探索的边界”
viewpoint 是“为了看这个边界而生成的观察点”
viewpoint 通常在 frontier 附近 0.8~1.8m，z 固定为 viewpoint_z，yaw 朝向 frontier
```


---

## 10. [frontier/viewpoint] 那么在厂房里面的时候开启算法，viewpoints出现在了占据栅格之外的区域，是什么原因导致的

**问题**

那么在厂房里面的时候开启算法，viewpoints出现在了占据栅格之外的区域，是什么原因导致的

**回答**

在厂房里面冷启动时，`viewpoints` 出现在“占据栅格之外”，通常不是它真的生成到地图外面，而是下面几类原因之一。

最常见的是：**RViz 里你看到的占据栅格不是完整地图，只是 occupied / local 部分**。

FUEL 的地图里其实有三种状态：

```text
free      已知自由空间
occupied  已知障碍物
unknown   未知空间
```

RViz 里 `/sdf_map/occupancy_local` 多半只明显显示障碍/局部占据体素，不会把所有 free space 和 unknown space 都清楚画出来。所以 viewpoint 看起来像在“栅格外”，但在 FUEL 内部可能仍然在地图范围内，只是 RViz 没显示那片 free/unknown。

第二个原因是：**viewpoint 是围绕 frontier 采样出来的，不是直接从 occupied grid 上取点**。

FUEL 的逻辑大概是：

```text
找到 frontier 边界
围绕 frontier 中心按半径采样候选 viewpoint
检查可见性、距离、信息增益
选一个最优 viewpoint
再尝试规划路径过去
```

所以 viewpoint 可能出现在 frontier 前方一段距离，比如你现在参数：

```xml
frontier_candidate_rmin = 0.8
frontier_candidate_rmax = 1.8
```

也就是它可能离 frontier 有 `0.8~1.8m`。如果厂房内冷启动时地图很少，frontier 在当前视野边缘，viewpoint 就可能被采样到“你 RViz 里还没看到栅格”的区域。

第三个原因是：**厂房内冷启动时 free space 不连续**。

你手动进厂房时 FUEL 没启动，它没有记录你走进来的路径。启动后它只知道当前相机看到的一小片区域。于是：

```text
frontier 有
候选 viewpoint 也能生成
但当前位置到 viewpoint 之间不是连续 free space
```

最后就报：

```text
No path to next viewpoint
```

第四个原因是：**viewpoint 高度层和实际建图高度层不完全一致**。

你现在 viewpoint 是：

```xml
viewpoint_z = 0.45
```

但之前你给的里程计里狗 base 高度大约是：

```text
z ≈ 0.30
```

如果 RViz 里你看的占据栅格主要集中在某个高度层，而 viewpoint 固定在 `z=0.45`，它也会显得“漂在栅格外”或“不在地图上”。

第五个要警惕的是：**坐标系/传感器位姿有偏差**。

如果 `/fuel_dog_adapter/sensor_pose`、`/Odometry_gazebo`、`world` 坐标没有对齐，那么 FUEL 建出来的 depth cloud、frontier、viewpoints 会互相错位。你之前也提到过 `/sdf_map/depth_cloud` 和锥形视场经常对不上，这类错位会直接导致 viewpoint 看起来跑到奇怪位置。

你现在可以按这个顺序确认：

```bash
rostopic echo -n1 /Odometry_gazebo/pose/pose/position
rostopic echo -n1 /fuel_dog_adapter/sensor_pose/pose/position
rostopic echo -n1 /planning_vis/viewpoints
```

RViz 里同时看：

```text
/sdf_map/depth_cloud
/sdf_map/occupancy_local
/planning_vis/frontier
/planning_vis/viewpoints
/fuel_dog_adapter/sensor_fov_marker
/planning/position_cmd_vis
```

判断标准很简单：

```text
如果 frontier 在视野边缘，viewpoint 在 frontier 前方 0.8~1.8m：正常
如果 viewpoint 在 unknown 区域且 A* 失败：冷启动 free space 不足
如果 depth_cloud、FOV、viewpoint 明显整体错位：坐标系/传感器 pose 问题
如果 viewpoint z=0.45，而地图主要在 z≈0.3：高度层不一致
```

所以你的现象最可能是：

```text
厂房内冷启动时，FUEL 只建到了很小一片局部地图；
frontier 在局部地图边缘；
viewpoint 按 rmin/rmax 被采样到边缘外侧；
但那块区域在当前 ESDF 里不可达，于是 No path。
```


---

## 11. [避障膨胀] 现在四足狗会与墙壁或者墙角发生碰撞，是否应该修改膨胀系数，在哪里改

**问题**

现在四足狗会与墙壁或者墙角发生碰撞，是否应该修改膨胀系数，在哪里改

**回答**

对，四足狗会贴墙/撞墙角时，**首先应该看障碍物膨胀/安全距离参数**。

在你现在的 FUEL 启动链路里，主要有两类参数：

**1. 地图障碍膨胀**

在：

[fuel_dog_visual_exploration.launch](/home/uestc/tzb/uestc-challenge2026/src/fuel/fuel_dog_adapter/launch/fuel_dog_visual_exploration.launch)

它 include 了：

```xml
<include file="$(find exploration_manager)/launch/algorithm.xml">
```

真正的膨胀参数在：

[algorithm.xml](/home/uestc/tzb/uestc-challenge2026/src/fuel/fuel_planner/exploration_manager/launch/algorithm.xml)

你要找类似：

```xml
<param name="sdf_map/obstacles_inflation" ... />
```

这个通常是占据栅格障碍膨胀半径。四足狗撞墙，优先把它调大。

你可以先搜：

```bash
grep -n "inflation" /home/uestc/tzb/uestc-challenge2026/src/fuel/fuel_planner/exploration_manager/launch/algorithm.xml
```

一般建议四足狗用：

```xml
sdf_map/obstacles_inflation = 0.25 ~ 0.40
```

如果现在是无人机默认的 `0.1` 或 `0.15`，对四足狗太小。

**2. 规划优化安全距离**

同一个文件里还有：

```xml
<param name="manager/clearance_threshold" ... />
<param name="optimization/dist0" ... />
```

你现在之前看到的配置里大概是：

```xml
manager/clearance_threshold = 0.2
optimization/dist0 = 0.7
```

含义大致是：

```text
clearance_threshold：路径搜索/管理器认为离障碍至少多远
optimization/dist0：轨迹优化时障碍距离代价开始生效的距离
```

如果撞墙角，优先级是：

```text
先调 sdf_map/obstacles_inflation
再调 manager/clearance_threshold
最后调 optimization/dist0
```

**建议你先这样改**

在 `algorithm.xml` 里把：

```xml
sdf_map/obstacles_inflation
```

调到：

```xml
0.30
```

如果还是贴墙，再试：

```xml
0.40
```

但别一上来太大。膨胀过大后，门口/窄通道会被 FUEL 认为堵死，容易出现：

```text
No path to next viewpoint
```

所以四足狗探索建议从：

```text
0.25 -> 0.30 -> 0.35
```

一点点试。

**实用判断**

如果 RViz 里 `/sdf_map/occupancy_local_inflate` 的橙色膨胀障碍贴得太薄，说明膨胀太小。  
如果门口直接被橙色膨胀障碍封住，说明膨胀太大。

你现在最该看的就是：

```text
/sdf_map/occupancy_local
/sdf_map/occupancy_local_inflate
```

撞墙角时，如果实际狗身体已经碰墙，但 FUEL 规划线还贴着非膨胀障碍走，说明 `obstacles_inflation` 需要加大。


---

## 12. [frontier/viewpoint] <arg name="init_x" default="0.0"/> <arg name="init_y" default="0.0"/> <arg name="init_z" default="0.

**问题**

<arg name="init_x" default="0.0"/>
  <arg name="init_y" default="0.0"/>
  <arg name="init_z" default="0.45"/>
  <arg name="viewpoint_z" default="0.45"/>
这几个参数是否还有作用，还是说只是fuel原版的遗留参数，毕竟我们将无人机改成了四足狗

**回答**

这几个参数**还有作用**，不是完全没用的遗留参数。只是它们现在的含义已经从“无人机真实起点/高度”变成了 **FUEL 内部规划参考点/观察点高度**。

```xml
<arg name="init_x" default="0.0"/>
<arg name="init_y" default="0.0"/>
<arg name="init_z" default="0.45"/>
<arg name="viewpoint_z" default="0.45"/>
```

分别说：

`init_x / init_y / init_z`

这三个会传给 `traj_server`：

```xml
<param name="traj_server/init_x" value="$(arg init_x)"/>
<param name="traj_server/init_y" value="$(arg init_y)"/>
<param name="traj_server/init_z" value="$(arg init_z)"/>
```

作用是：`traj_server` 启动后、还没收到 FUEL 轨迹前，会先发布一个初始 `/position_cmd`。  
对四足狗来说，它不会控制 z 起飞，因为适配器只取 `/position_cmd` 的 x/y 和 yaw 转 `/cmd_vel`，不管 z。

但 `init_x/init_y` 如果和狗当前 odom 差很多，启动初期可能会产生一段错误的 `/cmd_vel`。所以它们最好和狗启动位置一致，或者至少不要差太远。

`viewpoint_z`

这个更重要。它会传给 FUEL 的 frontier/viewpoint 生成逻辑：

```xml
<arg name="viewpoint_z" value="$(arg viewpoint_z)"/>
```

我们之前加过固定 viewpoint 高度的适配，所以现在候选观察点会尽量固定在这个高度。  
它影响的是：

```text
FUEL 生成 Next view 的 z
A* 搜索目标点的 z
前沿观察点的高度层
```

所以你日志里看到：

```text
Next view: x y 0.45, yaw
```

这个 `0.45` 就来自 `viewpoint_z`。

对四足狗来说，`viewpoint_z` 不是让狗真的飞到 0.45m，而是告诉 FUEL：

```text
把四足狗/相机的规划参考点放在 z=0.45 这一层
```

所以总结：

```text
init_x/init_y：有作用，影响启动初期 /position_cmd 的 xy
init_z：有作用，但主要是 traj_server 初始命令高度，对四足狗影响较小
viewpoint_z：很有作用，直接影响 FUEL 生成观察点和路径搜索高度
```

如果你发现 `/Odometry_gazebo` 的 base 高度大约是 `0.30~0.40`，而相机实际高度是 base 再加 `sensor_offset_z`，那 `viewpoint_z=0.45` 是有道理的。  
如果它导致 `No path to next viewpoint`，可以临时试：

```bash
roslaunch fuel_dog_adapter fuel_dog_visual_exploration.launch viewpoint_z:=0.35 init_z:=0.35
```

但它们不是废参数，尤其 `viewpoint_z` 现在是四足狗适配里很关键的参数。


---

## 13. [frontier/viewpoint] <arg name="fixed_z" default="0.30"/> <arg name="z_margin" default="0.05"/> 但是这两个参数也限制轨迹的高度的，和viewpoi

**问题**

<arg name="fixed_z" default="0.30"/>
  <arg name="z_margin" default="0.05"/>
但是这两个参数也限制轨迹的高度的，和viewpoint_z的区别

**回答**

我看一下这两个参数在 launch 里怎么传、源码里在哪里用，这样可以把 `fixed_z/z_margin` 和 `viewpoint_z` 的边界讲准确。

这两个和 `viewpoint_z` 作用层级不一样：

```xml
<arg name="fixed_z" default="0.30"/>
<arg name="z_margin" default="0.05"/>
```

它们限制的是 **轨迹优化阶段的 z 范围**。

在源码里是这里用的：

[bspline_optimizer.cpp](/home/uestc/tzb/uestc-challenge2026/src/fuel/fuel_planner/bspline_opt/src/bspline_optimizer.cpp:185)

```cpp
if (fixed_z_ > -100.0) {
  bmin[2] = max(bmin[2], fixed_z_ - z_margin_);
  bmax[2] = min(bmax[2], fixed_z_ + z_margin_);
}
```

所以：

```text
fixed_z = 0.30
z_margin = 0.05
```

表示 B-spline 优化时，轨迹控制点的 z 大致被限制在：

```text
0.25 ~ 0.35
```

而 `viewpoint_z` 是另一层：

```xml
<arg name="viewpoint_z" default="0.35"/>
```

它限制的是 **frontier 采样出来的候选观察点高度**。

在源码里是这里：

[frontier_finder.cpp](/home/uestc/tzb/uestc-challenge2026/src/fuel/fuel_planner/active_perception/src/frontier_finder.cpp:669)

```cpp
Vector3d sample_pos = frontier.average_ + rc * Vector3d(cos(phi), sin(phi), 0);
if (force_viewpoint_z_) sample_pos[2] = viewpoint_z_;
```

也就是：

```text
viewpoint_z = 0.35
```

会让 FUEL 生成的 `Next view` 的 z 变成：

```text
z = 0.35
```

**区别一句话**

```text
viewpoint_z：决定目标观察点 z 在哪里
fixed_z/z_margin：决定生成到这个目标点的轨迹 z 被限制在哪个高度范围内
```

举例：

```text
viewpoint_z = 0.35
fixed_z = 0.30
z_margin = 0.05
```

这是一组比较协调的配置：

```text
目标点高度：0.35
轨迹允许高度：0.25 ~ 0.35
```

但如果你设置成：

```text
viewpoint_z = 0.45
fixed_z = 0.30
z_margin = 0.05
```

就会不协调：

```text
目标 viewpoint 在 z=0.45
轨迹优化却只允许 z=0.25~0.35
```

这可能导致轨迹优化困难、路径不自然，甚至规划失败。

所以四足狗建议让它们保持一致，比如：

```xml
<arg name="init_z" default="0.30"/>
<arg name="viewpoint_z" default="0.35"/>
<arg name="fixed_z" default="0.30"/>
<arg name="z_margin" default="0.05"/>
```

或者更统一一点：

```xml
<arg name="init_z" default="0.35"/>
<arg name="viewpoint_z" default="0.35"/>
<arg name="fixed_z" default="0.35"/>
<arg name="z_margin" default="0.05"/>
```

经验上，`viewpoint_z` 最好落在：

```text
fixed_z - z_margin  到  fixed_z + z_margin
```

否则你是在让 FUEL “目标点在一层，轨迹只能走另一层”。


---

## 14. [重规划] 我发现只有给向前的速度四足狗才会从plan fail进入规划，其他方向的速度都不行，即使移动了一段距离，fuel的触发条件是什么？

**问题**

我发现只有给向前的速度四足狗才会从plan fail进入规划，其他方向的速度都不行，即使移动了一段距离，fuel的触发条件是什么？

**回答**

我看一下 FUEL 的 FSM 触发和失败后重规划条件，重点确认它到底是靠 waypoint trigger、odom、还是地图更新在驱动。

FUEL 的“触发条件”分两层：

第一层是**开始探索的 trigger**。源码里在 [fast_exploration_fsm.cpp](/home/uestc/tzb/uestc-challenge2026/src/fuel/fuel_planner/exploration_manager/src/fast_exploration_fsm.cpp:325)：

```cpp
void FastExplorationFSM::triggerCallback(const nav_msgs::PathConstPtr& msg) {
  if (msg->poses[0].pose.position.z < -0.1) return;
  if (state_ != WAIT_TRIGGER) return;
  fd_->trigger_ = true;
  transitState(PLAN_TRAJ, "triggerCallback");
}
```

也就是说，只要它在 `WAIT_TRIGGER` 状态，收到：

```text
/waypoint_generator/waypoints
```

并且第一个 pose 的 `z >= -0.1`，就进入 `PLAN_TRAJ`。

第二层是**能不能规划成功**。进入 `PLAN_TRAJ` 后，它每 0.01s 都会尝试：

```cpp
planExploreMotion(fd_->start_pt_, fd_->start_vel_, fd_->start_acc_, fd_->start_yaw_)
```

如果失败：

```cpp
ROS_WARN("plan fail");
fd_->static_state_ = true;
```

它不会回到等待触发，而是一直留在 `PLAN_TRAJ` 里反复规划。

所以你说的现象：

```text
plan fail 后，给向前速度 -> 能进入规划
其他方向速度 -> 不行
```

不是“触发条件不同”，而是**地图/视场更新方向不同**导致规划成功与否不同。

为什么只有向前速度有效？

因为你的相机是前向固定的，FUEL 建图用的是：

```text
/real_sense/depth/image_raw
/fuel_dog_adapter/sensor_pose
```

四足狗往前动时，相机视场向前扫，会把前方地面、门口、通道清成连续 free space。这样 A* 才能从当前位置连到 viewpoint。

其他方向，比如后退、横向、原地小转，可能出现：

```text
相机没有扫到 FUEL 想去的那个方向
前方通路仍然是 unknown
viewpoint 仍然不可达
```

FUEL 的路径搜索不愿意穿 unknown，所以继续：

```text
No path to next viewpoint
plan fail
```

还有一个关键点：你现在适配器是：

```xml
disable_lateral=true
forward_only=false
align_yaw_to_velocity=true
```

但相机仍然只看身体前方。即使你让狗后退了一段，地图中新清出来的主要仍然是“相机前方”，不一定是 FUEL 选中 viewpoint 所在方向的连通区域。

换句话说：

```text
FUEL 不是靠“移动了多远”触发成功；
它靠“当前地图中是否出现当前位置到 viewpoint 的连续 free space”成功。
```

向前速度刚好最容易补足这条 free-space 连通性。

建议你验证两个点：

```bash
rostopic echo -n1 /waypoint_generator/waypoints
rostopic echo -n1 /Odometry_gazebo/twist/twist
```

同时 RViz 看：

```text
/sdf_map/depth_cloud
/sdf_map/occupancy_local
/planning_vis/frontier
/planning_vis/viewpoints
```

如果向前给速度后 `/sdf_map/depth_cloud` 明显向前铺开，并且 viewpoint 出现，那就说明根因是：

```text
厂房内冷启动时前向 free space 不够；
向前运动让深度相机补上了可通行区域。
```


---

## 15. [视觉深度建图 / frontier/viewpoint] 向前给速度（需要比较大，0.6m/s以上）后 /sdf_map/depth_cloud没有 明显向前铺开，只是viewpoint突然出现

**问题**

向前给速度（需要比较大，0.6m/s以上）后 /sdf_map/depth_cloud没有 明显向前铺开，只是viewpoint突然出现

**回答**

这个现象更像“不是深度云铺开导致”，而是某个速度阈值/状态条件被满足后，FUEL 的规划分支终于走到了可视化或成功发布阶段。我查一下 viewpoint 是在哪个分支被画出来的。

这个现象说明得更细了：`viewpoint` 突然出现不一定代表 depth cloud 肉眼明显变了，因为当前代码里 viewpoint 可视化本身只在“规划成功发布轨迹”后才画。我再看一下速度在 cost/规划里具体怎么用。

这个新现象很有价值：**如果 `/sdf_map/depth_cloud` 没明显向前铺开，但 viewpoint 突然出现，那说明“向前 0.6m/s”不是靠新增一大片深度地图触发的，而更可能是靠 FUEL 内部的速度状态/规划成功分支触发的。**

先说一个关键点：**RViz 里的 `/planning_vis/viewpoints` 不是每次算出 Next view 都会显示。**

源码里 viewpoint 是在 `visualize()` 里画的：

[fast_exploration_fsm.cpp](/home/uestc/tzb/uestc-challenge2026/src/fuel/fuel_planner/exploration_manager/src/fast_exploration_fsm.cpp:229)

```cpp
visualization_->drawSpheres(ed_ptr->points_, ..., "global_viewpoints", ...);
visualization_->drawSpheres(ed_ptr->refined_points_, ..., "refined_viewpoints", ...);
```

而 `visualize()` 只在规划成功、进入 `PUB_TRAJ` 后调用：

```cpp
if (res == SUCCEED) {
  transitState(PUB_TRAJ, "FSM");
}
...
thread vis_thread(&FastExplorationFSM::visualize, this);
```

所以：

```text
plan fail 时 RViz 没 viewpoint
规划成功后 viewpoint 突然出现
```

并不表示之前没有内部 viewpoint。之前日志里 `Next view` 已经说明内部有候选/目标 viewpoint，只是失败分支没有画出来。

至于为什么必须向前速度大于约 `0.6m/s`，现在我更怀疑是 **FUEL 用了 odom 速度参与 viewpoint 代价和轨迹生成**。

源码里规划起点速度来自 odom：

```cpp
fd_->start_vel_ = fd_->odom_vel_;
```

然后传进：

```cpp
planExploreMotion(start_pt, start_vel, ...)
```

在 viewpoint 代价计算里速度方向会参与 cost：

[graph_node.cpp](/home/uestc/tzb/uestc-challenge2026/src/fuel/fuel_planner/active_perception/src/graph_node.cpp:68)

```cpp
if (v1.norm() > 1e-3) {
  Vector3d dir = (p2 - p1).normalized();
  Vector3d vdir = v1.normalized();
  double diff = acos(vdir.dot(dir));
  pos_cost += w_dir_ * diff;
}
```

含义是：

```text
如果当前速度非零，FUEL 会偏向选择“当前速度方向前方”的 viewpoint
```

所以当你给一个足够大的向前速度后，FUEL 当前速度方向明确，局部 tour/refine 可能会选择前方更容易连通/更合理的 viewpoint，于是规划成功，RViz 才画出 viewpoint。

其他方向不行，是因为：

```text
速度方向没有指向可达 frontier/viewpoint
或者选出来的 viewpoint 仍然不可达
```

为什么要 0.6m/s 以上？可能不是源码里硬阈值 0.6，而是因为：

```text
低速时 /Odometry_gazebo.twist 变化太小、不稳定、被滤波/控制器吃掉
FUEL 看到的 start_vel 仍然接近 0
只有命令足够大时，odom_vel 才明显变成前向速度
```

你可以验证这个判断：

```bash
rostopic echo /Odometry_gazebo/twist/twist
```

分别给：

```text
0.2 m/s forward
0.4 m/s forward
0.6 m/s forward
```

看 `/Odometry_gazebo.twist.twist.linear.x/y` 什么时候真的明显变大。

这里还有一个很可能的隐藏点：**FUEL 失败后一直在 PLAN_TRAJ 状态反复规划**。也就是说你给速度的那一刻，下一轮规划会立刻使用新的 odom 速度：

```text
start_pt = 当前 odom 位置
start_vel = 当前 odom 速度
start_yaw = 当前 odom yaw
```

所以即使位置没明显变，只要速度变了，选点/代价/轨迹初始化都可能变。

结论：

```text
向前 0.6m/s 不是触发了 FUEL 的“开始条件”；
它改变了 FUEL 的 start_vel，使 frontier viewpoint 排序/局部规划变成前向可达，于是规划成功。
RViz viewpoint 突然出现，是因为只有成功后 visualize() 才发布 viewpoint。
```

如果要进一步确认，最直接看这两个：

```bash
rostopic echo /Odometry_gazebo/twist/twist
rostopic echo /planning/bspline
```

当你给前向速度后，如果 `/planning/bspline` 开始发布，就说明确实是从 `plan fail` 变成 `SUCCEED` 了，而不是单纯 RViz 显示问题。


---

## 16. [视觉深度建图 / 避障膨胀] 现在有一个现象是：四足狗的相机靠近墙壁时，相机的视角会透过墙，你先分析原因，不改代码

**问题**

现在有一个现象是：四足狗的相机靠近墙壁时，相机的视角会透过墙，你先分析原因，不改代码

**回答**

这个现象大概率不是 FUEL “主动穿墙看”，而是 **Gazebo 深度相机/地图建模/视场可视化之间的模型不一致** 导致的。先按可能性从高到低分析。

**1. RViz 里的锥形只是几何可视化，不会被墙遮挡**

如果你说的是红色锥形 `/planning/position_cmd_vis` 或实际相机锥形 `/fuel_dog_adapter/sensor_fov_marker`，它们只是用几条线画出的 FOV 轮廓：

```text
相机位置 + 朝向 + 视场角 + 最大距离
```

这个 marker 本身不会做 raycast，也不会遇到墙就截断。所以靠近墙时，锥形线条看起来“穿过墙”是正常的可视化现象。

它不代表深度图真的看穿墙。要判断相机是否真的透墙，要看：

```text
/sdf_map/depth_cloud
/real_sense/depth/points
/real_sense/depth/image_raw
```

而不是只看锥形 marker。

**2. Gazebo 里的墙可能只有视觉，没有 collision**

深度相机一般依赖 Gazebo 渲染/深度传感器看到模型表面。如果建筑墙体的 SDF/URDF 里某些部分只有 `visual`，没有正确的 `collision`，物理碰撞和深度渲染可能不一致。

不过深度相机更多看 visual 渲染面，路径规划/碰撞看 collision。两者任意一个缺失或错位，都会出现：

```text
RViz 看像墙
深度图没挡住
或狗撞墙但深度云位置不对
```

你可以在 Gazebo 里打开 collision 显示，检查墙体 collision 是否和 visual 重合。

**3. 墙体单面材质/法线方向问题**

如果厂房墙是由很薄的单面 mesh 或 plane 生成的，深度相机从某一侧可能能看到，靠得很近或从背面看时可能渲染异常，看起来像穿透。

常见于：

```text
单面墙面
法线朝外/朝内不统一
没有厚度
mesh 没有双面渲染
```

这种情况下，靠近墙、斜视墙角时最容易出现“透过去”。

**4. 深度相机 near clipping 太大或 cutoff 造成近墙缺失**

你之前的 Gazebo RealSense 参数里有：

```xml
<near>0.05</near>
<far>8.0</far>
<pointCloudCutoff>0.4</pointCloudCutoff>
```

其中 `pointCloudCutoff=0.4` 很关键。它可能导致：

```text
0.4m 以内的点不进入点云
```

所以相机离墙太近时，墙面反而从 `/real_sense/depth/points` 或 FUEL 点云里消失。这样 FUEL/RViz 看起来就像视线穿过墙，或者墙后区域没有被挡住。

这对四足狗非常常见，因为相机很低、经常贴近墙角。

**5. FUEL 的感知模型不考虑墙遮挡，只画完整视场**

FUEL 内部做可见性时会用地图和 raycast 判断一部分可见性，但 RViz 里的锥形显示通常只画完整 frustum，不会按照已知障碍切掉。所以：

```text
FUEL 计算时可能知道墙挡住
RViz 锥形仍然画穿墙
```

不要把锥形当成真实深度结果。

**6. 坐标系/传感器位姿偏移**

如果 `/fuel_dog_adapter/sensor_pose` 的相机位置或朝向和 Gazebo 真相机不一致，RViz 里的实际相机 FOV 会从错误的位置发出，看起来穿墙。

你之前有过：

```text
/sdf_map/depth_cloud 和锥形视场经常对不上
```

如果现在仍然存在，那就要重点怀疑：

```text
sensor_offset_x/y/z
use_optical_frame
real_sense link 的真实 xacro 位姿
```

**建议你先这样判断是哪一种**

看三个东西是否一致：

```text
1. Gazebo 里相机是否真的贴墙
2. /real_sense/depth/points 是否在墙面处有点
3. RViz 的 /fuel_dog_adapter/sensor_fov_marker 是否从真实相机位置发出
```

如果只是锥形线穿墙，但 `/real_sense/depth/points` 在墙上截止，那没问题，只是 marker 可视化不截断。

如果 `/real_sense/depth/points` 也穿墙或墙面消失，优先怀疑：

```text
pointCloudCutoff=0.4 导致近距离墙点被过滤
墙体 mesh/visual/collision 有问题
```

如果点云没穿墙，但 FUEL 地图穿墙，优先怀疑：

```text
sensor_pose 坐标系或深度投影参数不匹配
```


---

## 17. [A*/B 样条] 在rviz中显示A*规划的轨迹（没有经过B 样条优化的）

**问题**

在rviz中显示A*规划的轨迹（没有经过B 样条优化的）

**回答**

我来把 FUEL 里已经保存的 `path_next_goal_` 接到可视化上。它正好就是到 next viewpoint 的 A* 几何路径，当前源码里有一行被注释掉了。

我会启用 `path_next_goal_` 的 marker，并把它放到 RViz 的 FUEL Planning 组里。话题会是 `/planning_vis/next_goal`，表示未优化的 A* 几何路径。

现在开始改文件：一处 C++ 可视化，一处 RViz 配置。

源码改好了。因为动了 `exploration_node` 的 C++，需要编译 `exploration_manager`。

正在编译修改过的 `fast_exploration_fsm.cpp`，等链接结束后我再确认 launch/RViz 配置。

编译通过了，只有已有 warning。最后检查 RViz launch 能正常解析。

已加上 A* 原始路径可视化。

现在 RViz 会显示一个新 marker：

```text
/planning_vis/next_goal
```

名字是：

```text
astar_path
```

它画的是 FUEL 到 `next viewpoint` 的 **A* 几何路径**，也就是还没经过 B 样条优化前的路径。

改动位置：

[fast_exploration_fsm.cpp](/home/uestc/tzb/uestc-challenge2026/src/fuel/fuel_planner/exploration_manager/src/fast_exploration_fsm.cpp:257)

```cpp
visualization_->drawLines(ed_ptr->path_next_goal_, 0.06, Vector4d(0, 1, 1, 1), "astar_path", 1, 6);
```

[RViz 配置](/home/uestc/tzb/uestc-challenge2026/src/fuel/fuel_dog_adapter/config/fuel_dog_exploration.rviz:264) 也加了显示项：

```text
Marker Topic: /planning_vis/next_goal
Name: astar_path
```

我已经编译通过：

```bash
catkin_make --pkg exploration_manager
```

也检查了 launch 解析正常。

你重新启动：

```bash
source devel/setup.bash
roslaunch fuel_dog_adapter fuel_dog_visual_exploration.launch
roslaunch fuel_dog_adapter rviz_fuel_dog.launch
```

在 RViz 里看 `FUEL Planning -> astar_path`。它应该是一条青色线，和红色/黄色的 B 样条轨迹对比看就很直观。


---

## 18. [A*/B 样条] fuel确定是用A*规划的吗

**问题**

fuel确定是用A*规划的吗

**回答**

是的，**FUEL 里面确实用了 A\***，但要注意：它不是“只用 A\* 直接输出最终轨迹”。

更准确地说，FUEL 的路径生成链路是：

```text
frontier/viewpoint 选择
-> A* 找一条几何路径到 next viewpoint
-> 根据 A* 路径生成初始轨迹
-> B 样条优化
-> traj_server 发布 /position_cmd
```

你现在看到的这一段就是 A\*：

[fast_exploration_manager.cpp](/home/uestc/tzb/uestc-challenge2026/src/fuel/fuel_planner/exploration_manager/src/fast_exploration_manager.cpp:234)

```cpp
planner_manager_->path_finder_->reset();
if (planner_manager_->path_finder_->search(pos, next_pos) != Astar::REACH_END) {
  ROS_ERROR("No path to next viewpoint");
  return FAIL;
}
ed_->path_next_goal_ = planner_manager_->path_finder_->getPath();
shortenPath(ed_->path_next_goal_);
```

这里的：

```cpp
path_finder_->search(pos, next_pos)
```

就是 A\* 搜索。

`path_finder_` 在 FUEL 里对应的是：

```text
path_searching / Astar
```

而且你之前报错：

```text
No path to next viewpoint
```

就是这一步 A\* 没有找到从当前位置到 viewpoint 的路径。

但是 A\* 找到的不是最终执行轨迹。后面还会走：

```cpp
planner_manager_->planExploreTraj(ed_->path_next_goal_, vel, acc, time_lb);
```

在 [planner_manager.cpp](/home/uestc/tzb/uestc-challenge2026/src/fuel/fuel_planner/plan_manage/src/planner_manager.cpp:267) 里，它会把 A\* 路径转成初始轨迹，然后做 B 样条优化。

所以一句话：

```text
FUEL 用 A* 找到 viewpoint 的粗路径，但最终发给机器人的 /position_cmd 是 B 样条优化后的轨迹。
```

你刚让我加的 `/planning_vis/next_goal` 显示的是：

```text
A* 原始/缩短后的几何路径
```

而 RViz 里的 `/planning_vis/trajectory` 显示的是：

```text
B 样条优化后的最终规划轨迹
```


---

## 19. [A*/B 样条] 我发现A*规划出来的可能会从墙的上方翻过，但是现在的本体是四足狗不是无人机，B样条优化的轨迹可能是被 <arg name="fixed_z" default="0.30"/> <arg name="z

**问题**

我发现A*规划出来的可能会从墙的上方翻过，但是现在的本体是四足狗不是无人机，B样条优化的轨迹可能是被
<arg name="fixed_z" default="0.30"/>
  <arg name="z_margin" default="0.05"/>
限制了，不会跟着A*的轨迹翻墙，但是会穿墙，给出解决A*轨迹翻墙的问题的方案，先不动代码

**回答**

对，你这个判断基本正确：现在有一个“不一致”的地方。

```text
A*：仍然是 3D A*，可以在 z 方向搜索，所以可能从墙上方绕过去
B 样条优化：被 fixed_z/z_margin 限在低高度，所以不能跟着 A* 翻墙
结果：优化轨迹被压回低高度后，就可能在平面上穿墙
```

也就是说，问题不是单纯 B 样条穿墙，而是：

```text
A* 给了一个对四足狗不合法的 3D 引导路径
```

**方案 1：把 A* 也限制在固定高度层，推荐**

这是最干净的四足狗方案。

思路：

```text
A* 搜索邻居时，不允许 z 方向变化
或者把 start/end 都投影到 fixed_z
只在 z = fixed_z 附近的一层 2D/2.5D 栅格上搜索
```

效果：

```text
A* 不会翻墙
B 样条也在同一高度层优化
两者约束一致
```

这应该是最优先方案，因为你现在已经用：

```xml
fixed_z = 0.30
z_margin = 0.05
viewpoint_z = 0.35
```

说明系统目标就是 2.5D 地面探索，不应该再让 A* 自由走 3D。

**方案 2：在 A* 搜索中限制 z 搜索范围**

比完全 2D A* 稍微宽松一点：

```text
A* 节点 z 必须在 fixed_z - z_margin 到 fixed_z + z_margin 内
```

例如：

```text
0.25 <= z <= 0.35
```

这样仍然允许一点高度浮动，但不会翻过墙。

这个和 B 样条的限制完全一致，是很合理的做法。

**方案 3：把地图 z 范围压低，让 A* 没有翻墙空间**

不改代码也可以先试参数：

```xml
<arg name="box_min_z" default="0.05"/>
<arg name="box_max_z" default="0.45"/>
```

或者更保守：

```xml
box_max_z = 0.40
```

这样 A* 的搜索空间最高只有 0.4m，理论上就不能从墙上方绕过去。

但这个方案有副作用：

```text
FUEL 的 frontier / map / ESDF 高度空间也会被压扁
高一点的障碍、门框、墙面信息可能被裁掉
视觉探索质量可能下降
```

它适合作为临时验证，不是最优长期方案。

**方案 4：把墙在低高度层膨胀/投影成不可通行柱体**

如果建筑墙体在地图中只在某些高度有 occupied，而 A* 在其他高度认为是 free，就会从上面钻过去。

可以做：

```text
检测到墙体占据后，把它在 z 方向投影到整个机器人高度范围
```

也就是类似 2D costmap 的做法：

```text
只要某个 x/y 位置有墙，就认为该 x/y 在狗的通行高度层不可走
```

这个对四足狗很合理，但实现比限制 A* z 稍复杂。

**方案 5：后处理 A* 路径，拒绝包含明显 z 爬升的路径**

例如：

```text
如果 A* path 中任一点 z 超出 fixed_z ± z_margin
直接判定该 path invalid，重新选 viewpoint 或 plan fail
```

这个实现简单，但缺点是：

```text
只是拒绝结果，不主动找平面可行路
可能导致 plan fail 增多
```

适合作为安全保护，不适合作为唯一方案。

**推荐优先级**

我建议按这个顺序：

```text
1. 临时验证：把 box_max_z 降到 0.4~0.5，看 A* 是否还翻墙
2. 正式修改：让 A* 搜索限制在 fixed_z ± z_margin
3. 进一步优化：把墙体占据投影到地面通行层，做真正 2.5D 地面 costmap
```

你现在最应该先验证的是方案 3，因为不用改代码：

```bash
roslaunch fuel_dog_adapter fuel_dog_visual_exploration.launch \
  box_min_z:=0.05 \
  box_max_z:=0.45 \
  fixed_z:=0.30 \
  z_margin:=0.05 \
  viewpoint_z:=0.35
```

如果这样 A* 不再翻墙，说明根因就确认了：

```text
3D A* 搜索空间太高，和四足狗 2.5D 运动约束不一致
```

最终更合理的代码级方案是：

```text
给 A* 加 fixed_z/z_margin 约束，让 path_next_goal_ 本身就是地面机器人可执行的路径。
```


---

## 20. [frontier/viewpoint] 四足狗在走廊中探索的时候，没有到走廊尽头就不继续探索，不出现frointier和viewpoint，然后就往回走了（后面有没探索的viewpoint），查看代码，分析原因

**问题**

四足狗在走廊中探索的时候，没有到走廊尽头就不继续探索，不出现frointier和viewpoint，然后就往回走了（后面有没探索的viewpoint），查看代码，分析原因

**回答**

我来查 frontier 的筛选/移除逻辑，重点看为什么走廊前方还没探索完却不再出现 frontier/viewpoint，以及为什么后方未探索点会重新被选中。

我看到几个很可疑的筛选点：frontier 可能被判“covered”触发重规划，也可能因为没有合格 viewpoint 被放进 dormant，导致 RViz 不显示。现在把这些关键函数展开看。

结合代码看，最可疑的不是“没有未知区域”，而是“走廊尽头的 frontier 被筛掉或转入 dormant，因此没有可显示 viewpoint”。我再确认你当前 launch 里的筛选阈值。

我看了代码，这个现象很可能不是“FUEL 不知道走廊尽头还有未知区域”，而是 **走廊尽头的 frontier 被筛掉了，或者被放进 dormant_frontiers_，因此没有可访问 viewpoint，最后只剩后方 frontier 可以选，所以往回走**。

关键链路在这里：

[fast_exploration_manager.cpp](/home/uestc/tzb/uestc-challenge2026/src/fuel/fuel_planner/exploration_manager/src/fast_exploration_manager.cpp:98)

```cpp
frontier_finder_->searchFrontiers();
frontier_finder_->computeFrontiersToVisit();
frontier_finder_->getFrontiers(ed_->frontiers_);
...
if (ed_->frontiers_.empty()) {
  ROS_WARN("No coverable frontier.");
  return NO_FRONTIER;
}
```

FUEL 不是找到 frontier 就一定探索，它要求 frontier 必须有合格 viewpoint。

筛选逻辑在 [frontier_finder.cpp](/home/uestc/tzb/uestc-challenge2026/src/fuel/fuel_planner/active_perception/src/frontier_finder.cpp:394)：

```cpp
sampleViewpoints(tmp_ftr);
if (!tmp_ftr.viewpoints_.empty()) {
  frontiers_.insert(...);
} else {
  dormant_frontiers_.push_back(tmp_ftr);
}
```

也就是说：

```text
有 frontier + 有 viewpoint -> 可探索 frontiers_
有 frontier + 没 viewpoint -> dormant，不参与当前探索
```

你说“没有 frontier 和 viewpoint 显示”，可能分两种：

```text
1. 真没检测到 frontier
2. 检测到了，但没有合格 viewpoint，被放进 dormant，RViz 不显示 active viewpoint
```

走廊尽头最容易触发第 2 种。

为什么走廊尽头的 viewpoint 会被筛掉？看这里：

[frontier_finder.cpp](/home/uestc/tzb/uestc-challenge2026/src/fuel/fuel_planner/active_perception/src/frontier_finder.cpp:663)

```cpp
sample_pos = frontier.average_ + rc * Vector3d(cos(phi), sin(phi), 0);
if (force_viewpoint_z_) sample_pos[2] = viewpoint_z_;

if (!isInBox(sample_pos) ||
    getInflateOccupancy(sample_pos) == 1 ||
    isNearUnknown(sample_pos))
  continue;

visib_num = countVisibleCells(sample_pos, avg_yaw, cells);
if (visib_num > min_visib_num_) {
  frontier.viewpoints_.push_back(vp);
}
```

你的当前参数是：

```xml
frontier_candidate_rmin = 0.8
frontier_candidate_rmax = 1.8
frontier_min_candidate_clearance = 0.12
frontier_min_visib_num = 3
```

这意味着走廊尽头的 viewpoint 必须满足：

```text
距离 frontier 0.8~1.8m
不在障碍膨胀区
附近不能有 unknown
能看到至少 3 个 frontier cell
```

在走廊尽头/墙角，这些条件很容易失败：

```text
候选点采到墙里
候选点太靠近侧墙膨胀区
候选点附近有 unknown，被 isNearUnknown 拒绝
低视角被墙角遮挡，看不到足够 frontier cell
frontier 太小，小于 cluster_min 或 min_visib_num
```

所以结果就是：

```text
前方还有未知区域
但前方 frontier 没有合格 viewpoint
-> 进入 dormant
-> RViz 不显示 viewpoint
-> TSP/全局选择只剩后方可访问 frontier
-> 狗往回走
```

还有一个代码点会导致“没到尽头就认为前方 frontier 覆盖了”：

[frontier_finder.cpp](/home/uestc/tzb/uestc-challenge2026/src/fuel/fuel_planner/active_perception/src/frontier_finder.cpp:700)

```cpp
isFrontierCovered()
```

它判断 frontier 有一定比例 cell 不再满足：

```cpp
knownfree(idx) && isNeighborUnknown(idx)
```

就认为 frontier 变化/覆盖了。默认：

```xml
frontier/min_view_finish_fraction = 0.2
```

也就是大约 20% 变化就可能触发 replan。走廊里深度图看到前方墙面或边界后，frontier cluster 可能快速变化，被移除或重算。

你可以看终端这几行判断是不是这个问题：

```text
new num: X, new dormant: Y
to visit: A, dormant: B
No coverable frontier.
```

如果你在走廊尽头附近看到：

```text
new dormant 增加
to visit 变少
```

那就基本确认是“frontier 有，但没有合格 viewpoint”。

当前最可疑的参数优先级是：

```text
frontier_candidate_rmin/rmax
frontier_min_candidate_clearance
frontier_min_visib_num
frontier_cluster_min
perception_max_dist
```

其中对走廊尽头影响最大的通常是：

```xml
frontier_candidate_rmin="0.8"
frontier_candidate_rmax="1.8"
```

如果走廊窄或者尽头空间小，0.8m 起步可能太远，候选点会采到墙/未知/膨胀区里。  
其次是：

```xml
frontier_min_candidate_clearance="0.12"
```

它会让靠近 unknown 的候选 viewpoint 被拒绝；在走廊尽头这种本来就贴近 unknown 的地方，容易过严。

所以代码层面的原因可以总结为：

```text
FUEL 只探索 active frontiers；
走廊尽头的 frontier 因为采样不到满足安全、可见性、距离要求的 viewpoint，
被移入 dormant 或被判 covered/changed；
于是 RViz 没有前方 viewpoint，规划器转而选择后方仍可访问的 frontier。
```


---

## 21. [frontier/viewpoint] rviz中紫色和绿色的viewpoint有什么区别

**问题**

rviz中紫色和绿色的viewpoint有什么区别

**回答**

我先看一下 RViz 配置和 FUEL 的可视化代码里这两个颜色分别绑定了哪个 marker，确认不是凭颜色猜。

RViz 里紫色和绿色的 `viewpoint` 都来自同一个话题：

`/planning_vis/viewpoints`

但它们是不同的 namespace：

- 绿色点/绿色线：`global_viewpoints` / `global_view_dirs`
  - 这是 FUEL 对所有当前 frontier 生成的“全局候选观察点”。
  - 作用是给全局 TSP/访问顺序用，表示“这些 frontier 大概要从哪些地方看”。

- 紫色/蓝紫色点或线：`refined_viewpoints` / `refined_view_dirs`
  - 这是对下一批要访问的 frontier 做局部优化后的观察点。
  - 更接近当前真正要去的目标，用于后续 A*、B 样条轨迹生成。
  - 也就是比绿色的更“精修”、更贴近当前执行规划。

对应代码在：

[src/fuel/fuel_planner/exploration_manager/src/fast_exploration_fsm.cpp](/home/uestc/tzb/uestc-challenge2026/src/fuel/fuel_planner/exploration_manager/src/fast_exploration_fsm.cpp:229)

关键逻辑是：

```cpp
// global
drawSpheres(ed_ptr->points_, ..., "global_viewpoints");
drawLines(ed_ptr->points_, ed_ptr->views_, ..., "global_view_dirs");

// refined
drawSpheres(ed_ptr->refined_points_, ..., "refined_viewpoints");
drawLines(ed_ptr->refined_points_, ed_ptr->refined_views_, ..., "refined_view_dirs");
```

所以简单说：

绿色 = 全局候选 viewpoint。  
紫色/蓝紫色 = 局部 refine 后、当前更可能执行的 viewpoint。

你在 RViz 左侧 `FUEL Planning -> viewpoints -> Namespaces` 里可以分别开关 `global_viewpoints`、`refined_viewpoints`，这样最容易看出区别。


---

## 22. [探索范围] 我发现四足狗总是走到走廊的一个地点就停止探索了，算法中是不是存在探索范围限制？查看代码，分析

**问题**

我发现四足狗总是走到走廊的一个地点就停止探索了，算法中是不是存在探索范围限制？查看代码，分析

**回答**

我去查一下 FUEL 的地图边界、frontier 搜索边界、以及 launch 里传进去的 box/range 参数。这个现象很像“局部地图/探索盒子边界到了”或者“frontier 被筛掉”，需要从参数链路确认。

已经看到明确有全局 `box_min/max`、地图尺寸、局部更新范围和 frontier 过滤参数。接下来我看源码里这些限制怎么生效，尤其是“到某个固定地点后没有 frontier/viewpoint”的路径。

关键代码链路已经比较清楚了：FUEL 不是无边界探索，它只在 `sdf_map/box_*` 内找 frontier，而且只在“这次传感器更新过的区域”附近增量搜索。再看一点地图更新 box 的生成逻辑，判断是否还叠加了局部更新半径。

我还发现一个很关键的硬编码：frontier 聚类时会丢掉 `z < 0.4` 的 frontier 点。你现在四足狗 fixed_z/viewpoint_z 在 0.30/0.35 附近，这个阈值可能让低高度 corridor frontier 变少甚至消失。

有，FUEL 里确实存在多层“探索范围/可探索区域”的限制。你这个“走廊走到某个地点就不继续探索、前方不出 frontier/viewpoint、然后回头”的现象，很可能不是单一原因，而是下面几类限制叠加导致的。

**1. 全局探索盒子限制**

当前 launch 里有：

```xml
<arg name="box_min_x" default="-25.0"/>
<arg name="box_min_y" default="-25.0"/>
<arg name="box_min_z" default="0.05"/>
<arg name="box_max_x" default="25.0"/>
<arg name="box_max_y" default="25.0"/>
<arg name="box_max_z" default="1.5"/>
```

位置在：

[src/fuel/fuel_dog_adapter/launch/fuel_dog_visual_exploration.launch](/home/uestc/tzb/uestc-challenge2026/src/fuel/fuel_dog_adapter/launch/fuel_dog_visual_exploration.launch:13)

这些参数传给 `algorithm.xml`，最后进入 `sdf_map/box_min_*` 和 `sdf_map/box_max_*`。

源码里 `isInBox()` 会直接拒绝 box 外的点：

[src/fuel/fuel_planner/plan_env/include/plan_env/sdf_map.h](/home/uestc/tzb/uestc-challenge2026/src/fuel/fuel_planner/plan_env/include/plan_env/sdf_map.h:180)

```cpp
if (pos[i] <= mp_->box_mind_[i] || pos[i] >= mp_->box_maxd_[i]) {
  return false;
}
```

所以如果走廊尽头或者候选 viewpoint 超出了 `[-25, 25]` 范围，FUEL 就不会认为那里可探索。

**2. 地图本身也有范围限制**

当前地图尺寸：

```xml
<arg name="map_size_x" default="50.0"/>
<arg name="map_size_y" default="50.0"/>
<arg name="map_size_z" default="1.8"/>
```

地图原点由源码固定成：

[src/fuel/fuel_planner/plan_env/src/sdf_map.cpp](/home/uestc/tzb/uestc-challenge2026/src/fuel/fuel_planner/plan_env/src/sdf_map.cpp:33)

```cpp
mp_->map_origin_ = Eigen::Vector3d(-x_size / 2.0, -y_size / 2.0, mp_->ground_height_);
```

所以 `map_size_x/y=50` 实际地图也是大约 `x/y = -25 ~ 25`。  
如果厂房坐标或走廊长度超过这个范围，前方会直接不在地图里。

你可以在走廊停止的位置查一下：

```bash
rostopic echo -n1 /Odometry_gazebo/pose/pose/position
```

如果 `x` 或 `y` 接近 `25` 或 `-25`，那基本就是范围撞边了。

**3. frontier 只在“更新过的局部区域附近”搜索**

FUEL 不是每次都全地图重新找 frontier，而是先拿传感器最近更新的区域：

[src/fuel/fuel_planner/active_perception/src/frontier_finder.cpp](/home/uestc/tzb/uestc-challenge2026/src/fuel/fuel_planner/active_perception/src/frontier_finder.cpp:60)

```cpp
edt_env_->sdf_map_->getUpdatedBox(update_min, update_max, true);
```

然后只在这个 update box 外扩一点范围里找 frontier：

```cpp
Vector3d search_min = update_min - Vector3d(1, 1, 0.5);
Vector3d search_max = update_max + Vector3d(1, 1, 0.5);
```

也就是说，如果走廊前方深度相机没有产生有效的新地图更新，frontier 搜索区域就不会继续往前扩。

这也能解释你之前的现象：给四足狗一个较大的向前速度后，viewpoint 突然出现。不是触发条件神秘，而是新的深度帧/姿态/局部 update box 让前方未知边界重新进入了 frontier 搜索范围。

**4. 传感器有效距离限制**

当前视觉探索里：

```xml
<arg name="perception_max_dist" default="4.5"/>
<arg name="perception_vis_dist" default="2.0"/>
```

以及地图融合里：

```xml
<param name="sdf_map/max_ray_length" value="4.5"/>
<param name="map_ros/depth_filter_maxdist" value="5.0"/>
```

这些决定了深度相机最多把多远的空间更新进地图。走廊很长时，远处未知区域只有靠狗继续向前移动才会被更新；如果中间某处前方视野被墙角、门框、深度缺失挡住，frontier 就可能断掉。

**5. 还有一个很关键的硬编码高度限制**

在 frontier 聚类时，有一行：

[src/fuel/fuel_planner/active_perception/src/frontier_finder.cpp](/home/uestc/tzb/uestc-challenge2026/src/fuel/fuel_planner/active_perception/src/frontier_finder.cpp:153)

```cpp
if (pos[2] < 0.4) continue;  // Remove noise close to ground
```

但我们现在四足狗是低高度探索：

```xml
<arg name="fixed_z" default="0.30"/>
<arg name="viewpoint_z" default="0.35"/>
```

这就有点冲突：  
轨迹/viewpoint 在 0.30~0.35 附近，但 frontier 聚类会丢掉 `z < 0.4` 的 frontier 点。走廊里如果主要可见未知边界集中在低高度，就可能被过滤掉，导致“不出现 frontier/viewpoint”。

这个是我目前看到的最可疑点之一。

**6. viewpoint 采样也会筛掉很多候选点**

即使找到了 frontier，也要能给它生成 viewpoint。源码在这里：

[src/fuel/fuel_planner/active_perception/src/frontier_finder.cpp](/home/uestc/tzb/uestc-challenge2026/src/fuel/fuel_planner/active_perception/src/frontier_finder.cpp:663)

候选 viewpoint 会被这些条件拒绝：

```cpp
!isInBox(sample_pos)
getInflateOccupancy(sample_pos) == 1
isNearUnknown(sample_pos)
visib_num <= min_visib_num_
```

当前参数：

```xml
frontier_candidate_rmin = 0.8
frontier_candidate_rmax = 1.8
frontier_min_candidate_clearance = 0.12
frontier_min_visib_num = 3
```

在窄走廊里，frontier 周围 0.8~1.8m 的圆环采样点可能落到墙里、膨胀层里、未知区旁边，于是没有可用 viewpoint。源码会把这种 frontier 放进 dormant：

```cpp
dormant_frontiers_.push_back(tmp_ftr);
```

结果就是：前方 frontier 可能存在过，但没有合格 viewpoint，所以不会显示成可访问目标；算法转而选择后面还有效的 viewpoint，于是狗回头。

**结论**

是的，算法中有探索范围限制，而且有几层：

1. `map_size_x/y/z`：地图最大范围。
2. `box_min/max`：允许探索的边界盒。
3. `updated_box`：每次只在最近传感器更新区域附近找 frontier。
4. `max_ray_length / perception_max_dist`：视觉建图距离限制。
5. `pos[2] < 0.4 continue`：frontier 高度硬过滤。
6. `candidate_rmin/rmax / clearance / min_visib_num`：viewpoint 采样过滤。

针对你这个“走廊固定位置停止”的现象，我优先怀疑这三个：

- 走廊前方超出 `box_min/max` 或接近地图边界；
- 前方 frontier 被 `z < 0.4` 硬过滤掉；
- 窄走廊里 viewpoint 候选点被 `candidate_rmin=0.8`、膨胀层、`isNearUnknown()` 筛掉，变成 dormant frontier。

建议你下一步先记录停止点的 odom 坐标，看是不是接近 `x/y = ±25`。如果不是，那更可能是 frontier/viewpoint 筛选问题。


---

## 23. [A*/B 样条] B样条的优化参数有哪些

**问题**

B样条的优化参数有哪些

**回答**

我查一下 `bspline_opt` 和 `algorithm.xml` 里实际读取的参数，给你按“launch 里能调的”和“源码内部用到的”分清楚。

B 样条优化参数主要在这个文件里设置：

[src/fuel/fuel_planner/exploration_manager/launch/algorithm.xml](/home/uestc/tzb/uestc-challenge2026/src/fuel/fuel_planner/exploration_manager/launch/algorithm.xml:188)

源码读取位置是：

[src/fuel/fuel_planner/bspline_opt/src/bspline_optimizer.cpp](/home/uestc/tzb/uestc-challenge2026/src/fuel/fuel_planner/bspline_opt/src/bspline_optimizer.cpp:26)

主要参数如下。

**代价权重**

```xml
optimization/ld_smooth = 20.0
optimization/ld_dist   = 10.0
optimization/ld_feasi  = 2.0
optimization/ld_start  = 100.0
optimization/ld_end    = 0.5
optimization/ld_guide  = 1.5
optimization/ld_waypt  = 0.3
optimization/ld_view   = 0.0
optimization/ld_time   = 1.0
```

含义：

- `ld_smooth`：轨迹平滑权重，越大轨迹越顺，但可能更贴近障碍或偏离 A* 路径。
- `ld_dist`：避障距离权重，越大越远离障碍。
- `ld_feasi`：动力学可行性权重，约束速度、加速度不要超限。
- `ld_start`：起点状态约束权重，保证轨迹从当前状态平滑接上。
- `ld_end`：终点约束权重。
- `ld_guide`：贴近引导路径的权重，通常就是贴近 A*/kino path。
- `ld_waypt`：经过指定 waypoint 的权重。
- `ld_view`：视角/感知约束权重，目前是 `0.0`，基本没启用。
- `ld_time`：时间优化权重，影响轨迹时间分配。

**安全距离和动力学限制**

```xml
optimization/dist0 = 0.7
optimization/max_vel = $(arg max_vel)
optimization/max_acc = $(arg max_acc)
```

- `dist0`：B 样条优化希望和障碍保持的安全距离。四足狗撞墙/贴墙时，这个参数很重要。
- `max_vel`：优化时允许的最大速度。
- `max_acc`：优化时允许的最大加速度。

注意：`dist0=0.7` 是优化层的避障距离，不等于地图膨胀半径 `sdf_map/obstacles_inflation`。  
膨胀半径是地图层“哪里不能走”，`dist0` 是轨迹优化层“尽量离障碍多远”。

**四足狗高度限制**

这是我们为了四足狗加的：

```xml
optimization/fixed_z = $(arg fixed_z)
optimization/z_margin = $(arg z_margin)
```

当前在 `fuel_dog_visual_exploration.launch` 里是：

```xml
<arg name="fixed_z" default="0.30"/>
<arg name="z_margin" default="0.05"/>
```

含义：

- `fixed_z=0.30`：希望 B 样条轨迹基本固定在 z=0.30m。
- `z_margin=0.05`：允许 z 在 `0.25 ~ 0.35m` 之间浮动。

这个防止原版无人机轨迹往上飞，适配四足狗平面运动。

**优化算法和迭代限制**

```xml
optimization/algorithm1 = 15
optimization/algorithm2 = 11

optimization/max_iteration_num1 = 2
optimization/max_iteration_num2 = 2000
optimization/max_iteration_num3 = 200
optimization/max_iteration_num4 = 200

optimization/max_iteration_time1 = 0.0001
optimization/max_iteration_time2 = 0.005
optimization/max_iteration_time3 = 0.003
optimization/max_iteration_time4 = 0.003
```

这些是 NLopt 优化器相关参数：

- `algorithm1`：二次型优化时用的算法。
- `algorithm2`：一般非线性优化时用的算法。
- `max_iteration_num*`：最大迭代次数。
- `max_iteration_time*`：最大优化时间。

一般不优先调这些，除非出现优化经常失败或耗时过高。

**B 样条时间重分配限制**

```xml
bspline/limit_vel = $(arg max_vel)
bspline/limit_acc = $(arg max_acc)
bspline/limit_ratio = 1.1
```

对应源码：

[src/fuel/fuel_planner/bspline/src/non_uniform_bspline.cpp](/home/uestc/tzb/uestc-challenge2026/src/fuel/fuel_planner/bspline/src/non_uniform_bspline.cpp:354)

含义：

- `limit_vel`：检查/拉伸 B 样条时间时的速度上限。
- `limit_acc`：加速度上限。
- `limit_ratio`：每次时间拉伸的最大比例。

简单说，如果轨迹速度/加速度超限，B 样条会把时间拉长，让轨迹变慢。

你现在如果重点解决四足狗碰墙、穿墙、贴墙，优先看这几个：

```xml
sdf_map/obstacles_inflation
optimization/dist0
manager/clearance_threshold
optimization/ld_dist
optimization/ld_guide
fixed_z / z_margin
```

如果重点解决“轨迹太激进、狗跟不上”，优先看：

```xml
max_vel
max_acc
optimization/max_vel
optimization/max_acc
bspline/limit_vel
bspline/limit_acc
bspline/limit_ratio
```


---

## 24. [A*/B 样条] 已经将A*算法从三维降到二维，应该如何调整参数使B样条适配

**问题**

已经将A*算法从三维降到二维，应该如何调整参数使B样条适配

**回答**

二维 A* 之后，B 样条要做两件事：**高度锁死**，以及**不要把二维 A* 路径平滑到穿墙/贴墙**。重点调 `algorithm.xml` 和 `fuel_dog_visual_exploration.launch` 里的这些参数。

**1. 让 B 样条和 A* 用同一个高度**

你现在 A* 已经用：

```xml
<param name="astar/fixed_z" value="$(arg fixed_z)"/>
<param name="astar/z_margin" value="$(arg z_margin)"/>
```

B 样条也要保持一致：

```xml
<param name="optimization/fixed_z" value="$(arg fixed_z)"/>
<param name="optimization/z_margin" value="$(arg z_margin)"/>
```

建议在 `fuel_dog_visual_exploration.launch` 里先用：

```xml
<arg name="fixed_z" default="0.30"/>
<arg name="z_margin" default="0.03"/>
<arg name="viewpoint_z" default="0.30"/>
```

或者最多：

```xml
viewpoint_z = 0.35
fixed_z = 0.30
z_margin = 0.05
```

但最稳的是三者尽量统一。否则 frontier viewpoint 在 0.35，A* 投影到 0.30，B 样条又约束在 0.30，容易出现目标高度不一致。

**2. 增大贴近 A* 路径的权重**

二维 A* 已经负责绕墙了，B 样条优化时不能太自由，否则会把拐角“抹平”到墙里。

当前：

```xml
<param name="optimization/ld_guide" value="1.5"/>
```

建议改成：

```xml
<param name="optimization/ld_guide" value="3.0"/>
```

如果还是明显切墙角，可以试：

```xml
ld_guide = 5.0
```

这个参数越大，B 样条越贴近 A* 给出的引导路径。

**3. 增强避障，但不要过大**

当前：

```xml
<param name="optimization/ld_dist" value="10.0"/>
<param name="optimization/dist0" value="0.7"/>
```

建议四足狗先用：

```xml
<param name="optimization/ld_dist" value="20.0"/>
<param name="optimization/dist0" value="0.45"/>
```

解释：

- `ld_dist`：避障代价权重，增大后更不愿靠墙。
- `dist0`：希望距离障碍至少多远。

你现在 `dist0=0.7` 对走廊可能偏大，尤其窄走廊里会让优化很难受。四足狗第一版建议 `0.4~0.6`。  
如果墙角还碰撞，再慢慢加到 `0.55` 或 `0.6`。

**4. 地图膨胀和 B 样条避障要配合**

地图膨胀在 `algorithm.xml`：

```xml
<param name="sdf_map/obstacles_inflation" value="0.3"/>
```

建议四足狗用：

```xml
<param name="sdf_map/obstacles_inflation" value="0.35"/>
```

如果还是擦墙：

```xml
0.4
```

不要一上来调太大，否则窄走廊会被膨胀堵死，A* 直接找不到路。

**5. 降低速度和加速度，让 B 样条更像地面机器人**

在 `fuel_dog_visual_exploration.launch`：

```xml
<arg name="max_vel" default="1.0"/>
<arg name="max_acc" default="1.2"/>
```

对四足狗建议：

```xml
<arg name="max_vel" default="0.4"/>
<arg name="max_acc" default="0.5"/>
```

如果跟踪还差：

```xml
max_vel = 0.3
max_acc = 0.4
```

这些会同步传到：

```xml
optimization/max_vel
optimization/max_acc
bspline/limit_vel
bspline/limit_acc
```

**推荐第一版组合**

可以先这样调：

```xml
<!-- fuel_dog_visual_exploration.launch -->
<arg name="fixed_z" default="0.30"/>
<arg name="z_margin" default="0.03"/>
<arg name="viewpoint_z" default="0.30"/>
<arg name="max_vel" default="0.4"/>
<arg name="max_acc" default="0.5"/>
```

```xml
<!-- algorithm.xml -->
<param name="sdf_map/obstacles_inflation" value="0.35"/>

<param name="optimization/ld_smooth" value="20.0"/>
<param name="optimization/ld_dist" value="20.0"/>
<param name="optimization/ld_feasi" value="2.0"/>
<param name="optimization/ld_guide" value="3.0"/>
<param name="optimization/dist0" value="0.45"/>

<param name="optimization/fixed_z" value="$(arg fixed_z)"/>
<param name="optimization/z_margin" value="$(arg z_margin)"/>
```

调参方向记住一句话：

- 穿墙/切墙角：加 `ld_guide`、加 `ld_dist`、适当加 `obstacles_inflation`。
- 规划失败/走廊过不去：减小 `obstacles_inflation` 或 `dist0`。
- 轨迹太飘、不贴 A*：加 `ld_guide`。
- 狗跟不上：降低 `max_vel/max_acc`。


---

## 25. [视觉深度建图] 深度相机的视角和距离是否可以调整

**问题**

深度相机的视角和距离是否可以调整

**回答**

可以调整，分成两层：

**1. Gazebo 真实深度相机参数**

你现在打开的这个文件就是相机仿真参数位置：

[src/unitree_guide/unitree_ros/robots/a1_description/xacro/gazebo.xacro](/home/uestc/tzb/uestc-challenge2026/src/unitree_guide/unitree_ros/robots/a1_description/xacro/gazebo.xacro)

通常要找这些字段：

```xml
<horizontal_fov>...</horizontal_fov>
<clip>
  <near>...</near>
  <far>...</far>
</clip>
<image>
  <width>...</width>
  <height>...</height>
</image>
```

含义：

- `horizontal_fov`：水平视角，单位是 rad。
- `clip/near`：最近可见距离。
- `clip/far`：最远可见距离。
- `width/height`：深度图分辨率。

例如：

```xml
<horizontal_fov>1.047</horizontal_fov>
```

约等于 60 度。  
如果改成：

```xml
<horizontal_fov>1.396</horizontal_fov>
```

约等于 80 度。

`far` 决定 Gazebo 相机最多能看多远，比如：

```xml
<far>5.0</far>
```

就是最多 5 米。

**2. FUEL 使用深度图建图/规划的参数**

即使 Gazebo 相机能看 10 米，FUEL 也可能只用前 4.5 米。你还要看：

[src/fuel/fuel_planner/exploration_manager/launch/algorithm.xml](/home/uestc/tzb/uestc-challenge2026/src/fuel/fuel_planner/exploration_manager/launch/algorithm.xml:136)

```xml
<param name="perception_utils/max_dist" value="$(arg perception_max_dist)"/>
<param name="perception_utils/vis_dist" value="$(arg perception_vis_dist)"/>
<param name="sdf_map/max_ray_length" value="4.5"/>
<param name="map_ros/depth_filter_maxdist" value="5.0"/>
```

以及在：

[src/fuel/fuel_dog_adapter/launch/fuel_dog_visual_exploration.launch](/home/uestc/tzb/uestc-challenge2026/src/fuel/fuel_dog_adapter/launch/fuel_dog_visual_exploration.launch:41)

```xml
<arg name="perception_max_dist" default="4.5"/>
<arg name="perception_vis_dist" default="2.0"/>
```

含义：

- `perception_max_dist`：FUEL 认为相机可感知/可视化视场的最大距离。
- `perception_vis_dist`：用于 viewpoint 可见性评价的有效距离。
- `sdf_map/max_ray_length`：建图 raycast 最大长度。
- `map_ros/depth_filter_maxdist`：深度图最大保留距离。

**建议保持一致**

如果你想让相机有效看 6 米，可以这样配：

Gazebo 相机：

```xml
<far>6.0</far>
```

FUEL launch：

```xml
<arg name="perception_max_dist" default="6.0"/>
<arg name="perception_vis_dist" default="3.0"/>
```

`algorithm.xml`：

```xml
<param name="sdf_map/max_ray_length" value="6.0"/>
<param name="map_ros/depth_filter_maxdist" value="6.0"/>
```

如果只改 Gazebo 的 `far`，FUEL 仍可能按 4.5m 用；如果只改 FUEL，Gazebo 相机本身没有更远深度，也没效果。


---

## 26. [frontier/viewpoint] 改成当前的参数后，在墙外也会出现viewpoint，分析原因

**问题**

改成当前的参数后，在墙外也会出现viewpoint，分析原因

**回答**

我先看一下你当前实际改成了哪些参数，尤其是相机距离、frontier 采样半径、box、高度和膨胀参数。墙外出现 viewpoint 通常是“frontier 在墙后未知区附近，候选点绕到了外侧且通过了可见性/安全筛选”。

会出现“墙外也有 viewpoint”，核心原因是：**FUEL 并不知道哪里是厂房内部/外部，它只知道 occupancy map 里的 free / occupied / unknown。只要某个候选 viewpoint 在地图 box 内、不是膨胀障碍、附近不是未知，并且能看到足够 frontier，它就会被接受。**

结合你现在的参数，比较可疑的原因有这几个。

**1. 你把探索范围放大到了厂房外**

现在是：

```xml
<arg name="map_size_y" default="100.0"/>
<arg name="box_min_y" default="-50.0"/>
<arg name="box_max_y" default="50.0"/>
```

这意味着 FUEL 允许在 `y=-50~50` 的大范围内生成 frontier 和 viewpoint。  
如果厂房墙外也在这个 box 内，FUEL 不会自动排除墙外区域。

所以 “viewpoint 在墙外” 不一定是 bug，而是因为墙外仍属于允许探索区域。

**2. viewpoint 是围绕 frontier 采样一圈，不保证在墙内**

源码里 viewpoint 生成逻辑是：

```cpp
sample_pos = frontier.average_ + rc * Vector3d(cos(phi), sin(phi), 0);
```

位置在：

[src/fuel/fuel_planner/active_perception/src/frontier_finder.cpp](/home/uestc/tzb/uestc-challenge2026/src/fuel/fuel_planner/active_perception/src/frontier_finder.cpp:663)

也就是说，它围绕 frontier 平均点按圆环采样。你现在参数是：

```xml
frontier_candidate_rmin = 0.8
frontier_candidate_rmax = 1.8
```

如果 frontier 靠近墙，圆环的一部分点自然可能落在墙外。只要这个点满足：

```cpp
isInBox(sample_pos)
getInflateOccupancy(sample_pos) != 1
!isNearUnknown(sample_pos)
visib_num > min_visib_num_
```

它就会被当作合法 viewpoint。

**3. 墙体在 FUEL 地图里可能没有被完整占据**

FUEL 的墙不是从 Gazebo 模型直接读来的，而是靠深度图建图得到的。  
如果深度相机某些角度看不到墙、墙边缘深度缺失、门框/墙角有空洞，FUEL 地图里可能出现“墙不连续”。

你现在还把建图距离改大了：

```xml
sdf_map/max_ray_length = 6.0
map_ros/depth_filter_maxdist = 6.0
```

Gazebo 深度相机本身：

```xml
<far>8.0</far>
```

深度距离变远后，墙边缘、门洞、细缝、缺失深度导致的 free ray 会影响更远，可能把墙外局部清成 free 或制造墙外 frontier。

**4. RViz 里的墙和 FUEL 的占据地图不是同一个东西**

Gazebo 里的厂房墙是仿真模型；FUEL 规划只看：

```text
/sdf_map/occupancy_local
/sdf_map/occupancy_local_inflate
/sdf_map/occupancy_all
/sdf_map/occupancy_all_inflate
```

如果 RViz 里你看着 viewpoint 在“墙外”，要确认 FUEL 的 `/sdf_map/occupancy_all_inflate` 里那堵墙是否真的连续。如果 FUEL 地图里墙没建出来或有洞，算法就会认为能走。

**5. 当前膨胀和 viewpoint 筛选不一定能挡住墙外点**

你现在：

```xml
sdf_map/obstacles_inflation = 0.3
frontier_min_candidate_clearance = 0.12
```

`obstacles_inflation=0.3` 只是把已知障碍膨胀 0.3m；如果墙本身没被完整建出来，膨胀也没用。  
`frontier_min_candidate_clearance=0.12` 只检查 viewpoint 附近距离未知空间的安全性，不是“必须在室内”。

**结论**

当前墙外出现 viewpoint 的最可能原因是：

1. `box_min_y/box_max_y` 扩大后，墙外也在允许探索范围内；
2. viewpoint 圆环采样可能绕到墙外；
3. FUEL 的占据地图里墙体不够连续，算法认为墙外点是 free；
4. 建图距离变到 6m 后，深度缺失/墙边缘误清空的影响变大。

建议你先在 RViz 打开这几个层确认：

```text
/sdf_map/occupancy_all
/sdf_map/occupancy_all_inflate
/planning_vis/viewpoints
/planning_vis/frontier
```

如果墙外 viewpoint 所在位置在 `/sdf_map/occupancy_all_inflate` 里没有被墙挡住，那就是地图层认为它可达，不是 B 样条的问题。  
如果地图里墙是连续的，但 viewpoint 仍在墙外，那就是 viewpoint 采样/可见性筛选需要加“连通性检查”或“室内约束”。


---

## 27. [frontier/viewpoint] 主要是墙外会出现frontier,看的感觉就像透视

**问题**

主要是墙外会出现frontier,看的感觉就像透视

**回答**

这个现象更像是**深度建图把墙后/墙外区域错误地更新成了 free/unknown 边界**，所以 frontier 出现在墙外；不是单纯 viewpoint 采样的问题。

FUEL 的 frontier 定义是：

```cpp
knownfree(cur) && isNeighborUnknown(cur)
```

也就是：**已知自由栅格旁边挨着未知栅格**。  
所以墙外出现 frontier，说明在 FUEL 地图里，墙外某些栅格已经被认为是 `FREE`，它旁边还有 `UNKNOWN`，于是形成了 frontier。算法不是“看到 Gazebo 墙外”，而是它的占据栅格里产生了“墙外自由空间”。

最可疑的地方是深度图无效值处理。源码在：

[src/fuel/fuel_planner/plan_env/src/map_ros.cpp](/home/uestc/tzb/uestc-challenge2026/src/fuel/fuel_planner/plan_env/src/map_ros.cpp:199)

```cpp
if (*row_ptr == 0 || depth > depth_filter_maxdist_)
  depth = depth_filter_maxdist_;
```

这句的意思是：**深度为 0 的无效像素，会被当成最大距离点处理**。  
然后 FUEL 会从相机到这个“最大距离点”做 raycast，把中间一路清成 free。

如果相机靠近墙、墙面反射/边缘/视角导致深度图有 0 值，FUEL 就会把这些无效深度当成“前方 6 米都没障碍”，于是 ray 穿过墙，把墙外空间清成 free。视觉上就像透视。

你现在又把距离改大了：

```xml
sdf_map/max_ray_length = 6.0
map_ros/depth_filter_maxdist = 6.0
```

Gazebo depth 相机：

```xml
<far>8.0</far>
```

这样无效深度被投到 6m，穿墙影响更明显。之前 4.5m 时影响小一些，现在 6m 会把墙后更远区域清出来，墙外 frontier 更容易出现。

另外还有一个小坑：这里代码检查的是 `*row_ptr == 0`，但前面 `row_ptr` 已经移动过：

```cpp
depth = (*row_ptr) * inv_factor;
row_ptr = row_ptr + skip_pixel;

if (*row_ptr == 0 || depth > depth_filter_maxdist_)
```

它判断的可能不是当前像素，而是下一个采样像素。这会让无效深度处理更不稳定。

**为什么看起来像透视**

因为建图逻辑不是“深度图上没点就不管”，而是：

1. 深度点有效：打到障碍，前面 ray 清 free，末端记 occupied。
2. 深度为 0 或超过最大距离：被当成最大距离，整条 ray 清 free，但末端不一定是真障碍。
3. 靠近墙时墙面部分像素无效：这些 ray 穿过墙，把墙后清成 free。
4. free 和 unknown 交界处生成 frontier。
5. RViz 里就看到 frontier 出现在墙外。

**你可以这样验证**

打开 RViz：

```text
/sdf_map/depth_cloud
/sdf_map/occupancy_all
/sdf_map/occupancy_all_inflate
/planning_vis/frontier
```

当相机贴近墙时，看 `/sdf_map/depth_cloud` 是否有点云穿到墙后，或者墙后 `/sdf_map/occupancy_all` 是否逐渐出现 free/unknown 边界。如果有，就是深度无效值 raycast 造成的。

也可以临时把距离调回短一点验证：

```xml
sdf_map/max_ray_length = 3.0~4.0
map_ros/depth_filter_maxdist = 3.0~4.0
perception_max_dist = 3.5~4.0
```

如果墙外 frontier 明显减少，就基本坐实是深度 ray 穿墙/最大距离过长导致的。

**根本解决方向**

后面如果要改代码，最关键是改 `map_ros.cpp` 的深度无效值处理：

- 深度为 0：不要当成最大距离 raycast；
- 超过最大距离：可以作为 free ray，但要谨慎；
- 靠近墙时加 raycast 截断或 invalid depth mask；
- 或者使用 `/real_sense/depth/points`，让 Gazebo 插件自己过滤无效深度，而不是自己把 0 深度投成远点。

目前你这个现象，首要怀疑就是：**无效深度被当成最大距离，raycast 穿墙，导致墙外 free 区域和 frontier。**


---

## 28. [视觉深度建图] 影响深度相机fOV视角的参数是在哪里改的

**问题**

影响深度相机fOV视角的参数是在哪里改的

**回答**

影响深度相机 FOV 的地方有两类：**Gazebo 真实深度相机 FOV** 和 **FUEL 规划里认为的 FOV**。两个最好一起改。

**1. Gazebo 深度相机真实 FOV**

在你当前打开的文件：

[src/unitree_guide/unitree_ros/robots/a1_description/xacro/gazebo.xacro](/home/uestc/tzb/uestc-challenge2026/src/unitree_guide/unitree_ros/robots/a1_description/xacro/gazebo.xacro:452)

深度相机是 `real_sense`：

```xml
<gazebo reference="real_sense">
  <sensor type="depth" name="real_sense">
    <camera>
      <horizontal_fov>${60.0*3.14/180.0}</horizontal_fov>
```

这里的：

```xml
<horizontal_fov>${60.0*3.14/180.0}</horizontal_fov>
```

就是 Gazebo 深度相机的水平视场角。当前是 **60 度**。

例如改成 80 度：

```xml
<horizontal_fov>${80.0*3.14/180.0}</horizontal_fov>
```

改成 90 度：

```xml
<horizontal_fov>${90.0*3.14/180.0}</horizontal_fov>
```

**2. FUEL 规划/可视化里认为的 FOV**

在：

[src/fuel/fuel_planner/exploration_manager/launch/algorithm.xml](/home/uestc/tzb/uestc-challenge2026/src/fuel/fuel_planner/exploration_manager/launch/algorithm.xml:132)

```xml
<param name="perception_utils/top_angle" value="0.56125"/>
<param name="perception_utils/left_angle" value="0.69222"/>
<param name="perception_utils/right_angle" value="0.68901"/>
```

这几个控制 FUEL 计算 viewpoint 可见性、红色视场锥体等用的视场角：

- `left_angle`：向左看的半角
- `right_angle`：向右看的半角
- `top_angle`：竖直方向半角

当前：

```text
left/right ≈ 0.69 rad ≈ 39.5 度
```

所以 FUEL 认为水平 FOV 大约是：

```text
39.5 + 39.5 = 79 度
```

但 Gazebo 深度相机现在是 60 度，这两个是不一致的。

如果你保持 Gazebo 深度相机 60 度，那 FUEL 这里建议改成半角 30 度：

```xml
<param name="perception_utils/left_angle" value="0.523599"/>
<param name="perception_utils/right_angle" value="0.523599"/>
```

如果你把 Gazebo 深度相机改成 80 度，那 FUEL 这里可以保持大约：

```xml
<param name="perception_utils/left_angle" value="0.698132"/>
<param name="perception_utils/right_angle" value="0.698132"/>
```

**3. 相机内参也要一致**

在：

[src/fuel/fuel_dog_adapter/launch/fuel_dog_visual_exploration.launch](/home/uestc/tzb/uestc-challenge2026/src/fuel/fuel_dog_adapter/launch/fuel_dog_visual_exploration.launch:31)

```xml
<arg name="fx" default="554.256"/>
<arg name="fy" default="554.256"/>
<arg name="cx" default="320.0"/>
<arg name="cy" default="240.0"/>
```

`fx/fy` 也隐含了 FOV。  
如果 Gazebo 相机是 640 宽，水平 FOV = 60 度，那么：

```text
fx = width / (2 * tan(fov/2))
fx = 640 / (2 * tan(30°)) ≈ 554.26
```

所以当前 `fx=554.256` 正好对应 60 度。

如果你改成 80 度，`fx` 应该约为：

```text
fx = 640 / (2 * tan(40°)) ≈ 381.34
```

也就是：

```xml
<arg name="fx" default="381.34"/>
```

简单总结：

- 真实深度相机 FOV：改 `gazebo.xacro` 的 `real_sense` 里的 `<horizontal_fov>`。
- FUEL 认为的 FOV：改 `algorithm.xml` 的 `perception_utils/left_angle/right_angle/top_angle`。
- 深度投影内参：改 `fuel_dog_visual_exploration.launch` 的 `fx/fy/cx/cy`，或者从 `/real_sense/depth/camera_info` 获取一致值。


---

## 29. [A*/B 样条] 在四足狗需要进入房间内时，A*算法可能会规划出一个U型轨迹，要求四足狗绕过墙进入房间，但是此时B样条优化出来的轨迹可能是直接连接U型轨迹的起点和终点，即穿墙，分析应该调哪个参数

**问题**

在四足狗需要进入房间内时，A*算法可能会规划出一个U型轨迹，要求四足狗绕过墙进入房间，但是此时B样条优化出来的轨迹可能是直接连接U型轨迹的起点和终点，即穿墙，分析应该调哪个参数

**回答**

这个现象本质是：**A* 给出的是绕墙的 U 型引导路径，但 B 样条优化时“抄近路”把 U 型抹平了**。  
所以优先调的不是 A*，而是 **B 样条对引导路径和障碍的约束强度**。

最相关的参数在 [algorithm.xml](/home/uestc/tzb/uestc-challenge2026/src/fuel/fuel_planner/exploration_manager/launch/algorithm.xml:188)。

**优先调这三个**

```xml
<param name="optimization/ld_guide" value="5.0"/>
<param name="optimization/ld_dist" value="10.0"/>
<param name="optimization/dist0" value="0.7"/>
```

含义：

- `ld_guide`：B 样条贴近 A* 引导路径的权重。
  - 你这个“U 型被拉直”最应该先加它。
  - 当前已经是 `5.0`，可以继续试 `8.0`、`10.0`。

- `ld_dist`：避障代价权重。
  - 如果 B 样条穿墙，说明优化层避障惩罚不够，或者墙在 ESDF/占据地图中不连续。
  - 可以从 `10.0` 提到 `20.0`、`30.0`。

- `dist0`：希望轨迹离障碍保持的安全距离。
  - 当前 `0.7` 已经不小。
  - 但如果走廊/门很窄，`dist0` 太大反而会导致优化困难。
  - 对四足狗可以试 `0.45~0.6`，不要盲目越大越好。

**推荐第一组尝试**

如果门/走廊不算很窄：

```xml
<param name="optimization/ld_guide" value="10.0"/>
<param name="optimization/ld_dist" value="20.0"/>
<param name="optimization/dist0" value="0.55"/>
```

如果门很窄，先用：

```xml
<param name="optimization/ld_guide" value="10.0"/>
<param name="optimization/ld_dist" value="20.0"/>
<param name="optimization/dist0" value="0.45"/>
```

**另外一个很关键的参数**

```xml
<param name="manager/control_points_distance" value="0.35"/>
```

这个影响 B 样条控制点间距。U 型轨迹被抹平，可能是控制点太稀，表达不出门口绕行的形状。

可以试小一点：

```xml
<param name="manager/control_points_distance" value="0.25"/>
```

甚至：

```xml
0.20
```

控制点更密，B 样条更容易贴着 A* 的 U 型路径走，但计算量会增加一点。

**还有这个参数也相关**

```xml
<param name="manager/local_segment_length" value="6.0"/>
```

如果局部优化段太长，起点和终点跨过一堵墙，B 样条有机会直接把中间拉直。可以适当减小：

```xml
<param name="manager/local_segment_length" value="3.0"/>
```

或者：

```xml
4.0
```

这样每次只优化较短的一段，更不容易跨墙直连。

**不要优先调这个**

```xml
sdf_map/obstacles_inflation
```

它当然也有影响，但你这个问题更像 “B 样条不贴 A*，把 U 型平滑掉”。  
如果一上来只增大膨胀，可能导致门口直接被堵死，A* 都进不去。

**推荐调参顺序**

1. 先加 `ld_guide`：
   ```xml
   5.0 -> 10.0
   ```

2. 再加 `ld_dist`：
   ```xml
   10.0 -> 20.0
   ```

3. 把 `control_points_distance` 减小：
   ```xml
   0.35 -> 0.25
   ```

4. 如果仍然跨墙，把 `local_segment_length` 减小：
   ```xml
   6.0 -> 4.0 或 3.0
   ```

5. 最后再微调 `dist0`：
   ```xml
   窄门：0.45~0.55
   宽区域：0.6~0.7
   ```

一句话：  
**U 型被 B 样条拉直，优先加 `optimization/ld_guide`，减小 `manager/control_points_distance`，必要时减小 `manager/local_segment_length`。**


---

## 30. [视觉深度建图] raycast是什么

**问题**

raycast是什么

**回答**

`raycast` 可以理解成：**从相机位置沿着某个方向打一条虚拟射线，看看这条线经过哪些栅格，最后打到哪里。**

在 FUEL 建图里，它的作用是把深度相机数据变成占据栅格地图。

举个例子，深度相机看到前方 3 米有一堵墙：

```text
相机  ---- 1m ---- 2m ---- 3m墙
```

FUEL 会从相机中心向这个深度点打一条 ray：

```text
相机 -> free -> free -> occupied
```

于是地图更新成：

- 射线中间经过的格子：自由空间 `FREE`
- 射线末端的深度点：障碍物 `OCCUPIED`
- 射线没扫到的地方：未知空间 `UNKNOWN`

所以 raycast 是建图的核心动作之一。

在代码里大概对应这个过程：

```cpp
caster_->input(pt_w, camera_pos);
while (caster_->nextId(idx))
  setCacheOccupancy(toAddress(idx), 0);
```

意思就是：从相机到深度点之间经过的格子都标成 free。

为什么你之前会看到“墙外 frontier 像透视”？  
因为如果深度无效值被当成远距离点，raycast 就可能穿过墙，把墙后面的格子错误标成 free，然后 free 和 unknown 的边界就生成了 frontier。


---

## 31. [视觉深度建图 / frontier/viewpoint / A*/B 样条 / 避障膨胀] 之前出现过四足狗靠近墙壁，frontier穿墙的问题，但是当时没有调<param name="sdf_map/min_ray_length" value="0.5"/>，而是直接调了gazebo.xa

**问题**

之前出现过四足狗靠近墙壁，frontier穿墙的问题，但是当时没有调<param name="sdf_map/min_ray_length" value="0.5"/>，而是直接调了gazebo.xacro里的参数<pointCloudCutoff>0.05</pointCloudCutoff>，也解决了靠近墙壁，frontier穿墙的问题，为什么

**回答**

我看一下 `pointCloudCutoff` 在你这个 depth 插件里的上下文，方便把它和 FUEL 的 `min_ray_length` 区分清楚。

因为这两个参数作用在**不同层级**，但最后都会影响“有没有错误的深度射线穿过墙”。

`pointCloudCutoff` 是 Gazebo 深度相机插件层的近距离点过滤：

```xml
<pointCloudCutoff>0.05</pointCloudCutoff>
```

它影响 `/real_sense/depth/points`，也可能影响插件生成深度数据时对太近/异常点的处理。你当时靠墙时出现 frontier 穿墙，可能是因为相机离墙太近，墙面/边缘产生了非常近的异常点、无效点或不稳定深度。调 `pointCloudCutoff` 后，插件不再输出这部分异常近点，后面的 FUEL 建图就少了错误 raycast，所以墙外 frontier 消失了。

而 `sdf_map/min_ray_length` 是 FUEL 建图层的参数：

```xml
<param name="sdf_map/min_ray_length" value="0.5"/>
```

但在你当前这套源码里，我只看到 `max_ray_length` 被 `SDFMap::inputPointCloud()` 明确读取使用，`min_ray_length` 没有被 `SDFMap` 读入使用。真正被 FUEL 深度图处理用到的近距离过滤是：

```xml
<param name="map_ros/depth_filter_mindist" value="0.2"/>
```

对应代码：

```cpp
else if (depth < depth_filter_mindist_)
  continue;
```

所以你没调 `sdf_map/min_ray_length` 也能解决，是合理的：**这个参数在当前 exploration 的深度建图链路里基本不是关键生效点**。

现在这几个参数的层级关系可以这样理解：

```text
Gazebo depth sensor
  <clip><near/far>
  <pointCloudCutoff>
        ↓
ROS depth image / point cloud
        ↓
FUEL map_ros
  depth_filter_mindist
  depth_filter_maxdist
        ↓
FUEL SDFMap raycast
  max_ray_length
        ↓
occupancy / frontier
```

你的问题是“靠墙时 frontier 像穿墙”，根因通常是靠墙深度异常导致 raycast 错误清空墙后空间。  
`pointCloudCutoff` 在源头减少异常深度/异常点，所以能解决；`depth_filter_mindist` 是在 FUEL 输入层过滤近距离深度，也可能解决；`max_ray_length` 控制错误 ray 最多穿多远；而 `sdf_map/min_ray_length` 在当前代码里不一定真的参与。


---

## 32. [视觉深度建图 / frontier/viewpoint / A*/B 样条] 那你解释为什么map_ros/depth_filter_maxdist sdf_map/max_ray_length perception_max_dist都改成6，frointier会穿墙，但是四足

**问题**

那你解释为什么map_ros/depth_filter_maxdist
sdf_map/max_ray_length    perception_max_dist都改成6，frointier会穿墙，但是四足狗没有靠近墙

**回答**

即使四足狗没有靠近墙，把这三个都改成 6 也可能让 frontier “穿墙”。原因不是只有“贴墙”才会穿，核心是：**远距离 raycast 更容易把墙后空间错误清成 free**。

这三个参数作用叠加起来是这样的：

```text
map_ros/depth_filter_maxdist = 6
  深度图里无效/过远像素会被当成 6m 远点处理

sdf_map/max_ray_length = 6
  FUEL 允许 raycast 清空相机到 6m 之间的栅格

perception_max_dist = 6
  FUEL 认为 6m 内都属于可感知/可见范围，用于 viewpoint/frontier 可见性评价
```

最关键的是 `map_ros/depth_filter_maxdist`。源码里有这段：

```cpp
if (*row_ptr == 0 || depth > depth_filter_maxdist_)
  depth = depth_filter_maxdist_;
```

也就是说，如果深度图某个像素是 0，FUEL 不会说“这个像素无效，跳过”，而是把它当成 **6 米远处有一个点**。

然后 `sdf_map/max_ray_length=6` 允许从相机到这个 6m 点之间整条 ray 都被清成 free：

```text
相机 ---- free ---- free ---- free ---- 6m远点
```

如果这个像素方向刚好穿过墙面缺失、墙边缘、门框边缘、薄墙缝隙，FUEL 就会把墙后区域清出来。于是墙后出现：

```text
free + unknown 的边界
```

这就是 frontier。

所以不是必须靠近墙才会发生。只要有这些情况就可能发生：

1. **远处墙面深度有空洞**
   - Gazebo 深度相机对远处墙、斜角墙、边缘、薄墙可能产生 0 深度或不稳定深度。
   - 你把最大距离设成 6 后，这些 0 深度会变成长 ray。

2. **墙体在深度图里不是完全连续的**
   - Gazebo 模型的墙可能很薄。
   - 深度图分辨率有限，墙边缘会有 aliasing。
   - 一些 ray 从门缝、墙角、模型缝隙穿过去，FUEL 就认为后面可见。

3. **FOV 或内参不一致**
   - Gazebo 深度相机是 60 度水平视角。
   - 如果 FUEL 的 `fx/fy` 或 `perception_utils/left_angle/right_angle` 和真实深度相机不一致，投影出来的点云可能比真实视场更宽或方向略偏。
   - 这也会造成“看起来像穿墙”。

4. **6m 比 4m 更容易穿过建筑结构**
   - 4m 时错误 ray 可能还没穿到墙后明显区域。
   - 6m 时同一条错误 ray 可以把墙后更远空间清出来，frontier 更明显。

5. **`perception_max_dist=6` 会让算法更愿意相信远处信息**
   - 它不直接建图，但会影响 viewpoint 可见性评价。
   - 墙后如果已经被错误清出 free/unknown 边界，6m 的感知距离会让这些 frontier 更容易被认为“可观察”。

所以这三个同时改成 6 后，链路变成：

```text
深度无效像素/墙边缘空洞
    ↓
被 depth_filter_maxdist 当成 6m 深度
    ↓
max_ray_length 允许清空 6m ray
    ↓
墙后被错误清成 free
    ↓
free 和 unknown 交界生成 frontier
    ↓
perception_max_dist=6 让这些 frontier 更容易参与 viewpoint 评价
```

如果想验证，可以只改一个参数做对比：

```xml
map_ros/depth_filter_maxdist = 4.0
sdf_map/max_ray_length = 4.0
perception_max_dist = 6.0
```

如果墙外 frontier 明显减少，说明主要是建图 ray 太长导致的。

再试：

```xml
map_ros/depth_filter_maxdist = 6.0
sdf_map/max_ray_length = 4.0
perception_max_dist = 6.0
```

如果也减少，说明 `sdf_map/max_ray_length` 是关键限制。

最稳的搭配一般是：

```xml
map_ros/depth_filter_maxdist = 4.0~5.0
sdf_map/max_ray_length = 4.0~5.0
perception_max_dist = 不大于 max_ray_length
```

不要让 `perception_max_dist` 明显大于实际可靠建图距离。对于你这个厂房/房间场景，先用 `4.0~4.5m` 会比 6m 稳很多。


---

## 33. [避障膨胀] 四足机器人的膨胀系数在哪里调

**问题**

四足机器人的膨胀系数在哪里调

**回答**

四足机器人在 FUEL 里的“膨胀系数”主要调这里：

[src/fuel/fuel_planner/exploration_manager/launch/algorithm.xml](/home/uestc/tzb/uestc-challenge2026/src/fuel/fuel_planner/exploration_manager/launch/algorithm.xml:52)

```xml
<param name="sdf_map/obstacles_inflation" value="0.3"/>
```

它表示把占据障碍在地图里向外膨胀多少米。比如：

```xml
0.3
```

就是障碍大约向外扩 `0.3m`，A* 和 B 样条都会把膨胀后的区域当成不可走/危险区域。

相关参数还有这几个：

```xml
<param name="optimization/dist0" value="0.7"/>
<param name="optimization/ld_dist" value="10.0"/>
<param name="manager/clearance_threshold" value="0.2"/>
```

区别是：

- `sdf_map/obstacles_inflation`：地图层膨胀，最关键，影响 A* 是否能走。
- `optimization/dist0`：B 样条优化希望离障碍多远。
- `optimization/ld_dist`：B 样条避障权重。
- `manager/clearance_threshold`：规划管理里判断安全距离的阈值。

四足狗容易撞墙/墙角，优先调：

```xml
<param name="sdf_map/obstacles_inflation" value="0.35"/>
```

如果还擦墙，再试：

```xml
0.4
```

但别太大。膨胀太大时，门口和走廊会被“堵死”，A* 可能直接 `No path to next viewpoint`。


---

## 34. [A*/B 样条] A*算法中有没有用到代价地图

**问题**

A*算法中有没有用到代价地图

**回答**

我直接看一下当前 `astar2.cpp` 的搜索条件，确认它是二值占据检查还是有连续代价参与。

当前 `A*` 里**没有用连续代价地图**，它用的是**二值占据/膨胀占据地图**。

也就是说，A* 只判断：

```text
这个格子能不能走
```

不是判断：

```text
这个格子代价高不高
```

源码在 [astar2.cpp](/home/uestc/tzb/uestc-challenge2026/src/fuel/fuel_planner/path_searching/src/astar2.cpp:106)：

```cpp
if (!edt_env_->sdf_map_->isInBox(nbr_pos)) continue;

if (edt_env_->sdf_map_->getInflateOccupancy(nbr_pos) == 1 ||
    edt_env_->sdf_map_->getOccupancy(nbr_pos) == SDFMap::UNKNOWN)
  continue;
```

含义是：

- 不在探索 box 内：不能走
- 膨胀障碍：不能走
- 未知区域：不能走
- 其他 free 区域：可以走

然后 A* 的代价只用了几何距离：

```cpp
double tmp_g_score = step.norm() + cur_node->g_score;
neighbor->f_score = tmp_g_score + lambda_heu_ * getDiagHeu(nbr_pos, end);
```

所以它没有像 `move_base costmap` 那样：

```text
离障碍越近，代价越高
离障碍越远，代价越低
```

FUEL 里面虽然有 ESDF 距离场：

```cpp
getDistance()
evaluateEDTWithGrad()
```

但这个主要给 **B 样条优化避障** 用，不是当前这个 `astar2.cpp` 的 A* 代价。

简单说：

- A*：二值地图，负责找一条可通路。
- `sdf_map/obstacles_inflation`：决定哪些区域被判为不可走。
- B 样条优化：用 ESDF 距离场做连续避障和平滑。

所以如果你想让 A* “更偏向走廊中间”，现在单靠 A* 参数做不到。要么增大 `obstacles_inflation`，要么改 A* 代价函数，把 `getDistance()` 加进去，让靠墙格子代价更高。


---

## 35. [frontier/viewpoint] 当viewpoint在四足狗的后方时，四足狗转动的时候没有按最小转动来转，而是转一大圈，分析一下原因

**问题**

当viewpoint在四足狗的后方时，四足狗转动的时候没有按最小转动来转，而是转一大圈，分析一下原因

**回答**

我看一下 `/position_cmd -> /cmd_vel` 适配器里 yaw 误差怎么计算，以及 FUEL 的 yaw 输出有没有做 wrap。这个问题通常出在角度没有归一化到 `[-pi, pi]`，或者“跟随速度方向”和“跟随 FUEL yaw”之间切换。

适配器里 yaw error 本身用了 `wrap_pi`，所以单看 `/cmd_vel` 控制器不是“永远绕大圈”的典型 bug。更可疑的是 FUEL 生成 yaw 轨迹时没有先把目标 yaw 改成相对当前 yaw 的最近等价角，导致 `/position_cmd.yaw` 连续轨迹本身走了长路径。

这个现象大概率不是 `local_map_margin`，而是 **yaw 目标来源和 yaw 前馈叠加** 导致的。

你现在 `/position_cmd -> /cmd_vel` 适配器里，yaw 控制逻辑在：

[src/fuel/fuel_dog_adapter/scripts/fuel_poscmd_to_cmdvel.py](/home/uestc/tzb/uestc-challenge2026/src/fuel/fuel_dog_adapter/scripts/fuel_poscmd_to_cmdvel.py:150)

关键代码是：

```python
yaw_ff = self.yaw_ff_gain * cmd.yaw_dot

if self.align_yaw_to_velocity and world_speed > self.min_heading_speed:
    target_yaw = math.atan2(world_ay, world_ax)
else:
    target_yaw = cmd.yaw

yaw_error = wrap_pi(target_yaw - yaw)
wz = yaw_ff + self.yaw_kp * yaw_error
```

`yaw_error` 本身做了 `wrap_pi`，理论上会选最小角度。但是这里还有一个问题：  
**`cmd.yaw_dot` 是 FUEL 原始 yaw 轨迹的角速度，它可能不是四足狗当前最小转角方向。**

所以即使：

```python
yaw_error = wrap_pi(target_yaw - yaw)
```

是正确的，后面：

```python
wz = yaw_ff + yaw_kp * yaw_error
```

里面的 `yaw_ff` 仍然可能把狗往 FUEL yaw 轨迹的方向带，造成“明明可以转 90 度，却绕一大圈”。

尤其当 viewpoint 在后方时，FUEL 原版是无人机逻辑，它会规划一个连续 yaw B 样条。这个 yaw 轨迹在源码里：

[src/fuel/fuel_planner/plan_manage/src/planner_manager.cpp](/home/uestc/tzb/uestc-challenge2026/src/fuel/fuel_planner/plan_manage/src/planner_manager.cpp:775)

```cpp
planYawExplore(...)
```

里面会根据轨迹前方方向插入一串 yaw waypoint：

```cpp
waypt(0) = atan2(pd(1), pd(0));
calcNextYaw(last_yaw, waypt(0));
```

这个逻辑是“沿着规划轨迹朝前看”，不是“四足狗原地按最小角度转到目标”。  
所以如果路径绕了一个 U 型，或者 FUEL yaw 轨迹连续跨过 `pi/-pi`，它可能生成一个连续的大转角 yaw 轨迹。

还有一个原因是你现在：

```xml
<param name="align_yaw_to_velocity" value="true"/>
<param name="disable_lateral" value="true"/>
<param name="forward_only" value="false"/>
```

这代表适配器优先让狗朝速度方向转。viewpoint 在后方时，如果位置控制输出的 `world_ax/world_ay` 方向不是稳定地指向后方，而是随着轨迹点移动绕了一圈，狗也会跟着绕。

**怎么确认是哪一个原因**

运行时看这几个：

```bash
rostopic echo /position_cmd/yaw
rostopic echo /position_cmd/yaw_dot
rostopic echo /cmd_vel/angular/z
rostopic echo /Odometry_gazebo/pose/pose/orientation
```

如果 `/position_cmd/yaw_dot` 在后方目标时持续给一个较大的固定方向，而 `/cmd_vel/angular.z` 也跟着这个方向转，说明是 `yaw_ff` 把它带偏。

最简单验证：把适配器里的这个参数先降到 0：

```xml
<param name="yaw_ff_gain" value="0.0"/>
```

现在你可能是：

```xml
<param name="yaw_ff_gain" value="0.4"/>
```

改成 0 后，`cmd_vel.angular.z` 就主要由：

```python
wrap_pi(target_yaw - yaw)
```

决定，会更倾向最小转角。

**优先建议**

先试这个：

```xml
<param name="yaw_ff_gain" value="0.0"/>
```

如果转向还是绕大圈，再看 `align_yaw_to_velocity` 造成的目标 yaw 是否绕了。可以临时改：

```xml
<param name="align_yaw_to_velocity" value="false"/>
```

让它直接跟 FUEL 的 `cmd.yaw`，对比现象。

但对四足狗，我更建议后面做一个专门模式：  
**不用 FUEL 的 yaw_dot 前馈，只用当前机器人 yaw 到目标方向的最小角度误差控制 `angular.z`。**  
这样 viewpoint 在后方时，它会按最短方向转过去，而不是继承无人机 yaw B 样条的绕圈逻辑。


---

## 36. [frontier/viewpoint] 是否有优先选择探索哪些viewpoints的参数可以调

**问题**

是否有优先选择探索哪些viewpoints的参数可以调

**回答**

我看一下 frontier 排序、TSP 和 local refine 的参数链路，区分“能直接调的偏好”和“需要改代码的选择策略”。

有，但 FUEL 原版不是用一个简单的“优先探索前方/左边/最近”的参数，而是通过 **frontier 生成、viewpoint 采样、TSP 全局排序、局部 refine** 这几层共同决定。

当前可调的主要参数在 [algorithm.xml](/home/uestc/tzb/uestc-challenge2026/src/fuel/fuel_planner/exploration_manager/launch/algorithm.xml:104)。

**1. 全局优先顺序：TSP 代价**

FUEL 会把每个 frontier 的最佳 viewpoint 拿出来，构造代价矩阵，然后用 LKH 解 TSP：

```cpp
findGlobalTour(pos, vel, yaw, indices);
```

位置：

[src/fuel/fuel_planner/exploration_manager/src/fast_exploration_manager.cpp](/home/uestc/tzb/uestc-challenge2026/src/fuel/fuel_planner/exploration_manager/src/fast_exploration_manager.cpp:127)

这个代价来自：

```cpp
ViewNode::computeCost(...)
```

主要考虑：

- 从当前位置到 viewpoint 的路径距离
- 当前速度方向和目标方向夹角
- yaw 转动代价

相关参数：

```xml
<param name="exploration/vm" value="$(eval 1.0 * arg('max_vel'))"/>
<param name="exploration/yd" value="$(eval 60 * 3.1415926 / 180.0)"/>
<param name="exploration/w_dir" value="1.5"/>
```

其中：

- `exploration/vm`：速度归一化，影响路径距离代价。
- `exploration/yd`：yaw 速度，越小，转向代价越大。
- `exploration/w_dir`：当前速度方向权重。

如果你希望**少回头、优先当前前进方向**，可以增大：

```xml
<param name="exploration/w_dir" value="2.5"/>
```

甚至试：

```xml
3.0
```

如果你希望**更愿意转头去近处目标**，就减小它，比如：

```xml
1.0
```

**2. 局部 refine 优先哪些 viewpoint**

全局 TSP 选出 frontier 顺序后，FUEL 会对前几个 frontier 的多个 viewpoint 做局部精修：

```xml
<param name="exploration/refined_num" value="7"/>
<param name="exploration/refined_radius" value="5.0"/>
<param name="exploration/top_view_num" value="15"/>
<param name="exploration/max_decay" value="0.8"/>
```

含义：

- `refined_num`：拿全局路径前几个 frontier 做局部优化。
  - 值大：考虑更多后续 frontier，行为更全局。
  - 值小：更短视，更关注当前最近几个。
- `refined_radius`：超过这个距离后，局部 refine 不再继续纳入更多 frontier。
  - 值小：更偏局部。
  - 值大：更考虑远处。
- `top_view_num`：每个 frontier 保留多少候选 viewpoint。
  - 值大：选择空间更大，但可能选到绕路/后方 viewpoint。
  - 值小：更稳定，但可能错过好视角。
- `max_decay`：保留候选 viewpoint 的可见性阈值。
  - 当前 `0.8` 表示只保留接近最佳可见性的 viewpoint。
  - 降低到 `0.6` 会允许更多候选视角。

如果你希望**更贪心、优先当前近处目标**，可以试：

```xml
<param name="exploration/refined_num" value="3"/>
<param name="exploration/refined_radius" value="3.0"/>
<param name="exploration/top_view_num" value="8"/>
```

如果你希望**少走回头路、更全局规划**，可以保持或增大：

```xml
refined_num = 7
refined_radius = 5.0~7.0
top_view_num = 15
```

**3. viewpoint 本身怎么生成**

这些在 `fuel_dog_visual_exploration.launch` 里作为 arg 配置，然后传给 `algorithm.xml`：

```xml
<arg name="frontier_candidate_rmin" default="0.8"/>
<arg name="frontier_candidate_rmax" default="1.8"/>
<arg name="frontier_min_candidate_dist" default="0.45"/>
<arg name="frontier_min_visib_num" default="3"/>
```

含义：

- `candidate_rmin/rmax`：viewpoint 围绕 frontier 多远采样。
- `min_candidate_dist`：离当前机器人太近的 viewpoint 会跳过。
- `min_visib_num`：至少能看到多少 frontier cell 才算有效。

如果你想减少“后方很远的 viewpoint 被选中”，可以缩小：

```xml
frontier_candidate_rmax = 1.2~1.5
```

如果窄走廊里经常没有 viewpoint，可以降低：

```xml
frontier_candidate_rmin = 0.4~0.6
frontier_min_visib_num = 1~2
```

**4. 没有现成参数能直接写“优先前方房间”**

目前没有类似这样的参数：

```xml
prefer_forward_viewpoint
prefer_left
prefer_room_inside
ignore_rear_frontier
```

如果你要明确“优先探索前方/不回头/先进房间”，需要改代价函数。位置主要在：

[src/fuel/fuel_planner/active_perception/src/graph_node.cpp](/home/uestc/tzb/uestc-challenge2026/src/fuel/fuel_planner/active_perception/src/graph_node.cpp:63)

```cpp
ViewNode::computeCost(...)
```

可以在这里给后方 viewpoint 加惩罚，或者给当前朝向前方的 viewpoint 降代价。

**推荐你先试的参数组合**

如果你的目标是“四足狗不要老选后方，优先沿当前走廊往前探索”，先试：

```xml
<param name="exploration/w_dir" value="3.0"/>
<param name="exploration/refined_num" value="3"/>
<param name="exploration/refined_radius" value="3.0"/>
<param name="exploration/top_view_num" value="8"/>
```

这个组合会让它更偏向当前运动方向和局部目标。  
如果还是会明显选后方，那就不是参数能完全解决，需要在 `ViewNode::computeCost()` 里加“后方惩罚”。


---

## 37. [重规划 / 多楼层] 探索结束后回到起点,，实现方案 2：在 FUEL FSM 的 FINISH 状态里加 RETURN_HOME

**问题**

探索结束后回到起点,，实现方案 2：在 FUEL FSM 的 FINISH 状态里加 RETURN_HOME

**回答**

我来直接改 FUEL 状态机，加一个 `RETURN_HOME` 流程：启动时记录 home，探索无 frontier 后先规划回 home，执行完成再进入 FINISH。先读一下 FSM 和 manager 的现有接口，尽量复用已有 A*/B 样条发布链路。

FSM 里已经有发布 bspline 的封装，我会复用它；缺的只是“规划一个指定 home 目标”的 manager 接口。现在继续看 `FastPlannerManager` 里哪些函数是 public，避免重复造轨迹生成逻辑。

我会在 `FastExplorationManager` 新增 `planReturnHome()`，里面用当前 2D A* 从当前位置搜到 home，再用现有 `planExploreTraj()` 生成 B 样条；FSM 在 `NO_FRONTIER` 时切到 `RETURN_HOME`，回到 home 后才 `FINISH`。这样不会动四足狗适配器。

准备编辑 4 个文件：FSM 头文件/数据结构、FSM 实现、探索 manager 头文件/实现。加的参数会放到 `algorithm.xml`：`fsm/return_home` 和 `fsm/return_home_tolerance`，默认启用。

接下来加返回起点的实际规划函数：它不会重新找 frontier，只搜当前位置到 home 的 A*，缩短路径，再走原来的 B 样条优化和 yaw 规划。

FSM 逻辑已经接上了；我再把 launch 参数落进去，并跑一次编译，看看有没有 C++ 语法或状态数组问题。

`roslaunch --nodes` 失败是因为这个 shell 没 source 工作空间，不是 launch 内容本身。现在我用工作空间环境跑编译；如果编译指出状态机语法问题，我马上修。

编译正在过新增的 `exploration_node`，目前已经进入新增文件编译阶段。等最终链接结果出来，我再决定是否需要补修。

编译通过了，只有原项目已有的 signed/unused 警告。现在我再用 source 后检查 launch 解析，确保参数入口没有问题。

已实现“探索结束后回到起点”的方案 2，改的是 FUEL FSM 内部，不动四足狗适配器。

改动点：

- [fast_exploration_fsm.h](/home/uestc/tzb/uestc-challenge2026/src/fuel/fuel_planner/exploration_manager/include/exploration_manager/fast_exploration_fsm.h)
  - 新增状态：`RETURN_HOME`、`PUB_RETURN`、`EXEC_RETURN`。

- [expl_data.h](/home/uestc/tzb/uestc-challenge2026/src/fuel/fuel_planner/exploration_manager/include/exploration_manager/expl_data.h)
  - 记录 home：`home_pos_`、`home_yaw_`、`have_home_`。
  - 新增参数：`return_home_`、`return_home_tolerance_`。

- [fast_exploration_fsm.cpp](/home/uestc/tzb/uestc-challenge2026/src/fuel/fuel_planner/exploration_manager/src/fast_exploration_fsm.cpp)
  - 在触发探索时记录当前 odom 作为起点。
  - 当探索返回 `NO_FRONTIER` 时，不直接 `FINISH`，而是进入 `RETURN_HOME`。
  - 回到起点附近后再进入 `FINISH`。
  - 如果返回途中检测到碰撞，会重新规划回起点。

- [fast_exploration_manager.cpp](/home/uestc/tzb/uestc-challenge2026/src/fuel/fuel_planner/exploration_manager/src/fast_exploration_manager.cpp)
  - 新增 `planReturnHome()`。
  - 使用当前 A* 从当前位置规划到起点，再用现有 B 样条生成轨迹。

- [algorithm.xml](/home/uestc/tzb/uestc-challenge2026/src/fuel/fuel_planner/exploration_manager/launch/algorithm.xml)
  - 新增参数：
    ```xml
    <param name="fsm/return_home" value="true"/>
    <param name="fsm/return_home_tolerance" value="0.5"/>
    ```

注意：这里的“起点”是你发布 `/waypoint_generator/waypoints` 触发 FUEL 那一刻的四足狗 odom 位置，不是 Gazebo 出生点。

已验证：

```bash
catkin_make --pkg exploration_manager
```

编译通过。  
也检查了 launch 解析，`fuel_dog_visual_exploration.launch` 正常识别节点。

如果不想自动回起点，把 `algorithm.xml` 里这个改成 false：

```xml
<param name="fsm/return_home" value="false"/>
```


---

## 38. [探索范围] <arg name="map_size_x" default="6.0"/> <arg name="map_size_y" default="9.0"/> <arg name="map_size_z"

**问题**

<arg name="map_size_x" default="6.0"/>
  <arg name="map_size_y" default="9.0"/>
  <arg name="map_size_z" default="1.6"/>

  <arg name="box_min_x" default="-3.0"/>
  <arg name="box_min_y" default="1.0"/>
  <arg name="box_min_z" default="0.05"/>
  <arg name="box_max_x" default="3.0"/>
  <arg name="box_max_y" default="10.0"/>我们就改了这几个参数

**回答**

这几个参数会导致 `exploration_node` 段错误，原因很明确：**你的 box 超出了 FUEL 实际地图范围**。

FUEL 地图原点不是 `box_min`，而是源码里固定按地图尺寸居中：

```cpp
map_origin = (-map_size_x/2, -map_size_y/2, ground_height)
```

你现在设置：

```xml
map_size_x = 6.0   -> x 范围约 [-3, 3]
map_size_y = 9.0   -> y 范围约 [-4.5, 4.5]
map_size_z = 1.6   -> z 范围约 [-1.0, 0.6]  因为 ground_height=-1.0
```

但是你的 box 是：

```xml
box_min_x = -3.0
box_max_x = 3.0

box_min_y = 1.0
box_max_y = 10.0
```

问题在这里：

```text
地图 y 最大只有 4.5
但 box_max_y = 10.0
```

如果 `box_max_z` 还是之前的 `1.5`，也有问题：

```text
地图 z 最大只有 0.6
但 box_max_z = 1.5
```

FUEL 里很多地方会按 `box_min/max` 遍历栅格，如果 box 超过地图数组范围，就可能访问越界，直接 `exit code -11`。

你要么把 box 改回地图范围内：

```xml
<arg name="map_size_x" default="6.0"/>
<arg name="map_size_y" default="9.0"/>
<arg name="map_size_z" default="2.6"/>

<arg name="box_min_x" default="-2.9"/>
<arg name="box_max_x" default="2.9"/>
<arg name="box_min_y" default="1.0"/>
<arg name="box_max_y" default="4.4"/>
<arg name="box_min_z" default="0.05"/>
<arg name="box_max_z" default="1.5"/>
```

要么保持你想探索 `y=1~10`，就必须把 `map_size_y` 放大到至少 `20` 以上：

```xml
<arg name="map_size_x" default="6.0"/>
<arg name="map_size_y" default="22.0"/>
<arg name="map_size_z" default="2.6"/>

<arg name="box_min_x" default="-3.0"/>
<arg name="box_max_x" default="3.0"/>
<arg name="box_min_y" default="1.0"/>
<arg name="box_max_y" default="10.0"/>
<arg name="box_min_z" default="0.05"/>
<arg name="box_max_z" default="1.5"/>
```

关键规则：

```text
box_min_x/y/z 和 box_max_x/y/z 必须在 map_size 形成的地图边界内
```

你当前这组参数里 `box_max_y=10.0` 超出 `map_size_y=9.0` 对应的地图上界 `4.5`，这就是最可疑的崩溃原因。


---

## 39. [多楼层] 我还是需要回到指定点，回到指定点后不要转圈，然后我也将return_home改成了true，但是四足狗探索结束没有回到指定点就停下来了

**问题**

我还是需要回到指定点，回到指定点后不要转圈，然后我也将return_home改成了true，但是四足狗探索结束没有回到指定点就停下来了

**回答**

我明白了：你要的是“探索结束后先回指定点，到点后停住不追 yaw”，而不是直接 FINISH 停。现在我去看 FSM 的回家判断和指定点参数传递，重点查为什么 `return_home:=true` 没有进入 RETURN_HOME。

代码里有一个很可疑的点：现在是否回家是用“当前位置到回家点的 XY 距离 > tolerance”来决定的。你当前 `return_home_tolerance` 是 `0.05`，理论上应该会回，但如果回家点参数没真正传进去、或者启动的不是这个 launch，就会直接 FINISH。我要加更明确的日志，并把“回家完成后追 yaw”的行为关掉。

还有一个更深的原因：回家轨迹的 yaw 现在被规划到 `return_home_yaw`，回到点后如果 `traj_server` 继续吐最后一帧，就会继续转到指定 yaw。解决方式是回家完成时同样发 `/planning/finish`，并且放宽“到点完成”的判断，避免还没走到就因为轨迹时间结束停掉。

我定位到一个实际 bug：`EXEC_RETURN` 里只要“回家轨迹时间快结束”，就会直接进入 `FINISH`，哪怕四足狗实际还没走到指定点。无人机能跟得上时问题不明显，四足狗跟踪慢就会“还没回到点就停”。我现在改成：只有距离到点小于阈值才 FINISH；轨迹时间结束但没到点就重新规划回家。

为了四足狗更符合实际，我还会把默认回家到点阈值从 `0.05m` 放宽到 `0.25m`。5 厘米对仿真四足狗的 `/cmd_vel` 跟踪太苛刻，容易在目标点附近反复重规划；25 厘米更像“到达指定点附近后停住”。

现在逻辑变成：探索结束，如果离指定点超过阈值就进入 `RETURN_HOME`；回家轨迹跑完但没到点，会继续重规划回家；真正到点后发布 `/planning/finish` 停止 `/position_cmd`，所以不会再追最后的 yaw 转圈。接下来我编译验证一下。

launch 解析已经通过，编译还在跑最后的 `exploration_node` 目标。我等它结束，避免留下半截状态。

已经改好了：保留“回到指定点”，并且回到指定点后不转圈。

这次主要修了两个点：

1. [fast_exploration_fsm.cpp](/home/uestc/tzb/uestc-challenge2026/src/fuel/fuel_planner/exploration_manager/src/fast_exploration_fsm.cpp)
   - 探索结束时会打印：
     ```text
     No frontier. return_home: ..., have_home: ..., dist_to_home: ..., tolerance: ...
     ```
     方便确认是否真的进入回家逻辑。
   - 原来 `EXEC_RETURN` 里只要“回家轨迹时间快结束”就会直接 `FINISH`，导致四足狗还没走到指定点就停。
   - 现在改成：
     - 距离指定点小于 `return_home_tolerance` 才 `FINISH`
     - 如果轨迹结束但还没到点，就重新规划回家
     - 到点后发布 `/planning/finish`，`traj_server` 停止 `/position_cmd`，四足狗不会继续追最后的 yaw 转圈

2. [fuel_dog_visual_exploration.launch](/home/uestc/tzb/uestc-challenge2026/src/fuel/fuel_dog_adapter/launch/fuel_dog_visual_exploration.launch)
   - `return_home` 保持 `true`
   - `return_home_tolerance` 默认从 `0.05` 改成 `0.25`
   - 5cm 对四足狗太苛刻，容易到点附近还一直修正；25cm 更适合“到指定点附近停住”

已验证：

```bash
source devel/setup.bash
catkin_make --pkg exploration_manager plan_manage
roslaunch fuel_dog_adapter fuel_dog_visual_exploration.launch --nodes
```

编译和 launch 解析都通过。

现在你需要调指定返回点就在这里改：

```xml
<arg name="return_home_x" default="0.8"/>
<arg name="return_home_y" default="2.62"/>
<arg name="return_home_z" default="0.31"/>
<arg name="return_home_yaw" default="0.0"/>
```

其中真正决定“回到哪里”的主要是 `return_home_x/y`，`return_home_yaw` 现在只参与回家轨迹规划；到点后会停止发布命令，所以不会为了对齐这个 yaw 一直转圈。


---

## 40. [探索范围] 四足狗在探索的过程中，探索的宽度需要变化，是否可以实现box_min_x box_max_x map_size_x的实时修改，先不改代码

**问题**

四足狗在探索的过程中，探索的宽度需要变化，是否可以实现box_min_x  box_max_x  map_size_x的实时修改，先不改代码

**回答**

可以实现，但要分清楚这三个量的性质：

`box_min_x / box_max_x` 可以相对容易做成“运行时动态修改”。

`map_size_x` 不建议实时改，甚至基本不适合运行时改。

原因是：

```xml
map_size_x
```

是 FUEL/SDF map 初始化体素地图时用的总地图尺寸。它通常在 `SDFMap::initMap()` 阶段分配数组、计算地图边界、体素索引范围。运行中改它，相当于要重建整张地图，包括 occupancy、ESDF、frontier、A* 搜索空间等，风险比较大，容易导致索引越界、已有地图丢失，甚至节点崩溃。

而：

```xml
box_min_x
box_max_x
```

更像是“探索边界”或“允许更新/规划的边界”。如果源码里只是用它们判断某个点是否在允许区域内，那可以改成运行时参数、dynamic_reconfigure，或者订阅一个边界话题来动态更新。

推荐方案是：

```text
map_size_x 设置成最大可能范围
box_min_x / box_max_x 运行中动态调整
```

比如一开始：

```xml
<arg name="map_size_x" default="20.0"/>
<arg name="box_min_x" default="-2.0"/>
<arg name="box_max_x" default="2.0"/>
```

探索过程中需要变宽时，只改：

```text
box_min_x: -4.0
box_max_x:  4.0
```

不要动 `map_size_x`。

实现上有三种方案：

1. 最简单：用 ROS param + 定时读取  
   在 FUEL 的地图/探索模块里定时读取：
   ```cpp
   nh.getParam("sdf_map/box_min_x", ...)
   nh.getParam("sdf_map/box_max_x", ...)
   ```
   然后用 `rosparam set` 实时改。

2. 更正规：dynamic_reconfigure  
   可以在 rqt 里拖动边界，适合调参。

3. 更灵活：订阅一个边界话题  
   比如 `/fuel/exploration_box`，消息里带 `min_x/max_x/min_y/max_y/min_z/max_z`，外部脚本根据四足狗位置或任务阶段实时发布。

我建议你用方案 1 或 3。

你现在这个需求是“探索宽度随过程变化”，最好不要改 `map_size_x`，而是：

```text
map_size_x = 整个厂房可能覆盖的最大宽度
box_min_x / box_max_x = 当前允许探索的动态窗口
```

这样地图内存一开始就够，运行中只是收窄或放宽探索区域，稳定很多。


---

## 41. [探索范围] 用方案1实现，我想实现的效果是：当四足狗做到y=9m的时候（通过/Odometry_gazebo知道），将box_min_x和box_max_x分别设置为-25和25

**问题**

用方案1实现，我想实现的效果是：当四足狗做到y=9m的时候（通过/Odometry_gazebo知道），将box_min_x和box_max_x分别设置为-25和25

**回答**

我来按“方案1：运行时读 ROS param”实现。目标是：FUEL 启动时还是用当前窄 box，运行中检测 `/Odometry_gazebo` 的 y 到 9m 后，把 `sdf_map/box_min_x` 和 `sdf_map/box_max_x` 动态改成 `-25/25`，同时 FUEL 地图模块能重新读取这些参数。

我看到当前 `box_min/max` 只在 `SDFMap::initMap()` 里读取一次，所以光 `rosparam set` 还不够，FUEL 内部不会自动更新。我会给 `SDFMap` 加一个轻量的运行时参数刷新函数，再加一个小 Python 节点监测里程计 y 到 9m 时写参数。

准备编辑三处：`SDFMap` 支持运行时刷新 box 参数；新增一个小脚本监听 `/Odometry_gazebo` 并在 y 达标后设置参数；launch 里把这个脚本作为可开关节点启动。

实现已经写入了。注意：这个动态扩大只改探索 box，不改 `map_size_x`，所以你的 `map_size_x` 必须一开始就足够大；你当前是 `50.0`，正好能覆盖 `[-25, 25]`。我现在补脚本可执行权限并编译检查。

`roslaunch --nodes` 已经能看到新增的 `/dynamic_exploration_box` 节点，说明 launch 接入没问题。编译还在跑整个依赖链，我等它完成。

编译到 `plan_env` 了，核心改动正在编译。中间这些 VTK/Gazebo 警告是环境里的老警告，不影响这次功能。

`libplan_env.so` 已经编过了，说明动态刷新 box 的核心 C++ 没问题。现在是下游包因为依赖它在重编，继续等最后结果。

已按方案 1 实现。

现在效果是：

```text
四足狗 /Odometry_gazebo 里 pose.y >= 9.0
-> 自动设置：
   /exploration_node/sdf_map/box_min_x = -25.0
   /exploration_node/sdf_map/box_max_x = 25.0
-> FUEL 的 SDFMap 每 0.5s 读取一次参数
-> 探索/建图 box 的 x 宽度实时扩大
```

改了这些文件：

- [sdf_map.h](/home/uestc/tzb/uestc-challenge2026/src/fuel/fuel_planner/plan_env/include/plan_env/sdf_map.h)
- [sdf_map.cpp](/home/uestc/tzb/uestc-challenge2026/src/fuel/fuel_planner/plan_env/src/sdf_map.cpp)

让 FUEL 的 `SDFMap` 支持运行时刷新：

```xml
sdf_map/box_min_x
sdf_map/box_max_x
sdf_map/box_min_y
sdf_map/box_max_y
sdf_map/box_min_z
sdf_map/box_max_z
```

- [dynamic_exploration_box.py](/home/uestc/tzb/uestc-challenge2026/src/fuel/fuel_dog_adapter/scripts/dynamic_exploration_box.py)

新增节点，监听 `/Odometry_gazebo`，当 `y >= 9.0` 后只触发一次扩大宽度。

- [fuel_dog_visual_exploration.launch](/home/uestc/tzb/uestc-challenge2026/src/fuel/fuel_dog_adapter/launch/fuel_dog_visual_exploration.launch)

新增参数：

```xml
<arg name="enable_dynamic_box" default="true"/>
<arg name="dynamic_box_trigger_y" default="9.0"/>
<arg name="dynamic_box_min_x_after" default="-25.0"/>
<arg name="dynamic_box_max_x_after" default="25.0"/>
```

并启动：

```text
/dynamic_exploration_box
```

- [algorithm.xml](/home/uestc/tzb/uestc-challenge2026/src/fuel/fuel_planner/exploration_manager/launch/algorithm.xml)

新增：

```xml
<param name="sdf_map/enable_dynamic_box" value="$(arg enable_dynamic_box)" type="bool"/>
<param name="sdf_map/dynamic_box_rate" value="2.0" type="double"/>
```

已验证：

```bash
source devel/setup.bash
catkin_make --pkg plan_env exploration_manager fuel_dog_adapter
roslaunch fuel_dog_adapter fuel_dog_visual_exploration.launch --nodes
```

编译和 launch 解析通过，`--nodes` 里已经能看到：

```text
/dynamic_exploration_box
```

注意一点：`map_size_x` 仍然不能运行时改。你现在 launch 里 `map_size_x=50.0`，所以 `box_min_x=-25`、`box_max_x=25` 是合理的。如果以后把 `map_size_x` 改小，比如 `6.0`，那么动态 box 会被 FUEL 自动夹到地图范围内，不可能真的扩到 `[-25, 25]`。


---

## 42. [探索范围] 我们想要的效果是尽量不去探索y<9m的左右区域，但是当四足狗回来的时候（探索范围已经变大了），还是会去探索y<9m的左右区域，如何解决这个问题

**问题**

我们想要的效果是尽量不去探索y<9m的左右区域，但是当四足狗回来的时候（探索范围已经变大了），还是会去探索y<9m的左右区域，如何解决这个问题

**回答**

问题根源是：你现在只在 `y>=9m` 后把 **整个全局 box 的 x 宽度** 从窄变宽了：

```text
box_min_x: -25
box_max_x:  25
```

但这个 box 是全局矩形，不带“分段规则”。一旦变宽，FUEL 会认为：

```text
所有 y 范围内，x 都可以到 [-25, 25]
```

所以四足狗回来经过 `y<9m` 时，FUEL 也会重新看到/生成 `y<9m` 左右区域的 frontier 和 viewpoint。这就是你现在看到的现象。

你想要的其实不是简单矩形 box，而是一个 **分段探索区域**：

```text
y < 9m:   x 只允许窄范围，比如 [-2, 2]
y >= 9m:  x 允许宽范围，比如 [-25, 25]
```

也就是 T 型 / 走廊后展开的区域。

当前 `box_min_x/box_max_x` 做不到这个，因为它只能表达一个矩形：

```text
x_min <= x <= x_max
y_min <= y <= y_max
```

不能表达：

```text
不同 y 区间使用不同 x 范围
```

解决方案有三个层级。

**方案 A：动态收窄回去**

当四足狗 `y >= 9m` 时扩宽：

```text
x = [-25, 25]
```

当四足狗回到 `y < 9m` 时再收窄：

```text
x = [-2, 2]
```

优点是改动最小，沿用现在的 `dynamic_exploration_box.py` 就能做。

缺点是：如果四足狗在 `y<9m` 回来时，FUEL 已经把左右区域标成 frontier，可能会有短暂残留；但下一轮 frontier 更新后会好很多。

适合快速验证。

**方案 B：在 frontier/viewpoint 生成阶段加分段区域过滤**

也就是写一个函数：

```cpp
bool isInDogExplorationRegion(pos) {
  if (pos.y() < 9.0)
    return pos.x() >= -2.0 && pos.x() <= 2.0;
  else
    return pos.x() >= -25.0 && pos.x() <= 25.0;
}
```

然后在 frontier 搜索、viewpoint 采样、规划目标选择时过滤掉不在区域内的点。

效果是：

```text
y<9m 的左右区域即使地图里存在，也不会成为 frontier/viewpoint
```

这是更符合你需求的方案。

**方案 C：用多边形探索边界**

用一个 polygon 表达允许探索区域，例如：

```text
(-2, 1)
( 2, 1)
( 2, 9)
(25, 9)
(25, 50)
(-25, 50)
(-25, 9)
(-2, 9)
```

然后所有 frontier/viewpoint/path goal 都检查是否在 polygon 内。

这是最通用的做法，但改动比方案 B 大。

我建议你用 **方案 B**。

因为你真正想限制的是“哪里可以被当成探索目标”，不是“地图在哪里存在”。地图可以大一点，但 frontier/viewpoint 不应该出现在 `y<9m` 的左右区域。

推荐实现位置：

```text
src/fuel/fuel_planner/active_perception/src/frontier_finder.cpp
```

重点过滤：

```text
frontier cluster
candidate viewpoint
```

以及最好同步过滤 A* 目标点，避免 FUEL 选到非法区域。

你现在这个临时动态 box 方案可以保留，但它只能控制矩形边界。要实现“前段窄、后段宽”，需要加分段规则。最稳的是：

```text
map_size_x = 50
box_min_x / box_max_x = [-25, 25]
然后用 dog exploration region 过滤：
  y < 9:  x in [-2, 2]
  y >= 9: x in [-25, 25]
```

这样四足狗回来的时候，即使探索范围已经变大，也不会再去 `y<9m` 的左右区域。


---

## 43. [多楼层] 四足狗当前已经可以探索完一楼并且回到电梯口，现在需要四足狗通过电梯上二楼并且探索二楼，这个过程涉及进出电梯，重新探索，给出实现方案

**问题**

四足狗当前已经可以探索完一楼并且回到电梯口，现在需要四足狗通过电梯上二楼并且探索二楼，这个过程涉及进出电梯，重新探索，给出实现方案

**回答**

我先看一下项目里电梯/门的接口文档和现有启动流程，这样方案能贴合你当前仿真环境，而不是泛泛讲“状态机”。重点会看电梯服务、楼层判断、坐标系/地图是否需要重置。

可以做，但建议不要让 FUEL “连续自然地从一楼探索到二楼”。你现在的 FUEL 接入是单层 2.5D 探索，最稳方案是做一个**任务级状态机**，把它拆成两个独立探索任务：

```text
一楼探索
-> 回到电梯口
-> 进电梯
-> 呼叫电梯到二楼
-> 出电梯
-> 重置/重启 FUEL
-> 二楼探索
-> 二楼结束
```

**核心思路**
FUEL 仍然只负责“当前楼层”的 frontier、建图、轨迹。电梯、切楼层、进出轿厢由一个外层 `mission_manager` 管。

不要让一楼地图和二楼地图混在一起。原因是：

- 你现在 FUEL 参数是 2.5D：`fixed_z`、`viewpoint_z`、`box_min_z/box_max_z` 都是按单层设置的。
- 电梯上行时机器人 z 会变，FUEL 的局部地图和 frontier 逻辑不适合跨楼层连续工作。
- 二楼和一楼在 XY 上可能重叠，如果不重置地图，FUEL 会把一楼已探索区域和二楼区域混淆。

**推荐状态机**
新增一个 `fuel_multifloor_mission.py` 或 C++ 节点，状态如下：

```text
EXPLORE_FLOOR_1
WAIT_FLOOR_1_FINISH
ENTER_ELEVATOR_PREPARE
ENTER_ELEVATOR
CALL_ELEVATOR_TO_FLOOR_2
EXIT_ELEVATOR_PREPARE
EXIT_ELEVATOR
RESET_FUEL_FOR_FLOOR_2
EXPLORE_FLOOR_2
WAIT_FLOOR_2_FINISH
DONE
```

**1. 一楼探索**
启动当前：

```bash
roslaunch fuel_dog_adapter fuel_dog_visual_exploration.launch
```

一楼探索结束后，你现在已经实现了：

```text
探索结束 -> return_home -> 回到指定点 -> yaw 对齐 -> /planning/finish
```

这个指定点就设置成一楼电梯口外的等待点，例如：

```xml
<arg name="return_home_x" default="电梯口外 x"/>
<arg name="return_home_y" default="电梯口外 y"/>
<arg name="return_home_yaw" default="朝向电梯门"/>
```

外层状态机监听：

```bash
/planning/finish
```

收到后进入电梯流程。

**2. 打开一楼电梯门**
调用：

```bash
rosservice call /call_elevator "{elevator_id: 'elevator_main', target_floor: 0, open_doors: false}"
rosservice call /set_door_state "{door_id: 'elevator_floor_0', open: true}"
```

注意文档里说电梯门动作可能是 60s，`rosservice call` 通常会等动作完成。

**3. 进电梯**
这里不要交给 FUEL。用外层控制器直接发布 `/cmd_vel`，或者用一个简单的点到点控制器。

推荐做两个电梯相关点：

```text
floor1_elevator_wait_pose：电梯门外
elevator_inside_pose：轿厢中心
```

进入电梯时，状态机根据 `/Odometry_gazebo` 控制四足狗走到轿厢中心：

```text
当前位置 -> elevator_inside_pose
```

控制方式可以很简单：

- yaw 对准目标点
- 小速度前进
- 到达阈值内停下

不建议用 FUEL 规划进电梯，因为轿厢、门、窄空间对 frontier 探索不友好。

**4. 关一楼门，上二楼**
进入轿厢中心后：

```bash
rosservice call /set_door_state "{door_id: 'elevator_floor_0', open: false}"
rosservice call /call_elevator "{elevator_id: 'elevator_main', target_floor: 1, open_doors: false}"
```

然后等待 `/Odometry_gazebo.pose.pose.position.z` 到二楼高度附近，或者等待服务返回后再延时几秒。

**5. 打开二楼门，出电梯**
调用：

```bash
rosservice call /set_door_state "{door_id: 'elevator_floor_1', open: true}"
```

然后用同样的点到点控制，让四足狗从轿厢中心走到二楼电梯口外：

```text
elevator_inside_pose -> floor2_elevator_exit_pose
```

这里也不要用 FUEL。

**6. 重启或重置 FUEL**
这是关键。

最稳是**重启 FUEL 节点**，而不是试图清空内部地图：

```text
关闭 exploration_node / traj_server / fuel_poscmd_to_cmdvel
重新 roslaunch fuel_dog_visual_exploration.launch
```

二楼启动时要换参数：

```text
init_x/init_y/init_z           = 二楼开始探索点
viewpoint_z/fixed_z            = 二楼机器人 base 高度附近
box_min_z/box_max_z            = 二楼所在 z 范围
return_home_x/y/z/yaw          = 二楼电梯口或最终集合点
```

如果仿真世界二楼高度比如是 `3.0m`，那二楼参数大概是：

```xml
<arg name="init_z" default="3.30"/>
<arg name="viewpoint_z" default="3.35"/>
<arg name="fixed_z" default="3.30"/>
<arg name="box_min_z" default="3.05"/>
<arg name="box_max_z" default="4.50"/>
```

具体值要用：

```bash
rostopic echo -n1 /Odometry_gazebo/pose/pose/position
```

在二楼实际站稳后读 z。

**7. 二楼重新触发探索**
FUEL 重启后，需要再次发布 trigger：

```bash
rostopic pub -1 /waypoint_generator/waypoints nav_msgs/Path "header:
  frame_id: 'world'
poses:
- header:
    frame_id: 'world'
  pose:
    position:
      x: 二楼起点x
      y: 二楼起点y
      z: 二楼探索高度
    orientation:
      w: 1.0"
```

然后进入二楼探索。

**建议新增的包/脚本**
建议新增：

```text
src/fuel/fuel_dog_adapter/scripts/fuel_multifloor_mission.py
```

它负责：

```text
监听 /planning/finish
调用 /set_door_state
调用 /call_elevator
发布 /cmd_vel 进出电梯
重启 FUEL 或提示外部 roslaunch 重启
发布二楼 trigger
```

**更稳的启动结构**
最好不要让一个 launch 里写死一楼/二楼所有参数，而是拆成：

```text
fuel_dog_floor1.launch
fuel_dog_floor2.launch
fuel_multifloor_mission.launch
```

其中：

```text
floor1.launch：一楼 FUEL 参数
floor2.launch：二楼 FUEL 参数
mission.launch：状态机 + 电梯控制
```

**最小可行版本**
第一版可以这样做：

1. 当前 FUEL 探索一楼，回到一楼电梯口。
2. 手写 mission 脚本接管 `/cmd_vel`，进电梯。
3. 调服务上二楼。
4. 出电梯。
5. kill 当前 FUEL。
6. 启动二楼 FUEL。
7. 发布二楼 trigger。
8. 二楼探索完成。

这个方案最符合你现在的系统：FUEL 保持单层探索，不强行改成多楼层；电梯作为任务切换动作处理。


---

## 44. [视觉深度建图 / 探索范围] sdf_map/box_min_z sdf_map/box_max_z sdf_map/map_size_z 之间如何搭配使用，和sdf_map/ground_height又有什么关系

**问题**

sdf_map/box_min_z   sdf_map/box_max_z  sdf_map/map_size_z  之间如何搭配使用，和sdf_map/ground_height又有什么关系

**回答**

这几个参数关系可以这样理解：

```text
ground_height + map_size_z  决定整张 SDF 地图的 z 总范围
box_min_z / box_max_z       决定允许探索/显示/更新的 z 子范围
```

也就是：

```text
地图总范围：
  z_min = sdf_map/ground_height
  z_max = sdf_map/ground_height + sdf_map/map_size_z

探索 box 范围：
  box_min_z <= z <= box_max_z
```

但有个关键限制：

```text
box_min_z / box_max_z 必须落在地图总范围内
```

否则会被裁剪，甚至在某些地方导致异常行为。

举例，一楼：

```xml
sdf_map/ground_height = -1.0
sdf_map/map_size_z    = 1.6
```

那么地图 z 范围是：

```text
[-1.0, 0.6]
```

如果四足狗 base 高度大约：

```text
z = 0.3
```

那就合理。

此时：

```xml
sdf_map/box_min_z = 0.05
sdf_map/box_max_z = 1.5
```

里面的 `box_max_z=1.5` 实际已经超过地图上界 `0.6`，不太合理。更合理是一楼用：

```xml
ground_height = -0.1
map_size_z = 1.8
box_min_z = 0.05
box_max_z = 1.5
```

地图范围：

```text
[-0.1, 1.7]
```

box 范围：

```text
[0.05, 1.5]
```

这样就匹配。

二楼假设四足狗 base z 是：

```text
z = 3.3
```

那你不能继续用：

```xml
ground_height = -1.0
map_size_z = 1.6
```

因为地图范围还是：

```text
[-1.0, 0.6]
```

二楼完全不在地图里。

二楼应该类似：

```xml
ground_height = 3.0
map_size_z = 1.8
box_min_z = 3.05
box_max_z = 4.5
fixed_z = 3.3
viewpoint_z = 3.35
```

地图范围：

```text
[3.0, 4.8]
```

box 范围：

```text
[3.05, 4.5]
```

这样 FUEL 才能在二楼建图、找 frontier。

**四个参数的职责**

`sdf_map/ground_height`

```text
地图 z 起点，也就是 SDFMap 的 origin z。
```

源码里是：

```cpp
mp_->map_origin_ = Eigen::Vector3d(-x_size / 2.0, -y_size / 2.0, mp_->ground_height_);
mp_->map_max_boundary_ = mp_->map_origin_ + mp_->map_size_;
```

所以它不是“地面高度语义参数”那么简单，它直接决定地图数组覆盖哪个 z 区间。

`sdf_map/map_size_z`

```text
地图 z 方向总高度。
```

配合 `ground_height` 决定：

```text
地图覆盖 [ground_height, ground_height + map_size_z]
```

`sdf_map/box_min_z`

```text
探索/建图允许区域的下边界。
```

通常应略低于机器人 base 或传感器所在平面，但不要低太多。

`sdf_map/box_max_z`

```text
探索/建图允许区域的上边界。
```

对四足狗 2.5D 探索，一般覆盖到门、墙、障碍高度即可，比如 `base_z + 1.2m` 左右。

**推荐搭配公式**

如果当前楼层四足狗 base 高度是：

```text
robot_z
```

推荐：

```text
ground_height = robot_z - 0.35
map_size_z    = 1.8
box_min_z     = robot_z - 0.25
box_max_z     = robot_z + 1.2
fixed_z       = robot_z
viewpoint_z   = robot_z + 0.05
```

例如：

```text
robot_z = 3.3
```

则：

```text
ground_height = 2.95
map_size_z    = 1.8
box_min_z     = 3.05
box_max_z     = 4.50
fixed_z       = 3.30
viewpoint_z   = 3.35
```

**一句话总结**

```text
ground_height / map_size_z 是地图外壳
box_min_z / box_max_z 是探索窗口
fixed_z / viewpoint_z 是四足狗轨迹和候选观察点高度
```

二楼直接 FINISH，通常就是“地图外壳还在一楼”，即 `ground_height` 没跟着楼层 z 更新。


---

## 45. [视觉深度建图] 修改sdf_map/ground_height

**问题**

修改sdf_map/ground_height

**回答**

我来把 `ground_height` 做成可传参，并在多楼层脚本重启 FUEL 时按当前 `/Odometry_gazebo` 的 z 自动设置。这样二楼启动的 SDF 地图外壳会跟着楼层高度走。

我先把这几个 launch 和 mission 脚本串起来看一下，确认 `ground_height` 现在是在哪里被固定住的，然后直接把它改成随楼层启动参数传入。

已经看到了：`algorithm.xml` 里 `sdf_map/ground_height` 仍然写死为 `-1.0`。这会让二楼重启 FUEL 时，地图 z 范围和当前楼层高度不一致；我现在把它改成从外层 launch/mission 传入，并用当前里程计 z 推算当前楼层的地面高度。

我会把 `ground_height` 定义成“当前楼层地面高度”，mission 重启 FUEL 时用 `当前 odom z - floor_z_box_low` 推出来。这样二楼启动时，SDF 地图的 z 原点会跟着二楼走，而不是还停在一楼附近。

`algorithm.xml` 已经不再写死 `-1.0` 了。接下来我把外层 `fuel_dog_visual_exploration.launch` 加上同名参数并传进去，这样手动 roslaunch 和 mission 脚本都能控制它。

现在改 mission 脚本：每次重新启动 FUEL 时根据当前 `/Odometry_gazebo` 的 z 计算楼层地面高度，并把 `ground_height:=...` 放进 roslaunch 参数。

脚本这边已经会传 `ground_height` 了。我再把 `floor_z_box_low/high` 和 `map_size_z` 暴露到 multi-floor launch，后面你要调二楼地图高度范围时不用进 Python 文件。

改动完成了，下面我跑一下 Python 语法检查和 roslaunch 解析，确认 launch 参数没有漏传或拼错。

检查通过：Python 能编译，两个 launch 都能正常解析节点。最后我看一下 diff，确保只改了和 `ground_height` 相关的地方。

diff 里能看到这个工作区之前已经有一些未提交的参数改动（比如 `map_size_z`、`box_max_z`、yaw tolerance），我没有动这些。我的新增点是 `ground_height` 的参数传递；我再确认一下 mission 脚本是不是已经包含这部分。

语法检查生成了一个 `__pycache__`，这是临时产物，我把它清掉，避免污染工作区。

已修改 `sdf_map/ground_height` 的传递逻辑：

- [algorithm.xml](/home/uestc/tzb/uestc-challenge2026/src/fuel/fuel_planner/exploration_manager/launch/algorithm.xml)  
  新增 `ground_height` 参数，并把原来写死的：
  ```xml
  <param name="sdf_map/ground_height" value="-1.0"/>
  ```
  改成从 launch 参数传入。

- [fuel_dog_visual_exploration.launch](/home/uestc/tzb/uestc-challenge2026/src/fuel/fuel_dog_adapter/launch/fuel_dog_visual_exploration.launch)  
  新增：
  ```xml
  <arg name="ground_height" default="0.0"/>
  ```
  并传给 `algorithm.xml`。

- [fuel_multifloor_mission.py](/home/uestc/tzb/uestc-challenge2026/src/fuel/fuel_dog_adapter/scripts/fuel_multifloor_mission.py)  
  每次重启 FUEL 时，用当前 `/Odometry_gazebo` 的高度计算：
  ```python
  ground_height = z - self.floor_z_box_low
  box_min_z = ground_height
  ```
  然后通过 roslaunch 参数传入：
  ```bash
  ground_height:=...
  ```

- [fuel_multifloor_mission.launch](/home/uestc/tzb/uestc-challenge2026/src/fuel/fuel_dog_adapter/launch/fuel_multifloor_mission.launch)  
  暴露了：
  ```xml
  floor_z_box_low
  floor_z_box_high
  map_size_z
  ```

验证已通过：

```bash
python3 -m py_compile src/fuel/fuel_dog_adapter/scripts/fuel_multifloor_mission.py
roslaunch fuel_dog_adapter fuel_dog_visual_exploration.launch --nodes
roslaunch fuel_dog_adapter fuel_multifloor_mission.launch --nodes
```

现在二楼重新启动 FUEL 时，SDF 地图的 z 范围会跟随当前楼层高度，而不是继续使用一楼附近的固定 `ground_height=-1.0`。


---

## 46. [frontier/viewpoint / 探索范围] 现在四足狗在2楼探索时，会照到上方，导致会出现frontier,我们是要通过修改box_max_z参数吗

**问题**

现在四足狗在2楼探索时，会照到上方，导致会出现frontier,我们是要通过修改box_max_z参数吗

**回答**

是的，优先就是调 `box_max_z`，但更准确地说：要限制 **FUEL 允许生成 frontier / viewpoint 的 z 范围**，避免把二楼上方、天花板以上、三楼附近的未知空间当成可探索区域。

你现在二楼重启 FUEL 时，脚本里是这样算的：

```python
ground_height = z - floor_z_box_low
box_min_z = ground_height
box_max_z = z + floor_z_box_high
```

也就是说 `box_max_z` 实际由 `floor_z_box_high` 决定。

如果四足狗 base 高度大约是 `z`，相机高度大约是 `z + 0.04`，那二楼探索通常不需要把 `box_max_z` 放得太高。建议先把：

```xml
<arg name="floor_z_box_high" default="1.2"/>
```

调小，比如：

```xml
<arg name="floor_z_box_high" default="0.6"/>
```

位置在：

[ fuel_multifloor_mission.launch ](/home/uestc/tzb/uestc-challenge2026/src/fuel/fuel_dog_adapter/launch/fuel_multifloor_mission.launch)

这样二楼启动 FUEL 时，`box_max_z = 当前狗的z + 0.6`，上方太高的 frontier 就不会被纳入探索范围。

同时手动启动单层 FUEL 时，对应的是：

```xml
<arg name="box_max_z" default="0.65"/>
```

位置在：

[ fuel_dog_visual_exploration.launch ](/home/uestc/tzb/uestc-challenge2026/src/fuel/fuel_dog_adapter/launch/fuel_dog_visual_exploration.launch)

注意区分：

- `box_max_z`：FUEL 探索边界的最高 z，影响 frontier 是否被保留
- `map_size_z`：SDF 地图整体高度范围，不直接等于探索边界
- `ground_height`：地图 z 起点
- `perception_max_dist` / `max_ray_length`：看多远，不能直接阻止“上方 frontier”

所以这个问题建议先调：

```xml
floor_z_box_high: 1.2 -> 0.5 或 0.6
```

如果还会看到上方 frontier，再考虑同时减小相机 FOV 的竖直方向或限制 frontier 的 `cluster_size_z`。


---

## 47. [A*/B 样条 / 探索范围] 四足狗探索完房间往回走的时候，还是可能在box_min_x和box_max_x之外留下veiwpoints，导致A*规划出路径，虽然四足狗最终不会到那个点，但是有趋势，如何彻底排除往回走时看到这些ve

**问题**

四足狗探索完房间往回走的时候，还是可能在box_min_x和box_max_x之外留下veiwpoints，导致A*规划出路径，虽然四足狗最终不会到那个点，但是有趋势，如何彻底排除往回走时看到这些veiwpoints

**回答**

我先看一下现在动态 box 是怎么改参数的，以及 frontier/viewpoint 在代码里是按什么条件过滤的。这个问题多半不是 A* 本身，而是“已经生成/缓存的 frontier 或 dormant viewpoint 没有被 box 收缩清掉”。

现在能确认一件事：动态 box 只是改了 SDFMap 当前允许区域，但它不会自动删除探索管理器里已经缓存的 `frontier/viewpoint`。我继续看 frontier 生成/保留逻辑，找最合适的“硬过滤”位置。

关键位置找到了：新 frontier 搜索会被 box 限制，但旧的 `frontiers_ / dormant_frontiers_` 只有在“地图更新区域重叠且内容变化”时才移除。box 收窄本身不算地图内容变化，所以收窄后遗留的 viewpoint 还可能参与 TSP/A*。

不是单纯再调 `box_min_x / box_max_x` 就能彻底解决。你现在的问题本质是：

`dynamic_exploration_box.py` 收窄了 SDFMap 的 box，但 FUEL 里已经生成过的 `frontiers_ / dormant_frontiers_ / viewpoints_` 可能还留在内存里。

也就是说：

1. 新生成的 frontier 会受当前 box 限制。
2. 旧的 frontier/viewpoint 不一定会因为 box 收窄而被删除。
3. 所以回来的时候，A* 还能拿到 box 外遗留 viewpoint，表现为“有往那边规划的趋势”。

相关代码位置：

- 动态改 box：
  [dynamic_exploration_box.py](/home/uestc/tzb/uestc-challenge2026/src/fuel/fuel_dog_adapter/scripts/dynamic_exploration_box.py)

- SDFMap 更新 box：
  [sdf_map.cpp](/home/uestc/tzb/uestc-challenge2026/src/fuel/fuel_planner/plan_env/src/sdf_map.cpp)

- frontier/viewpoint 缓存：
  [frontier_finder.cpp](/home/uestc/tzb/uestc-challenge2026/src/fuel/fuel_planner/active_perception/src/frontier_finder.cpp)

最彻底的解决方案是：在 `FrontierFinder` 里加一层硬过滤。

建议加三个点：

1. 在 `searchFrontiers()` 开始时，主动清理当前 box 外的 `frontiers_` 和 `dormant_frontiers_`

逻辑是：

```cpp
如果 frontier.average_ 不在 sdf_map 当前 box 内
或者 frontier.viewpoints_ 里没有任何 viewpoint 在当前 box 内
就删除这个 frontier
```

2. 在 `computeFrontiersToVisit()` 里，采样 viewpoint 后再次过滤

即使 frontier 本身还在 box 内，也要保证：

```cpp
viewpoint.pos_ 必须在当前 box 内
```

box 外 viewpoint 直接丢弃。

3. 在 `getTopViewpointsInfo()` / `getViewpointsInfo()` 返回给 TSP/A* 前再过滤一次

这是保险层，避免任何遗留 viewpoint 进入后面的全局 tour 和 A*。

所以结论是：

- `box_min_x / box_max_x` 只是限制“地图和新 frontier 搜索范围”
- 要彻底排除往回走时看到的旧 viewpoint，需要改 `frontier_finder.cpp`
- 最关键是清理 FUEL 内存中已经缓存的 frontier/viewpoint，而不是只改 launch 参数

如果你想保守一点，优先实现第 2 和第 3 点；如果要彻底干净，就三个点都加。


---

## 48. [A*/B 样条] 现在初步整个流程已经跑通了，但是还有几个问题需要细细分析。一：四足狗的速度控制，主要时yaw角的控制，现象就是会转过头。二：B样条的调优，有时候B样条出来的轨迹还是会穿墙。三：整个探索流程还可以调优，

**问题**

现在初步整个流程已经跑通了，但是还有几个问题需要细细分析。一：四足狗的速度控制，主要时yaw角的控制，现象就是会转过头。二：B样条的调优，有时候B样条出来的轨迹还是会穿墙。三：整个探索流程还可以调优，有哪些方向呢

**回答**

我先按你现在的三类问题拆开看：控制层、轨迹层、探索策略层。这里先不急着改代码，先把“现象可能来自哪一层”分清楚，不然很容易一边调 yaw 一边被 B 样条或 viewpoint 选择带偏。这三个问题其实对应 FUEL 改四足狗后的三个层级：**跟踪控制层、轨迹生成层、探索决策层**。建议分开调，不要混在一起调。

**一、速度控制，尤其 yaw 转过头**

你现在的 yaw 问题大概率不只是 `max_angular_speed` 一个参数的问题，而是几个因素叠加：

1. FUEL 原始 `position_cmd.yaw` 是给无人机相机/机头看的  
   它不是专门给四足狗底盘“平滑转向”的 yaw。无人机可以边飞边快速转视角，四足狗底盘转向有惯性和低层控制延迟，所以直接跟 `cmd.yaw` 很容易过头。

2. `fuel_poscmd_to_cmdvel.py` 里如果同时有“朝向轨迹速度方向”和“跟随 FUEL yaw”的逻辑，就可能出现目标 yaw 跳变  
   尤其 viewpoint 在身后、侧后方时，目标 yaw 可能突然跨过 `pi/-pi`，如果角度归一化或方向选择没处理好，就会转大圈或过冲。

3. yaw 控制器可能太“硬”  
   主要看这些参数：
   ```xml
   <param name="yaw_kp" value="2.8"/>
   <param name="max_angular_speed" value="$(arg max_angular_speed)"/>
   <param name="angular_acc_limit" value="2.0"/>
   <param name="yaw_ff_gain" value="0.0"/>
   ```
   过头通常优先降低：
   ```xml
   yaw_kp
   angular_acc_limit
   max_angular_speed
   ```
   但注意：如果只是降低 `max_angular_speed`，狗可能更跟不上红色视场；如果降低 `yaw_kp/角加速度限制`，会更平滑。

我建议的调法顺序：

```xml
yaw_kp: 2.8 -> 1.5 ~ 2.0
angular_acc_limit: 2.0 -> 0.8 ~ 1.2
max_angular_speed: 1.5 -> 0.8 ~ 1.0
```

另外，四足狗更合理的策略是：

- 行走时 yaw 主要跟随速度方向
- 低速或到达 viewpoint 附近时，再慢慢对准 FUEL yaw
- 不要在高速行走时强行追红色锥形视场

也就是说，底盘 yaw 应该服从“运动稳定”，相机视角 yaw 只是次要目标。

**二、B 样条有时还是穿墙**

这个问题通常不是 A* 的问题，而是 B 样条优化把几何路径“拉直”了。

典型现象是：

A* 路径是绕墙的 U 型：

```text
起点 -> 门口 -> 房间内
```

但 B 样条优化后变成：

```text
起点 -------- 房间内
```

于是穿墙。

相关参数主要在 [algorithm.xml](/home/uestc/tzb/uestc-challenge2026/src/fuel/fuel_planner/exploration_manager/launch/algorithm.xml)：

```xml
<param name="manager/clearance_threshold" value="0.2"/>
<param name="manager/control_points_distance" value="0.35"/>
<param name="manager/local_segment_length" value="6.0"/>
<param name="manager/use_geometric_path" value="true"/>
<param name="sdf_map/obstacles_inflation" value="0.3"/>
```

优先调这几个：

1. 增大障碍物膨胀

```xml
sdf_map/obstacles_inflation: 0.3 -> 0.4 或 0.45
```

这会让 B 样条离墙更远，但代价是窄门、窄走廊更容易规划失败。

2. 增大 clearance

```xml
manager/clearance_threshold: 0.2 -> 0.3 或 0.35
```

这是优化器希望轨迹离障碍的安全距离。四足狗建议不要太小。

3. 减小局部段长度

```xml
manager/local_segment_length: 6.0 -> 3.0 或 4.0
```

这个很关键。局部优化段太长时，B 样条容易跨过门、墙角，把 U 型路径拉直。四足狗场景建议小一点。

4. 减小控制点间距

```xml
manager/control_points_distance: 0.35 -> 0.25 或 0.3
```

控制点更密，轨迹更能贴合 A* 的绕行形状。

我建议先试一组保守参数：

```xml
<param name="sdf_map/obstacles_inflation" value="0.35"/>
<param name="manager/clearance_threshold" value="0.3"/>
<param name="manager/local_segment_length" value="3.5"/>
<param name="manager/control_points_distance" value="0.28"/>
```

如果还是穿墙，再继续加 inflation/clearance；如果开始频繁 `No path to next viewpoint`，说明膨胀太大或门太窄，需要回退一点。

**三、整个探索流程还能优化哪些方向**

我建议按优先级分这几类。

1. viewpoint 选择更适合四足狗

FUEL 原来更适合无人机，viewpoint 可能出现在四足狗不舒服的位置，比如侧后方、墙角附近、门框旁边。

可以优化：

```xml
frontier_candidate_rmin
frontier_candidate_rmax
frontier_min_candidate_clearance
frontier_min_candidate_dist
exploration/w_dir
```

建议方向：

```xml
frontier_candidate_rmin: 0.8 -> 1.0
frontier_candidate_rmax: 1.8 -> 2.0
frontier_min_candidate_clearance: 0.12 -> 0.2 或 0.25
exploration/w_dir: 1.5 -> 2.0
```

`w_dir` 增大后，会更偏向当前运动方向，少突然回头或去身后 viewpoint。

2. 分区探索策略

你现在已经做了动态 box，这是很有价值的。后续可以继续做：

- 一楼走廊优先窄 box
- 到达关键 y 后扩宽
- 回来时收窄
- 二楼重新设置 z 范围和 x/y box
- 电梯附近单独设置小范围导航，不让 FUEL 自己乱选 viewpoint

这比纯靠 FUEL 自主探索更稳定。

3. 探索结束判定

FUEL 的 “没有 frontier” 不一定等价于“真的探索完”。尤其四足狗相机视角有限，可能因为某个角度没有看到，就提前 FINISH。

可以优化：

- 探索结束后原地慢速扫视一次
- 或在 FINISH 前检查是否还有较大未知区域
- 或者在关键区域附近强制给一个 trigger / waypoint

你现在用 `send_waypoint_and_move.sh` 解决启动问题，其实就是这种思路。

4. 多楼层流程状态机

现在流程初步跑通，后面可以把状态拆得更清楚：

```text
EXPLORE_FLOOR
RETURN_ELEVATOR
ENTER_ELEVATOR
CALL_NEXT_FLOOR
EXIT_ELEVATOR
RESET_FUEL_MAP
EXPLORE_NEXT_FLOOR
TOP_DETECTED
RETURN_FLOOR0
DONE
```

每个状态都加超时、失败重试、当前位置判断。这样比一个线性脚本更稳。

5. 地图高度和相机感知限制

二楼已经遇到“看到上方 frontier”的问题，所以每层启动 FUEL 时要严格同步：

```text
ground_height
box_min_z
box_max_z
map_size_z
fixed_z
viewpoint_z
```

四足狗 2.5D 探索里，`box_max_z` 不宜太高。否则它会把上方未知区域当成 frontier。

**我建议的调试顺序**

1. 先调 yaw 控制，让狗不会转过头  
   先降 `yaw_kp` 和 `angular_acc_limit`，观察 `/cmd_vel/angular.z` 是否平滑。

2. 再调 B 样条防穿墙  
   优先改 `local_segment_length`、`clearance_threshold`、`obstacles_inflation`。

3. 最后调探索策略  
   优先动 `frontier_candidate_*`、`w_dir`、动态 box、楼层 z 范围。

不要一开始就同时调所有参数。我的建议是先固定探索范围和 B 样条参数，只看 yaw；yaw 稳了以后，再专门制造“进房间 U 型路径”场景调 B 样条。


---

## 49. [探索范围] 现在我们走到二楼或者三楼时，在进入走廊时，在左右两边存在空的区域，四足狗探索时容易掉下去，这个我们有什么解决方案吗？我们原本是通过修改box_min_x和box_max_x想来解决，但作用不大。你有什

**问题**

现在我们走到二楼或者三楼时，在进入走廊时，在左右两边存在空的区域，四足狗探索时容易掉下去，这个我们有什么解决方案吗？我们原本是通过修改box_min_x和box_max_x想来解决，但作用不大。你有什么解决方法

**回答**

有，且我建议不要再主要依赖 `box_min_x / box_max_x`。它只能限制 **frontier/viewpoint 的生成范围**，但不能真正告诉规划器“这里是楼板边缘，不能走”。所以它对“掉下去”这种安全问题作用有限。

这里要把问题当成 **可通行区域约束** 来处理，而不是探索范围约束。

**核心原因**

FUEL 的规划主要看 SDF/ESDF 里的占据障碍。楼板旁边的空洞、栏杆缺失、左右两边的空区域，在地图里往往是：

```text
不是障碍物，只是未知/空
```

对无人机来说没问题，可以飞过去；但对四足狗来说，这是“没有地面支撑”的危险区域。FUEL 原版并不知道“脚下必须有地面”，所以即使你把 viewpoint 限制在 `box_min_x / box_max_x` 内，B 样条或局部路径仍然可能靠近边缘，甚至穿到空区域附近。

**方案 1：加“可行走区域 mask”，最推荐**

给四足狗单独定义一个 walkable region，只允许 A* 和 B 样条在这个区域内规划。

例如走廊阶段只允许：

```text
x in [-0.9, 0.9]
y in [某个范围]
z 接近当前楼层高度
```

但这个不要只作用在 frontier，而要作用在：

- A* 搜索节点
- B 样条碰撞检查
- viewpoint 候选点
- 最终轨迹点检查

实现方式可以分两层：

1. 参数层定义 walkable box

```xml
walkable_min_x
walkable_max_x
walkable_min_y
walkable_max_y
```

2. 代码里加硬约束

在 A* 里：

```cpp
if (!isInWalkableRegion(pos)) continue;
```

在 B 样条轨迹检查里：

```cpp
if (!isInWalkableRegion(control_point)) return false;
```

这比 `box_min_x / box_max_x` 更强，因为它直接限制“路能不能走”。

**方案 2：把楼板边缘外侧伪造为障碍物**

这是工程上很有效的办法。

如果你知道走廊左右两侧是空的，就在 FUEL 的地图里人为加入“虚拟墙”或“虚拟障碍带”。

例如二楼/三楼走廊阶段，在：

```text
x < -1.0
x > 1.0
```

生成两条虚拟占据墙，让 SDFMap 认为那里是障碍。

效果：

- A* 不会往外走
- B 样条会被 ESDF 推回来
- viewpoint 也不容易选到危险边缘
- RViz 里能看见膨胀障碍

这是对 FUEL 最友好的方式，因为不用大改规划器逻辑，只是让地图表达“空洞边缘不可走”。

可以做一个节点：

```text
virtual_walkway_wall.py / virtual_safety_wall.cpp
```

根据当前楼层高度，发布或写入两侧虚拟障碍。

如果只是为了比赛稳定，我很推荐这个。

**方案 3：在地图里识别“脚下无地面”**

更符合四足狗语义，但实现复杂一点。

对每个候选点 `(x, y, z)`，检查它脚下是否有占据地面：

```text
在 z - 0.3 到 z - 0.6 范围内，是否存在 occupied voxel
```

如果脚下没有地面，就认为这个位置不可走。

这个可以用于：

- viewpoint 过滤
- A* 节点扩展
- B 样条轨迹检查

逻辑类似：

```cpp
bool hasGroundSupport(pos) {
  for dz from 0.2 to 0.8:
    if occupancy(pos.x, pos.y, pos.z - dz) == OCCUPIED:
      return true;
  return false;
}
```

优点是自动适配不同楼层和平台；缺点是依赖深度相机是否真的看到了地面，建图质量不好时可能误判。

**方案 4：降低探索阶段的横向自由度**

这只是辅助方案，不是根治。

你可以继续动态调整：

```xml
box_min_x
box_max_x
dynamic_box_gate_min_x
dynamic_box_gate_max_x
```

但是它只能减少“想看两边”的概率，不能保证轨迹不靠边。

可以配合调：

```xml
frontier_candidate_rmin
frontier_candidate_rmax
frontier_min_candidate_clearance
exploration/w_dir
```

例如让 viewpoint 更靠走廊中线：

```xml
frontier_min_candidate_clearance: 0.12 -> 0.25
frontier_candidate_rmin: 0.8 -> 1.0
exploration/w_dir: 1.5 -> 2.0
```

但如果地图里空洞不是障碍，B 样条仍然可能靠过去。

**我建议的实际路线**

如果你想快速稳定跑通二楼/三楼：

1. 先做方案 2：虚拟安全墙  
   在二楼/三楼走廊左右两侧加虚拟障碍，直接防止规划到空洞。

2. 再做方案 1：walkable region 硬约束  
   用于 A* 和 B 样条，彻底保证轨迹不出可行走区域。

3. 最后再做方案 3：脚下地面检测  
   让系统更通用，不依赖手动走廊宽度。

**最推荐的第一版实现**

做一个“楼层走廊安全边界”：

```text
当前楼层 z 附近
当 y 在走廊区域内
如果 x < -1.0 或 x > 1.0
则视为不可通行
```

然后不要只过滤 viewpoint，而是至少加到：

- A* 搜索
- B 样条轨迹有效性检查

如果你希望改动小，第一版可以先在 `astar2.cpp` 里加硬约束，防止 A* 出走廊；然后在 B 样条轨迹检查处加同样约束，防止优化后越界。这样比继续调 `box_min_x / box_max_x` 有用得多。


---

## 50. [视觉深度建图] 对于方案2，你通过什么方式伪造，fuel是视觉探索算法

**问题**

对于方案2，你通过什么方式伪造，fuel是视觉探索算法

**回答**

方案 2 的“伪造障碍物”，不是去骗真实 Gazebo 相机，而是 **在 FUEL 自己使用的 SDF/占据地图里额外注入虚拟占据体**。

也就是说，真实深度相机还是正常工作；只是 FUEL 建图模块在融合完深度图之后，再额外把某些区域标成 occupied。这样 A*、ESDF、B 样条都会把这些区域当成障碍。

**它不是改视觉输入，而是改 FUEL 的内部地图。**

FUEL 的链路大概是：

```text
Gazebo 深度相机
  -> /real_sense/depth/image_raw
  -> map_ros
  -> sdf_map occupancy
  -> ESDF
  -> A*
  -> B样条
  -> /position_cmd
```

方案 2 插入的位置是：

```text
Gazebo 深度相机
  -> /real_sense/depth/image_raw
  -> map_ros
  -> sdf_map occupancy
  + 虚拟安全墙 occupied voxels
  -> ESDF
  -> A*
  -> B样条
```

所以它和“视觉探索”不冲突。视觉仍然决定哪些地方未知、哪些地方可见；虚拟墙只是额外告诉规划器：

```text
这里虽然视觉上不是墙，但四足狗不能走。
```

**为什么不能只发一个点云？**

理论上可以伪造一个 `/virtual_wall_cloud`，再让 FUEL 把它融合进地图。但你当前 FUEL 的 map_ros 通常只接一个深度图或一个点云输入，而且你现在用的是：

```text
/map_ros/depth := /real_sense/depth/image_raw
/map_ros/cloud := /unused_cloud
```

如果改成点云融合，可能要处理：

- 深度图和虚拟点云怎么合并
- 时间戳
- 坐标系
- raycast 清空问题
- 虚拟障碍会不会被后续 miss ray 清掉

所以“发假点云”不是最稳的第一版。

**更推荐的伪造方式**

直接在 `SDFMap` 内部加虚拟障碍区域。

比如定义参数：

```xml
<param name="sdf_map/enable_virtual_safety_wall" value="true"/>
<param name="sdf_map/safety_wall_min_y" value="2.5"/>
<param name="sdf_map/safety_wall_max_y" value="20.0"/>
<param name="sdf_map/safety_wall_left_x" value="-1.0"/>
<param name="sdf_map/safety_wall_right_x" value="1.0"/>
<param name="sdf_map/safety_wall_height" value="1.0"/>
```

然后在 `sdf_map.cpp` 的地图更新后，把：

```text
x < -1.0
x > 1.0
y 在走廊范围内
z 在当前楼层高度附近
```

这些 voxel 标成 occupied 或 inflated occupied。

这样规划器看到的就是：

```text
走廊左右两侧有虚拟墙
```

**它会不会影响 frontier 探索？**

会影响，而且这是我们想要的。

因为虚拟墙外侧被标成 occupied 后：

- frontier 不会再把“边缘外侧空洞”当成可探索通道
- viewpoint 不会跑到边缘外侧
- A* 不会规划过去
- B 样条会被 ESDF 推离边缘

但它不会阻止四足狗探索走廊正前方，也不会影响房间内正常探索，只要你把虚拟墙的 y 范围、x 范围设置好。

**注意一个坑**

如果你把虚拟墙做成“永久 occupied”，后续进入左右房间时可能被挡住。

所以它最好是动态的：

```text
走廊阶段：开启虚拟墙，限制 x 在安全通道内
到达房间入口/需要左右探索时：关闭或放宽虚拟墙
返回走廊/电梯区域：重新开启
```

这和你现在的 `dynamic_exploration_box.py` 思路类似，但更强，因为它影响的是地图障碍，而不是只影响探索边界。

**第一版我建议这样做**

不要伪造相机数据，不发假点云。直接改 FUEL 内部 SDFMap：

1. 在 `algorithm.xml` 加参数：
   ```xml
   sdf_map/enable_virtual_safety_wall
   sdf_map/safety_corridor_min_x
   sdf_map/safety_corridor_max_x
   sdf_map/safety_corridor_min_y
   sdf_map/safety_corridor_max_y
   ```

2. 在 `SDFMap` 每次更新 ESDF 前，把走廊外侧 voxel 设成占据。

3. 让 `dynamic_exploration_box.py` 或另一个脚本动态开关这些参数。

这样最符合你现在的系统结构，也最容易让 A* 和 B 样条同时生效。


---

## 51. [frontier/viewpoint] 我想在四足狗刚开始探索的时候原地转一圈，让它能看完附近周围的viewpoints和frointier，然后直接去较远的点，可以实现吗

**问题**

我想在四足狗刚开始探索的时候原地转一圈，让它能看完附近周围的viewpoints和frointier，然后直接去较远的点，可以实现吗

**回答**

可以实现，而且这个思路是对的：**先让 FUEL 建一圈近场地图，再触发探索规划**。这样附近的 frontier/viewpoint 会更完整，FUEL 更容易选择远处有效目标，而不是刚启动时只看到一个窄扇形就急着规划。

推荐流程是：

```text
启动 FUEL 节点
等待深度图 / 里程计正常
四足狗原地旋转 360 度
停止 /cmd_vel
再发布 /waypoint_generator/waypoints 触发 FUEL 开始探索
```

关键点是：**原地转圈要发生在 FUEL 触发探索之前**，不要在 FUEL 已经发布 `/position_cmd` 后再强行转。否则会和 `fuel_poscmd_to_cmdvel.py` 抢 `/cmd_vel`，控制会打架。

你现在最适合改的位置是：

[fuel_multifloor_mission.py](/home/uestc/tzb/uestc-challenge2026/src/fuel/fuel_dog_adapter/scripts/fuel_multifloor_mission.py)

在这里：

```python
self.launch_fuel(floor)
self.send_trigger()
```

中间插入：

```python
self.spin_in_place()
```

变成：

```python
self.launch_fuel(floor)
self.spin_in_place()
self.send_trigger()
```

这样每一层重新启动 FUEL 后，都会先原地扫一圈，再开始规划。

实现方式建议用闭环 yaw，而不是简单固定时间开环转。也就是通过 `/Odometry_gazebo` 读取当前 yaw，累计转过的角度，转满 `2*pi` 后停止。参数可以设：

```xml
<arg name="startup_spin" default="true"/>
<arg name="startup_spin_angle" default="6.283"/>
<arg name="startup_spin_speed" default="0.45"/>
```

不建议转太快，`0.35 ~ 0.5 rad/s` 比较合适。太快会导致深度图拖影、建图不稳定，frontier 反而乱。

另外，如果你第一层 FUEL 是外部提前启动的，而不是 `fuel_multifloor_mission.py` 启动的，那么第一层不会自动执行这个 spin。要么把第一层也改成 mission 自动启动：

```xml
<arg name="auto_start_first_fuel" default="true"/>
```

要么单独写一个启动前扫视脚本。

结论：可以实现，推荐放在 **FUEL launch 之后、send_trigger 之前**。这样 FUEL 的 SDF map 已经在接收深度图，但探索 FSM 还没开始选 viewpoint。


---

## 52. [视觉深度建图 / frontier/viewpoint] 原地转一圈后，以四足狗中心为圆心到相机为半径构成一个圆型区域还是存在frointier，希望把他消掉

**问题**

原地转一圈后，以四足狗中心为圆心到相机为半径构成一个圆型区域还是存在frointier，希望把他消掉

**回答**

这个现象像是“相机安装偏移 + 近距离盲区”留下了一圈近场 frontier。解决方式我会直接在 FUEL 的 frontier 层加一个“启动点近场忽略圆”：以启动时四足狗中心为圆心，在 XY 平面内半径内的 frontier 直接丢掉，不进入 viewpoint/TSP/A*。

我会把这个“近场圆形 frontier 清除”做成参数化：默认开启，以 `init_x/init_y` 作为圆心，半径默认 `0.5m`。它只按 XY 平面过滤，适合你说的“以四足狗中心为圆心”的圆形区域。

先加了成员和函数声明。接下来在构造函数读参数，并在旧 frontier 清理、新 frontier 加入前都应用这个近场圆过滤。

新生成的近场 frontier 已经会被跳过。现在把旧的缓存 frontier 也纳入清理条件，避免转圈过程中已经生成的近场点继续残留。

接下来把参数从 launch 传进去：`fuel_dog_visual_exploration.launch` 用 `init_x/init_y/init_z` 作为忽略圆心，`algorithm.xml` 负责落到 `frontier/startup_ignore_*` 参数。

`algorithm.xml` 参数已经补好。现在在四足狗的外层 launch 里给出默认半径并传递圆心。

默认半径先设成 `0.5m`，比相机前向偏移 `0.28m` 稍大，能覆盖你看到的那一圈。现在编译和解析 launch。

launch 参数解析已经通过，C++ 正在编译。等它链接完成。

已实现“启动点近场圆形区域 frontier 清除”。

现在 FUEL 会以启动时的 `init_x/init_y` 为圆心，在 XY 平面内过滤掉半径内的 frontier。也就是你说的：

```text
以四足狗中心为圆心，到相机附近为半径形成一个圆形区域
```

这个区域内的 frontier 不会进入后续：

```text
viewpoint 生成
TSP
A*
B 样条规划
```

改动位置：

- [frontier_finder.h](/home/uestc/tzb/uestc-challenge2026/src/fuel/fuel_planner/active_perception/include/active_perception/frontier_finder.h)
- [frontier_finder.cpp](/home/uestc/tzb/uestc-challenge2026/src/fuel/fuel_planner/active_perception/src/frontier_finder.cpp)
- [algorithm.xml](/home/uestc/tzb/uestc-challenge2026/src/fuel/fuel_planner/exploration_manager/launch/algorithm.xml)
- [fuel_dog_visual_exploration.launch](/home/uestc/tzb/uestc-challenge2026/src/fuel/fuel_dog_adapter/launch/fuel_dog_visual_exploration.launch)

新增参数：

```xml
<arg name="frontier_startup_ignore_near" default="true"/>
<arg name="frontier_startup_ignore_radius" default="0.5"/>
```

默认半径是 `0.5m`，比相机前向偏移 `0.28m` 稍大一点，先覆盖那圈近场残留 frontier。后面如果还看到一圈，可以调大到：

```xml
frontier_startup_ignore_radius:=0.7
```

验证已通过：

```bash
roslaunch fuel_dog_adapter fuel_dog_visual_exploration.launch --nodes
roslaunch fuel_dog_adapter fuel_multifloor_mission.launch --nodes
catkin_make --pkg active_perception exploration_manager
```

运行时如果启用，会打印类似：

```text
Frontier startup ignore circle enabled: center x y, radius 0.500
```


---

## 53. [探索范围 / 多楼层] 现在需要优化流程：探索完一层return home的时候就开电梯门，出电梯门后，到指定点就直接开始转圈，不需要等电梯门关

**问题**

现在需要优化流程：探索完一层return home的时候就开电梯门，出电梯门后，到指定点就直接开始转圈，不需要等电梯门关

**回答**

我先看一下当前 multi-floor 状态流程，重点是探索完成后开关门、进出电梯、启动下一层 FUEL 的顺序。然后把门动作和转圈/触发探索的时序调成你要的：回到电梯口就开门，出电梯到指定点后立即转圈，不等关门。


---

## 54. [探索范围 / 多楼层] 不是回到电梯口才开门，而是探索完一层触发return home状态机的时候就开电梯门

**问题**

不是回到电梯口才开门，而是探索完一层触发return home状态机的时候就开电梯门

**回答**

明白了：你要的不是 `/planning/finish` 后开门，而是 FUEL 判断“本层探索完，开始 RETURN_HOME” 的那一刻就通知 mission 开电梯门。也就是门提前打开，四足狗回到电梯口时不用等门。

我会加一个新 topic，比如 `/planning/return_home_start`：FUEL 一进入 `RETURN_HOME` 就发布一次；mission 订阅它，收到后立即开当前楼层电梯门。这样不需要等 return home 完成。刚才输出里有点截断，我再读一下 mission 文件对应函数，避免改错位置。

我会做两个调整：第一，FUEL 进入 `RETURN_HOME` 时发布 `/planning/return_home_start`；第二，mission 收到这个 topic 立刻开当前楼层电梯门。顺手把“到下一层出电梯后先启动 FUEL 转圈，再关门”等待顺序也改掉。

FUEL 侧通知已经加好。现在改 mission：订阅 `/planning/return_home_start`，收到后按当前楼层开门；如果门已经提前开了，后续进入电梯前不会重复等开门。

这里我再加一个事件同步：如果回家过程中门正在打开，主流程到了电梯口会等这个开门动作完成，而不是只看一个布尔值就直接往里走。

接下来调整主流程：每层探索拆成“启动探索”和“等待探索完成”。这样从电梯出来到指定点后，可以马上启动 FUEL、转圈、触发探索，然后再关电梯门；不会先等关门。

流程已经改完。现在给 launch 暴露 `return_home_start_topic` 参数，然后做语法检查、launch 解析和 C++ 编译。

开始验证。这里会编 `exploration_manager`，因为新增了 FUEL 的 return-home-start topic。

语法和 launch 解析已经通过。编译还在跑，等它结束确认没有链接问题。

已按你的意思改了：现在不是 return home 到点后才开门，而是 **FUEL 一进入 `RETURN_HOME` 状态机时就开电梯门**。

改动如下：

- FUEL 新增发布：
  ```text
  /planning/return_home_start
  ```
  在 `PLAN_TRAJ` 发现 `NO_FRONTIER` 并切到 `RETURN_HOME` 前发布。

- mission 脚本订阅这个话题，收到后立刻开当前楼层电梯门：
  ```text
  multifloor mission: FUEL return-home started, opening elevator door on floor X
  ```

- 主流程里回到电梯口后会检查门是否已经提前打开：
  如果已经打开，就不重复等开门。

- 出电梯到指定点后，流程也改成：
  ```text
  开当前楼层电梯门
  出电梯到指定点
  立即启动 FUEL
  原地转圈
  发送 trigger
  然后再关电梯门
  ```
  也就是不再等电梯门关完才开始转圈。

涉及文件：

- [fast_exploration_fsm.cpp](/home/uestc/tzb/uestc-challenge2026/src/fuel/fuel_planner/exploration_manager/src/fast_exploration_fsm.cpp)
- [fast_exploration_fsm.h](/home/uestc/tzb/uestc-challenge2026/src/fuel/fuel_planner/exploration_manager/include/exploration_manager/fast_exploration_fsm.h)
- [fuel_multifloor_mission.py](/home/uestc/tzb/uestc-challenge2026/src/fuel/fuel_dog_adapter/scripts/fuel_multifloor_mission.py)
- [fuel_multifloor_mission.launch](/home/uestc/tzb/uestc-challenge2026/src/fuel/fuel_dog_adapter/launch/fuel_multifloor_mission.launch)

验证通过：

```bash
python3 -m py_compile src/fuel/fuel_dog_adapter/scripts/fuel_multifloor_mission.py
roslaunch fuel_dog_adapter fuel_multifloor_mission.launch --nodes
roslaunch fuel_dog_adapter fuel_dog_visual_exploration.launch --nodes
catkin_make --pkg exploration_manager fuel_dog_adapter
```


---

## 55. [A*/B 样条] 指出B样条的具体参数位置

**问题**

指出B样条的具体参数位置

**回答**

我直接定位一下 `algorithm.xml` 里 B 样条相关参数，以及代码里它们被读取的位置，方便你对应调参。

B 样条相关参数主要在这里：

[algorithm.xml](/home/uestc/tzb/uestc-challenge2026/src/fuel/fuel_planner/exploration_manager/launch/algorithm.xml:221)

```xml
<!-- trajectory optimization -->
<param name="optimization/ld_smooth" value="20.0"/>
<param name="optimization/ld_dist" value="20.0"/>
<param name="optimization/ld_feasi" value="2.0"/>
<param name="optimization/ld_start" value="100.0"/>
<param name="optimization/ld_end" value="0.5"/>
<param name="optimization/ld_guide" value="10.0"/>
<param name="optimization/ld_waypt" value="0.3"/>
<param name="optimization/ld_view" value="0.0"/>
<param name="optimization/ld_time" value="1.0"/>
<param name="optimization/dist0" value="0.55"/>
<param name="optimization/max_vel" value="$(arg max_vel)"/>
<param name="optimization/max_acc" value="$(arg max_acc)"/>
<param name="optimization/algorithm1" value="15"/>
<param name="optimization/algorithm2" value="11"/>
<param name="optimization/fixed_z" value="$(arg fixed_z)"/>
<param name="optimization/z_margin" value="$(arg z_margin)"/>
<param name="optimization/max_iteration_num1" value="2"/>
<param name="optimization/max_iteration_num2" value="2000"/>
<param name="optimization/max_iteration_num3" value="200"/>
<param name="optimization/max_iteration_num4" value="200"/>
<param name="optimization/max_iteration_time1" value="0.0001"/>
<param name="optimization/max_iteration_time2" value="0.005"/>
<param name="optimization/max_iteration_time3" value="0.003"/>
<param name="optimization/max_iteration_time4" value="0.003"/>
```

还有 B 样条速度/加速度限制：

```xml
<param name="bspline/limit_vel" value="$(arg max_vel)"/>
<param name="bspline/limit_acc" value="$(arg max_acc)"/>
<param name="bspline/limit_ratio" value="1.1"/>
```

几个最关键的：

- `optimization/ld_smooth`：平滑项权重，越大轨迹越顺，但越可能切角。
- `optimization/ld_dist`：避障距离项权重，越大越远离障碍物。
- `optimization/dist0`：期望安全距离，越大越保守。
- `optimization/ld_guide`：贴近引导路径的权重，越大越不容易偏离 A* 原始路径。
- `optimization/ld_waypt`：经过 waypoint 的约束权重，越大越不容易直接拉直穿墙。
- `optimization/max_vel` / `optimization/max_acc`：优化时的速度、加速度约束。
- `bspline/limit_vel` / `bspline/limit_acc`：B 样条最终时间重分配的速度、加速度限制。
- `optimization/fixed_z` / `optimization/z_margin`：你们四足狗固定高度相关限制。

如果你现在主要问题是 **B 样条把 U 型 A* 轨迹拉直导致穿墙**，优先看这三个：

```xml
<param name="optimization/ld_guide" value="10.0"/>
<param name="optimization/ld_waypt" value="0.3"/>
<param name="optimization/ld_smooth" value="20.0"/>
```

通常方向是：增大 `ld_guide` / `ld_waypt`，适当降低 `ld_smooth`。


---

## 56. [A*/B 样条] A*规划出来的轨迹较短的时候，B样条优化出来的轨迹偏离很多

**问题**

A*规划出来的轨迹较短的时候，B样条优化出来的轨迹偏离很多

**回答**

这个现象是合理的，而且在 FUEL 从无人机改成四足狗后会更明显：**A\* 路径短的时候，B 样条优化的约束点太少，平滑项/避障项/动力学项会更容易“主导”轨迹形状，导致它偏离原始 A\* 路径。**

你现在的参数里尤其明显：

```xml
<param name="optimization/ld_smooth" value="20.0"/>
<param name="optimization/ld_dist" value="20.0"/>
<param name="optimization/ld_guide" value="10.0"/>
<param name="optimization/ld_waypt" value="0.3"/>
```

问题点在这里：

- `ld_smooth=20.0` 很强，会倾向于把短路径拉成更圆滑、更直的曲线。
- `ld_dist=20.0` 也很强，靠近障碍物时会把轨迹往外推。
- `ld_guide=10.0` 虽然有，但不一定足够约束短路径。
- `ld_waypt=0.3` 很小，说明 B 样条不太“必须经过”A\* 给出的中间点。
- 短 A\* 路径本身 waypoint 数量少，B 样条优化自由度反而大。

所以短路径容易出现：

```text
A*：  起点 -> 拐点 -> 终点
B样条：起点 -------- 终点
```

甚至在门口、墙角、U 型绕行这种场景里，B 样条会直接切过去。

优先调这几个参数：

```xml
<param name="optimization/ld_guide" value="20.0"/>
<param name="optimization/ld_waypt" value="5.0"/>
<param name="optimization/ld_smooth" value="8.0"/>
```

思路是：

- 增大 `ld_guide`：让 B 样条更贴近 A\* 引导路径。
- 增大 `ld_waypt`：让它更愿意经过中间点。
- 降低 `ld_smooth`：减少“为了平滑而切角”。

如果仍然偏离，可以继续：

```xml
<param name="optimization/ld_guide" value="30.0"/>
<param name="optimization/ld_waypt" value="10.0"/>
<param name="optimization/ld_smooth" value="5.0"/>
```

但不要一开始调太猛，否则轨迹可能变得不够平滑，四足狗跟踪时会抖或者频繁急转。

还有一个很关键的结构性原因：**短路径 A\* 采样点少**。如果代码里给 B 样条的 guide path 太稀疏，光调权重不一定彻底解决。更根本的方案是：

```text
A* 路径进入 B 样条优化前，对路径做 densify/重采样
```

比如每 `0.2m` 插一个中间点。这样 B 样条就不容易在短 U 型、门口、墙角路径上直接拉直。

所以优先级我建议：

1. 先调 `ld_guide ↑`、`ld_waypt ↑`、`ld_smooth ↓`
2. 如果短路径仍然偏离明显，再做 A\* 路径重采样
3. 最后再考虑提高 `dist0` 或 `ld_dist`，但这两个主要是避障，不是防止偏离 A\* 路径的核心参数


---

## 57. [A*/B 样条 / 重规划 / 探索范围] 1.参数已经修改，进房间门的时候，A*规划出比较短且比较曲折的轨迹时，B样条偏差还是比较大 2.房间门是左右对称的，在准备进房时，轨迹会左右横跳 3.重规划时间间隔对于四足狗来说太快

**问题**

1.参数已经修改，进房间门的时候，A*规划出比较短且比较曲折的轨迹时，B样条偏差还是比较大
2.房间门是左右对称的，在准备进房时，轨迹会左右横跳
3.重规划时间间隔对于四足狗来说太快

**回答**

我先把这三个现象对应到 FUEL 里的参数和机制上，不急着改代码。尤其第 3 个要确认当前 `replan_time` 是在哪个命名空间下被 `traj_server` 和 FSM 使用的。

这三个现象其实是同一类问题：**FUEL 原本偏无人机，重规划快、轨迹平滑强、目标视点切换敏感；四足狗执行慢且非全向，所以会表现成 B 样条偏离、门口横跳、频繁改主意。**

**1. 短且曲折的 A\*，B 样条仍偏很多**

仅调 `ld_guide / ld_waypt / ld_smooth` 还不够时，说明问题不只是权重，而是 **A\* 提供给 B 样条的约束点太少**。

重点看：

```xml
<param name="manager/control_points_distance" value="0.35"/>
<param name="manager/local_segment_length" value="6.0"/>
<param name="optimization/ld_smooth" .../>
<param name="optimization/ld_guide" .../>
<param name="optimization/ld_waypt" .../>
```

建议方向：

```xml
<param name="manager/control_points_distance" value="0.20"/>
<param name="manager/local_segment_length" value="3.0"/>
```

含义是让短路径里也有更多控制点，B 样条不容易一把拉直。`local_segment_length` 太长时，优化器会拿较长局部段一起优化，更容易为了整体平滑切掉门口的小 U 型。

如果还是偏，代码层面更根本：**A\* 路径进入 B 样条前重采样/densify**，例如每 `0.15~0.2m` 插点。这个比继续堆权重更有效。

**2. 房间门左右对称，准备进房时轨迹左右横跳**

这是典型的“对称视点竞争”。门左右两侧 frontier/viewpoint 收益差不多，代价也差不多，每次重规划稍微看到一点新地图，最优解就在左右之间切换。

相关参数：

```xml
<param name="exploration/refine_local" value="true"/>
<param name="exploration/refined_num" value="7"/>
<param name="exploration/top_view_num" value="15"/>
<param name="exploration/w_dir" value="1.5"/>
<param name="exploration/refined_radius" value="5.0"/>
```

建议先试：

```xml
<param name="exploration/top_view_num" value="8"/>
<param name="exploration/refined_num" value="4"/>
<param name="exploration/w_dir" value="3.0"/>
```

含义：

- `top_view_num` 降低：减少每个 frontier 候选视点数量，降低左右等价候选抖动。
- `refined_num` 降低：局部优化少看几个目标，不频繁重新排序。
- `w_dir` 增大：更偏向当前运动方向，减少左右横跳。

如果还跳，最有效的是加“目标锁定/迟滞”：选中一个 next viewpoint 后，只要距离没到、没有碰撞、收益没有明显变差，就不要换到另一个对称 viewpoint。这个需要改 FSM 或 exploration manager。

**3. 重规划时间对四足狗太快**

当前在 [algorithm.xml](/home/uestc/tzb/uestc-challenge2026/src/fuel/fuel_planner/exploration_manager/launch/algorithm.xml:126)：

```xml
<param name="fsm/replan_time" value="0.200" type="double"/>
```

`0.2s` 对四足狗确实太快了。无人机可以接受，四足狗还没完成 yaw 调整，下一条轨迹又来了，就容易横跳、转过头、轨迹追不上。

建议改到：

```xml
<param name="fsm/replan_time" value="0.8" type="double"/>
```

或者更稳一点：

```xml
<param name="fsm/replan_time" value="1.0" type="double"/>
```

另外 `traj_server` 这里：

```xml
<param name="traj_server/time_forward" value="1.0" type="double"/>
```

它会影响 `/position_cmd` 采样时往前取轨迹点的程度。四足狗执行慢时，如果发现它总是在追很远的点，可以考虑降到：

```xml
<param name="traj_server/time_forward" value="0.4"/>
```

优先顺序我建议：

1. 先把 `fsm/replan_time` 从 `0.2` 改到 `0.8~1.0`
2. 再把 `manager/control_points_distance` 从 `0.35` 改到 `0.20`
3. 把 `manager/local_segment_length` 从 `6.0` 改到 `3.0~4.0`
4. 把 `top_view_num/refined_num` 降低，`w_dir` 提高
5. 如果门口短 U 型还是被拉直，再实现 A\* 路径 densify 和 viewpoint 锁定

这里最关键的是第 1 点。重规划太快会放大前两个问题，先慢下来，很多“左右横跳”和“B 样条看起来飘”的现象会明显缓和。


---

## 58. [A*/B 样条] <param name="manager/control_points_distance" value="0.20"/> <param name="manager/local_segment_leng

**问题**

<param name="manager/control_points_distance" value="0.20"/>
<param name="manager/local_segment_length" value="3.0"/>
修改这两个参数作用不大，我发现B样条的终点不在A*轨迹的终点，甚至偏离很多

**回答**

我去看一下 B 样条是怎么从 A* / kinodynamic path 里生成控制点和终点的，重点确认它到底是“跟随 A* 终点”，还是只保证局部段可行。

我再补一段 `localExplore` 和轨迹参数化的入口，看看你现在说的“B 样条终点偏离 A* 终点”是在探索的哪条链路上发生的。

我再确认一下优化器在 `NORMAL_PHASE` 下到底有没有把终点当硬约束。这个会决定“终点偏离”是正常现象还是参数没配对。

我已经确认了一个关键点：`NORMAL_PHASE` 确实包含 `END`，但它是“代价项”，不是绝对硬约束。再看一下 `END` 这项怎么罚，就能解释为什么终点会漂。

我已经找到最关键的地方了：在 `localExplore()` 里，终点边界约束其实是被注释掉的，所以这条链路里 B 样条并不会强制落到 A* 终点。再把具体位置标出来给你。

对，这个现象已经不是单纯调 `control_points_distance` / `local_segment_length` 能解决的了。关键原因是：

**在 `localExplore()` 这条链路里，B 样条的终点根本没有被强约束到 A* 的终点 `gi`。**

你看这里：

[planner_manager_dev.cpp:249-303](/home/uestc/tzb/uestc-challenge2026/src/fuel/fuel_planner/plan_manage/src/planner_manager_dev.cpp:249)

```cpp
auto path = path_finder_->getPath();
...
// construct initial value
...
// set start value
...
// state_xyz.setZero();    // set end value
// state_xyz.row(0)    = gi;
// ...
// pts.push_back(p_0);
// pts[pts.size() - 2] = p_1;
// pts[pts.size() - 3] = p_2;
```

这段“设置终点”的代码是**注释掉的**，所以：

- A* 的 `gi` 只作为搜索目标
- 但 B 样条初始化时，没有把末端控制点强行钉到 `gi`
- 后面的优化就可能把末端往别的方向拉

而且优化器里的 `END` 也只是**代价项**，不是硬约束：

[bspline_optimizer.cpp:20-23](src/fuel/fuel_planner/bspline_opt/src/bspline_optimizer.cpp:20)

```cpp
const int BsplineOptimizer::NORMAL_PHASE = BsplineOptimizer::SMOOTHNESS | BsplineOptimizer::DISTANCE |
    BsplineOptimizer::FEASIBILITY | BsplineOptimizer::START | BsplineOptimizer::END;
```

[bspline_optimizer.cpp:614-621](src/fuel/fuel_planner/bspline_opt/src/bspline_optimizer.cpp:614)

```cpp
if (cost_function_ & END) {
  ...
  f_combine += ld_end_ * f_end;
  ...
}
```

也就是说它只是“尽量靠近终点”，不是“必须到终点”。

所以你现在看到的情况本质上是：

1. A* 找到一个短而弯的路径
2. B 样条优化时为了平滑、避障，把末端也一起往外推
3. 结果终点偏离 A* 终点，甚至差很多

这个和你改 `control_points_distance`、`local_segment_length` 的关系不大，因为问题不是“采样太粗”，而是**终点约束结构就没钉死**。

另外，如果你说的是 **topo/kinodynamic 那条链路**，它虽然也有 `END`，但同样是 penalty，不是 hard constraint；只是它比 `localExplore()` 更完整一些。

结论很直接：

- **短曲折路径终点漂移**：主要是 `localExplore()` 里终点约束被注释掉了
- **B 样条不是严格贴着 A* 终点**：因为 `END` 只是软约束
- **单纯调参数不够**：要么恢复终点边界约束，要么增强 waypoint / end 权重，再不然就要改参数化方式

如果你愿意，我下一步可以直接帮你定位“应该恢复哪几行”，让 B 样条终点真正钉到 A* 终点。


---

## 59. [探索范围] 现在末端钉回目标点的问题暂时解决了。回到房间门左右对称，准备进房时轨迹左右横跳的问题，我们该如何解决

**问题**

现在末端钉回目标点的问题暂时解决了。回到房间门左右对称，准备进房时轨迹左右横跳的问题，我们该如何解决

**回答**

这是典型的“对称目标抖动”。

原因很直接：门口左右两侧的 viewpoint 收益、距离、可见性都很接近，而 FUEL 每次重规划都会重新算一遍最优解，所以很容易这次选左、下次选右，轨迹就左右横跳。

**最有效的方案不是继续堆权重，而是加迟滞/锁定。**

建议这样做：

1. **进门阶段锁侧**
   - 一旦第一次选中了门左侧或右侧的 viewpoint，就暂时锁定同侧。
   - 只有当前侧 viewpoint 不可达、碰撞、或收益明显下降时，才允许换边。

2. **给 viewpoint 选择加 margin**
   - 新候选必须比当前候选好一截才允许切换。
   - 例如“新分数 > 当前分数 + 阈值”才切。

3. **降低重规划频率**
   - 你们现在 `fsm/replan_time = 0.2` 太快了。
   - 对四足狗，门口场景建议先提到 `0.8 ~ 1.0`，减少反复改主意。

4. **给门口加状态机**
   - 单独定义一个“approach-door / enter-door”状态。
   - 在这个状态里，不做全局 viewpoint 重排序，只跟随已经选定的门口一侧目标。

**最推荐的实现位置**
- `exploration_manager/src/fast_exploration_manager.cpp`
- 或 `fast_exploration_fsm.cpp`

**最小改法**
- 记录 `last_next_pos_ / last_cluster_id_ / last_side_sign_`
- 下次重规划时，优先保留同侧候选
- 只有差异超过阈值才允许换边

如果你要，我下一步可以直接帮你把“门口 viewpoint 迟滞锁定”这套机制加到当前 `exploration_node` 里。


---

## 60. [探索范围] 如何实现进门阶段锁侧

**问题**

如何实现进门阶段锁侧

**回答**

我先把“锁侧”落到具体函数上：找出当前 viewpoint 是在哪一步被重新排序、以及门口附近哪个量最适合拿来判定“左侧/右侧”。

可以，最稳的是在 **`exploration_node` 里加一个“门口锁侧”状态**，不是去改 `traj_server`。

现在左右横跳发生的位置在这里：

- [fast_exploration_manager.cpp](</home/uestc/tzb/uestc-challenge2026/src/fuel/fuel_planner/exploration_manager/src/fast_exploration_manager.cpp:153>)
- `getViewpointsInfo(...)`
- `refineLocalTour(...)`
- 然后直接 `next_pos = ed_->refined_points_[0]`

所以锁侧就应该插在这条链路上。

**做法：**

1. **定义门口参考系**
   - 设一个门中心 `door_center`
   - 再设一个“左右分界轴” `side_axis`
   - 比如门朝向沿 `y`，那左右就看 `x - door_center.x`

2. **第一次进入门口时定侧**
   - 当机器人还在门口附近时，第一次选中的 viewpoint 记为：
     - 左侧：`sign((next_pos - door_center).dot(side_axis)) > 0`
     - 右侧：`< 0`
   - 保存成 `locked_side_`

3. **锁侧阶段过滤候选**
   - 后续每次重规划时，只保留和 `locked_side_` 同侧的 viewpoint
   - 过滤条件类似：
     ```cpp
     double s = (candidate - door_center).dot(side_axis);
     if (s * locked_side_ < 0) continue;
     ```
   - 如果过滤后为空，再退回原逻辑

4. **离开门口后解锁**
   - 当机器人已经明显穿过门，比如离 `door_center` 超过一个阈值，或者进入房间后，清掉 `locked_side_`

**建议放的位置：**
- `src/fuel/fuel_planner/exploration_manager/src/fast_exploration_manager.cpp`
- 再加少量参数到 `algorithm.xml`

**最小实现路径：**
- 加成员变量：
  - `bool door_lock_active_`
  - `int locked_side_`
  - `Eigen::Vector3d door_center_`
  - `Eigen::Vector3d side_axis_`
  - `double door_lock_radius_`
- 在 `planExploreMotion()` 里：
  - 先拿到候选 viewpoint
  - 如果已经锁侧，就先过滤同侧候选
  - 再做 `refineLocalTour`

如果你愿意，我下一步可以直接帮你把这套“进门锁侧”机制落成代码。


---

## 61. [重规划] 你这个实现起来有点难度，可以先降低重规划的频率来看看能否解决问题

**问题**

你这个实现起来有点难度，可以先降低重规划的频率来看看能否解决问题

**回答**

我先核对一下“重规划频率”在这条链路里到底对应哪个参数，避免把别的量当成节奏控制。

这里有个很关键的点：`fsm/replan_time` 不是重规划间隔，它只是新轨迹发布时间往后延迟的量；真正控制周期性重规划的是 `fsm/thresh_replan3`。你现在这几个 `thresh_replan*` 是注释状态，代码默认值会比较危险，我先把它们显式写出来。

参数已经显式打开了：周期重规划先放到 1 秒，frontier covered 的重规划也至少等 0.8 秒。这个不用编译，重启 launch 后生效。

已改。

关键点是：`fsm/replan_time=0.200` 不是重规划频率，它只是规划成功后给新轨迹设置的启动延迟。真正导致频繁重规划的是这几个参数之前被注释了：

```xml
<param name="fsm/thresh_replan1" value="0.2"/>
<param name="fsm/thresh_replan2" value="0.8"/>
<param name="fsm/thresh_replan3" value="1.0"/>
```

含义：

- `thresh_replan1=0.2`：轨迹快执行完，剩 0.2s 时才重规划。
- `thresh_replan2=0.8`：至少执行 0.8s 后，才允许因为 frontier covered 触发重规划。
- `thresh_replan3=1.0`：周期性重规划间隔变成约 1s。

你现在重启：

```bash
roslaunch fuel_dog_adapter fuel_multifloor_mission.launch
```

然后重点观察门口是否还会左右横跳。如果还是跳，可以继续把 `thresh_replan3` 提到 `1.5`，同时把 `exploration/w_dir` 从 `1.5` 提到 `2.5~3.0`。


---

## 62. [重规划] 为什么有时我们的轨迹没有执行多少它就进行了重规划

**问题**

为什么有时我们的轨迹没有执行多少它就进行了重规划

**回答**

我看一下 FSM 里触发重规划的所有条件，结合你刚改的几个阈值解释为什么“没走多少就重规划”。

还有一个安全重规划定时器，每 0.05s 查碰撞。这个也会让轨迹刚发出去就重规划，尤其你刚把终点硬钉后，曲率/靠墙处可能更容易触发碰撞检查。

主要有 4 个触发条件，所以看起来会出现“轨迹刚走一点就重规划”。

在 [fast_exploration_fsm.cpp](/home/uestc/tzb/uestc-challenge2026/src/fuel/fuel_planner/exploration_manager/src/fast_exploration_fsm.cpp:157) 里：

```cpp
double t_cur = (ros::Time::now() - info->start_time_).toSec();
double time_to_end = info->duration_ - t_cur;
```

然后：

**1. 轨迹快执行完**

```cpp
if (time_to_end < fp_->replan_thresh1_)
```

你现在是：

```xml
<param name="fsm/thresh_replan1" value="0.2"/>
```

如果 B 样条本身很短，可能 1 秒不到就执行完，因此很快重规划。

**2. 当前 frontier 被认为已经覆盖**

```cpp
if (t_cur > fp_->replan_thresh2_ && frontier_finder_->isFrontierCovered())
```

你现在是：

```xml
<param name="fsm/thresh_replan2" value="0.8"/>
```

也就是说轨迹执行超过 0.8s 后，如果相机已经看到目标 frontier，马上重规划。这在进门口很常见，因为稍微转头/靠近一点，frontier 就被标记 covered。

**3. 周期性重规划**

```cpp
if (t_cur > fp_->replan_thresh3_ && !classic_)
```

你现在是：

```xml
<param name="fsm/thresh_replan3" value="1.0"/>
```

所以即使没有碰撞、没有覆盖，只要执行超过 1 秒，也会周期性重规划。

**4. 安全碰撞检查**

在 [fast_exploration_fsm.cpp](/home/uestc/tzb/uestc-challenge2026/src/fuel/fuel_planner/exploration_manager/src/fast_exploration_fsm.cpp:472)：

```cpp
if (!safe) {
  ROS_WARN("Replan: collision detected");
  transitState(...);
}
```

这个定时器是 `0.05s` 查一次。如果 B 样条靠墙、穿过膨胀区，或者刚才“末端钉回目标点”后局部轨迹更贴近障碍物，就会很快触发安全重规划。

你可以看终端日志是哪一种：

```text
Replan: traj fully executed
Replan: cluster covered
Replan: periodic call
Replan: collision detected
```

对应的解决方向不同。

如果你只是想先降低重规划频率，建议先改成：

```xml
<param name="fsm/thresh_replan1" value="0.3"/>
<param name="fsm/thresh_replan2" value="1.5"/>
<param name="fsm/thresh_replan3" value="2.0"/>
```

这样至少会让轨迹执行更久一点，不会刚进门就频繁左右切换。


---

## 63. [重规划 / 多楼层] <!-- Fsm --> <param name="fsm/thresh_replan1" value="0.5" type="double"/> <param name="fsm/thresh_re

**问题**

<!-- Fsm -->
    <param name="fsm/thresh_replan1" value="0.5" type="double"/>
    <param name="fsm/thresh_replan2" value="0.5" type="double"/>
    <param name="fsm/thresh_replan3" value="1.5" type="double"/>
    <param name="fsm/return_home" value="$(arg return_home)" type="bool"/>
    <param name="fsm/return_home_tolerance" value="$(arg return_home_tolerance)" type="double"/>
    <param name="fsm/return_home_yaw_tolerance" value="$(arg return_home_yaw_tolerance)" type="double"/>
    <param name="fsm/use_fixed_return_home" value="$(arg use_fixed_return_home)" type="bool"/>
    <param name="fsm/return_home_x" value="$(arg return_home_x)" type="double"/>
    <param name="fsm/return_home_y" value="$(arg return_home_y)" type="double"/>
    <param name="fsm/return_home_z" value="$(arg return_home_z)" type="double"/>
    <param name="fsm/return_home_yaw" value="$(arg return_home_yaw)" type="double"/>

    <param name="fsm/thresh_replan1" value="0.2" type="double"/>
    <param name="fsm/thresh_replan2" value="0.8" type="double"/>
    <param name="fsm/thresh_replan3" value="1.0" type="double"/>
    <param name="fsm/replan_time" value="0.200" type="double"/>
这里怎么有重复的参数

**回答**

你看得很对，这里现在确实重复了。我把重复的后一组清掉，只保留你当前想用的那组阈值。

对，这里是重复参数。

ROS 参数服务器里同名参数会被后面的值覆盖，所以你前面写的：

```xml
<param name="fsm/thresh_replan1" value="0.5"/>
<param name="fsm/thresh_replan2" value="0.5"/>
<param name="fsm/thresh_replan3" value="1.5"/>
```

实际上会被后面的：

```xml
<param name="fsm/thresh_replan1" value="0.2"/>
<param name="fsm/thresh_replan2" value="0.8"/>
<param name="fsm/thresh_replan3" value="1.0"/>
```

覆盖。

我已经把后面重复的三行删掉了，现在只保留你前面这一组：

```xml
<param name="fsm/thresh_replan1" value="0.5"/>
<param name="fsm/thresh_replan2" value="0.5"/>
<param name="fsm/thresh_replan3" value="1.5"/>
```

`fsm/replan_time` 保留不变。


---

## 64. [视觉深度建图 / 探索范围 / 避障膨胀] 我注意到一个现象，当目标点不在四足狗的视野范围内，规划出来的轨迹会边转变前进，这样会向外绕一圈。这种情况下我的想法是先转到朝向目标点，然后再执行，当然这建立在当前点和目标点连线不存在障碍物。你能明白我

**问题**

我注意到一个现象，当目标点不在四足狗的视野范围内，规划出来的轨迹会边转变前进，这样会向外绕一圈。这种情况下我的想法是先转到朝向目标点，然后再执行，当然这建立在当前点和目标点连线不存在障碍物。你能明白我的意思吗

**回答**

能明白，而且你这个判断很对。

你现在看到的现象本质是：

```text
目标点不在相机/狗头视野方向内
↓
FUEL 已经给出目标点和轨迹
↓
四足狗一边转 yaw 一边执行线速度
↓
因为四足狗不能横移/不适合倒退，实际轨迹会绕一个弧线
↓
看起来像“向外绕一圈”
```

你的想法是：

```text
如果当前位置到目标点直线无遮挡
并且目标点不在当前朝向/视野范围内
那就先原地转向目标点
等 yaw 对准后，再开始沿轨迹前进
```

这是非常适合四足狗的策略。它相当于给执行层加一个 **yaw-first / rotate-before-move** 逻辑。

可以放在 `fuel_poscmd_to_cmdvel.py` 里实现，比较简单：

1. 计算目标方向：

```python
target_heading = atan2(target_y - robot_y, target_x - robot_x)
yaw_error = wrap_pi(target_heading - current_yaw)
```

2. 如果偏差大，比如超过 `45°`：

```python
if abs(yaw_error) > rotate_first_yaw_threshold:
    linear.x = 0
    linear.y = 0
    angular.z = yaw_kp * yaw_error
```

3. 当转到比较接近，比如小于 `15°`，再恢复正常跟踪。

最好做成滞回，避免一会儿转一会儿走：

```text
进入 rotate-first：|yaw_error| > 45°
退出 rotate-first：|yaw_error| < 15°
```

你提到的“当前点和目标点连线不存在障碍物”也很关键。严格来说这需要查占据栅格或 A* 路径。但第一版可以先不查障碍物，只对 `/position_cmd` 的目标方向做 yaw-first，这样已经能明显减少绕圈。

如果要更稳，可以在 FUEL 层判断：

- A* 路径长度接近直线距离
- 比如：
  ```text
  path_length / straight_dist < 1.2
  ```
- 才启用“先转再走”

这个条件比直接 raycast 更容易接入，因为 `fast_exploration_manager.cpp` 里已经有 A* 路径 `ed_->path_next_goal_`。

所以可以有两个实现层级：

**简单版：在 `fuel_poscmd_to_cmdvel.py` 执行层做**
- 优点：改动小，立刻影响四足狗执行
- 缺点：不知道前方是否有障碍，只根据目标方向转

**稳健版：在 FUEL 规划层判断直线路径，再通知执行层**
- 优点：只在直线可走时 rotate-first
- 缺点：需要多加状态/话题/参数，改动更大

我建议先做简单版，参数化开关：

```xml
<param name="rotate_first" value="true"/>
<param name="rotate_first_enter_yaw" value="0.785"/>
<param name="rotate_first_exit_yaw" value="0.262"/>
```

这样先验证现象是否明显改善。


---

## 65. [视觉深度建图] 当前相机的视角是多少度？如何修改？

**问题**

当前相机的视角是多少度？如何修改？

**回答**

我先确认 Gazebo 相机模型和 FUEL 里可视化/可见性判断的视角参数，因为这两处经常不是同一个值。

当前有三套“视角”参数，要分清：

1. **Gazebo 真实深度相机视角**
在 [gazebo.xacro](/home/uestc/tzb/uestc-challenge2026/src/unitree_guide/unitree_ros/robots/a1_description/xacro/gazebo.xacro:458)：

```xml
<horizontal_fov>${60.0*3.14/180.0}</horizontal_fov>
<width>640</width>
<height>480</height>
```

所以真实深度图水平视角是 **60°**。  
垂直视角由 640x480 比例推算，大约是 **46.8°**。

2. **FUEL 建图用的相机内参**
在 [fuel_dog_visual_exploration.launch](/home/uestc/tzb/uestc-challenge2026/src/fuel/fuel_dog_adapter/launch/fuel_dog_visual_exploration.launch:39)：

```xml
<arg name="fx" default="554.256"/>
<arg name="fy" default="554.256"/>
```

这组参数对应 640x480 下水平 FOV 约 **60°**，和 Gazebo 相机基本匹配。

如果你改 Gazebo 的 `horizontal_fov`，这里的 `fx/fy` 也要一起改。公式是：

```text
fx = width / (2 * tan(horizontal_fov / 2))
```

例如改成 80°：

```text
fx = 640 / (2 * tan(40°)) ≈ 381.4
```

3. **FUEL 视场可视化/可见性判断**
在 `fuel_dog_visual_exploration.launch` 的 `traj_server` 里：

```xml
<param name="perception_utils/top_angle" value="0.523599"/>
<param name="perception_utils/left_angle" value="0.523599"/>
<param name="perception_utils/right_angle" value="0.523599"/>
```

这里 `0.523599 rad = 30°`，所以水平是：

```text
left + right = 30° + 30° = 60°
```

也和当前 Gazebo 相机水平视角一致。

但是注意：`algorithm.xml` 里还有一组：

```xml
<param name="perception_utils/top_angle" value="0.56125"/>
<param name="perception_utils/left_angle" value="0.69222"/>
<param name="perception_utils/right_angle" value="0.68901"/>
```

这组水平大约是：

```text
39.7° + 39.5° ≈ 79.2°
```

比真实相机 60° 更宽。这个可能会让 FUEL 以为能看到更宽的区域。

**建议**

如果你想保持当前真实相机 60°，建议把 `algorithm.xml` 里的可见性参数也改成一致：

```xml
<param name="perception_utils/top_angle" value="0.408"/>
<param name="perception_utils/left_angle" value="0.523599"/>
<param name="perception_utils/right_angle" value="0.523599"/>
```

其中 `0.408 rad ≈ 23.4°`，对应 640x480 下 60° 水平视角的垂直半视角。


---

## 66. [视觉深度建图] 我想扩大相机的水平视角，比如说增大到100度

**问题**

我想扩大相机的水平视角，比如说增大到100度

**回答**

要把真实深度相机改到 100°，需要同步改 Gazebo 的 `horizontal_fov`、FUEL 建图内参 `fx/fy`，以及 FUEL 可见性/视场可视化参数；我会一起改，避免“真实看得宽但算法还按旧视角算”或反过来。

我算一下 100° 对应的内参：640 宽时 `fx = 640 / (2*tan(50°)) ≈ 268.51`；如果保持方形像素，`fy` 也用 268.51，垂直半视角约 41.8°。

我现在把真实相机、FUEL 建图内参、FUEL 视场判断和 RViz 视场显示一起同步到 100°。

`fuel_dog_visual_exploration.launch` 这边已经同步，我再改 `algorithm.xml` 里的探索可见性判断，否则 viewpoint 选择仍可能按旧视角计算。

我顺手把 RViz 视场 marker 的内参也同步一下，避免红色视锥还显示成旧的 60°。

参数已经同步到 100°。这个改动不需要 C++ 编译，但需要重新启动 Gazebo/robot description 和 FUEL，单独重启探索节点可能拿不到新的 Gazebo 相机模型。

已把深度相机水平视角改成 **100°**，并同步了算法侧参数。

改动包括：

- [gazebo.xacro](/home/uestc/tzb/uestc-challenge2026/src/unitree_guide/unitree_ros/robots/a1_description/xacro/gazebo.xacro:458)

```xml
<horizontal_fov>${100.0*3.14/180.0}</horizontal_fov>
```

- [fuel_dog_visual_exploration.launch](/home/uestc/tzb/uestc-challenge2026/src/fuel/fuel_dog_adapter/launch/fuel_dog_visual_exploration.launch:39)

```xml
<arg name="fx" default="268.512"/>
<arg name="fy" default="268.512"/>
```

- FUEL 可见性判断 / 视锥参数：

```xml
<param name="perception_utils/top_angle" value="0.729388"/>
<param name="perception_utils/left_angle" value="0.872665"/>
<param name="perception_utils/right_angle" value="0.872665"/>
```

含义：

- 水平视角：`left + right = 50° + 50° = 100°`
- 垂直视角：约 `83.6°`

也同步改了 `rviz_fuel_dog.launch`，避免 RViz 里红色视锥还是旧的 60°。

注意：这个需要重启 Gazebo/机器人模型和 FUEL 才会完全生效。视角变宽后，如果又出现 frontier 穿墙或墙外 frontier，可以再适当减小 `sdf_map/max_ray_length` 或 `perception_max_dist`。


---

## 67. [frontier/viewpoint / 探索范围] 现在有一个bug，四足狗往回探索进房间时，误选择了y<9范围的viewpoint，于是四足狗走到y<9范围范围的时候，原本还没探索的房间的viewpoint也一起消失。我想到了一个解决方案：四足狗跨过

**问题**

现在有一个bug，四足狗往回探索进房间时，误选择了y<9范围的viewpoint，于是四足狗走到y<9范围范围的时候，原本还没探索的房间的viewpoint也一起消失。我想到了一个解决方案：四足狗跨过y<9范围后，不仅要恢复x的探索范围，还需要将y的探索范围变为[9,50]，是否合理

**回答**

合理，而且这是一个很直接有效的方案。

你现在的问题本质是：**动态 box 只收窄了 x，没有收窄 y**。所以当四足狗往回走、探索范围已经扩大后，`y<9` 区域仍然属于合法探索 box，FUEL 仍可能把那里的 frontier/viewpoint 当成候选目标。然后它一旦往回靠近这些区域，地图更新会触发 frontier 重新计算，导致原本还没探索的房间候选被覆盖/移除/重排，看起来就是“房间 viewpoint 一起消失”。

把探索范围改成：

```text
x: 恢复宽范围
y: 限制为 [9, 50]
```

是合理的，因为你的真实意图是：

```text
过了 y=9 之后，只探索 y>=9 的房间区域，不再关心 y<9 的左右区域
```

这样做的效果：

- `y<9` 的 frontier 不再进入合法探索范围
- `y<9` 的 viewpoint 不会被选中
- 往回经过时不会把低 y 区域重新加入探索目标
- 二楼/三楼进走廊后的探索会更稳定

实现上应该改 `dynamic_exploration_box.py`，让它在扩宽 x 的同时，也更新：

```text
box_min_y = 9.0
box_max_y = 50.0
```

也就是状态切换成 after/expanded 后：

```text
box_min_x = -25
box_max_x = 25
box_min_y = 9
box_max_y = 50
```

如果之后又需要回收范围，则切回：

```text
box_min_x = 原始值
box_max_x = 原始值
box_min_y = 原始值
box_max_y = 原始值
```

但你这个需求听起来更像是：**跨过 y=9 后，不再允许 y<9 参与探索**，那就不要再恢复 `box_min_y` 到原始值，而是保持 `box_min_y=9`。

要注意一点：如果 return-home 或去电梯需要经过 `y<9`，这个 box 限制可能会影响 return-home 的 A* 搜索。如果 return-home 目标也在 `y>=9` 或者你回电梯不依赖 FUEL 探索 box，那没问题；如果需要回到 `y<9`，就要在 return-home 状态临时放开 y 范围。


---

## 68. [探索范围] 我们是需要回到y<9的

**问题**

我们是需要回到y<9的

**回答**

那就不能一直把 `y_min` 固定成 `9`。

更合理的是：

- **探索阶段**：跨过 `y=9` 后，把 box 收成 `y >= 9`
- **回程阶段**：恢复 `y < 9` 可探索/可规划的范围

否则四足狗回电梯、回到一楼时，A* 可能直接找不到路，或者 frontier/goal 被你自己过滤掉。

所以你的目标应该是：

```text
探索时：不再看 y<9
回程时：允许 y<9
```

这比“永远限制 y>=9”更符合实际流程。

更稳一点的做法有两种：

1. **按状态切换 box**
   - `exploration` 状态：`y_min = 9`
   - `return_home` 状态：恢复原始 `y_min`

2. **只对 frontier 过滤，不限制导航 box**
   - 导航仍允许去 `y<9`
   - 但 frontier/viewpoint 生成时忽略 `y<9`
   - 这样回程更安全

如果你现在的主要问题是“回去的时候还想继续探索 y<9 的旧区域”，那最直接的是：

- **探索时收窄 y**
- **回程时放开 y**

这才是和你流程一致的。


---

## 69. [探索范围] 我现在需要改变的只有一个过程，那就是四足狗通过dynamic_box_shrink_y这个位置后，不仅是x的范围从[-1.6,1.6]变为[-25,25]，还需要y的范围从[-2.5,50]变为[dy

**问题**

我现在需要改变的只有一个过程，那就是四足狗通过dynamic_box_shrink_y这个位置后，不仅是x的范围从[-1.6,1.6]变为[-25,25]，还需要y的范围从[-2.5,50]变为[dynamic_box_shrink_y,50]，其他一切都不变

**回答**

明白：只改 dynamic box 收窄/扩宽那一刻的 box 更新逻辑，跨过 `dynamic_box_shrink_y` 后把 `box_min_y` 也抬到这个阈值，其它流程不动。我先看一下当前脚本和 launch 参数。

当前脚本只写 `/sdf_map/box_min_x` 和 `/sdf_map/box_max_x`，所以我会扩展它，让 expanded 状态同时写 `/sdf_map/box_min_y=shrink_y`、`/sdf_map/box_max_y=原 box_max_y`；shrunk 状态恢复原 y 范围。先确认 SDF map 动态读取这些参数的名字。

SDF map 会动态读取 `sdf_map/box_min_y` 和 `sdf_map/box_max_y`，所以脚本只要把这两个参数一起写进去就行。我现在改脚本和 launch 参数传递。

脚本已支持 y 范围动态切换；我再把 launch 里的原始 y 和 after y 参数传进去，其中 after 的 `box_min_y` 就用 `dynamic_box_shrink_y`。

我跑一下 Python 语法检查，确认脚本没问题；launch 这边也简单 grep 看参数是否齐全。

语法检查通过了。我顺手把 launch 的缩进整理一下；这里逻辑是：仍按原来的 `expand_y/shrink_y` 滞回触发，不改变触发流程，只是在 expanded 后把 `box_min_y` 设成 `dynamic_box_shrink_y`。

已实现你说的这个变化。

现在 dynamic box 的行为是：

初始：

```text
x: [-1.6, 1.6]
y: [-2.5, 50]
```

expanded 后：

```text
x: [-25, 25]
y: [dynamic_box_shrink_y, 50]
```

对应当前默认值就是：

```text
x: [-25, 25]
y: [9.0, 50]
```

修改位置：

- [dynamic_exploration_box.py](/home/uestc/tzb/uestc-challenge2026/src/fuel/fuel_dog_adapter/scripts/dynamic_exploration_box.py)
- [fuel_dog_visual_exploration.launch](/home/uestc/tzb/uestc-challenge2026/src/fuel/fuel_dog_adapter/launch/fuel_dog_visual_exploration.launch)

注意：我没有改变原来的触发机制，仍然是：

- `y >= dynamic_box_expand_y` 时进入 expanded
- `y <= dynamic_box_shrink_y` 时恢复 shrunk

只是 expanded 状态下，除了改 x，也同时改 y 范围。脚本语法检查已通过。


---

## 70. [重规划 / 探索范围 / 多楼层] 现在改完之后，我们在探索完要回家时，显示plan fail没有路径生成，因为这时候我们的y没有小于9，所以y的范围没有变回去

**问题**

现在改完之后，我们在探索完要回家时，显示plan fail没有路径生成，因为这时候我们的y没有小于9，所以y的范围没有变回去

**回答**

对，这就是当前 y 收窄的副作用：return-home 规划发生在机器人还没退到 y<9 之前，所以 box 没恢复，A* 被限制在 y>=9 里找不到回家路径。我给 dynamic box 加一个 `/planning/return_home_start` 监听，一进入返航就立刻恢复原始范围。

脚本已经会在返航开始时恢复原始 box。我再把 launch 参数传进去，使用你现有的 `/planning/return_home_start`。

我跑一下脚本语法检查，确保这个回调加得没问题。

对，问题就是这个：返航规划是在机器人还处于 `y>9` 时触发的，而 dynamic box 只有等机器人走到 `y<=9` 才恢复，所以 A* 当时被限制在：

```text
y: [9, 50]
```

自然找不到回到 `y<9` 的路径。

我已经修了：`dynamic_exploration_box.py` 现在会监听：

```text
/planning/return_home_start
```

一旦 FUEL 进入 return-home，就立刻恢复原始 box：

```text
x: [-1.6, 1.6]
y: [-2.5, 50]
```

而不是等机器人实际走到 `y<9`。

也在 `fuel_dog_visual_exploration.launch` 里传了：

```xml
<param name="return_home_start_topic" value="/planning/return_home_start"/>
```

脚本语法检查已通过。重启 mission 后，返航规划应该不会再因为 y box 没恢复而 `plan fail`。


---

## 71. [A*/B 样条 / 多楼层] 现在主要是一旦 FUEL 进入 return-home，就立刻恢复原始 box，这时候四足狗还在房间在[-1.6,1.6]外，这样A*还是生成不了路径，你有什么解决方案吗？先别改源码

**问题**

现在主要是一旦 FUEL 进入 return-home，就立刻恢复原始 box，这时候四足狗还在房间在[-1.6,1.6]外，这样A*还是生成不了路径，你有什么解决方案吗？先别改源码

**回答**

明白。现在的问题是：**return-home 一触发就恢复成原始窄 x box `[-1.6, 1.6]`，但机器人此刻还在房间里，x 可能已经在 `[-1.6, 1.6]` 外，所以起点直接落在 box 外，A* 当然无路可走。**

更合理的方案不是“立刻完全恢复原始 box”，而是把 return-home 拆成两个阶段：

**方案 A：返航开始时只恢复 y，不恢复 x**
return-home 触发后：

```text
x: 继续保持 [-25, 25]
y: 恢复 [-2.5, 50]
```

这样 A* 可以从房间内部规划回走廊/电梯方向。  
等四足狗回到走廊中线附近，比如：

```text
x ∈ [-0.9, 0.9]
且 y <= 9.0
```

再恢复原始窄 x：

```text
x: [-1.6, 1.6]
y: [-2.5, 50]
```

这是我最推荐的。

**方案 B：return-home 阶段始终保持大 box**
进入 return-home 后直接：

```text
x: [-25, 25]
y: [-2.5, 50]
```

一直到 FUEL 结束。  
优点是最不容易 plan fail。缺点是返航时 A* 可能允许从侧边绕，但如果地图和障碍膨胀可靠，一般也能接受。

**方案 C：单独设置 return-home box**
增加一组参数：

```text
return_box_min_x = -25
return_box_max_x = 25
return_box_min_y = -2.5
return_box_max_y = 50
```

return-home 开始后切到这组 box，不再复用探索阶段的 before/after box。  
这是最清晰、可调性最好的工程方案。

你现在这个场景，我建议用 **方案 A**：

```text
探索阶段跨过 y=9：
  x: [-25,25]
  y: [9,50]

return-home 刚开始：
  x: [-25,25]
  y: [-2.5,50]

返航回到走廊中线附近：
  x: [-1.6,1.6]
  y: [-2.5,50]
```

这样既解决了房间内起点不在窄 x box 的问题，又避免返航后一直保持过宽探索范围。


---

## 72. [frontier/viewpoint / 探索范围] x ∈ [-0.9, 0.9] 且 y <= 9.0我记得我们之前不是还写了一个在从y>9走进这个范围，会消除范围内的viewpoint

**问题**

x ∈ [-0.9, 0.9]
且 y <= 9.0我记得我们之前不是还写了一个在从y>9走进这个范围，会消除范围内的viewpoint

**回答**

我看一下当前保留下来的 dynamic box 和 frontier 过滤逻辑，确认你说的“进入范围后消掉 viewpoint”现在具体是由哪一段触发的。

我先核一下当前脚本里的实际逻辑，避免只凭记忆回答。这个问题的关键在 `dynamic_exploration_box.py` 的状态切换，以及 frontier 过滤是不是跟着 box 参数走。

对，你记得没错，我们现在的 `dynamic_exploration_box.py` 里确实有这个逻辑：

```python
if x < self.gate_min_x or x > self.gate_max_x:
    return

if self.expanded and y <= self.shrink_y:
    set_box(before ...)
```

对应参数就是：

```xml
dynamic_box_gate_min_x = -0.9
dynamic_box_gate_max_x = 0.9
dynamic_box_shrink_y = 9.0
```

也就是：只有四足狗进入 `x ∈ [-0.9, 0.9]` 这个走廊中线范围，并且 `y <= 9.0` 时，才会把 box 从扩宽状态恢复回原始范围。

而 viewpoint/frontier 会消失，是因为 `frontier_finder.cpp` 里会按当前 box 过滤：

```cpp
filterViewpointsInCurrentBox(*iter);
if (!sdf_map_->isInBox(iter->average_) || iter->viewpoints_.empty()) {
  erase...
}
```

所以 box 一旦收窄，范围外的 viewpoint 会在下一次 frontier 更新时被删掉。

但现在的问题是：我们后来加了 `/planning/return_home_start` 回调，它一收到 return-home 事件就立刻恢复原始 box：

```python
return_home_start_callback -> set_box(before ...)
```

这会绕过“必须进入 `x ∈ [-0.9,0.9] 且 y<=9` 才收窄”的逻辑。于是四足狗还在房间里、`x` 还在 `[-1.6,1.6]` 外时，box 已经提前收窄，A* 就可能找不到回家的路径。

所以结论是：

原来的“从 `y>9` 走回 `x ∈ [-0.9,0.9] 且 y<=9` 后消除范围外 viewpoint”的逻辑还在；  
现在真正冲突的是 `return_home_start_callback` 恢复得太早。

更合理的策略是 return-home 开始时不要直接恢复窄 x，而是先恢复 y 范围、保持 x 仍然宽，例如：

```text
return-home 开始:
x = [-25, 25]
y = [-2.5, 50]

等机器人真正回到:
x ∈ [-0.9, 0.9] 且 y <= 9.0

再恢复:
x = [-1.6, 1.6]
y = [-2.5, 50]
```

这样既能让它从房间里规划回走廊，又能在回到走廊后清掉左右房间里的 viewpoint。


---

## 73. [探索范围] 修改当y>9时，会将y的探索范围设置为【9，50】这个y参数在哪里修改

**问题**

修改当y>9时，会将y的探索范围设置为【9，50】这个y参数在哪里修改

**回答**

这个参数在 [fuel_dog_visual_exploration.launch](/home/uestc/tzb/uestc-challenge2026/src/fuel/fuel_dog_adapter/launch/fuel_dog_visual_exploration.launch:22) 里改：

```xml
<arg name="dynamic_box_shrink_y" default="9.0"/>
```

下面这里把它传给动态 box 脚本，作为扩宽后的 `box_min_y_after`：

```xml
<param name="box_min_y_after" value="$(arg dynamic_box_shrink_y)"/>
```

所以现在 `y>9` 后，探索范围变成 `[9, box_max_y]`，本质就是因为：

```xml
dynamic_box_shrink_y = 9.0
dynamic_box_max_y_after = $(arg box_max_y)
```

如果你想改成 `[8.5, 50]`，就改：

```xml
<arg name="dynamic_box_shrink_y" default="8.5"/>
```

如果想改上界，则改：

```xml
<arg name="box_max_y" default="50.0"/>
```

或者单独改：

```xml
<arg name="dynamic_box_max_y_after" default="50.0"/>
```


---

## 74. [探索范围] 我们现在不想让这个修改y的探索范围和这个动态调整范围相关联。比如就是说y>8.8时我的探索范围可以是[y,50]，这个y是可以我们自己设置的值

**问题**

我们现在不想让这个修改y的探索范围和这个动态调整范围相关联。比如就是说y>8.8时我的探索范围可以是[y,50]，这个y是可以我们自己设置的值

**回答**

明白，你想把“触发位置”和“扩宽后 y 下界”拆开：比如 `y > 8.8` 触发扩宽，但扩宽后的探索范围可以单独设成 `[9.0, 50]` 或 `[8.5, 50]`，不再绑定 `dynamic_box_shrink_y`。我先看一下当前 launch 参数名，尽量用最小改动加一个独立参数。

当前确实是这一行把扩宽后的 y 下界绑到了 `dynamic_box_shrink_y`：`box_min_y_after = $(arg dynamic_box_shrink_y)`。我给它单独加一个 `dynamic_box_min_y_after`，以后触发 y、收窄 y、扩宽后的 y 下界就可以分别调。

参数已经拆开了。现在我检查一下 launch 片段，确认名字和传参一致。

已拆开。现在在 [fuel_dog_visual_exploration.launch](/home/uestc/tzb/uestc-challenge2026/src/fuel/fuel_dog_adapter/launch/fuel_dog_visual_exploration.launch:22) 里有三个独立参数：

```xml
<arg name="dynamic_box_expand_y" default="9.0"/>
<arg name="dynamic_box_shrink_y" default="8.8"/>
<arg name="dynamic_box_min_y_after" default="9.0"/>
```

含义现在是：

```text
dynamic_box_expand_y      什么时候扩宽，例如 y >= 8.8
dynamic_box_shrink_y      什么时候收窄，例如 y <= 8.8
dynamic_box_min_y_after   扩宽后 y 探索范围下界，例如 9.0
```

下面传参也改成了：

```xml
<param name="box_min_y_after" value="$(arg dynamic_box_min_y_after)"/>
```

所以如果你想要：

```text
y > 8.8 时触发扩宽
扩宽后探索范围 y = [9.0, 50.0]
```

可以这样设：

```xml
<arg name="dynamic_box_expand_y" default="8.8"/>
<arg name="dynamic_box_min_y_after" default="9.0"/>
<arg name="dynamic_box_max_y_after" default="50.0"/>
```

我也跑了 `git diff --check`，没有格式问题。


---

## 75. [探索范围] 对于房间门左右对称的问题，是否可以优先探索左边？比如说优先探索x<0的部分

**问题**

对于房间门左右对称的问题，是否可以优先探索左边？比如说优先探索x<0的部分

**回答**

我先看一下 FUEL 里“选哪个 viewpoint / frontier”的打分点在哪。这样能判断是能直接用参数偏置，还是必须在 frontier 评分里加一条偏好。

可以，而且更建议做成“偏好”而不是硬限制。

现在 FUEL 选 viewpoint 的核心是在 [fast_exploration_manager.cpp](/home/uestc/tzb/uestc-challenge2026/src/fuel/fuel_planner/exploration_manager/src/fast_exploration_manager.cpp) 里算 `tmp_cost`，然后选最小值；它本身没有“优先左边”的现成参数。  
所以如果你想“优先探索 `x < 0` 的部分”，一般有两种做法：

1. 软偏好  
   给 `x > 0` 的 viewpoint/ frontier 额外加一点代价，让算法更倾向先选左边，但右边仍然保留为备选。

2. 硬偏好  
   在某一阶段直接过滤掉 `x > 0` 的 viewpoint，只让左边先参与决策。这个更强，但容易把必要路径也卡掉。

如果你的目标是“门左右对称时，先稳定走左边”，我建议软偏好。  
最合适的加点位置是：
- `fast_exploration_manager.cpp` 里 viewpoint 最小代价选择处
- 或 `frontier_finder.cpp` 里对 viewpoints 排序/筛选时加偏置

一句话：能做，但现在不是靠 launch 参数直接配出来的，要在选点打分里加一个 `x < 0` 的偏置。


---

## 76. [frontier/viewpoint / 避障膨胀] 现在发现一个问题，偶尔会突然在障碍物中出现一个frontier和viewpoint，即使之前已经探索过这个地方了，分析原因

**问题**

现在发现一个问题，偶尔会突然在障碍物中出现一个frontier和viewpoint，即使之前已经探索过这个地方了，分析原因

**回答**

我先从 frontier 生成和地图更新两条线看：一个是“为什么已探索区域会重新变成 frontier”，另一个是“为什么 viewpoint 会落到障碍物里”。这类问题通常不是单一参数，而是占据栅格、膨胀地图、动态 box 和 frontier 缓存一起造成的。

我再看一下 SDF 地图更新的细节，尤其是“命中/空闲”的 log-odds 更新和膨胀缓存刷新。这个现象如果是偶发的，很可能和深度点云噪声、raycast 清空、或者动态 box 后重新扫描有关。

这个现象大概率不是“FUEL 主动想探索障碍物”，而是地图里某些栅格被短暂判断成了：

```text
FREE 且旁边有 UNKNOWN
```

于是被当成 frontier；然后 viewpoint 采样时又认为该点附近没有膨胀障碍，于是生成了 viewpoint。

代码链路是这样的：

1. frontier 生成条件在 `frontier_finder.cpp`

```cpp
knownfree(cur) && isNeighborUnknown(cur)
```

也就是：当前 voxel 是 `FREE`，邻居里有 `UNKNOWN`，就可能成为 frontier。

2. viewpoint 生成条件在 `sampleViewpoints()`

```cpp
!isInBox(sample_pos)
getInflateOccupancy(sample_pos) == 1
isNearUnknown(sample_pos)
```

只要这些检查都通过，viewpoint 就会被认为可用。

所以“障碍物中突然出现 frontier/viewpoint”的根因通常是：障碍物附近的某些 voxel 在当前 SDFMap 里没有被稳定识别为 occupied/inflated occupied。

主要可能原因有几个：

1. 深度相机噪声或穿透导致障碍物内部被 raycast 清成 free  
   `sdf_map.cpp` 里 raycast 会把相机到深度点之间的 voxel 标成 miss/free：

```cpp
while (caster_->nextId(idx))
  setCacheOccupancy(toAddress(idx), 0);
```

如果深度点偶尔落到墙后、障碍物后，raycast 就可能把障碍物内部或墙后空间短暂清成 free。这样就会产生假 frontier。

2. log-odds 地图会被反复 hit/miss 更新，不是一次占用就永久占用  
   当前占据判断是：

```cpp
occ > p_occ_log -> OCCUPIED
否则 -> FREE
```

如果某个障碍物 voxel 被多次 miss 更新，概率可能从 occupied 降到 free。之前探索过不代表永远保持 occupied。

3. 膨胀缓存是局部刷新，偶发情况下 inflate 状态可能滞后  
   `occupancy_buffer_inflate_` 是在局部更新范围里先清零再重新膨胀。如果某个障碍物附近局部更新边界不完整，可能短时间出现“原始 occupancy 还没稳定、inflate 也没覆盖”的空洞。

4. frontier 本身就是“free 与 unknown 的边界”  
   如果障碍物内部或墙后有一小块 unknown，而障碍物表面附近被误清成 free，就会刚好满足 frontier 条件，所以看起来像“障碍物里长出一个 frontier”。

5. 你们扩大 FOV、增大探测距离后，这种现象更容易出现  
   视野越大、raycast 越远，深度边缘噪声、墙角误差、墙后点云错误越容易影响地图。之前你们遇到过 frontier 穿墙，也是类似机制。

优先排查顺序：

1. 在 RViz 同时看 `/occupancy` 或 inflated map，确认那个 frontier 位置在地图里到底是 `FREE` 还是 `OCCUPIED`。
2. 如果它在地图里确实是 free，说明是深度/raycast 把障碍物清空了。
3. 如果 occupancy 是 occupied，但 viewpoint 还在里面，说明 `occupancy_buffer_inflate_` 或 viewpoint 检查有问题。
4. 如果只出现一帧然后消失，基本是深度噪声或 log-odds 短暂波动。
5. 如果稳定存在，说明地图参数或障碍物模型/深度相机数据有系统性穿透。

最相关的参数是：

```xml
sdf_map/p_hit
sdf_map/p_miss
sdf_map/p_occ
sdf_map/max_ray_length
map_ros/depth_filter_maxdist
sdf_map/obstacles_inflation
frontier/min_candidate_clearance
frontier/min_visib_num
```

如果要抑制这种假 frontier，优先考虑：
- 减小 `max_ray_length` / `depth_filter_maxdist`
- 增大 `obstacles_inflation`
- 增大 `frontier/min_candidate_clearance`
- 增大 `frontier/min_visib_num`
- 让障碍物更难被 miss 清空：减弱 `p_miss` 或提高占据稳定性

一句话总结：  
它不是 viewpoint 选择策略的问题，而是 SDFMap 某些障碍物附近 voxel 被短暂更新成了 `FREE + UNKNOWN neighbor`，frontier 检测就会把它当成新的可探索边界。


---

## 77. [frontier/viewpoint / 避障膨胀] frontier在障碍物里，viewpoint不一定在，可能在外面，但是四足狗看不见障碍物里的frontier，这个frontier也不会消掉

**问题**

frontier在障碍物里，viewpoint不一定在，可能在外面，但是四足狗看不见障碍物里的frontier，这个frontier也不会消掉

**回答**

对，这个现象比“viewpoint 在障碍物里”更麻烦一些。

现在 FUEL 的逻辑是：

```text
frontier = FREE voxel + 邻居 UNKNOWN
viewpoint = 从外面能看到这个 frontier 的采样点
```

所以即使 frontier 视觉上在障碍物里，只要地图里那个位置被误判成 `FREE`，并且旁边还是 `UNKNOWN`，它就会一直存在。

关键点是：**frontier 不会因为四足狗“看不见它”自动消失**。它只有在地图更新后不再满足：

```cpp
knownfree(idx) && isNeighborUnknown(idx)
```

才会被删掉。也就是说，如果障碍物里的那个 frontier 实际上没有被深度相机重新观测到，地图状态就不会改变，它就会卡在那里。

这类卡住的原因通常是：

1. 障碍物内部被误清成 free  
   raycast 曾经穿过障碍物或深度点异常，导致障碍物内部某些 voxel 变成 `FREE`。

2. frontier 在障碍物内部，但没有新的深度射线能打到那里  
   它没有被重新标成 occupied，也没有被完全探索成 free，所以状态长期保持。

3. viewpoint 在外面，但真实视线被障碍物挡住  
   FUEL 用的是当前 SDFMap 判断可见性。如果地图里障碍物没有稳定占用，它会误以为 viewpoint 能看到这个 frontier；但真实深度相机看不到，于是 frontier 永远不被覆盖。

所以你观察到的现象是合理的：  
**frontier 在障碍物里，viewpoint 在外面，四足狗实际看不见它，因此 frontier 不消失。**

解决方向有三类：

1. 从地图源头减少假 free  
   调 `sdf_map/max_ray_length`、`map_ros/depth_filter_maxdist`、`sdf_map/p_miss`、`sdf_map/p_hit`，避免 raycast 把障碍物内部清空。

2. frontier 生成时更严格  
   不仅要求 frontier cell 是 `FREE`，还要求它周围不能太靠近 occupied / inflated occupied。这样可以过滤掉“贴在障碍物内部或边界上的假 frontier”。

3. 增加 stale frontier 清理机制  
   如果某个 frontier 多次被选中或长期存在，但实际地图没有变化、没有被覆盖，就把它加入黑名单或 dormant，不再作为目标。

我更推荐第 2 + 第 3 一起做。  
第 2 解决“障碍物里的假 frontier”；第 3 解决“即使漏掉了，也不要一直卡住探索”。


---

## 78. [frontier/viewpoint / 避障膨胀] <param name="frontier/obstacle_max_fraction" value="0.35" type="double"/> 这个参数有什么作用，解释一下

**问题**

<param name="frontier/obstacle_max_fraction" value="0.35" type="double"/>
这个参数有什么作用，解释一下

**回答**

`frontier/obstacle_max_fraction` 是我们刚加的“障碍物附近 frontier 过滤阈值”。

它配合这个参数一起用：

```xml
<param name="frontier/obstacle_clearance" value="0.12" type="double"/>
<param name="frontier/obstacle_max_fraction" value="0.35" type="double"/>
```

含义是：

```text
检查一个 frontier cluster 里的 cell。
如果某个 cell 在 obstacle_clearance 距离内靠近 inflated obstacle，
就认为这个 cell 是 near obstacle。

near obstacle 的 cell 数量 / frontier 总 cell 数量 = fraction

如果 fraction > obstacle_max_fraction
这个 frontier 就被过滤掉。
```

当前值 `0.35` 表示：

```text
如果一个 frontier 中超过 35% 的 cell 靠近障碍物，
就认为这个 frontier 很可能是障碍物内部/墙边假 frontier，
直接删除。
```

调参方向：

```text
值越小：过滤越严格，更容易删掉靠近障碍物的 frontier
值越大：过滤越宽松，更不容易误删门口、墙边、窄通道 frontier
```

例如：

```xml
0.20  更严格，假 frontier 更容易被删，但可能误删门边 frontier
0.35  当前折中
0.50  更宽松，适合门口/窄走廊 frontier 被误删时
```

如果你发现房间门口、墙边正常 frontier 消失，就把它调大。  
如果障碍物里的假 frontier 还很多，就把它调小。


---

## 79. [frontier/viewpoint / 避障膨胀] <param name="frontier/obstacle_clearance" value="0.12" type="double"/>;这个参数怎么解释

**问题**

<param name="frontier/obstacle_clearance" value="0.12" type="double"/>;这个参数怎么解释

**回答**

`frontier/obstacle_clearance` 是判断 frontier cell 是否“靠近障碍物”的距离阈值，单位是米。

当前：

```xml
<param name="frontier/obstacle_clearance" value="0.12" type="double"/>
```

意思是：

```text
对 frontier 里的每个 cell，检查它周围 0.12m 范围内有没有 inflated obstacle。
如果有，就认为这个 cell 靠近障碍物。
```

然后再配合：

```xml
<param name="frontier/obstacle_max_fraction" value="0.35" type="double"/>
```

一起判断整个 frontier 是否要删除：

```text
如果超过 35% 的 frontier cell 在 0.12m 范围内靠近障碍物，
就删除这个 frontier。
```

调参方向：

```text
obstacle_clearance 越大：
检查范围越宽，过滤越严格，更容易删掉墙里/障碍物里的假 frontier。
但也更可能误删门边、墙边、窄走廊的正常 frontier。

obstacle_clearance 越小：
检查范围越窄，过滤越宽松，不容易误删正常 frontier。
但障碍物里的假 frontier 可能还会留下。
```

当前 `0.12m` 比较保守。  
如果假 frontier 还在障碍物里出现，可以试：

```xml
0.15
0.20
```

如果门口 frontier 被误删，就降回：

```xml
0.08
0.10
```


---

## 80. [frontier/viewpoint] 我们当前的参数已经调整的很宽松了，但是进入们口时，前面的的frontier依旧没有viewpoint

**问题**

我们当前的参数已经调整的很宽松了，但是进入们口时，前面的的frontier依旧没有viewpoint

**回答**

我先把当前 launch 里的 frontier/viewpoint 参数和 `sampleViewpoints()` 的过滤条件对齐看一下。门口“有 frontier 但没有 viewpoint”通常不是 frontier 被删，而是候选 viewpoint 在采样阶段被全部过滤掉。

我看到当前障碍物过滤确实已经很宽松：`clearance=0.08`、`max_fraction=0.7`。所以门口前方 frontier 没 viewpoint，主要嫌疑已经不是我们刚加的障碍物过滤，而是 `sampleViewpoints()` 里另外两个条件：候选点靠近 UNKNOWN 被拒，或者可见 cell 数不够。

这个现象说明：**frontier 本身通过了，但 `sampleViewpoints()` 采样出来的候选 viewpoint 全部被过滤掉了。**

当前生成 viewpoint 的关键条件在 `frontier_finder.cpp`：

```cpp
if (!isInBox(sample_pos) ||
    getInflateOccupancy(sample_pos) == 1 ||
    isNearUnknown(sample_pos))
  continue;
```

以及后面：

```cpp
int visib_num = countVisibleCells(sample_pos, avg_yaw, cells);
if (visib_num > min_visib_num_)
  frontier.viewpoints_.push_back(vp);
```

你现在门口前方 frontier 没 viewpoint，最可能是这几个原因：

1. `frontier/min_candidate_clearance = 0.12` 仍然会拒绝靠近 UNKNOWN 的候选点  
   地图分辨率是 `0.1m`，所以：

   ```cpp
   floor(0.12 / 0.1) = 1
   ```

   实际会检查候选 viewpoint 周围 `±1` 个栅格。门口前方正好 unknown 很多，所以候选点很容易被 `isNearUnknown()` 删掉。

2. `min_visib_num=3` 实际要求至少 4 个可见 cell  
   因为代码是：

   ```cpp
   if (visib_num > min_visib_num_)
   ```

   不是 `>=`。  
   所以 `min_visib_num=3` 时，必须 `visib_num >= 4` 才能生成 viewpoint。门口 narrow frontier 很容易只有 1-3 个 cell 可见，于是没有 viewpoint。

3. `countVisibleCells()` 对 UNKNOWN 很敏感  
   它从 frontier cell 到 viewpoint 做 raycast，只要中间遇到 UNKNOWN，就认为不可见：

   ```cpp
   if (getInflateOccupancy(idx) == 1 ||
       getOccupancy(idx) == SDFMap::UNKNOWN)
     visib = false;
   ```

   门口前面很多区域还没探索，raycast 很容易穿过 UNKNOWN，所以可见数量不足。

4. 候选半径 `[0.4, 1.0]` 在门口可能不合适  
   门口很窄时，围绕 frontier average 采样的圆可能大量落在墙、门框、未知区，导致没有合法 sample。

所以现在“参数已经宽松，但还是没 viewpoint”的核心不是障碍物过滤，而是：

```text
isNearUnknown 太严格
visib_num 阈值实际偏高
可见性 raycast 不允许穿 UNKNOWN
候选采样半径在门口不好落点
```

建议优先试这两个最小调整：

```xml
<arg name="frontier_min_candidate_clearance" default="0.05"/>
<arg name="frontier_min_visib_num" default="1"/>
```

注意 `min_visib_num=1` 实际要求 `visib_num >= 2`。

如果还不行，下一步就不是继续调宽参数了，而是改代码：把 `visib_num > min_visib_num_` 改成 `>=`，或者给门口阶段允许候选 viewpoint 靠近 UNKNOWN。


---

## 81. [视觉深度建图 / 探索范围] 当前的深度相机水平视野范围是100度，那么单目相机的视野范围是否和深度一致

**问题**

当前的深度相机水平视野范围是100度，那么单目相机的视野范围是否和深度一致

**回答**

我先看一下相机在 Gazebo xacro 里是怎么定义的：如果深度和彩色/单目是同一个 `<sensor>` 或同一组 camera 参数，那 FOV 才一致；如果是两个 sensor，各自有 `<horizontal_fov>`。

不一致。

当前代码里有两个相机：

1. **单目相机 `front_camera`**
   在 [gazebo.xacro](/home/uestc/tzb/uestc-challenge2026/src/unitree_guide/unitree_ros/robots/a1_description/xacro/gazebo.xacro:410)：

```xml
<sensor type="camera" name="camera">
  <camera name="head">
    <horizontal_fov>1.3962634</horizontal_fov>
```

`1.3962634 rad ≈ 80°`

所以单目相机水平视野是 **约 80 度**。

2. **深度相机 `real_sense`**
   在 [gazebo.xacro](/home/uestc/tzb/uestc-challenge2026/src/unitree_guide/unitree_ros/robots/a1_description/xacro/gazebo.xacro:452)：

```xml
<sensor type="depth" name="real_sense">
  <camera>
    <horizontal_fov>${100.0*3.14/180.0}</horizontal_fov>
```

所以深度相机水平视野是 **约 100 度**。

结论：

```text
深度相机 HFOV = 100°
单目相机 HFOV = 80°
当前两者不一致
```

如果你想让单目相机也变成 100°，需要把单目相机这里改成：

```xml
<horizontal_fov>${100.0*3.14/180.0}</horizontal_fov>
```

或者直接写近似值：

```xml
<horizontal_fov>1.74533</horizontal_fov>
```


---

## 82. [视觉深度建图 / frontier/viewpoint / 避障膨胀] 之前的假frontier问题，你分析过“深度相机噪声或穿透导致障碍物内部被 raycast 清成 free”的原因，能不能从过滤深度相机噪声这个角度去解决

**问题**

之前的假frontier问题，你分析过“深度相机噪声或穿透导致障碍物内部被 raycast 清成 free”的原因，能不能从过滤深度相机噪声这个角度去解决

**回答**

可以，这个方向比“直接删 frontier”更干净，因为它从源头减少错误的 free/unknown 更新。我先看一下当前深度图进入 SDFMap 前有哪些滤波入口，这样方案能落到具体参数和代码位置。

可以，而且这是更推荐的方向。假 frontier 的根源是：**错误深度点或深度空洞让 raycast 把障碍物内部清成 free**。所以从深度输入端过滤，比后面硬删 frontier 更安全。

当前深度进入地图的链路是：

```text
/real_sense/depth/image_raw
 -> map_ros.cpp::proessDepthImage()
 -> 生成 point_cloud_
 -> sdf_map.cpp::inputPointCloud()
 -> raycast 清 free / 标 occupied
 -> frontier_finder 生成 frontier
```

最应该改的位置是：

```cpp
src/fuel/fuel_planner/plan_env/src/map_ros.cpp
MapROS::proessDepthImage()
```

当前这里只做了很基础的过滤：

```cpp
if (*row_ptr == 0 || depth > depth_filter_maxdist_)
  depth = depth_filter_maxdist_;
else if (depth < depth_filter_mindist_)
  continue;
```

这对墙边、门框、深度跳变处不够。建议加三类深度滤波：

**1. 深度跳变边缘过滤**

如果某个像素和周围像素深度差太大，说明它可能在物体边缘、墙边、门框边缘，容易造成穿透 ray。直接跳过这个点。

例如：

```text
当前 depth = 4.8m
邻域里有很多点是 1.2m
=> 这个点可能是边缘穿透/空洞，不用于建图
```

这个对“墙后假 free”最有用。

**2. 邻域一致性过滤**

一个深度点如果周围没有足够多相近深度的邻居，就认为是孤立噪声点，丢弃。

```text
周围 3x3 或 5x5 内，至少 N 个像素深度和当前点差 < 0.15m
否则丢弃
```

这能去掉孤立飞点。

**3. 无效深度不要简单当成 maxdist 点**

现在无效/超远点容易导致长 ray 清空前方空间。更保守的方式是：

```text
depth == 0 或 depth > maxdist
不要生成点
或者只允许短距离清 free，不让它穿过障碍物清到远处
```

对于你们这个“假 frontier 在障碍物内部”的问题，我最推荐优先做：

```text
深度边缘过滤 + 邻域一致性过滤
```

参数可以设计成：

```xml
<param name="map_ros/enable_depth_edge_filter" value="true"/>
<param name="map_ros/depth_edge_threshold" value="0.25"/>
<param name="map_ros/depth_support_radius" value="1"/>
<param name="map_ros/depth_support_min_count" value="4"/>
<param name="map_ros/depth_support_threshold" value="0.15"/>
```

含义：

```text
depth_edge_threshold:
当前像素和邻域差超过 0.25m，认为在深度边缘，跳过。

depth_support_radius=1:
检查 3x3 邻域。

depth_support_min_count=4:
至少 4 个邻居支持当前深度。

depth_support_threshold=0.15:
邻居和当前深度差小于 0.15m 才算支持。
```

这样不会像之前的 frontier 过滤那样误删门口 frontier，而是减少错误 raycast 进入地图。  
缺点是：深度边缘会更保守，墙边/门框边缘建图可能稍微慢一点，但对四足狗探索更稳。


---

## 83. [重规划] <param name="fsm/thresh_replan1" value="0.3" type="double"/> <param name="fsm/thresh_replan2" value=

**问题**

<param name="fsm/thresh_replan1" value="0.3" type="double"/>
    <param name="fsm/thresh_replan2" value="1.5" type="double"/>
    <param name="fsm/thresh_replan3" value="5.0" type="double"/>
    <param name="fsm/replan_time" value="0.200" type="double"/>
这四个参数代表什么意思，怎么调

**回答**

这 4 个都是 FUEL FSM 里控制“什么时候触发重规划”的参数。

```xml
<param name="fsm/thresh_replan1" value="0.3"/>
<param name="fsm/thresh_replan2" value="1.5"/>
<param name="fsm/thresh_replan3" value="5.0"/>
<param name="fsm/replan_time" value="0.200"/>
```

大致含义是：

`fsm/replan_time`

重规划检查周期，单位秒。

```text
0.200 = 每 0.2 秒检查一次是否需要重规划
```

值越小，重规划越频繁，轨迹越容易抖、左右横跳；值越大，轨迹执行更稳定，但对新障碍或新 frontier 反应更慢。

对四足狗建议：

```xml
<param name="fsm/replan_time" value="0.5"/>
```

甚至可以试 `0.8 ~ 1.0`。

`fsm/thresh_replan1`

通常表示当前轨迹刚开始执行后，距离起点/当前位置太近时触发快速重规划的阈值。可以理解成“刚走出去多远以内，允许重新规划”。

值越小，越不容易刚开始就重规划。

你现在：

```xml
0.3
```

比较小，已经偏稳。

`fsm/thresh_replan2`

通常表示距离当前轨迹终点较近时，提前开始下一次规划的阈值。可以理解成“离目标还剩多远就开始准备下一段”。

值越大，越早重规划；值越小，越晚重规划。

四足狗如果还没执行多少就换轨迹，可以减小它，例如：

```xml
<param name="fsm/thresh_replan2" value="0.8"/>
```

`fsm/thresh_replan3`

通常是更大的安全/全局重规划距离阈值，和局部目标、全局目标或轨迹剩余长度判断有关。值越大，越容易提前触发全局层面的重新规划；值越小，越坚持当前轨迹。

你现在：

```xml
5.0
```

偏大，可能导致还没走完就提前重新选点/重规划。四足狗建议先降到：

```xml
<param name="fsm/thresh_replan3" value="2.0"/>
```

我建议你先试这一组：

```xml
<param name="fsm/thresh_replan1" value="0.3" type="double"/>
<param name="fsm/thresh_replan2" value="0.8" type="double"/>
<param name="fsm/thresh_replan3" value="2.0" type="double"/>
<param name="fsm/replan_time" value="0.5" type="double"/>
```

如果还是左右横跳，再把 `replan_time` 提到 `0.8`。如果反应太慢，再把 `thresh_replan2` 提回 `1.0 ~ 1.2`。


---

## 84. [避障膨胀] 目前我们的轨迹是有高度的，有时当遇到比较矮的障碍物或者球时，不会进行避障，然后据我了解，一般会有baselink,basefootprint坐标系，这两个坐标系一个是以机器人中心为坐标原点的坐标系，一

**问题**

目前我们的轨迹是有高度的，有时当遇到比较矮的障碍物或者球时，不会进行避障，然后据我了解，一般会有baselink,basefootprint坐标系，这两个坐标系一个是以机器人中心为坐标原点的坐标系，一个是以机器人中心投影到地面为坐标原点的坐标系。我们现在轨迹应该是以机器人中心为坐标原点的坐标系进行规划的，那如果把轨迹以机器人中心投影到地面为坐标原点的坐标系进行规划，能否解决这个问题。你评估一下这个方案的可行性

**回答**

这个方案值得评估，但我先把结论放前面：单纯把规划参考点从 `base_link` 换成 `base_footprint`，不一定能解决“矮障碍物不避障”，甚至可能引入新的高度不一致问题。我先快速看一下你们当前 FUEL 里轨迹 Z、地图 Z、膨胀半径是怎么接起来的，再按代码评估可行性。

我先按代码路径把这个问题钉一下：关键不是 `base_link/base_footprint` 这两个名字，而是 FUEL 实际在哪个 `z` 上做碰撞检查、B 样条优化和轨迹发布。这样评估会更靠谱。

从搜索结果看，当前工程已经不是“自由 3D 高度规划”了，而是主动把 A* 和 B 样条都压在 `fixed_z` 附近。也就是说问题更像是“用一个离地高度点代表整只四足狗做碰撞”，而不是单纯坐标系名字选错。

明白你的意思。结论先说：**把规划坐标从机器人中心改成 `base_footprint` 有一定可行性，但它不是最推荐的第一方案，也不能保证根治。你现在这个问题的根因更像是：规划用一个固定高度的点代表四足狗，而障碍膨胀没有在 Z 方向覆盖低矮障碍。**

我看了当前代码，关键点是：

[algorithm.xml](/home/uestc/tzb/uestc-challenge2026/src/fuel/fuel_planner/exploration_manager/launch/algorithm.xml) 里传入：

```xml
<param name="astar/fixed_z" value="$(arg fixed_z)" type="double"/>
<param name="optimization/fixed_z" value="$(arg fixed_z)" type="double"/>
```

而 [fuel_dog_visual_exploration.launch](/home/uestc/tzb/uestc-challenge2026/src/fuel/fuel_dog_adapter/launch/fuel_dog_visual_exploration.launch) 里现在是：

```xml
<arg name="fixed_z" default="0.30"/>
<arg name="z_margin" default="0.05"/>
```

也就是说，A* 和 B 样条大体都在 `z=0.30` 附近规划。

在 [astar2.cpp](/home/uestc/tzb/uestc-challenge2026/src/fuel/fuel_planner/path_searching/src/astar2.cpp) 里，开启 `fixed_z` 后，邻居点会被强行设成：

```cpp
if (use_fixed_z_) nbr_pos(2) = fixed_z_;
```

然后只检查这个高度上的占据：

```cpp
getInflateOccupancy(nbr_pos)
```

更关键的是，在 [sdf_map.h](/home/uestc/tzb/uestc-challenge2026/src/fuel/fuel_planner/plan_env/include/plan_env/sdf_map.h) 里，当前障碍膨胀是：

```cpp
/* ---------- XY only inflate, no Z ---------- */
for (int x = -step; x <= step; ++x)
  for (int y = -step; y <= step; ++y)
    pts[num++] = Eigen::Vector3i(pt(0) + x, pt(1) + y, pt(2));
```

也就是**只在 XY 平面膨胀，不在 Z 方向膨胀**。

所以如果一个球或者矮障碍物主要占据在 `z=0.05 ~ 0.20`，而轨迹碰撞检查在 `z=0.30`，那么它可能会认为 `z=0.30` 这一层是空的，于是规划直接从上方“穿过去”。这和你观察到的现象是吻合的。

**关于改成 base_footprint**
理论上，如果把轨迹点改成地面投影点，比如 `z=0.05` 或接近地面，那么低矮障碍会更容易被碰撞检查命中。

但风险也很大：

1. FUEL 的轨迹、viewpoint、frontier、A*、B 样条、控制器现在都默认用一个“机器人中心高度”的点在跑。

2. 如果直接把 `fixed_z` 改低，可能导致轨迹点贴地，B 样条优化和 ESDF 距离场会认为机器人在地面附近运动，容易和地面、台阶、噪声体素冲突。

3. 控制器最终还是控制四足狗的机体运动，不是真的控制 `base_footprint`。如果规划输出是地面点，但控制器按机体中心理解，就会出现 Z 语义不一致。

所以：**base_footprint 方案可行，但属于系统级改造，不适合直接小改。**

更推荐的方案是：

**方案 1：保持轨迹高度为机体中心 `fixed_z=0.30`，但把障碍在 Z 方向也膨胀。**

也就是不要只做 XY 膨胀，而是让低矮障碍在地图中向上膨胀到机器人身体高度附近。例如球在 `z=0.1`，膨胀后 `z=0.3` 也被认为不可通行。这样 A* 和 B 样条继续在 `z=0.30` 规划，但能避开低矮障碍。

**方案 2：给地面机器人加“垂直柱碰撞检查”。**

轨迹点仍然是 `(x, y, 0.30)`，但是判断这个点是否安全时，不只查 `z=0.30`，而是查一根柱子：

```text
x, y 固定
z 从 0.05 到 0.45
```

只要这根柱子里有占据，就认为这个 `(x,y)` 不可走。这个更符合四足狗的真实碰撞体，也比整体切换到 `base_footprint` 稳。

**我的建议**
不要优先改成 `base_footprint`。你们当前代码里真正暴露的问题是：**碰撞检查高度固定在 0.30，但障碍膨胀没有覆盖 Z 方向**。

优先级我建议：

1. 先实现 Z 向障碍膨胀，或者柱状碰撞检查。
2. 保持 `fixed_z=0.30`、`viewpoint_z=0.35` 不动。
3. 如果低矮障碍仍然漏，再考虑把 `box_min_z`、深度滤波、相机俯仰角、球体点云是否进入 SDF 一起查。
4. 最后才考虑完整切换到 `base_footprint` 语义。

简单说：**你的思路方向是对的，确实是在解决“轨迹高度和低障碍不相交”的问题；但工程上更稳的做法不是改参考坐标系，而是让规划点代表整只狗的占地体积。**


---

## 85. [避障膨胀] "实现 Z 向障碍膨胀，或者柱状碰撞检查" 这个方案难实现吗

**问题**

"实现 Z 向障碍膨胀，或者柱状碰撞检查"
这个方案难实现吗

**回答**

不难，属于**小到中等改动**，而且比整体切到 `base_footprint` 安全很多。

我建议优先做 **Z 向障碍膨胀**，因为当前代码已经有膨胀函数，只是现在写成了 **XY only inflate, no Z**。把它改成 XY+Z 膨胀，改动点集中在：

[sd f_map.h](/home/uestc/tzb/uestc-challenge2026/src/fuel/fuel_planner/plan_env/include/plan_env/sdf_map.h)

现在逻辑类似：

```cpp
for (int x = -step; x <= step; ++x)
  for (int y = -step; y <= step; ++y)
    pts[num++] = Eigen::Vector3i(pt(0) + x, pt(1) + y, pt(2));
```

也就是只扩 `x/y`，不扩 `z`。第一版可以加一个参数，比如：

```xml
<param name="sdf_map/obstacles_inflation_z" value="0.25"/>
```

然后让障碍在 Z 方向也膨胀若干格。这样低矮球体、矮障碍物只要被深度相机建进地图，就会在 `z=0.30` 这一层也变成障碍，A* 和 B 样条就更容易避开。

**柱状碰撞检查**也可行，但实现稍微更分散。要改 A*、B 样条优化里的碰撞/距离代价，甚至未来路径安全检查也要统一改。它语义更准确：检查机器人 `(x,y)` 下面一整根柱子是否安全。但第一版改动面更大，调参也多一点。

所以难度大概是：

```text
Z 向障碍膨胀：低难度，推荐先做
柱状碰撞检查：中等难度，更精确，但改动点更多
切换 base_footprint：高风险，不推荐第一版
```

我建议先做 **参数化 Z 膨胀**：默认可以设 `0.25m` 左右。你们现在 `fixed_z=0.30`，如果低障碍最高到 `0.10~0.15m`，向上膨胀 `0.20~0.30m` 基本就能让规划层看到它。


---

## 86. [避障膨胀] 做 参数化 Z 膨胀

**问题**

做 参数化 Z 膨胀

**回答**

我来做参数化版本：保留现有 XY 膨胀参数，再单独加一个 `sdf_map/obstacles_inflation_z` 控制 Z 方向膨胀高度。这样可以先温和调试，不会把原来的 XY 行为搅乱。

我准备改三个地方：`MapParam` 增加 Z 膨胀参数，`sdf_map.cpp` 读取参数并按 XY/Z 分别分配膨胀点，`algorithm.xml` 和四足狗 launch 暴露参数。默认值我设成 `0.0` 以保持旧行为，launch 里再给四足狗配置一个可调默认值。

核心函数已经改成支持 Z 方向膨胀了。接下来把参数读进来，并保证膨胀点数组大小跟新的三维膨胀范围一致。

参数已经进入 `algorithm.xml`。我再在四足狗启动文件里加一个入口，这样你后面只改 launch 参数就能调 Z 膨胀，不用再动 C++。

我还要补一个小保护：如果有人把 `obstacles_inflation_z` 误设成负数，就按 0 处理，避免数组大小异常。然后我会至少编译相关包确认没有语法问题。

现在进入验证阶段。我先看一下工作区的构建方式，然后只编译受影响的包，尽量不浪费你机器时间。

diff 看起来符合预期。现在我跑一次相关构建；因为改的是 `plan_env` 的头文件，依赖它的包可能也会被触发，但这是必要的编译检查。

这个工作区之前是用 `catkin_make` 构建的，所以不能混用 `catkin build`。我切回 `catkin_make --pkg plan_env` 来验证。

`plan_env` 已经编译通过了，只有原来代码里的一个 signed/unsigned warning，和这次改动无关。最后我取一下行号，方便你直接定位参数。

已实现“参数化 Z 向障碍膨胀”。

改动位置：

- [sdf_map.h](/home/uestc/tzb/uestc-challenge2026/src/fuel/fuel_planner/plan_env/include/plan_env/sdf_map.h:96)：新增 `obstacles_inflation_z_`
- [sdf_map.h](/home/uestc/tzb/uestc-challenge2026/src/fuel/fuel_planner/plan_env/include/plan_env/sdf_map.h:243)：`inflatePoint()` 从原来的只膨胀 XY，改成 XY + 可选 Z 膨胀
- [sdf_map.cpp](/home/uestc/tzb/uestc-challenge2026/src/fuel/fuel_planner/plan_env/src/sdf_map.cpp:25)：读取参数 `sdf_map/obstacles_inflation_z`
- [algorithm.xml](/home/uestc/tzb/uestc-challenge2026/src/fuel/fuel_planner/exploration_manager/launch/algorithm.xml:73)：传入 `sdf_map/obstacles_inflation_z`
- [fuel_dog_visual_exploration.launch](/home/uestc/tzb/uestc-challenge2026/src/fuel/fuel_dog_adapter/launch/fuel_dog_visual_exploration.launch:15)：新增可调参数

现在你可以在 `fuel_dog_visual_exploration.launch` 里调：

```xml
<arg name="obstacles_inflation_z" default="0.25"/>
```

含义是：障碍除了原来的 XY 膨胀 `0.3m`，还会在 Z 方向上下膨胀 `0.25m`。你们当前 `fixed_z=0.30`，这个值可以先从 `0.20 ~ 0.30` 试起。

验证过：

```bash
source devel/setup.bash
catkin_make --pkg plan_env
```

编译通过。只有一个旧的 signed/unsigned warning，和这次改动无关。


---

## 87. [A*/B 样条] 现在的情况是，B样条有时会生成“打结”的轨迹，这是什么原因

**问题**

现在的情况是，B样条有时会生成“打结”的轨迹，这是什么原因

**回答**

我先看一下你们当前 B 样条优化和轨迹生成的代码路径，重点查控制点初始化、端点约束、平滑/距离/可行性权重，以及重规划时是否可能把很短路径交给优化器。这个问题通常不是一个参数单独造成的，需要把生成链路串起来看。

我看到一个很重要的配置：你们现在 `use_topo_path=true`，也就是可能走“拓扑路径 + guide 优化”这条链路。B 样条打结往往出现在“导引路径、平滑项、避障项、端点约束”互相拉扯，尤其是门口、短路径、窄通道这种局部几何条件下。

`planner_manager_dev.cpp` 里探索轨迹不是完整 topo 重规划那套，而是本地探索链路：先拿 `A* path`，再按路径长度生成 guide/control points，然后做 NORMAL_PHASE 优化。这个结构下，“打结”更容易来自短而弯的 guide path 或优化权重把控制点推过头。

会。“B 样条打结”通常不是 B 样条本身坏了，而是**优化问题约束不够、初值太差、代价函数互相拉扯**导致控制点绕回来。

结合你们当前代码，最可疑的是这几类原因：

1. **A* 路径短且曲折，控制点太少**

在 [planner_manager_dev.cpp](/home/uestc/tzb/uestc-challenge2026/src/fuel/fuel_planner/plan_manage/src/planner_manager_dev.cpp:258) 里：

```cpp
double len = topo_prm_->pathLength(path);
int seg_num = len / pp_.ctrl_pt_dist * 1.2;
int ctrl_pt_num = seg_num + 3;
double dt = (len / pp_.max_vel_) / seg_num;
```

如果门口路径很短，但又有左右绕障，`seg_num` 会很小。三阶 B 样条在控制点少、路径弯折大的情况下，很容易出现过冲、回绕、局部打结。

尤其你现在：

```xml
<param name="manager/control_points_distance" value="0.35"/>
```

如果 A* 路径只有 1m 左右，实际控制点数量非常有限。

2. **A* path 只是初始化，不是优化约束**

这里：

```cpp
topo_prm_->pathToGuidePts(path, seg_num + 1, pts);
```

A* 路径只是生成初始控制点。后面优化用的是：

```cpp
int cost_func = BsplineOptimizer::NORMAL_PHASE;
```

而 [bspline_optimizer.cpp](/home/uestc/tzb/uestc-challenge2026/src/fuel/fuel_planner/bspline_opt/src/bspline_optimizer.cpp:22) 里：

```cpp
NORMAL_PHASE = SMOOTHNESS | DISTANCE | FEASIBILITY | START | END;
```

注意：**NORMAL_PHASE 没有 GUIDE**。

也就是说，B 样条优化时并没有强制贴着 A* 路径走。它只管平滑、避障、速度加速度、起终点。门口这种对称/窄通道环境里，距离场梯度一推，控制点就可能绕到另一侧，甚至形成局部环。

3. **平滑项会主动“抹掉”曲折 A* 路径**

你现在参数是：

```xml
<param name="optimization/ld_smooth" value="8.0"/>
<param name="optimization/ld_dist" value="20.0"/>
<param name="optimization/ld_feasi" value="2.0"/>
<param name="optimization/ld_guide" value="20.0"/>
```

虽然 `ld_guide` 配了，但在 `localExplore()` 这条链路里没用上 GUIDE 代价。于是平滑项会倾向于把短而曲折的 A* 路径拉直；距离项又会把控制点从障碍旁推开，两者一起就可能把轨迹推出一个“结”。

4. **起点速度/加速度方向和新路径方向冲突**

在 `localExplore()` 里，前三个控制点是用当前 `start_vel/start_acc` 构造的：

```cpp
state_xyz.row(0) = start;
state_xyz.row(1) = start_vel;
state_xyz.row(2) = start_acc;
```

如果四足狗正在转弯、回头、刚重规划，新目标却在另一个方向，起始控制点会带着旧速度方向。短路径下这几个控制点影响特别大，可能导致开头先绕一下再回来，看起来就是打结。

5. **我们刚加的 Z 膨胀可能会放大窄通道推力**

Z 膨胀本身是为了解决低矮障碍漏检，但它会让 ESDF 中“障碍影响范围”更大。门口本来就窄，如果 `obstacles_inflation_z` 配得比较大，距离代价可能更强烈地把控制点往左右推。这个不一定是根因，但可能会放大已有问题。

**优先解决方向**

我建议按这个顺序处理：

1. **给短路径设置最小控制点数**

比如 `seg_num` 至少为 4 或 5，避免短路径控制点太少。

2. **在 localExplore 的 B 样条优化里加入 GUIDE 约束**

现在 `ld_guide` 参数虽然存在，但这条流程没有用。可以先做两阶段：

```text
GUIDE_PHASE：先贴着 A* path
NORMAL_PHASE：再平滑和避障
```

或者在 `NORMAL_PHASE` 里临时加上 `GUIDE`。

3. **门口/短路径降低平滑权重，增强 guide/end 约束**

比如门口阶段让轨迹更忠实于 A*，不要过度平滑。

4. **重规划时限制起始速度方向**

如果新目标方向和当前速度方向夹角太大，可以先减小 `start_vel`，甚至置零，这样 B 样条不会被旧速度“甩”出一个弯。

一句话总结：**你们现在 B 样条打结，最核心原因大概率是 A* 只作为初值，优化阶段没有强制跟随 A*，短路径/门口场景下控制点太少，再叠加起始速度方向和避障梯度，就容易绕成结。**


---

## 88. [A*/B 样条] 为什么一定要加一个夹角阈值，不能在任何情况下都是保持当前速度作为B样条的初始速度吗

**问题**

为什么一定要加一个夹角阈值，不能在任何情况下都是保持当前速度作为B样条的初始速度吗

**回答**

可以，而且从你现在的目标来看，**完全可以先不加夹角阈值，任何情况下都保持当前速度作为 B 样条初始速度**。

夹角阈值的目的，是处理这种情况：

```text
四足狗当前还在往左走
新 A* 路径第一段要求往右走
```

如果 B 样条必须严格继承“往左”的初速度，它就可能先往左冲一小段，再绕回右边，形成回钩。但这是一个“防打结”的启发式，不是必须条件。

你现在明确要求的是：

```text
B 样条生成轨迹的初始速度方向
必须和四足狗当前速度方向一致
```

那么就不应该加投影修正。应该直接：

```cpp
PolynomialTraj::waypointsTraj(pos, cur_vel, zero, cur_acc, zero, times, init_traj);
hardenCubicBoundary(ctrl_pts, dt, tour.front(), cur_vel, cur_acc, tour.back(), zero, zero);
```

也就是当前原始逻辑本身已经是在用 `cur_vel`。

问题是：虽然代码传入了 `cur_vel`，但我们需要验证 **最终 B 样条优化后是否仍然保持这个起始速度**。因为优化器可能改变控制点，所以需要打印：

```text
当前四足狗速度 cur_vel
优化完成后的 B 样条 t=0 速度
```

这比打印“传入优化器的 used velocity”更准确。

所以我建议新的实现应该改成：

1. 不做任何夹角修正。
2. 保持当前速度作为初始速度。
3. 在 B 样条优化完成后，计算：

```cpp
local_data_.position_traj_.getDerivative().evaluateDeBoorT(0.0)
```

或者在 `hardenCubicBoundary()` 后直接由控制点算起点速度。
4. 单独终端打印：

```text
odom/current velocity
planned bspline initial velocity
direction difference
```

这样才能真正证明：**B 样条轨迹的初始速度方向是否和四足狗当前速度方向一致**。


---

## 89. [A*/B 样条] B样条的起点是基于哪个坐标系的

**问题**

B样条的起点是基于哪个坐标系的

**回答**

B 样条的起点也是基于 **`world` 坐标系** 的。

代码链路是：

1. 规划开始时，FSM 取当前里程计位置：

[fast_exploration_fsm.cpp](/home/uestc/tzb/uestc-challenge2026/src/fuel/fuel_planner/exploration_manager/src/fast_exploration_fsm.cpp:100)

```cpp
fd_->start_pt_ = fd_->odom_pos_;
fd_->start_vel_ = fd_->odom_vel_;
```

这里的 `odom_pos_` 来自：

```text
/odom_world
```

而你们 launch 里把它 remap 到：

```text
/Odometry_gazebo
```

2. 这个 `start_pt_` 传给探索规划器：

```cpp
planExploreMotion(fd_->start_pt_, fd_->start_vel_, ...)
```

3. 最后生成 B 样条：

[planner_manager.cpp](/home/uestc/tzb/uestc-challenge2026/src/fuel/fuel_planner/plan_manage/src/planner_manager.cpp:300)

```cpp
planExploreTraj(..., cur_vel, cur_acc, ...)
```

其中路径点、控制点、B 样条点都在同一个全局坐标系下。

4. RViz 画 B 样条时，Marker frame 也是：

```cpp
mk.header.frame_id = "world";
```

所以：

```text
B 样条起点 = 规划时刻四足狗 odom_pos_
坐标系 = world
不是相机坐标系
不是 base 坐标系
```

不过要注意一点：你看到的那个跟着狗动的坐标轴是 `/fuel_dog_adapter/sensor_pose`，它是相机 pose；但 B 样条不是基于这个相机坐标系生成的，而是基于 `/Odometry_gazebo` 给出的世界坐标位置生成的。


---

## 90. [A*/B 样条] 那么，B 样条是基于 /Odometry_gazebo 给出的世界坐标位置生成的，黄色轨迹的终点也是四足狗 odom 位置位置，为什么两者不重合

**问题**

那么，B 样条是基于 /Odometry_gazebo 给出的世界坐标位置生成的，黄色轨迹的终点也是四足狗 odom 位置位置，为什么两者不重合

**回答**

这个问题要把“黄色轨迹到底是哪条黄色”再钉死一下。RViz 配置里启用的黄色是 `/fuel_dog_adapter/executed_path`，我去看它的发布代码，因为它不一定等同于 `/planning/travel_traj`。

原因是：**两者都在 `world` 坐标系，但不是同一时刻的同一个点。**

你 RViz 里启用的黄色轨迹是：

```text
/fuel_dog_adapter/executed_path
```

配置在 [fuel_dog_exploration.rviz](/home/uestc/tzb/uestc-challenge2026/src/fuel/fuel_dog_adapter/config/fuel_dog_exploration.rviz:311)：

```yaml
Name: executed_path
Class: rviz/Path
Topic: /fuel_dog_adapter/executed_path
Color: 255; 190; 60
```

它由 [fuel_path_visualizer.py](/home/uestc/tzb/uestc-challenge2026/src/fuel/fuel_dog_adapter/scripts/fuel_path_visualizer.py:45) 发布：

```python
pose.pose = msg.pose.pose
```

而 `msg` 来自：

```python
self.odom_topic = "/Odometry_gazebo"
```

所以黄色轨迹终点是：

```text
当前最新 /Odometry_gazebo 累计出来的实际 odom 轨迹末端
```

也就是“狗已经走到哪里了”。

---

B 样条红色轨迹的起点来自规划时刻的 odom。

在 [fast_exploration_fsm.cpp](/home/uestc/tzb/uestc-challenge2026/src/fuel/fuel_planner/exploration_manager/src/fast_exploration_fsm.cpp:99)：

```cpp
fd_->start_pt_ = fd_->odom_pos_;
fd_->start_vel_ = fd_->odom_vel_;
```

然后规划出来的 B 样条在 RViz 里画成红色：

[fast_exploration_fsm.cpp](/home/uestc/tzb/uestc-challenge2026/src/fuel/fuel_planner/exploration_manager/src/fast_exploration_fsm.cpp:386)

```cpp
visualization_->drawBspline(info->position_traj_, 0.1, Vector4d(1.0, 0.0, 0.0, 1), ...)
```

所以红色 B 样条起点是：

```text
规划开始那一刻的 odom_pos
```

---

关键区别在这里：

```text
黄色终点 = 当前最新 odom 位置
红色起点 = 上一次规划时记录的 odom 位置
```

在两者之间，四足狗还在继续运动。

尤其 FUEL 里新轨迹不是规划完立刻执行，而是设置了一个未来的 `start_time`：

[fast_exploration_fsm.cpp](/home/uestc/tzb/uestc-challenge2026/src/fuel/fuel_planner/exploration_manager/src/fast_exploration_fsm.cpp:245)

```cpp
ros::Time time_r = ros::Time::now() + ros::Duration(fp_->replan_time_);
```

然后：

```cpp
info->start_time_ = ... time_r;
```

只有等到这个时间到了，才发布新 B 样条：

```cpp
if (dt > 0) {
  bspline_pub_.publish(fd_->newest_traj_);
}
```

所以你在 RViz 里看到时：

```text
红色 B 样条起点停留在规划那一刻
黄色实际轨迹终点已经继续往前走了一段
```

因此它们不重合。

---

还有一个视觉上的小偏差：黄色 `executed_path` 在 RViz 里加了 Z 偏移：

[fuel_dog_exploration.rviz](/home/uestc/tzb/uestc-challenge2026/src/fuel/fuel_dog_adapter/config/fuel_dog_exploration.rviz:311)

```yaml
Offset:
  Z: 0.05999999865889549
```

所以即使 XY 很接近，Z 方向也会被 RViz 人为抬高约 `0.06m`。

总结：

```text
不是坐标系问题。
两者都是 world。
不重合主要是时间差：红色是规划时刻 odom，黄色是当前最新 odom 历史末端。
另外黄色 Path 在 RViz 里还有 0.06m 的 Z 偏移。
```


---

## 91. [A*/B 样条] 但是机会每次B样条规划出来的轨迹起点都在黄色轨迹的身后啊，这肯定是不好的吧

**问题**

但是机会每次B样条规划出来的轨迹起点都在黄色轨迹的身后啊，这肯定是不好的吧

**回答**

你这个观察是对的：如果红色 B 样条起点几乎每次都落在黄色实际轨迹末端后面，那不是单纯“显示问题”，而是规划起点和真正执行起点之间存在系统性滞后。我再看一下 FSM 里现在是怎么取 `start_pt_` 的，确认偏差来源。

对，如果**几乎每次**红色 B 样条起点都在黄色实际轨迹终点后面，那确实不好。这个不是坐标系问题，而是**规划起点滞后于执行位置**。

当前代码里是这样：

在 [fast_exploration_fsm.cpp](/home/uestc/tzb/uestc-challenge2026/src/fuel/fuel_planner/exploration_manager/src/fast_exploration_fsm.cpp:99)：

```cpp
fd_->start_pt_ = fd_->odom_pos_;
fd_->start_vel_ = fd_->odom_vel_;
```

也就是规划时，用的是**当前这一刻的 odom 位置**。

但后面新 B 样条不是立刻执行，而是设置了未来的开始时间：

```cpp
ros::Time time_r = ros::Time::now() + ros::Duration(fp_->replan_time_);
```

也就是说：

```text
B 样条起点 = 规划时刻 odom
真正开始执行 = replan_time 之后
```

在这段时间里四足狗还在继续走，所以黄色实际轨迹末端已经往前移动了。假设：

```text
速度 1.0 m/s
replan_time 0.2 s
```

那么红色起点天然就会落后大约：

```text
0.2 m
```

如果加上规划耗时、显示延迟、轨迹发布延迟，可能更明显。

这会带来几个问题：

1. **新轨迹起点在狗身后**

控制器接到新 B 样条后，会发现期望位置落后于当前实际位置，容易产生“往回拉”的趋势。

2. **B 样条看起来从后面开始**

RViz 上红色轨迹起点总在黄色轨迹尾部后面，就是这个现象。

3. **进入门口、窄通道时更容易横跳或打结**

因为规划器认为起点还在后面，但狗实际已经更靠前了，局部几何关系变了。

更合理的做法是：

```text
B 样条起点不要用规划时刻 odom_pos
而要用新轨迹真正开始执行时刻的预测位置
```

简单版就是：

```cpp
double dt = fp_->replan_time_;
fd_->start_pt_ = fd_->odom_pos_ + fd_->odom_vel_ * dt;
fd_->start_vel_ = fd_->odom_vel_;
```

更稳一点可以加参数，比如：

```xml
<param name="fsm/start_state_predict_time" value="0.2"/>
```

然后用这个预测时间控制 B 样条起点提前量。

所以结论是：

```text
偶尔不重合正常；
每次都明显在身后，不好，应该修正规划起点预测。
```

你现在这个现象更像是 `start_pt_` 用了当前 odom，但 `start_time_` 在未来，导致系统性滞后。


---

## 92. [重规划] 那我将replan_time设置为0怎么说

**问题**

那我将replan_time设置为0怎么说

**回答**

如果你把：

```xml
<param name="fsm/replan_time" value="0.0"/>
```

那按当前代码逻辑，新轨迹的 `start_time` 基本就是：

```text
ros::Time::now()
```

也就是规划完马上发布、马上执行。

这样可以减少这部分滞后：

```text
规划时刻 odom_pos 和 新轨迹执行时刻 odom_pos 的差
```

所以红色 B 样条起点落在黄色轨迹身后的现象，理论上会明显减轻。

但它**不一定完全消失**，因为还有这些延迟：

1. **规划计算耗时**

`fd_->start_pt_ = fd_->odom_pos_` 是在进入 `PLAN_TRAJ` 时取的。之后还要执行：

```cpp
planExploreMotion(...)
```

即使 `replan_time=0`，规划本身也会花时间。狗在这段时间里还会继续动。

2. **FSM 发布和 traj_server 接收有延迟**

规划成功后，FSM 从 `PLAN_TRAJ` 转到 `PUB_TRAJ`，再发布 `/planning/bspline`，`traj_server` 接收后才开始采样。

3. **黄色轨迹是最新 odom 累计，红色轨迹是规划完成时画出来的**

RViz 显示也有刷新延迟。黄色路径持续跟 odom 更新，红色路径只在规划后更新一次。

所以：

```text
replan_time=0 可以减少“未来 start_time”造成的滞后，
但不能消除“规划耗时 + 发布延迟 + RViz刷新”造成的滞后。
```

如果你设置为 0 后仍然看到明显在身后，那说明主要滞后不是 `replan_time`，而是：

```text
规划耗时期间四足狗继续运动
```

这种情况下就仍然需要预测起点：

```cpp
start_pt = odom_pos + odom_vel * predict_time
```

只不过 `predict_time` 不应该等于 `replan_time`，而应该近似：

```text
规划耗时 + 通信/执行延迟
```

比如先试：

```text
0.1 ~ 0.2 s
```

而不是固定依赖 `replan_time`。

---


---
