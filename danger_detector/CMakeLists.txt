cmake_minimum_required(VERSION 3.0.2)
project(danger_detector)

find_package(catkin REQUIRED COMPONENTS
  cv_bridge
  gazebo_msgs
  geometry_msgs
  message_filters
  nav_msgs
  rospy
  sensor_msgs
  std_msgs
  tf2_geometry_msgs
  tf2_ros
  visualization_msgs
)

catkin_python_setup()

catkin_package()

catkin_install_python(PROGRAMS
  scripts/box_red_balls_dataset.py
  scripts/collect_yolo_dataset_from_truth.py
  scripts/danger_detector_node.py
  scripts/live_yolo_route_eval.py
  scripts/record_yolo_depth_demo.py
  scripts/route_truth_yolo_pipeline.py
  scripts/teleport_yolo_viewpoints.py
  scripts/train_yolov10_models.py
  DESTINATION ${CATKIN_PACKAGE_BIN_DESTINATION}
)

install(DIRECTORY launch
  DESTINATION ${CATKIN_PACKAGE_SHARE_DESTINATION}
)

if(CATKIN_ENABLE_TESTING)
  catkin_add_nosetests(test)
endif()
