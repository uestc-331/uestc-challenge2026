# SimEnv 危险源视觉模块使用说明

本仓库面向 `ROS1 Noetic + Gazebo Classic + Unitree A1`。本说明重点描述视觉模块如何与随机地图路线配合：

- 路线同学根据本轮随机地图生成世界坐标航点；
- `live_yolo_route_eval.py` 负责沿该路线发送 `/cmd_vel`；
- 视觉模块同步读取 RealSense RGB、深度和相机内参；
- 指定的 YOLO 权重只检测 `red_sphere`；
- RGB-D 深度门控计算球心，多帧聚类后写出 `detected_danger.json`。

危险源真值只用于仿真评测和绿色 GT 框，不参与 YOLO 推理、深度定位或聚类。

## 先看结论：不要启动旧的 danger_detector.launch

当前 YOLO + RGB-D 方案的正确入口是：

```text
src/danger_detector/scripts/live_yolo_route_eval.py
```

不要使用下面的命令启动当前方案：

```bash
roslaunch danger_detector danger_detector.launch
```

这个 launch 启动的是旧的传统 CV 节点，不会调用当前 YOLO 权重，也没有使用当前的 `strong/weak` 深度门控和确定性地标聚类。

更重要的是，Gazebo RealSense 插件输出的图像点采用相机光学坐标：

```text
X_cam：图像向右
Y_cam：图像向下
Z_cam：镜头向前，即深度
```

但消息的 `frame_id` 是 `real_sense`，URDF 中的 `real_sense` link 又与机体轴同向。旧节点直接把光学坐标点交给 `real_sense → world` 的 TF 时，可能把 `Z_cam=5 m` 的前向距离当成世界高度，因此出现输出坐标 `z≈5 m`。

当前 `live_yolo_route_eval.py` 使用经过验证的显式轴变换：

```text
p_cam  = (X_cam, Y_cam, Z_cam)
p_base = (Z_cam + 0.28, -X_cam, -Y_cam + 0.043)
p_world = t_world_base + R_world_base · p_base
```

所以：

- `depth_m≈5` 表示目标距相机约 5 m，是正常值；
- `world_position[2]≈0.15` 才是红球球心的世界高度；
- 若最终 JSON 的第三个坐标约为 5，说明使用了相机坐标或旧 TF 路径，而不是当前代码的 `camera_to_world()` 结果。

## 路线与视觉的职责边界

当前代码采用“路线文件交接”，不是两个节点同时控制机器人：

```text
随机地图生成
    ↓
路线同学生成 route.json（world 坐标系二维航点）
    ↓
live_yolo_route_eval.py 读取 route.json 并独占发布 /cmd_vel
    ↓
视觉在运动过程中持续检测和积累三维观测
    ↓
路线完成后输出 detected_danger.json
```

路线同学不要在运行 `live_yolo_route_eval.py` 的同时再发布 `/cmd_vel`，否则两个控制源会互相覆盖。如果路线模块必须自己在线控制 `/cmd_vel`，当前提交还缺少“纯视觉旁路模式”；在增加该模式前，应先让路线模块导出航点 JSON，再交给本脚本执行。

## 1. 路线文件格式

路线必须保存为 JSON，坐标系是 `world`，每个航点只包含 `[x, y]`：

```json
{
  "name": "random_scene_route",
  "frame": "world",
  "waypoints": [
    [0.0, 6.5],
    [2.0, 6.5],
    [2.0, 10.0],
    [-2.0, 10.0]
  ]
}
```

要求：

- 至少两个航点；
- 每个航点必须是长度为 2 的数组；
- 单位为米；
- 航点必须和 `/gazebo/model_states` 中 `a1_gazebo` 的世界坐标一致；
- 不要把栅格像素、地图数组下标或相机坐标直接写进路线文件。

可在启动视觉前检查路线：

```bash
python3 - <<'PY'
import json

path = "config/current_random_route.json"
payload = json.load(open(path, "r", encoding="utf-8"))
waypoints = payload["waypoints"]
assert len(waypoints) >= 2
assert all(isinstance(point, list) and len(point) == 2 for point in waypoints)
print("route OK, waypoint count =", len(waypoints))
PY
```

## 2. 使用正确的模型

本地验证使用的权重为：

```text
runs/detect/outputs/yolo_runs_route/yolov10s_route/weights/best.pt
```

参考文件信息：

```text
文件大小：16581122 bytes
SHA256：dee58742fbedb29a3a8172dbcb90eed48fac8520ed5c5ea8a4817341139372d8
类别：{0: red_sphere, 1: red_box, 2: green_sphere}
```

权重通常不会随普通 Git 源码自动下载。服务器上的同学必须显式设置绝对路径，不要依赖各自机器不同的默认目录：

```bash
export MODEL_PATH=/absolute/path/to/best.pt
export YOLO_PYTHON=/absolute/path/to/yolo/bin/python
```

启动前检查文件和类别：

```bash
sha256sum "$MODEL_PATH"

"$YOLO_PYTHON" - "$MODEL_PATH" <<'PY'
import sys
import torch
from ultralytics import YOLO

model = YOLO(sys.argv[1])
print("model classes:", model.names)
print("CUDA available:", torch.cuda.is_available())
assert "red_sphere" in model.names.values()
PY
```

`live_yolo_route_eval.py` 会从三类模型中只选择 `red_sphere` 进行在线推理。不要把 YOLO 官方预训练权重、旧训练轮次的 `last.pt` 或其他人的 `best.pt` 误设为 `MODEL_PATH`。

## 3. 代码文件要求

视觉主逻辑位于：

```text
src/danger_detector/scripts/live_yolo_route_eval.py
src/danger_detector/src/danger_detector/vision.py
```

其中：

- `live_yolo_route_eval.py`：调用 YOLO、订阅 ROS 话题、相机到世界坐标变换、路线控制和结果写入；
- `vision.py`：红色区域提取、深度质量门控、球面拟合和多帧地标聚类。

运行时还需要：

```text
src/danger_detector/scripts/route_truth_yolo_pipeline.py
src/danger_detector/package.xml
src/danger_detector/CMakeLists.txt
src/danger_detector/setup.py
src/danger_detector/src/danger_detector/__init__.py
```

缺少 `route_truth_yolo_pipeline.py` 会导致路线函数导入失败；缺少 catkin/Python 包文件会导致 `import danger_detector.vision` 失败。

## 4. 编译和环境检查

在 ROS 容器或 ROS 主机中执行：

```bash
cd /path/to/SimEnv
source /opt/ros/noetic/setup.bash

catkin_make --only-pkg-with-deps danger_detector -j2
source ./devel/setup.bash

python3 -c \
  "from danger_detector.vision import localize_yolo_sphere, cluster_landmarks; print('vision import OK')"
python3 -c \
  "import rospy, cv2; from cv_bridge import CvBridge; print('ROS Python OK')"
"$YOLO_PYTHON" -c \
  "import cv2, torch, ultralytics; print('YOLO Python OK')"
```

这里有两个 Python 进程：

- ROS 主进程使用系统 `python3`，需要 `rospy` 和 `cv_bridge`；
- YOLO worker 使用 `YOLO_PYTHON`，需要 `torch`、`ultralytics` 和 `cv2`。

不要直接用 YOLO conda 环境启动 ROS 主进程，除非该环境也正确安装并配置了 ROS Python 包。

## 5. 启动随机地图和机器人

先由地图/路线同学按团队现有流程启动本轮随机地图、A1、RealSense 和控制器。视觉启动前，至少应存在这些话题：

| 话题 | 类型 | 用途 |
|------|------|------|
| `/real_sense/rgb/image_raw` | `sensor_msgs/Image` | YOLO RGB 输入 |
| `/real_sense/depth/image_raw` | `sensor_msgs/Image` | 深度输入 |
| `/real_sense/rgb/camera_info` | `sensor_msgs/CameraInfo` | 相机内参 |
| `/gazebo/model_states` | `gazebo_msgs/ModelStates` | A1 世界位姿 |
| `/cmd_vel` | `geometry_msgs/Twist` | 路线跟随速度输出 |

检查命令：

```bash
rostopic type /real_sense/rgb/image_raw
rostopic type /real_sense/depth/image_raw
rostopic type /real_sense/rgb/camera_info
rostopic type /gazebo/model_states

rostopic hz /real_sense/rgb/image_raw
rostopic hz /real_sense/depth/image_raw

rostopic echo -n 1 /real_sense/rgb/image_raw/header
rostopic echo -n 1 /real_sense/rgb/camera_info/header
```

当前仿真中 RGB 和深度应约为 `640×480 / 10 Hz`。视觉脚本默认最多以 5 Hz 请求推理。

还要确认 `/gazebo/model_states` 中的机器人名称是 `a1_gazebo`：

```bash
rostopic echo -n 1 /gazebo/model_states/name
```

如果名称不同，通过 `--robot-model-name` 指定实际名称。

## 6. 正确启动视觉与路线执行

假设：

- 随机场景已启动；
- A1 已经站立并进入 RL 模式；
- 路线同学生成了 `config/current_random_route.json`；
- 本轮仿真真值位于 `results/danger_truth.json`；
- 模型和 YOLO Python 已通过前面的检查。

在新的终端运行：

```bash
cd /path/to/SimEnv
source /opt/ros/noetic/setup.bash
source ./devel/setup.bash

export MODEL_PATH=/absolute/path/to/best.pt
export YOLO_PYTHON=/absolute/path/to/yolo/bin/python
export OUTPUT_DIR="$PWD/outputs/current_random_run"

python3 src/danger_detector/scripts/live_yolo_route_eval.py run \
  --model "$MODEL_PATH" \
  --yolo-python "$YOLO_PYTHON" \
  --truth-file "$PWD/results/danger_truth.json" \
  --route-file "$PWD/config/current_random_route.json" \
  --output-dir "$OUTPUT_DIR" \
  --detected-file "$PWD/results/detected_danger.json" \
  --device 0 \
  --conf 0.25 \
  --imgsz 960 \
  --inference-fps 5 \
  --cluster-radius 0.35 \
  --min-cluster-observations 5 \
  --min-strong-observations 2 \
  --camera-x 0.28 \
  --camera-y 0.0 \
  --camera-z 0.043 \
  --duration 2400
```

无桌面服务器不要添加 `--show`。有 GPU 时 `--device 0`；没有 GPU 时改成：

```text
--device cpu
```

脚本启动后会：

1. 加载指定的 `best.pt` 并检查 `red_sphere` 类别；
2. 等待 `/gazebo/model_states` 中的 A1 位姿；
3. 沿路线文件中的航点发布 `/cmd_vel`；
4. 同步 RGB、深度和内参；
5. 对每个 YOLO 框调用 `localize_yolo_sphere()`；
6. 将光学坐标球心正确变换到 `world`；
7. 路线完成后执行 `cluster_landmarks()`；
8. 写出比赛格式 JSON。

如果路线同学更换了传感器话题，可使用：

```text
--rgb-topic
--depth-topic
--camera-info-topic
--model-states-topic
--cmd-topic
```

但不要仅修改图像话题而忘记匹配对应的 `camera_info`。

## 7. 结果文件

对接其他模块时主要读取：

```text
results/detected_danger.json
```

格式为：

```json
{
  "exploration_time": 221.9,
  "detected_danger_sources": [
    {"position": [-8.80, 13.31, 0.15]},
    {"position": [8.25, 19.46, 0.15]}
  ]
}
```

这里的 `position` 必须是 `world` 坐标。正常地面红球球心的第三维应接近球半径 `0.15 m`，不应等于相机到目标的深度。

调试文件位于 `OUTPUT_DIR`：

```text
outputs/current_random_run/
├── frames.jsonl
├── overlay.mp4
├── evaluation_detail.json
└── depth_diagnostics/       # 仅使用 --save-depth-diagnostics 时存在
```

`frames.jsonl` 中每个检测包含：

- `depth_m`：相机前向距离；
- `world_position`：变换后的世界坐标；
- `depth_status`：`strong`、`weak` 或 `rejected`；
- `depth_reason`：质量判断原因；
- `depth_metrics`：有效深度点数、MAD、球半径、RMSE 和平面占比。

`weak` 观测可以关联已有地标，但不能独立生成最终危险源；`rejected` 不进入三维地标池。

## 8. `z≈5 m` 的排查步骤

先查看逐帧日志，不要只看最终 JSON：

```bash
python3 - "$OUTPUT_DIR/frames.jsonl" <<'PY'
import json
import sys

for line in open(sys.argv[1], "r", encoding="utf-8"):
    frame = json.loads(line)
    for detection in frame["detections"]:
        if detection["world_position"] is not None:
            print("depth_m      =", detection["depth_m"])
            print("world_position=", detection["world_position"])
            print("depth_status =", detection["depth_status"])
            raise SystemExit
print("no positioned detection found")
PY
```

正确示例：

```text
depth_m       = 4.85
world_position = [2.13, 17.26, 0.16]
```

错误示例：

```text
depth_m       = 4.85
world_position = [..., ..., 4.85]
```

出现错误示例时依次检查：

1. 是否误用了 `roslaunch danger_detector danger_detector.launch`；
2. 是否把 `DepthObservation.position_camera` 直接写入最终 JSON；
3. 是否把 `depth_m` 当成 `world z`；
4. 是否绕过了 `live_yolo_route_eval.camera_to_world()`；
5. `/gazebo/model_states` 中是否存在正确的 `a1_gazebo` 位姿；
6. `--camera-x/y/z` 是否仍对应当前安装位置 `(0.28, 0.0, 0.043)`；
7. 是否同时运行了另一套旧检测节点并覆盖同一个 JSON。

可以搜索是否有多个写入进程：

```bash
pgrep -af "danger_detector|live_yolo_route_eval"
```

对接时应以 `live_yolo_route_eval.py` 写出的 `detected_danger.json` 为准，并确保旧节点没有使用同一个 `--detected-file` 路径。

## 9. 判断模型是否真的在工作

启动输出中应该打印模型类别和设备。运行过程中可检查：

```bash
test -s "$OUTPUT_DIR/frames.jsonl"
ls -lh "$OUTPUT_DIR/overlay.mp4"
tail -n 1 "$OUTPUT_DIR/frames.jsonl"
```

如果 `frames.jsonl` 一直为空，重点检查：

- RGB、深度和 `camera_info` 是否同时发布；
- 三个消息的时间戳差是否小于同步容差 `0.08 s`；
- `/gazebo/model_states` 是否包含机器人；
- YOLO worker 是否成功加载模型；
- `--device 0` 时容器是否能访问 GPU。

如果有 YOLO 框但没有世界坐标，查看 `depth_reason`：

- `insufficient_masked_depth`：红色区域内有效深度点不足；
- `depth_multimodal`：深度多峰，可能存在遮挡；
- `geometry_conflict`：半径、球面误差或平面比例不符合红球；
- `edge_truncated`：目标处在图像边缘，只作为弱观测；
- `sphere_fit_uncertain`：有候选坐标，但几何证据不足。

不要通过提高 YOLO 置信度阈值解决这些深度问题；当前推荐阈值保持为 `0.25`。

## 10. 固定场景脚本的用途

`run_fixed_floor_detection_eval.sh` 仅用于视觉模块自测和回归，它会重启 Gazebo 并切换到固定高密度场景。路线同学调试随机地图时不要运行这个脚本，否则当前随机场景会被替换。

本地固定场景参考结果：

| 项目 | 结果 |
|------|------|
| 路线 | 57/57 航点完成 |
| 危险源 | 8/8 正确 |
| 漏报 / 虚警 | 0 / 0 |
| 平均 / 最大定位误差 | 0.0284 m / 0.0413 m |

## 11. 单元测试

```bash
cd /path/to/SimEnv

PYTHONPATH=src/danger_detector/src \
python3 -m unittest discover \
  -s src/danger_detector/test \
  -p 'test_*.py' \
  -v
```

测试覆盖 RGB-D 球心定位、灰箱和门框遮挡、红色平面拒绝、贴边弱观测、聚类确定性、子簇合并、桥接点隔离和坐标变换。

## 比赛输出格式

比赛最终读取：

```text
results/detected_danger.json
```

离线评测命令：

```bash
python3 src/building_obstacles/scripts/evaluate_danger.py \
  --truth-file results/danger_truth.json \
  --detected-file results/detected_danger.json \
  --output-file results/evaluation_result.json \
  --verbose
```

如果只生成 `detected_danger.json`，不代表视觉没有运行；它是正式算法输出。`evaluation_result.json` 只有在额外执行真值评测后才会出现。

## 关键代码

| 文件 | 作用 |
|------|------|
| `src/danger_detector/scripts/live_yolo_route_eval.py` | 当前 YOLO ROS 入口、路线控制、世界坐标变换和结果写入 |
| `src/danger_detector/src/danger_detector/vision.py` | RGB-D 质量门控与地标聚类核心 |
| `run_fixed_floor_detection_eval.sh` | 固定场景回归，不用于随机地图日常联调 |
| `src/danger_detector/test/test_vision.py` | 深度定位和聚类测试 |
| `src/danger_detector/test/test_live_yolo_route_eval.py` | 坐标变换、去重和评测测试 |

本文档用于团队内部接入。若路线模块需要保持自己的在线控制器并让视觉完全旁路运行，应先新增不发布 `/cmd_vel`、不要求路线文件的视觉-only ROS 入口；当前提交尚未提供这个运行模式。
