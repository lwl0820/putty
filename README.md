# PuTTY

这是 PuTTY 套件的跨平台桌面项目。组件放在 `apps/`，固定上游源码和公共构建工具分别放在 `vendor/`、`cmake/` 和 `tools/`。

目前实现 [PuTTYgen GUI](apps/puttygen/README.md)：使用官方 PuTTY 0.85 密钥核心，为 Windows 和 Linux 提供传统图形操作、中英双语以及密钥格式转换。其他套件工具尚未移植；本期目标系统、功能与验证边界见组件说明。

在仓库根目录构建 PuTTYgen，需要 CMake 3.24 以上、Ninja、C99/C++17 编译器和 Qt 6.8 以上的 Widgets 与 Test 开发模块：

```bash
cmake -S . -B .tmp/build/debug -G Ninja \
  -DPUTTY_BUILD_PUTTYGEN=ON -DCMAKE_BUILD_TYPE=Debug -DBUILD_TESTING=ON
cmake --build .tmp/build/debug --parallel
ctest --test-dir .tmp/build/debug --output-on-failure
```

`PUTTY_BUILD_PUTTYGEN` 默认开启。关闭时不配置组件、寻找 Qt 或构建密钥目标，可作为后续套件组件的顶层入口：

```bash
cmake -S . -B .tmp/build/suite-only -G Ninja -DPUTTY_BUILD_PUTTYGEN=OFF
cmake --build .tmp/build/suite-only
```

目录责任如下：

| 路径 | 内容 |
| --- | --- |
| `apps/puttygen/` | PuTTYgen 的核心适配、界面、资源、测试和发布配置 |
| `vendor/putty/`、`vendor/patches/` | 官方固定源码、许可证与本地最小补丁 |
| `cmake/UpstreamPuTTY.cmake` | 共享上游密钥和密码目标 |
| `tools/qt/` | Qt 来源锁、构建和许可收集 |
| `tools/ubuntu-build-dependencies.sh` | Ubuntu 构建环境依赖 |
| `.github/workflows/puttygen.yml` | PuTTYgen 的平台构建和发布包检查 |
| `.tmp/` | 下载、构建、测试及打包暂存 |

Actions 同时提供 Windows ZIP、两个 Ubuntu DEB 和 Arch Linux x86_64 的 `.pkg.tar.zst` 原生包；Arch 包使用系统 Qt，由 pacman 安装运行依赖。

发布与平台验证见 [构建与发布](docs/构建与发布.md)，依赖来源见 [第三方软件与来源](THIRD_PARTY_NOTICES.md)。所有开发中间文件使用根目录 `.tmp/`。来源关系和校验保存在 [上游来源](vendor/来源.md)，自有代码许可证位于 [LICENSE](LICENSE)。
