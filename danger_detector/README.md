# danger_detector

`danger_detector` 是面向 SimEnv 比赛环境的红色球形危险源识别包。它使用 RealSense RGB-D 输入进行候选检测、深度定位和多帧融合，最终输出比赛要求的危险源坐标文件。

运行时不读取 `danger_truth.json`、`layout_metadata.json` 或其他真值/生成元数据文件。

## 运行方式

在 SimEnv 的 catkin 工作空间中构建并启动：

```bash
cd /home/karl/challenge/SimEnv
source /opt/ros/noetic/setup.bash
catkin_make --only-pkg-with-deps danger_detector
source ./devel/setup.bash
roslaunch danger_detector danger_detector.launch
```

节点订阅 RealSense RGB-D 相关话题，并持续写出：

```text
results/detected_danger.json
```

同时会发布聚类后的危险源候选点，便于在 RViz 中调试：

```text
/vision/danger_candidates
```

## 识别方法

- 使用 HSV 红色双区间和 RGB 通道比例约束分割红色区域。
- 对 2D 连通域按面积、长宽比、填充率和圆度筛选，先排除明显红色方块。
- 根据深度图和相机内参把候选像素反投影到相机坐标系。
- 对候选 3D 点拟合球体：接受半径接近危险源预期尺寸的目标，拒绝主要呈平面的红色方块面片。
- 使用 TF 将相机坐标系下的中心点转换到 `world` 坐标系；TF 不可用时，用 `/Odometry_gazebo` 做兜底估计。
- 对多帧观测做半径聚类，同一个危险源只输出一个三维坐标。

## 离线数据集框选

本机 `yolo` conda 环境包含 OpenCV，可用于批量处理已有数据集：

```bash
cd /home/karl/challenge/SimEnv
PYTHONPATH=src/danger_detector/src conda run -n yolo python \
  src/danger_detector/scripts/box_red_balls_dataset.py \
  --input-dir /home/karl/challenge/datasets/room_objects/run_7.1 \
  --output-dir outputs/red_ball_boxes/run_7.1 \
  --label-dir outputs/yolo_labels/run_7.1
```

输出内容：

- `outputs/red_ball_boxes/run_7.1`：带红球候选框的调试图片。
- `outputs/red_ball_boxes/run_7.1/red_ball_boxes.csv`：候选框、分数和几何指标。
- `outputs/yolo_labels/run_7.1`：YOLO 格式伪标签，可作为初始训练标注。

这些离线框选结果适合用来启动 YOLO 数据标注流程，但正式比赛运行时仍应只使用传感器话题。

## 后续 YOLO 方案

YOLO 不建议只作为“简单训练一个检测器”来汇报。推荐把它作为 RGB-D 危险源识别框架中的 2D 候选生成模块，再结合深度反投影、球面拟合、红方块硬负样本、多帧聚类和模型轻量化形成完整方案。

详细方案见：

```text
docs/yolo-depth-danger-detection.md
```
