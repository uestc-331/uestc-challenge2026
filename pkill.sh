#!/usr/bin/env bash

pkill -f gzserver
pkill -f gzclient
pkill -f pointcloud2livo
pkill -f state_from_gaze
pkill -f robot_state_pub
pkill -f rosmaster