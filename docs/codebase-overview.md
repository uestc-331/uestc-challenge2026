# SimEnv 代码框架总览

本文档用于新聊天窗口或新接手人员快速恢复项目上下文。它不是比赛使用说明，而是代码结构、启动链路和常见修改入口的工程地图。

## 一句话概览

`/home/uestc/SimEnv` 是一个 ROS1 Noetic + Gazebo Classic 的 catkin 工作区。主任务是随机生成多楼层室内比赛场景，启动 Unitree A1 机器狗及传感器，开放 `/cmd_vel`、里程计、点云、图像、门和电梯服务，并用 `results/detected_danger.json` 评估危险源识别结果。

核心运行入口是仓库根目录的 `auto.sh`：

1. 清理旧的 Gazebo、roslaunch、控制器和可选手柄进程。
2. source `/opt/ros/noetic/setup.bash` 和本工作区 `devel/setup.bash`。
3. 调用 `building_obstacles/scripts/generate_competition_scene.py` 生成随机楼栋、危险源、干扰源和真值。
4. 设置 `BUILDING_WORLD_FILE`、机器人初始位姿、传感器参数、Gazebo plugin/model path。
5. 通过 `roslaunch unitree_guide multi_floor_gazeboSim.launch` 启动 Gazebo、A1、传感器、关节控制器和辅助节点。
6. 启动 `building_generator_classic_control`，提供门和电梯服务。
7. 默认以前台方式启动 `devel/lib/unitree_guide/junior_ctrl`。在该终端输入 `2` 站立，再输入 `6` 进入 RL 模式后，`/cmd_vel` 生效。

## 顶层目录

| 路径 | 作用 |
| --- | --- |
| `README.md` | 面向参赛选手的总入口。 |
| `auto.sh` | 当前最重要的启动编排脚本。 |
| `setup_multi_floor_simulation.py` | 只生成场景并提示如何运行的简化辅助脚本。 |
| `docs/` | 比赛接口和专题说明；本文档是代码框架索引。 |
| `src/` | catkin 源码空间，包含场景生成、A1 控制、传感器、UAV 仿真和工具包。 |
| `generated_building/` | 每次启动时生成的楼栋、world、门/电梯配置、manifest 和元数据。 |
| `results/` | 裁判真值和参赛算法输出。算法应写 `detected_danger.json`，不应读 `danger_truth.json`。 |
| `logs/` | `auto.sh` 启动的 Gazebo、门电梯服务、控制器等日志和 pid。 |
| `build/`, `devel/` | catkin 构建输出。 |
| `src/third_party/` | 本地第三方依赖，主要有 LCM、libtorch、CUDA 兼容库、navigation_msgs。 |
| `src/uav_simulator/` | 另一套 UAV 仿真/控制/地图工具，当前比赛主线不是 A1 启动链路的核心。 |

## ROS 包分组

### 楼栋与比赛场景

| 包 | 关键文件 | 职责 |
| --- | --- | --- |
| `building_generator_core` | `building_generator_core/generator.py`, `exporter.py`, `layout.py`, `constraints.py` | 纯 Python 楼栋布局生成和 SDF/JSON/YAML 导出。 |
| `building_generator_classic` | `control_runtime.py`, `control_server.py` | Gazebo Classic 门、电梯运行时逻辑；ROS 服务节点入口是 `building_generator_classic_control`。 |
| `building_generator_interfaces` | `srv/SetDoorState.srv`, `srv/CallElevator.srv` | 门和电梯服务定义。 |
| `building_obstacles` | `scripts/generate_competition_scene.py`, `evaluate_danger.py` | 组合楼栋生成、危险源/干扰源放置、比赛 world 写出和结果评估。 |

重要数据流：

`generate_competition_scene.py` -> `generate_layout()` -> `export_sdf()` -> `generated_building/world.sdf`/`model.sdf`/`layout_metadata.json`/`door_config.yaml`/`elevator_config.yaml` -> 注入危险源和干扰源 -> `competition_scene.world` -> `results/danger_truth.json` 与 `generated_building/scene_manifest.json`。

### Unitree A1 仿真与控制

| 包/目录 | 关键文件 | 职责 |
| --- | --- | --- |
| `unitree_guide/unitree_guide/unitree_guide` | `launch/multi_floor_gazeboSim.launch` | 主 launch：加载 generated world，spawn A1，启动状态桥、控制器、robot_state_publisher、可选 Livox 转换。 |
| 同上 | `src/main.cpp` | `junior_ctrl` 主程序，创建 ROS/Gazebo I/O 和控制状态机。 |
| 同上 | `src/FSM/State_RL_test.cpp` | RL 状态，订阅 `/cmd_vel`，调用 TorchScript 策略推理输出 12 个关节目标。 |
| 同上 | `src/interface/IOROS.cpp` | Gazebo I/O：发布 12 个电机命令，订阅 IMU、关节状态、足端/机体真值和 `/joy`。 |
| 同上 | `src/state_from_gazebo.cpp` | 从 `/gazebo/link_states` 提取 `a1_gazebo::base`，发布 `/Odometry_gazebo` 并广播 `map->odom`、`odom->base` TF。 |
| 同上 | `scripts/pointcloud2livox.py` | 将 `/scan` 原始点云过滤/坐标变换后发布 PointCloud2，同时发布 `unitree_guide/CustomMsg` 到 `/livox/lidar2`。 |
| `unitree_ros/robots/a1_description` | `xacro/robot.xacro`, `xacro/gazebo.xacro` | A1 URDF/Xacro、Gazebo 插件和传感器配置。 |
| `unitree_ros/unitree_controller` | `launch/set_ctrl.launch` 等 | Gazebo 关节控制相关节点/配置。 |
| `unitree_ros/unitree_legged_control` | `joint_controller.cpp` | Unitree 关节控制插件。 |
| `unitree_ros_to_real` | 多个 `unitree_legged_*` 包 | 面向真机或 SDK 的代码，当前仿真主线通常不改。 |

`unitree_guide` 的 CMake 会强制使用 `src/third_party/libtorch-cu128-sm120/libtorch` 和 `src/third_party/lcm/install`。`junior_ctrl` 链接 Torch、LCM 和 catkin 库；RL 策略文件目前在 `src/unitree_guide/logs/policy_act_inference_plane.pt` 与 `policy_act_inference_stair.pt`。

### 传感器

| 包/位置 | 作用 |
| --- | --- |
| `Mid360_imu_sim` | Livox/Mid360 Gazebo 传感器插件、URDF 和 scan pattern。A1 的 xacro 中也使用了 Mid360 相关模型/扫描配置。 |
| `a1_description/xacro/gazebo.xacro` | 配置 `/scan`、前视 RGB、RealSense 深度相机、IMU、接触/足端状态等 Gazebo 插件。 |
| `unitree_guide/scripts/pointcloud2livox.py` | 运行时点云转换和过滤节点，可由 launch 参数 `enable_livox_converter` 开关控制。 |

主要外部接口见 `docs/sensors-and-topics.md`，常用话题包括 `/scan`、`/camera/image_raw`、`/real_sense/depth/points`、`/trunk_imu` 和 `/Odometry_gazebo`。

### 工具包

| 包 | 关键文件 | 职责 |
| --- | --- | --- |
| `competition_tools` | `scripts/keyboard_competition_control.py` | 辅助键盘控制：发布 `/cmd_vel`，调用门和电梯服务，可选显示相机。 |
| `uav_simulator/*` | `mockamap`, `local_sensing_node`, `so3_control`, `so3_quadrotor_simulator`, `Utils/*` | UAV 地图、深度渲染、SO3 控制、四旋翼动力学和消息工具。当前 A1 比赛启动链路通常不会直接调用，但会一起参与 catkin 构建。 |

## 启动链路细节

### `auto.sh` 环境变量

常用变量：

| 变量 | 默认值 | 作用 |
| --- | --- | --- |
| `SEED` | 空 | 场景随机种子，空则随机。 |
| `FLOOR_COUNT` | `3` | 楼层数。 |
| `ROOMS_PER_FLOOR` | `4` | 每层房间数，可传范围格式。 |
| `DANGER_COUNT` | `3:6` | 危险源数量或范围。 |
| `DISTRACTOR_COUNT` | `4:8` | 干扰源数量或范围。 |
| `SIM_FAST` | `0` | 快速仿真 profile，降低部分传感器开销。 |
| `GUI`, `PAUSED` | `true`, `true` | Gazebo 图形界面和初始暂停。 |
| `START_CONTROLLER` | `1` | 是否启动 `junior_ctrl`。 |
| `CONTROLLER_FOREGROUND` | `1` | 控制器是否前台运行，前台才能直接输入 `2`、`6`。 |
| `START_BUILDING_CONTROL` | `1` | 是否启动门/电梯服务。 |
| `ROBOT_X/Y/Z/YAW` | `0.0/-1.5/0.6/1.5708` | 机器人初始位姿。 |
| `ENABLE_REALSENSE`, `ENABLE_LIVOX`, `ENABLE_CAMERA` | profile 决定 | 传感器开关。 |

示例：

```bash
cd /home/uestc/SimEnv
source /opt/ros/noetic/setup.bash
catkin_make -j
source devel/setup.bash
SEED=123 SIM_FAST=1 FLOOR_COUNT=2 ./auto.sh
```

### 主 launch

`src/unitree_guide/unitree_guide/unitree_guide/launch/multi_floor_gazeboSim.launch` 做这些事：

1. 从环境变量 `BUILDING_WORLD_FILE` 读取 `generated_building/competition_scene.world`。
2. include `gazebo_ros/launch/empty_world.launch`。
3. xacro 展开 A1 机器人描述，透传传感器开关、频率、分辨率和 Livox 参数。
4. `gazebo_ros/spawn_model` 生成模型名 `a1_gazebo` 的机器人。
5. 启动 `state_from_gazebo` 发布 `/Odometry_gazebo`。
6. 加载 `a1_description/config/robot_control.yaml` 并启动 12 个关节控制器。
7. 启动 `robot_state_publisher`。
8. 可选启动 `/joy` 节点。
9. include `unitree_controller/launch/set_ctrl.launch`。
10. 可选启动 `pointcloud2livox.py`。

## 比赛接口速查

| 接口 | 类型 | 来源/用途 |
| --- | --- | --- |
| `/cmd_vel` | `geometry_msgs/Twist` | 参赛算法速度输入；`State_RL_test.cpp` 在 RL 模式下订阅。 |
| `/Odometry_gazebo` | `nav_msgs/Odometry` | `state_from_gazebo.cpp` 发布。 |
| `/scan` | `sensor_msgs/PointCloud`/点云 | Mid360/Gazebo 插件输出，文档按 PointCloud2 使用场景说明。 |
| `/livox/lidar2` | `unitree_guide/CustomMsg` | `pointcloud2livox.py` 发布的 Livox 风格消息。 |
| `/camera/image_raw` | `sensor_msgs/Image` | 前视 RGB。 |
| `/real_sense/depth/points` | `sensor_msgs/PointCloud2` | 深度相机点云。 |
| `/set_door_state` | `building_generator_interfaces/SetDoorState` | 开关动态门。 |
| `/call_elevator` | `building_generator_interfaces/CallElevator` | 移动电梯轿厢到目标楼层。 |

## 生成物和文件语义

| 文件 | 写入方 | 语义 |
| --- | --- | --- |
| `generated_building/competition_scene.world` | `generate_competition_scene.py` | Gazebo 实际加载的完整 world。 |
| `generated_building/world.sdf` | `export_sdf()` 后被覆盖为完整比赛 world | 保留给工具链/调试。 |
| `generated_building/model.sdf` | `export_sdf()` | 不含危险源的楼栋模型。 |
| `generated_building/layout_metadata.json` | `export_sdf()` | 楼层、房间、走廊、门、电梯、目标点等布局元数据。 |
| `generated_building/door_config.yaml` | `export_sdf()` | 动态门配置，供控制服务和键盘工具读取。 |
| `generated_building/elevator_config.yaml` | `export_sdf()` | 电梯配置，供控制服务和键盘工具读取。 |
| `generated_building/generation_checks.json` | `export_sdf()` | 生成自检结果。 |
| `generated_building/scene_manifest.json` | `generate_competition_scene.py` | 本次场景总 manifest。 |
| `results/danger_truth.json` | `generate_competition_scene.py` | 裁判真值，算法不应读取。 |
| `results/detected_danger.json` | 参赛算法 | 算法输出，用 `evaluate_danger.py` 评估。 |

## 常见修改入口

| 想改的内容 | 优先看这里 |
| --- | --- |
| 楼层数、房间数、建筑尺寸、危险源数量 | `auto.sh` 环境变量；底层参数在 `generate_competition_scene.py`。 |
| 楼栋布局规则、楼梯/电梯/房间尺寸 | `building_generator_core/generator.py`。 |
| SDF 几何、材质、门模型、电梯模型、metadata/config 导出 | `building_generator_core/exporter.py`。 |
| 危险源/干扰源形状、颜色、放置规则、真值格式 | `building_obstacles/scripts/generate_competition_scene.py`。 |
| 门/电梯服务行为、动画、搬运机器人逻辑 | `building_generator_classic/control_runtime.py` 和 `control_server.py`。 |
| 主启动流程和默认传感器 profile | `auto.sh`。 |
| Gazebo/A1/传感器 launch 参数 | `unitree_guide/launch/multi_floor_gazeboSim.launch`。 |
| A1 传感器安装位置、话题、更新率、xacro 开关 | `unitree_ros/robots/a1_description/xacro/robot.xacro` 和 `gazebo.xacro`。 |
| `/cmd_vel` 到 RL 策略输入 | `unitree_guide/src/FSM/State_RL_test.cpp`。 |
| 控制器循环周期 | `UNITREE_CTRL_DT` 环境变量和 `unitree_guide/src/main.cpp`。 |
| `/Odometry_gazebo` 或 TF | `unitree_guide/src/state_from_gazebo.cpp`。 |
| 键盘辅助控制、门/电梯快捷键、相机窗口 | `tools/scripts/keyboard_competition_control.py`。 |
| 评分规则和匹配半径 | `building_obstacles/scripts/evaluate_danger.py`。 |

## 测试与验证

Python 生成器有单元测试：

```bash
cd /home/uestc/SimEnv
python3 -m pytest src/building_generator_core/test src/building_generator_classic/test
```

ROS/catkin 编译：

```bash
cd /home/uestc/SimEnv
source /opt/ros/noetic/setup.bash
catkin_make -j
source devel/setup.bash
```

只生成场景：

```bash
python3 src/building_obstacles/scripts/generate_competition_scene.py \
  --output-dir generated_building \
  --results-dir results \
  --seed 123
```

评估结果：

```bash
python3 src/building_obstacles/scripts/evaluate_danger.py \
  --truth-file results/danger_truth.json \
  --detected-file results/detected_danger.json \
  --output-file results/evaluation_result.json
```

## 当前代码注意事项

- 工作区经常会有 `generated_building/`、`logs/`、`results/` 的运行时改动；做代码修改时不要把这些生成物当成必须回滚的源码。
- `auto.sh` 会主动 `pkill` 旧 Gazebo、roslaunch、`junior_ctrl`、点云转换等进程；调试时要注意它会清掉已有仿真。
- `junior_ctrl` 默认前台运行，终端交互是进入控制状态的关键路径。
- `State_RL::exit()` 里会 join 调试线程，若未来改线程开关需小心空指针/未启动线程情况。
- `state_from_gazebo.cpp` 中 odometry twist 字段存在连续赋值到 `.linear.x` 和 `.angular.x` 的代码形态；如果后续排查速度输出异常，应重点检查这里。
- `exporter.py` 里有中文注释标记的本地物理参数/透明度修改，说明该文件已有比赛定制，不宜盲目按上游版本覆盖。
- 文档中 `/scan` 的类型有历史差异：生成/转换代码订阅 `sensor_msgs/PointCloud`，对外文档常按点云接口描述；改传感器时需要同时核对 xacro、转换脚本和算法订阅类型。

## 给下一次 AI 的快速接手提示

新窗口可以先读：

1. `docs/codebase-overview.md`
2. `README.md`
3. 需要接算法接口时读 `docs/algorithm-interfaces.md`、`docs/sensors-and-topics.md`、`docs/doors-and-elevator.md`
4. 需要改启动链路时读 `auto.sh` 和 `src/unitree_guide/unitree_guide/unitree_guide/launch/multi_floor_gazeboSim.launch`
5. 需要改场景生成时读 `src/building_obstacles/scripts/generate_competition_scene.py`、`src/building_generator_core/building_generator_core/generator.py`、`src/building_generator_core/building_generator_core/exporter.py`
6. 需要改 A1 控制时读 `src/unitree_guide/unitree_guide/unitree_guide/src/main.cpp`、`src/unitree_guide/unitree_guide/unitree_guide/src/FSM/State_RL_test.cpp`、`src/unitree_guide/unitree_guide/unitree_guide/src/interface/IOROS.cpp`
