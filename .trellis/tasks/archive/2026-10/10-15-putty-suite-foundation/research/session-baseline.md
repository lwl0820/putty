# 实施会话基线

日期：2026-10-15。用户最终方案获批后启动共享基础子任务，开发分支为 `codex/putty-suite-completion`。

## 工作区与工具

- 工作区中已有 Trellis、平台技能和任务目录等未跟踪文件，全部保留；没有将它们作为产品改动一起提交。
- 本机：Arch Linux x64，GCC/G++ 16.2.1，CMake 4.4.4，Python 3.14.8，系统 Qt 6.12.0。
- 本机构建只能提供开发回归，不能代替锁定 Qt 6.8.3 或目标 Windows/Ubuntu 平台验收。
- 发现现有 Ubuntu 22.04/24.04 构建镜像及 `.tmp/qt-ubuntu22.04/`、`.tmp/qt-ubuntu24.04/` 前缀。只读探针确认 22.04 镜像 GCC 11.4.0，内置 CMake 3.22.1，后续需使用现有项目内较新 CMake。

## 原有 PuTTYgen 回归

命令：

```bash
ctest --test-dir .tmp/build/gui-dev --output-on-failure
```

结果：3/3 通过，耗时 15.44 秒。`core-roundtrip` 6.49 秒、`core-interop` 2.89 秒、`gui-interactions` 6.06 秒。测试夹具、偏好、运行目录与截图均由原 CTest 配置置于项目 `.tmp/`，GUI 使用 offscreen。

此结果仅为修改前的本机基线。共享构建完成后仍需执行受影响回归，不将这次结果视为新核心、三个 CLI 或真实桌面通过。

## 并行边界

- 构建实施负责根 CMake、`cmake/Suite*` 和三个 CLI 构建入口。
- 运行时实施负责 `libs/putty-runtime/`。
- 夹具实施负责 `tools/suite/`。
- 主会话负责集成、目标系统验证和任务证据；各代理不得回退其它工作。

所有新增临时数据位于项目 `.tmp/`，没有运行个人 Pageant、读取个人会话或写入个人配置。
