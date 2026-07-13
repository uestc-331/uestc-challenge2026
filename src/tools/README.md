# competition_tools

键盘控制比赛仿真的辅助工具。

## 房间物品数据集自动录制

`record_room_dataset.py` 会自动启动仿真，固定一层楼和十个房间，默认打开 Gazebo GUI，关闭 Livox 和 RealSense，仅保留前视相机；随后给前台 `junior_ctrl` 输入 `2` 和 `6`，打开主大门，依次移动到每个房间门口并左右扫视录制图像。
脚本会在每一轮重新创建对应的 `run_XXX` 目录，避免日志和数据追加到上一次结果里。

```bash
cd /home/uestc/SimEnv
source /opt/ros/noetic/setup.bash
source devel/setup.bash
rosrun competition_tools record_room_dataset.py --runs 3
```

默认输出到 `datasets/room_objects/run_001/room_01_...`。运行时会打印当前完成到第几次、第几个门。
如果需要无界面采集，可以加 `--no-gui`。

## 启动流程

1. 启动仿真：

   ```bash
   cd /home/uestc/SimEnv
   ./auto.sh
   ```

2. 在 `junior_ctrl` 终端里先按 `2`，再按 `6`，进入 RL 控制模式。

3. 新开终端启动键盘工具：

   ```bash
   cd /home/uestc/SimEnv
   source devel/setup.bash
   rosrun competition_tools keyboard_competition_control.py --show-camera
   ```

   如果只想控制运动和门，不打开相机窗口：

   ```bash
   rosrun competition_tools keyboard_competition_control.py
   ```

## 快捷键

- `W/S`：前进速度增大 / 减小
- `A/D`：左移速度增大 / 减小
- `Q/E`：左转角速度增大 / 减小
- `Space` 或 `X`：停止
- `+/-`：增减线速度
- `[` / `]`：增减角速度
- `1` 到 `9`：切换动态门开关，默认按下面规则绑定
- `F1/F2/F3...`：呼叫电梯到楼层索引 `0/1/2...`
- `Esc` 或 `Ctrl-C`：退出，并发送零速度

## 门按键

脚本会读取 `/home/uestc/SimEnv/generated_building/door_config.yaml`，然后按固定规则排序：

| 按键 | 门 ID | 作用 |
| --- | --- | --- |
| `1` | `main_entrance` | 主入口门开/关 |
| `2` | `elevator_floor_0` | 1 楼电梯厅门开/关 |
| `3` | `elevator_floor_1` | 2 楼电梯厅门开/关 |
| `4` | `elevator_floor_2` | 3 楼电梯厅门开/关 |
| `5` | `elevator_floor_3` | 4 楼电梯厅门开/关，本次场景存在该楼层时有效 |
| `6` | `elevator_floor_4` | 5 楼电梯厅门开/关，本次场景存在该楼层时有效 |

如果本次场景没有对应楼层，按下该数字会提示没有绑定的门。工具界面里也会实时显示每个数字当前绑定的门和状态。

电梯轿厢不是数字键控制：

| 按键 | 作用 |
| --- | --- |
| `F1` | 呼叫电梯到楼层索引 `0`，也就是 1 楼 |
| `F2` | 呼叫电梯到楼层索引 `1`，也就是 2 楼 |
| `F3` | 呼叫电梯到楼层索引 `2`，也就是 3 楼 |
| `F4` | 呼叫电梯到楼层索引 `3`，也就是 4 楼，本次场景存在该楼层时有效 |
| `F5` | 呼叫电梯到楼层索引 `4`，也就是 5 楼，本次场景存在该楼层时有效 |

## 相机

`--show-camera` 会自动查找 `sensor_msgs/Image` 话题，优先使用：

- `/camera/image_raw`
- `/camera/rgb/image_raw`
- `/real_sense/rgb/image_raw`
- `/real_sense/depth/image_raw`

也可以手动指定：

```bash
rosrun competition_tools keyboard_competition_control.py --show-camera --image-topic /camera/image_raw
```

运动按键是增量控制：例如按一次 `Q` 会让 `angular.z` 增加一个步长，再按一次 `E` 会减回去，而不是直接跳到反方向。`Space` 或 `X` 会把 `/cmd_vel` 全部清零。

默认每次按键步长为 `0.2 m/s` 和 `0.3 rad/s`，速度上限为 `2.5 m/s` 和 `3.0 rad/s`。

也可以启动时指定：

```bash
rosrun competition_tools keyboard_competition_control.py --linear-step 0.3 --angular-step 0.5
```

如果想让松开键盘后自动回零：

```bash
rosrun competition_tools keyboard_competition_control.py --command-timeout 0.25
```
