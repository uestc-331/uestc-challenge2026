#!/usr/bin/env bash
set -euo pipefail

WORKSPACE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$WORKSPACE_DIR"

set +u
source /opt/ros/noetic/setup.bash
source "$WORKSPACE_DIR/devel/setup.bash"
set -u

export USER="${USER:-simenv}"
export LOGNAME="${LOGNAME:-$USER}"
export YOLO_CONFIG_DIR="${YOLO_CONFIG_DIR:-/tmp/ultralytics}"
export TORCHINDUCTOR_CACHE_DIR="${TORCHINDUCTOR_CACHE_DIR:-/tmp/torchinductor}"
export MPLCONFIGDIR="${MPLCONFIGDIR:-/tmp/matplotlib}"
mkdir -p "$YOLO_CONFIG_DIR" "$TORCHINDUCTOR_CACHE_DIR" "$MPLCONFIGDIR"

FIXED_SCENE_DIR="${FIXED_SCENE_DIR:-$WORKSPACE_DIR/fixed_scenes/floor0_dense_seed_20550755}"
FIXED_RESULTS_DIR="${FIXED_RESULTS_DIR:-$FIXED_SCENE_DIR/results}"
ROUTE_FILE="${ROUTE_FILE:-$WORKSPACE_DIR/config/fixed_floor0_dense_route.json}"
MODEL_PATH="${MODEL_PATH:-$WORKSPACE_DIR/runs/detect/outputs/yolo_runs_route/yolov10s_route/weights/best.pt}"
RUN_DURATION="${RUN_DURATION:-2400}"
INFERENCE_FPS="${INFERENCE_FPS:-5}"
CONF="${CONF:-0.25}"
IMGSZ="${IMGSZ:-960}"
DEVICE="${DEVICE:-0}"
SHOW="${SHOW:-1}"
SIM_GUI="${SIM_GUI:-true}"
MAX_WZ="${MAX_WZ:-1.0}"
CLUSTER_RADIUS="${CLUSTER_RADIUS:-0.35}"
MIN_CLUSTER_OBSERVATIONS="${MIN_CLUSTER_OBSERVATIONS:-5}"
MIN_STRONG_OBSERVATIONS="${MIN_STRONG_OBSERVATIONS:-2}"
SAVE_DEPTH_DIAGNOSTICS="${SAVE_DEPTH_DIAGNOSTICS:-0}"
RUN_NAME="yolo_eval_$(date +%Y%m%d_%H%M%S)"
OUTPUT_DIR="$WORKSPACE_DIR/outputs/yolo_route_eval/$RUN_NAME"
CONTROLLER_BIN="$WORKSPACE_DIR/devel/lib/unitree_guide/junior_ctrl"
CONTROLLER_SOURCE="$WORKSPACE_DIR/src/unitree_guide/unitree_guide/unitree_guide/src/interface/KeyBoard.cpp"

if pgrep -f "live_yolo_route_eval.py run" >/dev/null; then
  echo "检测到已有跑图检测进程，请先在原终端按 Ctrl+C，或结束旧进程后再运行。" >&2
  exit 1
fi

if [ ! -x "$CONTROLLER_BIN" ] || [ "$CONTROLLER_SOURCE" -nt "$CONTROLLER_BIN" ]; then
  echo "junior_ctrl 尚未包含自动 RL 修改，请先重新编译：" >&2
  echo "catkin_make --only-pkg-with-deps unitree_guide danger_detector -DTORCH_ROOT=/host/libtorch-cpu-cxx11 -j2" >&2
  exit 1
fi

if [ ! -f "$MODEL_PATH" ]; then
  echo "找不到模型权重：$MODEL_PATH" >&2
  exit 1
fi

if [ ! -f "$ROUTE_FILE" ]; then
  echo "找不到固定路线：$ROUTE_FILE" >&2
  exit 1
fi

if [ -z "${YOLO_PYTHON:-}" ]; then
  for candidate in /host/yolo/bin/python /host/torch116/bin/python "$(command -v python3)"; do
    if [ -x "$candidate" ] && "$candidate" -c "import cv2, torch, ultralytics" >/dev/null 2>&1; then
      YOLO_PYTHON="$candidate"
      break
    fi
  done
fi
if [ -z "${YOLO_PYTHON:-}" ] || [ ! -x "$YOLO_PYTHON" ]; then
  echo "找不到同时包含 ultralytics、PyTorch 和 OpenCV 的 Python。" >&2
  echo "请把主机 yolo 环境映射为 /host/yolo，或设置 YOLO_PYTHON=/实际路径/bin/python。" >&2
  exit 1
fi

python3 -c "import rospy, cv2; from cv_bridge import CvBridge"
"$YOLO_PYTHON" -c '
import sys, torch
from ultralytics import YOLO
model = YOLO(sys.argv[1])
if "red_sphere" not in model.names.values():
    raise SystemExit("模型中没有 red_sphere 类别")
device = sys.argv[2]
if device != "cpu" and not torch.cuda.is_available():
    raise SystemExit("YOLO 环境无法访问 GPU；请检查 --gpus all 和显卡映射，或设置 DEVICE=cpu")
print("YOLO模型:", sys.argv[1])
print("类别:", model.names)
print("设备:", device)
' "$MODEL_PATH" "$DEVICE"

if [ ! -f "$FIXED_SCENE_DIR/competition_scene.world" ]; then
  echo "首次运行：生成固定单层地图..."
  mkdir -p "$FIXED_SCENE_DIR" "$FIXED_RESULTS_DIR"
  python3 "$WORKSPACE_DIR/src/building_obstacles/scripts/generate_competition_scene.py" \
    --output-dir "$FIXED_SCENE_DIR" \
    --results-dir "$FIXED_RESULTS_DIR" \
    --seed 20550755 \
    --floor-count 1 \
    --rooms-per-floor 4 \
    --width 20.0 \
    --length 36.0 \
    --danger-count 8 \
    --distractor-count 16 \
    --extra-obstacles-per-room 2 \
    --robot-x 0.0 \
    --robot-y 6.5 \
    --robot-z 0.6 \
    --robot-yaw 1.5708 \
    > "$FIXED_SCENE_DIR/scene_manifest.stdout.json"
else
  echo "复用固定地图：$FIXED_SCENE_DIR/competition_scene.world"
fi

GENERATE_SCENE=0 \
SCENE_OUTPUT_DIR="$FIXED_SCENE_DIR" \
RESULTS_DIR="$FIXED_RESULTS_DIR" \
ROBOT_X=0.0 \
ROBOT_Y=6.5 \
ROBOT_Z=0.6 \
ROBOT_YAW=1.5708 \
GUI="$SIM_GUI" \
PAUSED=false \
START_CONTROLLER=1 \
CONTROLLER_FOREGROUND=0 \
UNITREE_AUTO_RL=1 \
"$WORKSPACE_DIR/auto.sh"

echo "等待机器狗自动站立并进入 RL 模式..."
sleep 6
mkdir -p "$OUTPUT_DIR"
if [ -n "${RUN_OUTPUT_POINTER:-}" ]; then
  printf '%s\n' "$OUTPUT_DIR" > "$RUN_OUTPUT_POINTER"
fi

SHOW_ARGS=()
if [ "$SHOW" = "1" ]; then
  SHOW_ARGS+=(--show)
fi
DEPTH_DIAGNOSTIC_ARGS=()
if [ "$SAVE_DEPTH_DIAGNOSTICS" = "1" ]; then
  DEPTH_DIAGNOSTIC_ARGS+=(--save-depth-diagnostics)
fi

set +e
MPLCONFIGDIR=/tmp/matplotlib python3 \
  "$WORKSPACE_DIR/src/danger_detector/scripts/live_yolo_route_eval.py" run \
  --model "$MODEL_PATH" \
  --yolo-python "$YOLO_PYTHON" \
  --truth-file "$FIXED_RESULTS_DIR/danger_truth.json" \
  --route-file "$ROUTE_FILE" \
  --output-dir "$OUTPUT_DIR" \
  --detected-file "$OUTPUT_DIR/detected_danger.json" \
  --duration "$RUN_DURATION" \
  --inference-fps "$INFERENCE_FPS" \
  --conf "$CONF" \
  --imgsz "$IMGSZ" \
  --device "$DEVICE" \
  --max-wz "$MAX_WZ" \
  --cluster-radius "$CLUSTER_RADIUS" \
  --min-cluster-observations "$MIN_CLUSTER_OBSERVATIONS" \
  --min-strong-observations "$MIN_STRONG_OBSERVATIONS" \
  "${SHOW_ARGS[@]}" \
  "${DEPTH_DIAGNOSTIC_ARGS[@]}"
DETECT_STATUS=$?
set -e

if [ -f "$OUTPUT_DIR/detected_danger.json" ]; then
  python3 "$WORKSPACE_DIR/src/building_obstacles/scripts/evaluate_danger.py" \
    --truth-file "$FIXED_RESULTS_DIR/danger_truth.json" \
    --detected-file "$OUTPUT_DIR/detected_danger.json" \
    --output-file "$OUTPUT_DIR/evaluation_result.json" \
    --verbose
fi

echo "检测与评测输出：$OUTPUT_DIR"
exit "$DETECT_STATUS"
