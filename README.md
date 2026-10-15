# PuTTYgen GUI

用于 Windows 和 Linux 的图形密钥工具。基于官方 PuTTY 0.85 密钥核心和 Qt Widgets，提供中英双语界面，首次启动默认中文。本项目只开发和交付 PuTTYgen。

支持 RSA、DSA、ECDSA、Ed25519 和 Ed448 密钥生成，包含素数策略、强 RSA 素数、PPK v2/v3 与 Argon2 参数、公私钥格式转换、指纹及 SSH 证书操作。

## 安装与运行

| 系统 | 发布形式与使用方式 |
| --- | --- |
| Windows 10（1809+）／11 x64 | 解压 ZIP，双击 `puttygen-gui.exe`；包含 Qt 与所需 MSVC 运行库。 |
| Ubuntu 22.04／24.04 x64 | 安装与系统版本对应的 DEB，从应用菜单打开“PuTTYgen 密钥工具”。 |
| Arch Linux x86_64 | 使用 `sudo pacman -U <包文件>.pkg.tar.zst` 安装，依赖发行版 Qt 6.8 以上及翻译、字体包。 |

CI 构建产物可从仓库 Actions 下载。Windows／Ubuntu 包包含匹配的 Qt 动态库，Arch 包使用系统 Qt。平台构建、自动化检查与真实桌面验收分别记录；离屏测试不能替代 Windows 或 X11／Wayland 的人工操作验收。

## 开发构建

需要 CMake 3.24 以上、Ninja、C99／C++17 编译器，以及 Qt 6.8 以上的 Widgets 与 Test 开发模块。在仓库根目录执行，构建与测试文件均写入 `.tmp/`：

```bash
cmake -S . -B .tmp/build/debug -G Ninja -DCMAKE_BUILD_TYPE=Debug -DBUILD_TESTING=ON
cmake --build .tmp/build/debug --parallel
ctest --test-dir .tmp/build/debug --output-on-failure
```

生成的程序位于 `.tmp/build/debug/src/puttygen-gui`（Windows 为 `.exe`）。只构建和测试密钥核心时无需 Qt：

```bash
cmake -S . -B .tmp/build/core -G Ninja -DPG_BUILD_GUI=OFF -DBUILD_TESTING=ON
cmake --build .tmp/build/core --parallel
ctest --test-dir .tmp/build/core --output-on-failure
```

脚本回归可独立运行：

```bash
python -B -m unittest discover -s tools/qt/tests -v
python -B -m unittest discover -s packaging/tests -v
```

核心测试覆盖算法、格式、证书、失败处理和 OpenSSH 互操作；GUI 测试覆盖会话、参数、语言切换和交互状态。Unix 默认额外构建固定上游 `puttygen` 作为互操作参考，它不会进入安装包。

## 目录

| 路径 | 内容 |
| --- | --- |
| `src/core/` | 密钥核心 C 接口与平台适配 |
| `src/app/`、`src/ui/` | Qt 会话、语言与窗口 |
| `resources/` | 翻译、帮助、图标与嵌入许可 |
| `tests/` | 核心、互操作及 GUI 回归 |
| `packaging/` | Windows／Ubuntu 部署、Arch 配方与包检查 |
| `cmake/` | 密钥核心接入与安装打包配置 |
| `tools/qt/` | 固定 Qt 源码、构建与工具回归 |
| `docs/` | 平台构建、发布及验收说明 |
| `vendor/` | 固定的 PuTTY 0.85 第三方源码及必要补丁 |

完整步骤见 [构建与发布](docs/构建与发布.md)。自有代码采用 [MIT 许可证](LICENSE)，依赖许可见 [第三方软件与来源](THIRD_PARTY_NOTICES.md)，PuTTY 源码校验及补丁见 [上游来源](vendor/来源.md)。第三方完整源树作为密钥核心和测试参考的来源保留，其它上游程序不作为本项目产品构建或交付。
