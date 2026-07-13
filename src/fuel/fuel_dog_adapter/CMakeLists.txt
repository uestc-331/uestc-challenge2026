cmake_minimum_required(VERSION 3.0.2)
project(fuel_dog_adapter)

find_package(catkin REQUIRED COMPONENTS
  building_generator_interfaces
  geometry_msgs
  nav_msgs
  rospy
  std_msgs
  tf
  visualization_msgs
)

catkin_package()

catkin_install_python(PROGRAMS
  scripts/fuel_poscmd_to_cmdvel.py
  scripts/cmd_vel_marker.py
  scripts/fuel_path_visualizer.py
  scripts/sensor_fov_marker.py
  scripts/odom_to_sensor_pose.py
  scripts/dynamic_exploration_box.py
  scripts/fuel_multifloor_mission.py
  DESTINATION ${CATKIN_PACKAGE_BIN_DESTINATION}
)
