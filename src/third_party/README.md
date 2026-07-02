# Third-party Dependencies

Place large local third-party dependencies for this catkin workspace here.
The actual dependency files are ignored by git.

Expected layout:

```text
src/third_party/
├── libtorch-cu128-sm120/
│   └── libtorch/
│       └── share/cmake/Torch/TorchConfig.cmake
├── lcm/
│   └── install/
│       ├── include/
│       └── lib/
└── cuda_compat/
    └── lib/
```

`unitree_guide` currently reads these paths from:

- `src/third_party/libtorch-cu128-sm120/libtorch`
- `src/third_party/lcm/install`
- `src/third_party/cuda_compat`

