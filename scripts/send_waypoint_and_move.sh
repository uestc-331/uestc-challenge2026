#!/usr/bin/env bash
set -euo pipefail

# Source ROS
source /opt/ros/noetic/setup.bash
source "$(dirname "$0")/../devel/setup.bash" 2>/dev/null || true

echo "[$(date '+%H:%M:%S')] Publishing waypoint..."
rostopic pub -1 /waypoint_generator/waypoints nav_msgs/Path "
header:
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

echo "[$(date '+%H:%M:%S')] Waiting 2 seconds..."
sleep 2

echo "[$(date '+%H:%M:%S')] Publishing /cmd_vel linear.x=0.5 for 1 second..."
rostopic pub -r 30 /cmd_vel geometry_msgs/Twist "
linear:
  x: 0.5
  y: 0.0
  z: 0.0
angular:
  x: 0.0
  y: 0.0
  z: 0.0" &
PID=$!
sleep 2
kill $PID 2>/dev/null || true

# Stop the robot
rostopic pub -1 /cmd_vel geometry_msgs/Twist "
linear:
  x: 0.0
  y: 0.0
  z: 0.0
angular:
  x: 0.0
  y: 0.0
  z: 0.0"

echo "[$(date '+%H:%M:%S')] Done."
