# PuTTYgen GUI

为 Windows 和 Linux 提供 PuTTYgen 图形密钥工具。以官方 PuTTY 0.85 密钥核心为基础，使用 Qt Widgets；界面中英双语，首次启动默认中文。

目标系统为 Windows 10（1809 及以后）/11 x64、Ubuntu 22.04/24.04 x64，以及 Arch Linux x86_64（滚动更新）。功能覆盖目标是原版 Windows PuTTYgen GUI，包括全部算法、素数策略、强素数、PPK/KDF 参数、格式转换和证书。平台与功能的实际验证情况以任务验收记录为准；提供构建脚本不表示对应平台已经验收通过。

Windows 发布 ZIP 解压后双击 `puttygen-gui.exe`。Ubuntu 使用与系统版本一致的 DEB 安装，从应用菜单打开“PuTTYgen 密钥工具”。Windows/Ubuntu 发布包包含匹配的 Qt 动态库；普通用户无需安装开发工具。Ubuntu 包管理器会安装声明的系统运行依赖。Arch 从 Actions 下载 `.pkg.tar.zst` 后使用 `sudo pacman -U <包文件>` 安装，使用发行版的 Qt 6（最低 6.8）、翻译与字体依赖，不私带 Qt。

开发构建要求 CMake 3.24 以上、Ninja、C99/C++17 编译器、Qt 6.8 以上的 Widgets 与 Test 开发模块。在仓库根目录执行，所有中间文件放在根目录 `.tmp/`：

```bash
cmake -S . -B .tmp/build/debug -G Ninja -DPUTTY_BUILD_PUTTYGEN=ON -DCMAKE_BUILD_TYPE=Debug -DBUILD_TESTING=ON
cmake --build .tmp/build/debug --parallel
ctest --test-dir .tmp/build/debug --output-on-failure
```

无显示环境的 Qt 逻辑测试使用离屏平台，不能替代 Windows 原生界面和 Linux X11/Wayland 桌面验收。完整发布构建、工具链、安装验证与源码获取见 [构建与发布](../../docs/构建与发布.md)，依赖来源见 [第三方软件与来源](../../THIRD_PARTY_NOTICES.md)。

PuTTYgen 是本仓库 PuTTY 套件中的组件，顶层通过 `PUTTY_BUILD_PUTTYGEN` 选择是否构建。本期只提供 PuTTYgen GUI；其他 PuTTY 套件工具与 macOS 不在支持范围。原版项目来源为用户指定的 [github/putty](https://github.com/github/putty)，固定的官方上游源码位于仓库根目录 `vendor/putty/`。
