# Qt 组件目录

PuTTYgen 图形组件位于 `apps/puttygen/`。其内部划分依据责任，而非创建独立仓库或把套件整体改名为 PuTTYgen。

| 路径 | 职责 |
| --- | --- |
| `src/app/` | 单工作线程会话、公开状态和语言管理 |
| `src/ui/` | 主窗、参数对话框和用户交互 |
| `resources/` | 翻译、离线帮助、图标及 Qt 资源清单 |
| `tests/gui/` | GUI 行为回归、配置和文件流程验证 |
| `cmake/Packaging.cmake` | 组件安装规则及发布包配置 |
| `packaging/` | 桌面入口、启动脚本、包校验与安装冒烟 |
| `README.md` | 组件使用与构建说明 |

表中路径相对于 `apps/puttygen/`。套件的总体入口和目录说明位于根 README；公共构建与发布文档位于 `docs/`；此组件的 GitHub Actions 工作流位于 `.github/workflows/puttygen.yml`。

窗口仅展示产品操作文字。所有测试账户配置、文件夹具和截图写入套件根 `.tmp/`，按构建目录隔离，不写入组件源码树或用户个人配置。

迁移或新增资源时同时核对 CMake、资源清单、打包、CI、文档和测试路径。Qt 资源中的 PuTTY 许可证引用必须指向共享 `vendor/putty/LICENCE`。组件目录调整后的测试需要重新执行，旧布局结果不能作为新布局通过证据。

## 发布依赖约束

普通 `add_executable` 创建的 Qt 主程序在生成部署脚本后调用 `qt_finalize_target`，否则不会生成完整插件清单。Linux 发布目标显式关联 `Qt6::WaylandClient`，通过 `qt_import_plugins` 收集 XCB、Wayland generic/EGL、offscreen，以及 IBus、Compose、XdgDesktopPortal 插件；沿 Qt 依赖图部署 Wayland shell、graphics、decorations 与所需动态库。Windows 不引入 Linux 模块。

正式发布以完整性检查和无 Qt SDK 环境的实际启动为依据；平台插件的文件名存在不能替代动态依赖可加载。Ubuntu 使用 `LC_ALL=C.UTF-8` 和固定 CMake，避免归档中文文档名称时失败。安装冒烟的缺省 `XDG_RUNTIME_DIR` 写入项目 `.tmp/` 并设为 0700；已有 Wayland 运行时目录保留，不能改断合成器连接。

Arch 原生包使用 `PUTTYGEN_ARCH_PACKAGING` 系统安装分支，运行库、平台插件和标准控件翻译由系统 Qt 包提供，不复制上述私有部署文件。程序、desktop、图标和许可分别安装到标准 `/usr` 目录，精确构建及验证契约见 [Arch 包规范](../backend/arch-package.md)。
