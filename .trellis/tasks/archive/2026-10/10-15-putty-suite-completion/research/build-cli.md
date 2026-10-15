# 共享构建与命令行工具研究

- 问题：完整客户端与密钥核心如何并存，三个 CLI 如何保持原版行为，如何隔离构建和测试。
- 范围：本地固定 PuTTY 0.85、现有 PuTTYgen 工程与 Qt 6.8 官方资料。
- 日期：2026-10-15。

## 构建证据与方案

- `vendor/putty/cmake/setup.cmake` 使用 `CMAKE_SOURCE_DIR` 定位许可证、`be_list.c` 和上游脚本，并创建通用目标名。不能未经适配直接 `add_subdirectory(vendor/putty)`，也不能覆盖根变量来修补路径。
- `apps/puttygen/tests/core/CMakeLists.txt` 已有 `ExternalProject_Add` 参考构建，独立源码根、生成目录及缓存。发布 CLI 复用该隔离方式，只构建 `plink`、`pscp`、`psftp`，声明产物及安装规则，不执行上游全量安装。
- `cmake/UpstreamPuTTY.cmake` 的 `pg-putty` 定义 `NO_GSSAPI`、`NOT_X_WINDOWS` 并包含 `stubs/no-timing.c`，仅供密钥工具。Qt 客户端/代理需独立配置、前缀目标、平台源和事件循环；每个进程只链接一套所需上游核心。
- 上游 `cmake/setup.cmake` 强制保留 Release 断言；新目标也须保留。`cmake/platforms/unix.cmake` 默认 GSSAPI 为 `DYNAMIC`，Windows 默认为开启，发布不可继承密钥参考工具的关闭设置。
- 上游 GTK 发现影响 `NOT_X_WINDOWS`；CLI 可关闭 GTK，Qt 主程序则需单独接入 X11 相关能力，不能连带删除 X11 转发。

## CLI 对照矩阵

直接复用上游入口和解析器，保留原名、参数、标准流、交互和退出状态，不通过 shell 拼接用户参数。Windows CLI 保持控制台子系统，GUI 使用图形子系统。

| 组 | 必需验证 |
| --- | --- |
| C01 通用 | 版本/帮助/非法参数、端口/用户名、已保存会话、主机密钥/CA、代理及禁用代理、批处理不提示、网络拒绝/超时/错误认证。 |
| C02 Plink | 二进制 stdout、独立 stderr、远程退出码、交互/非交互、命令文件、TTY、转发、其它适用协议与 Git 调用。 |
| C03 PSCP | SCP/SFTP、上传/下载、递归、元数据、通配符/引用、空文件/大文件/中文空格路径、进度与真实失败状态。 |
| C04 PSFTP | 交互、引用/通配符、get/put/mget/mput/reget/reput、目录/改名/权限/删除、-b/-bc/-be/-batch、命令失败语义。 |
| C05 协作 | 新旧代理分别认证，GUI 会话被 CLI 读取，PuTTYgen 密钥和证书可用。 |

参数完整清单从 `cmdline.c`、平台 `plink.c`、`pscp.c`、`psftp.c` 和对应 `doc/*.but` 提取；上表不能替代完整参数覆盖。

## 测试隔离

- Linux 使用上游 `PUTTYDIR`、`PUTTYSESSIONS`、`PUTTYSSHHOSTKEYS`、`PUTTYRANDOMSEED`，见 `vendor/putty/unix/storage.c:93`、`:165`、`:182`、`:197`，全部指向独立 `.tmp/`，不修改 HOME。
- Windows 上游存储使用 HKCU。原版程序在专用测试身份运行；自有测试进程可在启动前用 `RegOverridePredefKey` 映射至项目 `.tmp/` 临时 hive，退出时恢复。隔离方案必须验证后才可运行会话写入，不读写真实个人配置。
- 测试子进程清理继承的代理环境，使用测试 socket/管道及专用身份；不终止用户现有 Pageant。
- 本地 SSH/SFTP/转发服务仅监听受控本地地址，配置、密钥和数据放 `.tmp/`。原生 Windows IPC 不能用 Linux socket 测试代替。

## Qt 官方资料

使用 `context7-mcp` 解析至 `/websites/doc_qt_io_qt-6_8`，查询事件桥与托盘，随后读取官方页面。在线 6.8 文档当前标为较新的补丁版本；本项目仍固定现有 Qt 6.8.3，具体 API 以锁定头文件及构建验证。

- [QSocketNotifier](https://doc.qt.io/qt-6.8/qsocketnotifier.html)：同一描述符每种方向只能有一个通知器，写事件按需启停；Windows 注册会使 socket 非阻塞。
- [QWinEventNotifier](https://doc.qt.io/qt-6.8/qwineventnotifier.html)：Windows HANDLE 异步通知；手动复位事件由调用方处理，不能将 HANDLE 当成 Unix 描述符。
- [Qt 定时器](https://doc.qt.io/qt-6.8/timers.html)：定时器在所属线程启动、停止。
- [QSystemTrayIcon](https://doc.qt.io/qt-6.8/qsystemtrayicon.html)：探测托盘可用性；Linux 桌面激活能力有差异，主窗口与菜单必须提供完整操作。

## 发布与现有改动

PuTTYgen `Packaging.cmake` 拥有根 `CPackConfig.cmake`、安装目录和 Qt 部署规则。套件模式需顶层唯一 CPack 所有者，各组件仅登记安装内容，避免互相覆盖和重复部署；PuTTYgen 单组件模式保持原行为。套件 Ubuntu 安装使用独立 `/opt/putty-suite`，系统已有上游程序不得被无提示覆盖。

PuTTYgen Arch 模式使用系统 Qt、安装 `/usr`，与携带运行库的 Ubuntu/Windows 发布模式分离。新六组件承诺不含 Arch，共用改动仍回归其既有路径。

## 未执行项

本轮仅研究。CLI 产物映射、Qt 完整核心链接、事件桥、临时 hive、包共存和实际部署均需实施验证。缺失目标环境不能改写成验收通过，也不能据此删除产品功能。
