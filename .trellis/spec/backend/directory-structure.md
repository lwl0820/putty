# 套件与密钥核心目录

本仓库属于 PuTTY 套件；当前可构建的图形组件是 PuTTYgen。组件代码、测试、资源和打包文件放在 `apps/puttygen/`，不得重新占用根目录的 `src/`、`tests/` 或 `packaging/`。

| 路径 | 职责 |
| --- | --- |
| `CMakeLists.txt` | 套件入口，通过 `PUTTY_BUILD_PUTTYGEN` 选择组件 |
| `apps/puttygen/src/core/` | 自有 C 密钥适配、平台随机和文件操作 |
| `apps/puttygen/tests/core/` | 核心矩阵和独立互操作检查 |
| `vendor/putty/` | 固定版本官方源码，其他组件可复用 |
| `vendor/patches/` | 对上游的必要补丁及来源记录 |
| `cmake/UpstreamPuTTY.cmake` | 上游最小密钥库构建 |
| `tools/qt/` | 公共 Qt 源码构建脚本与版本锁 |
| `tools/ubuntu-build-dependencies.sh` | 目标 Ubuntu 构建依赖 |
| `.tmp/` | 所有构建、下载、夹具、截图及验证报告 |

核心接口仍服务于 PuTTYgen，不因套件命名提前抽象成多组件业务接口。新增套件组件时独立建立 `apps/<组件>/`，只有已有实际复用需求的构建工具和第三方依赖才放在公共目录。

组件 CMake 使用组件源码根定位内部资源，使用套件源码根定位 `vendor/`、公共许可和 `.tmp/`。构建路径变化不应改变上游 target 名、C 接口和密钥行为。关闭组件的配置应不依赖 Qt。

质量检查包含组件启用时的完整 CTest，以及关闭 `PUTTY_BUILD_PUTTYGEN` 的独立配置与构建。
