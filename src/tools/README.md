# competition_tools

键盘控制比赛仿真的辅助工具。

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

- `W/S`：前进 / 后退
- `A/D`：左移 / 右移
- `Q/E`：左转 / 右转
- `Space` 或 `X`：停止
- `+/-`：增减线速度
- `[` / `]`：增减角速度
- `1` 到 `9`：按 `generated_building/door_config.yaml` 中的动态门顺序切换开关
- `F1/F2/F3...`：呼叫电梯到楼层索引 `0/1/2...`
- `Esc` 或 `Ctrl-C`：退出，并发送零速度

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

默认速度为 `0.45 m/s` 和 `0.9 rad/s`。也可以启动时指定：

```bash
rosrun competition_tools keyboard_competition_control.py --linear-speed 0.7 --angular-speed 1.2
```
