cmake_minimum_required(VERSION 3.0.2)
project(competition_tools)

find_package(catkin REQUIRED COMPONENTS
  rospy
  geometry_msgs
  sensor_msgs
  cv_bridge
  building_generator_interfaces
)

catkin_package()

catkin_install_python(
  PROGRAMS
    scripts/keyboard_competition_control.py
    scripts/record_room_dataset.py
  DESTINATION ${CATKIN_PACKAGE_BIN_DESTINATION}
)
