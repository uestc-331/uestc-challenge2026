# src 功能包清单

生成位置：`/home/uestc/SimEnv/txt/src_function_packages_inventory.md`

说明：本清单根据 `src` 目录下的 `package.xml`、`README.md`、`launch`、脚本和源码入口整理。`src` 中既有直接位于顶层的 catkin/ROS1 功能包，也有嵌套源码集合、第三方依赖和生成资源目录。

## 一、顶层与 uav_simulator 下的 ROS 功能包

| 路径 | ROS 包名 | 大概作用 |
| --- | --- | --- |
| `src/uav_simulator/local_sensing` | `local_sensing_node` | 局部感知/深度渲染相关节点。根据点云或深度数据生成相机/深度图感知输出，源码中包含 `pcl_render_node.cpp`、`pointcloud_render_node.cpp` 等，依赖 `cv_bridge`、`image_transport`、`pcl_ros`、`quadrotor_msgs`。 |
| `src/uav_simulator/mockamap` | `mockamap` | 随机地图生成包。提供 2D/3D 迷宫、柱状障碍、Perlin 噪声地图等 launch，主要用于无人机规划/避障仿真的点云或障碍地图生成。 |
| `src/uav_simulator/map_generator` | `map_generator` | 随机森林/障碍地图感知生成节点。主要可执行程序为 `random_forest`，用于生成或发布随机森林场景相关地图信息。 |
| `src/uav_simulator/so3_control` | `so3_control` | 四旋翼 SO(3) 控制器包。提供 SO3 控制算法、nodelet 插件、增益配置和控制示例，订阅里程计/控制目标并输出四旋翼控制指令。 |
| `src/uav_simulator/so3_quadrotor_simulator` | `so3_quadrotor_simulator` | 四旋翼动力学仿真包。提供 `quadrotor_simulator_so3` 节点和示例 launch，用于模拟四旋翼在 SO3 控制框架下的运动。 |
| `src/Mid360_imu_sim` | `livox_laser_simulation` | Livox MID360 激光雷达 + IMU Gazebo 仿真包。包含 Gazebo 雷达插件、URDF、world、RViz 配置和点云格式转换脚本，输出 `/scan`、`/livox/imu`，转换后可输出 `/livox/lidar2` 供 Fast-LIO 等使用。 |
| `src/building_generator_core` | `building_generator_core` | 随机楼栋生成器核心库。负责楼栋约束解析、楼层/房间拓扑生成、Gazebo SDF 导出、CLI 和 Python API，面向 ROS1 Noetic + Gazebo Classic 的多楼层室内训练场景生成。 |
| `src/building_generator_interfaces` | `building_generator_interfaces` | 楼栋控制接口包。定义 `CallElevator.srv` 和 `SetDoorState.srv`，用于控制生成楼栋中的电梯和门状态。 |
| `src/building_generator_classic` | `building_generator_classic` | Gazebo Classic 适配层。负责把核心楼栋布局导出为 Classic 可用的 bundle，并提供 `rospy` 控制服务节点和门/电梯内存状态机。 |
| `src/building_obstacles` | `building_obstacles` | 竞赛/训练场景辅助包。包含生成竞赛场景、多楼层楼栋、红色危险源球体，以及危险源评估脚本，依赖 `gazebo_msgs`、`geometry_msgs`、`rospy`、`python3-yaml`。 |

## 二、Unitree 四足机器人相关嵌套 ROS 功能包

`src/unitree_guide` 是一个嵌套源码集合，顶层本身不是标准 ROS 包，但其内部包含多个 catkin 包。

| 路径 | ROS 包名 | 大概作用 |
| --- | --- | --- |
| `src/unitree_guide/unitree_guide/unitree_guide` | `unitree_guide` | 宇树四足机器人控制示例主包。包含 Gazebo 仿真启动文件、FSM 控制器入口 `junior_ctrl` 相关源码，以及从 Gazebo 获取状态的节点。 |
| `src/unitree_guide/unitree_guide/unitree_move_base` | `unitree_move_base` | Unitree 与 ROS Navigation/move_base 对接示例包。包含 `move_base`、TF、点云转激光、RViz 等 launch，用于仿真或实机导航演示。 |
| `src/unitree_guide/unitree_guide/unitree_actuator_sdk/unitree_motor_ctrl` | `unitree_motor_ctrl` | Unitree 电机控制示例包。基于 `roscpp`，用于电机级控制/调试。 |
| `src/unitree_guide/unitree_ros/robots/a1_description` | `a1_description` | Unitree A1 机器人模型描述包。通常包含 URDF/Xacro、mesh 等模型资源，供 Gazebo/RViz 加载。 |
| `src/unitree_guide/unitree_ros/unitree_gazebo` | `unitree_gazebo` | Unitree Gazebo 仿真启动包。提供 `normal.launch`、`z1.launch` 等，用于加载机器人和仿真世界。 |
| `src/unitree_guide/unitree_ros/unitree_legged_control` | `unitree_legged_control` | Gazebo 关节控制插件/控制器包。实现四足机器人关节位置、速度、力矩控制接口，依赖 `controller_interface`、`hardware_interface`、`pluginlib`。 |
| `src/unitree_guide/unitree_ros/unitree_controller` | `unitree_controller` | Unitree 仿真控制示例包。包含站立控制、外力扰动、运动发布等示例节点，用于 Gazebo 中控制机器人关节和姿态。 |
| `src/unitree_guide/unitree_ros_to_real/unitree_legged_msgs` | `unitree_legged_msgs` | Unitree 机器人 ROS 消息定义包。定义高/低层控制命令、状态、IMU、电机、BMS、LED 等消息。 |
| `src/unitree_guide/unitree_ros_to_real/unitree_legged_real` | `unitree_legged_real` | ROS 到 Unitree 实机的接口包。通过 Unitree SDK 与真实机器人通信，支持高层速度控制和低层关节控制，并提供键盘控制 launch。 |
| `src/unitree_guide/unitree_ros_to_real/unitree_legged_sdk` | `unitree_legged_sdk` | Unitree 官方通信 SDK 的 catkin 包装。提供与 Go1 等机器人底层通信的 C++ SDK、示例程序和 Python wrapper。 |

## 三、第三方 ROS 消息/接口包

| 路径 | ROS 包名 | 大概作用 |
| --- | --- | --- |
| `src/third_party/navigation_msgs/move_base_msgs` | `move_base_msgs` | ROS Navigation 的 `move_base` action 与恢复状态消息定义包，提供 `MoveBase.action`、`RecoveryStatus.msg` 等接口。 |
| `src/third_party/navigation_msgs/map_msgs` | `map_msgs` | 地图增量更新、投影地图、ROI 地图服务等消息/服务定义包，供导航和建图模块使用。 |

## 四、非 ROS 功能包但重要的源码/资源目录

| 路径 | 类型 | 大概作用 |
| --- | --- | --- |
| `src/uav_simulator/Utils` | 辅助源码目录 | `uav_simulator` 相关工具代码目录。当前目录层级下未发现 `package.xml`，不是独立 catkin 包。 |
| `src/generated_building` | 生成场景资源 | 已生成的 Gazebo 楼栋资源，包含 `hotel_building.world`、`multi_floor_building.world`、模型 `model.sdf`、`model.config` 和配置 JSON。 |
| `src/third_party/libtorch` | 第三方库 | PyTorch C++/LibTorch 依赖目录，供 C++ 深度学习推理或相关模块链接使用。 |
| `src/third_party/libtorch-cu128-sm120` | 第三方库 | CUDA 12.8 / sm120 相关 LibTorch 版本目录，供特定 GPU/CUDA 环境使用。 |
| `src/third_party/cuda_compat` | 第三方兼容库 | CUDA 兼容运行库目录。 |
| `src/third_party/lcm` | 第三方通信库 | LCM 通信库源码，包含 C/C++、Python、Lua、Go 等绑定和工具，用于轻量级消息通信。 |
| `src/third_party/downloads` | 下载缓存/资源目录 | 第三方依赖下载缓存或压缩包存放目录。 |

## 五、整体功能关系速览

- 无人机仿真链路：`mockamap` / `map_generator` 生成障碍地图，`local_sensing_node` 生成局部感知，`so3_control` 控制四旋翼，`so3_quadrotor_simulator` 提供四旋翼动力学仿真。
- 激光雷达/IMU 仿真：`livox_laser_simulation` 提供 MID360 + IMU Gazebo 传感器仿真，并可转换点云格式适配 Fast-LIO。
- 楼栋/竞赛场景生成：`building_generator_core` 负责生成楼栋，`building_generator_classic` 负责 Gazebo Classic 适配，`building_generator_interfaces` 提供门/电梯服务，`building_obstacles` 提供竞赛障碍和危险源生成/评估。
- 四足机器人链路：`unitree_guide`、`unitree_ros`、`unitree_ros_to_real` 相关包覆盖 Unitree A1/Go1 的 Gazebo 仿真、模型描述、关节控制、导航示例、实机通信和消息定义。
- 导航接口补充：`move_base_msgs`、`map_msgs` 为 Unitree 导航示例或其他导航模块提供缺失的 ROS 消息/服务接口。
