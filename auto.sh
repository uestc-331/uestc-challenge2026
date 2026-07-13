#!/usr/bin/env bash
set -euo pipefail

WORKSPACE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$WORKSPACE_DIR"

SEED="${SEED:-}"
FLOOR_COUNT="${FLOOR_COUNT:-3}"
ROOMS_PER_FLOOR="${ROOMS_PER_FLOOR:-4}"
BUILDING_WIDTH="${BUILDING_WIDTH:-20.0}"
BUILDING_LENGTH="${BUILDING_LENGTH:-36.0}"
DANGER_COUNT="${DANGER_COUNT:-3:6}"
DISTRACTOR_COUNT="${DISTRACTOR_COUNT:-4:8}"
GUI="${GUI:-true}"
PAUSED="${PAUSED:-true}"
START_CONTROLLER="${START_CONTROLLER:-1}"
START_VIRTUAL_JOY="${START_VIRTUAL_JOY:-0}"
START_JOY_NODE="${START_JOY_NODE:-0}"
CONTROLLER_FOREGROUND="${CONTROLLER_FOREGROUND:-1}"
START_BUILDING_CONTROL="${START_BUILDING_CONTROL:-1}"
RESET_ROS_MASTER="${RESET_ROS_MASTER:-1}"
UNITREE_CTRL_DT="${UNITREE_CTRL_DT:-0.004}"
SIM_FAST="${SIM_FAST:-0}"
ROBOT_X="${ROBOT_X:-0.0}"
ROBOT_Y="${ROBOT_Y:--1.5}"
ROBOT_Z="${ROBOT_Z:-0.6}"
ROBOT_YAW="${ROBOT_YAW:-1.5708}"

if [ "$SIM_FAST" = "1" ]; then
  ENABLE_REALSENSE="${ENABLE_REALSENSE:-false}"
  ENABLE_LIVOX="${ENABLE_LIVOX:-false}"
  ENABLE_CAMERA="${ENABLE_CAMERA:-true}"
  ENABLE_LIVOX_CONVERTER="${ENABLE_LIVOX_CONVERTER:-0}"
  CONTACT_UPDATE_RATE="${CONTACT_UPDATE_RATE:-40}"
  IMU_UPDATE_RATE="${IMU_UPDATE_RATE:-250}"
  LIVOX_UPDATE_RATE="${LIVOX_UPDATE_RATE:-5}"
  LIVOX_SAMPLES="${LIVOX_SAMPLES:-6000}"
  LIVOX_DOWNSAMPLE="${LIVOX_DOWNSAMPLE:-4}"
  CAMERA_UPDATE_RATE="${CAMERA_UPDATE_RATE:-15}"
  CAMERA_WIDTH="${CAMERA_WIDTH:-640}"
  CAMERA_HEIGHT="${CAMERA_HEIGHT:-480}"
  REALSENSE_UPDATE_RATE="${REALSENSE_UPDATE_RATE:-5}"
  REALSENSE_WIDTH="${REALSENSE_WIDTH:-320}"
  REALSENSE_HEIGHT="${REALSENSE_HEIGHT:-240}"
  PHYSICS_MAX_STEP_SIZE="${PHYSICS_MAX_STEP_SIZE:-0.001}"
  PHYSICS_REAL_TIME_UPDATE_RATE="${PHYSICS_REAL_TIME_UPDATE_RATE:-1000}"
else
  ENABLE_REALSENSE="${ENABLE_REALSENSE:-true}"
  ENABLE_LIVOX="${ENABLE_LIVOX:-true}"
  ENABLE_CAMERA="${ENABLE_CAMERA:-true}"
  ENABLE_LIVOX_CONVERTER="${ENABLE_LIVOX_CONVERTER:-1}"
  CONTACT_UPDATE_RATE="${CONTACT_UPDATE_RATE:-100}"
  IMU_UPDATE_RATE="${IMU_UPDATE_RATE:-1000}"
  LIVOX_UPDATE_RATE="${LIVOX_UPDATE_RATE:-10}"
  LIVOX_SAMPLES="${LIVOX_SAMPLES:-24000}"
  LIVOX_DOWNSAMPLE="${LIVOX_DOWNSAMPLE:-1}"
  CAMERA_UPDATE_RATE="${CAMERA_UPDATE_RATE:-30}"
  CAMERA_WIDTH="${CAMERA_WIDTH:-800}"
  CAMERA_HEIGHT="${CAMERA_HEIGHT:-800}"
  REALSENSE_UPDATE_RATE="${REALSENSE_UPDATE_RATE:-10}"
  REALSENSE_WIDTH="${REALSENSE_WIDTH:-640}"
  REALSENSE_HEIGHT="${REALSENSE_HEIGHT:-480}"
  PHYSICS_MAX_STEP_SIZE="${PHYSICS_MAX_STEP_SIZE:-0.001}"
  PHYSICS_REAL_TIME_UPDATE_RATE="${PHYSICS_REAL_TIME_UPDATE_RATE:-1000}"
fi

echo "Terminating previous Gazebo, launch, controller, and optional joystick processes..."
pkill -f "[r]oslaunch unitree_guide multi_floor_gazeboSim.launch" 2>/dev/null || true
pkill -f "building_generator_classic_control" 2>/dev/null || true
pkill -x "gzserver" 2>/dev/null || true
pkill -x "gzclient" 2>/dev/null || true
pkill -x "gazebo" 2>/dev/null || true
pkill -f "[j]unior_ctrl" 2>/dev/null || true
pkill -f "[v]irtual_joy.py" 2>/dev/null || true
pkill -f "[s]tate_from_gazebo" 2>/dev/null || true
pkill -f "[p]ointcloud2livox.py" 2>/dev/null || true
pkill -f "[r]obot_state_publisher" 2>/dev/null || true
pkill -f "[s]pawner joint_state_controller" 2>/dev/null || true
if [ "$RESET_ROS_MASTER" = "1" ]; then
  pkill -f "[r]osmaster --core -p 11311" 2>/dev/null || true
fi
sleep 1

echo "Sourcing ROS environment..."
source /opt/ros/noetic/setup.bash
source "$WORKSPACE_DIR/devel/setup.bash"

BUILDING_OBSTACLES_DIR="$(rospack find building_obstacles)"
UNITREE_GAZEBO_MODELS="$(rospack find unitree_gazebo)/models"
SCENE_OUTPUT_DIR="$WORKSPACE_DIR/generated_building"
RESULTS_DIR="$WORKSPACE_DIR/results"
mkdir -p "$SCENE_OUTPUT_DIR" "$RESULTS_DIR" "$WORKSPACE_DIR/logs"

echo "Generating competition scene..."
GENERATOR_ARGS=(
  --output-dir "$SCENE_OUTPUT_DIR"
  --results-dir "$RESULTS_DIR"
  --floor-count "$FLOOR_COUNT"
  --rooms-per-floor "$ROOMS_PER_FLOOR"
  --width "$BUILDING_WIDTH"
  --length "$BUILDING_LENGTH"
  --danger-count "$DANGER_COUNT"
  --distractor-count "$DISTRACTOR_COUNT"
  --robot-x "$ROBOT_X"
  --robot-y "$ROBOT_Y"
  --robot-z "$ROBOT_Z"
  --robot-yaw "$ROBOT_YAW"
  --physics-max-step-size "$PHYSICS_MAX_STEP_SIZE"
  --physics-real-time-update-rate "$PHYSICS_REAL_TIME_UPDATE_RATE"
)
if [ -n "$SEED" ]; then
  GENERATOR_ARGS+=(--seed "$SEED")
fi
python3 "$BUILDING_OBSTACLES_DIR/scripts/generate_competition_scene.py" "${GENERATOR_ARGS[@]}" \
  > "$SCENE_OUTPUT_DIR/scene_manifest.stdout.json"

export BUILDING_WORLD_FILE="$SCENE_OUTPUT_DIR/competition_scene.world"
export COMPETITION_ROBOT_X="$ROBOT_X"
export COMPETITION_ROBOT_Y="$ROBOT_Y"
export COMPETITION_ROBOT_Z="$ROBOT_Z"
export COMPETITION_ROBOT_YAW="$ROBOT_YAW"
export UNITREE_CTRL_DT
export GAZEBO_PLUGIN_PATH="$WORKSPACE_DIR/devel/lib:${GAZEBO_PLUGIN_PATH:-}"
export GAZEBO_MODEL_PATH="${GAZEBO_MODEL_PATH:-}:$SCENE_OUTPUT_DIR:$UNITREE_GAZEBO_MODELS"

echo "=========================================="
echo "Competition scene is ready"
echo "  World:   $BUILDING_WORLD_FILE"
echo "  Truth:   $RESULTS_DIR/danger_truth.json"
echo "  Manifest:$SCENE_OUTPUT_DIR/scene_manifest.json"
echo "  Result:  $RESULTS_DIR/detected_danger.json"
echo "  Profile: SIM_FAST=$SIM_FAST realsense=$ENABLE_REALSENSE livox=$ENABLE_LIVOX livox_converter=$ENABLE_LIVOX_CONVERTER"
echo "=========================================="

if [ "$START_VIRTUAL_JOY" = "1" ]; then
  echo "Starting virtual joystick. This may require uinput permissions."
  rosrun unitree_guide virtual_joy.py > "$WORKSPACE_DIR/logs/virtual_joy.log" 2>&1 &
  echo $! > "$WORKSPACE_DIR/logs/virtual_joy.pid"
fi

echo "Launching Gazebo, Unitree A1 model, sensors, and ROS interfaces..."
setsid nohup roslaunch unitree_guide multi_floor_gazeboSim.launch \
  gui:="$GUI" \
  paused:="$PAUSED" \
  start_joy:="$START_JOY_NODE" \
  enable_realsense:="$ENABLE_REALSENSE" \
  enable_livox:="$ENABLE_LIVOX" \
  enable_camera:="$ENABLE_CAMERA" \
  enable_livox_converter:="$ENABLE_LIVOX_CONVERTER" \
  contact_update_rate:="$CONTACT_UPDATE_RATE" \
  imu_update_rate:="$IMU_UPDATE_RATE" \
  livox_update_rate:="$LIVOX_UPDATE_RATE" \
  livox_samples:="$LIVOX_SAMPLES" \
  livox_downsample:="$LIVOX_DOWNSAMPLE" \
  camera_update_rate:="$CAMERA_UPDATE_RATE" \
  camera_width:="$CAMERA_WIDTH" \
  camera_height:="$CAMERA_HEIGHT" \
  realsense_update_rate:="$REALSENSE_UPDATE_RATE" \
  realsense_width:="$REALSENSE_WIDTH" \
  realsense_height:="$REALSENSE_HEIGHT" \
  user_debug:=False \
  rname:=a1 \
  robot_x:="$ROBOT_X" \
  robot_y:="$ROBOT_Y" \
  robot_z:="$ROBOT_Z" \
  robot_yaw:="$ROBOT_YAW" \
  </dev/null \
  > "$WORKSPACE_DIR/logs/competition_gazebo.log" 2>&1 &
LAUNCH_PID=$!
echo "$LAUNCH_PID" > "$WORKSPACE_DIR/logs/competition_gazebo.pid"
sleep 6

if [ "$START_BUILDING_CONTROL" = "1" ]; then
  echo "Starting building door/elevator control service..."
  rosrun building_generator_classic building_generator_classic_control \
    --door-config "$SCENE_OUTPUT_DIR/door_config.yaml" \
    --elevator-config "$SCENE_OUTPUT_DIR/elevator_config.yaml" \
    > "$WORKSPACE_DIR/logs/building_control.log" 2>&1 &
  echo $! > "$WORKSPACE_DIR/logs/building_control.pid"
fi

if [ "$START_CONTROLLER" = "1" ]; then
  if [ "$CONTROLLER_FOREGROUND" = "1" ]; then
    echo "Starting junior_ctrl controller in the foreground."
    echo "UNITREE_CTRL_DT=$UNITREE_CTRL_DT seconds."
    echo "Use keyboard input in this terminal: 2 = stand, 6 = RL mode."
    "$WORKSPACE_DIR/devel/lib/unitree_guide/junior_ctrl"
  else
    echo "Starting junior_ctrl controller in the background. Keyboard state switching may not be available."
    echo "UNITREE_CTRL_DT=$UNITREE_CTRL_DT seconds."
    "$WORKSPACE_DIR/devel/lib/unitree_guide/junior_ctrl" \
      > "$WORKSPACE_DIR/logs/junior_ctrl.log" 2>&1 &
    echo $! > "$WORKSPACE_DIR/logs/junior_ctrl.pid"
  fi
fi

echo "Simulation startup command completed."
echo "Controller mode remains governed by unitree_guide keyboard/joy input; publish geometry_msgs/Twist to /cmd_vel after RL mode is enabled."
