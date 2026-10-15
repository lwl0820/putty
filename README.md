# PuTTY

这是 PuTTY 套件的跨平台桌面项目。组件放在 `apps/`，固定上游源码和公共构建工具分别放在 `vendor/`、`cmake/` 和 `tools/`。

目前实现 [PuTTYgen GUI](apps/puttygen/README.md)：使用官方 PuTTY 0.85 密钥核心，为 Windows 和 Linux 提供传统图形操作、中英双语以及密钥格式转换。Plink、PSCP、PSFTP 已接入独立上游构建，保留原版命令行入口。Qt PuTTY 和 Qt Pageant 正在开发，尚未提供可运行的图形程序。

在仓库根目录构建 PuTTYgen，需要 CMake 3.24 以上、Ninja、C99/C++17 编译器和 Qt 6.8 以上的 Widgets 与 Test 开发模块：

```bash
cmake -S . -B .tmp/build/debug -G Ninja \
  -DPUTTY_BUILD_PUTTYGEN=ON -DCMAKE_BUILD_TYPE=Debug -DBUILD_TESTING=ON
cmake --build .tmp/build/debug --parallel
ctest --test-dir .tmp/build/debug --output-on-failure
```

`PUTTY_BUILD_PUTTYGEN` 默认开启，其余组件默认关闭。全部关闭时无需 Qt：

```bash
cmake -S . -B .tmp/build/suite-only -G Ninja -DPUTTY_BUILD_PUTTYGEN=OFF
cmake --build .tmp/build/suite-only
```

只构建三个命令行工具也无需 Qt；产物位于构建目录的 `suite-cli/`，不会全量构建或安装上游附加程序：

```bash
cmake -S . -B .tmp/build/suite-cli -G Ninja \
  -DCMAKE_BUILD_TYPE=Release -DPUTTY_BUILD_PUTTYGEN=OFF \
  -DPUTTY_BUILD_PLINK=ON -DPUTTY_BUILD_PSCP=ON -DPUTTY_BUILD_PSFTP=ON
cmake --build .tmp/build/suite-cli --parallel 4
cmake --install .tmp/build/suite-cli --prefix "$PWD/.tmp/stage/suite-cli"
```

三个开关可独立选择，安装只包含选中的程序。`PUTTY_BUILD_RUNTIME=ON` 可单独构建新图形程序共用的核心线程与事件桥，依赖 Qt Core，测试另需 Qt Network；它用于开发验证，不代表两个图形组件已经完成。完整套件的 Windows、Ubuntu 与真实桌面验收仍在进行。

目录责任如下：

| 路径 | 内容 |
| --- | --- |
| `apps/puttygen/` | PuTTYgen 的核心适配、界面、资源、测试和发布配置 |
| `apps/plink/`、`apps/pscp/`、`apps/psftp/` | 原版命令行程序的独立构建与安装入口 |
| `libs/putty-runtime/` | 新图形组件的核心线程、类型化消息与平台事件桥 |
| `vendor/putty/`、`vendor/patches/` | 官方固定源码、许可证与本地最小补丁 |
| `cmake/UpstreamPuTTY.cmake` | 共享上游密钥和密码目标 |
| `cmake/SuitePuTTY.cmake`、`cmake/SuiteCLI.cmake` | 完整客户端核心与独立命令行构建 |
| `tools/suite/` | 仅在项目 `.tmp/` 运行的隔离与受控服务夹具 |
| `tools/qt/` | Qt 来源锁、构建和许可收集 |
| `tools/ubuntu-build-dependencies.sh` | Ubuntu 构建环境依赖 |
| `.github/workflows/puttygen.yml` | PuTTYgen 的平台构建和发布包检查 |
| `.tmp/` | 下载、构建、测试及打包暂存 |

Actions 同时提供 Windows ZIP、两个 Ubuntu DEB 和 Arch Linux x86_64 的 `.pkg.tar.zst` 原生包；Arch 包使用系统 Qt，由 pacman 安装运行依赖。

发布与平台验证见 [构建与发布](docs/构建与发布.md)，依赖来源见 [第三方软件与来源](THIRD_PARTY_NOTICES.md)。所有开发中间文件使用根目录 `.tmp/`。来源关系和校验保存在 [上游来源](vendor/来源.md)，自有代码许可证位于 [LICENSE](LICENSE)。
