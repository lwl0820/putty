# 共享构建实施证据

日期：2026-10-15。本文仅覆盖构建代理负责的根入口、共享完整核心和三个 CLI 的构建入口，不能作为基础任务全部 AC 或父任务产品功能验收通过的证明。

## 行为与文件边界

原工程只有 PuTTYgen 入口，`pg-putty` 使用密钥专用裁剪。新增根组件开关及默认关闭的 `PUTTY_BUILD_RUNTIME`；GUI 自动加入运行时，尚无产品实现的 GUI 开关明确配置失败。

- `cmake/SuitePuTTY.cmake`：独立 `suite-config` 接口和 `suite-putty` 静态归档；生成头位于当前构建的 `suite-config/`，所有功能探针使用 `SUITE_*` 缓存，不继承 `pg-putty` 的 `NO_GSSAPI`、`NOT_X_WINDOWS` 或 `no-timing.c`。
- `cmake/SuitePuTTYSources.cmake`：固定上游完整客户端、终端、代理和平台源清单。软件密码实现保留全部算法，CPU 专用加速保持软件回退。单个静态归档自动解析核心模块间的循环引用。
- Unix 使用真实 `uxsel`、网络、计时、GSSAPI、SSH 共享、串口、X11 与原版存储；无 GTK 依赖。Windows 使用真实网络、HANDLE、`select-gui.c` 消息链；计时通知由 Qt 桥拥有，不重复加入原 GUI 计时前端。
- `suite_add_backend_list()` 为各宿主生成专属 `suite-<target>-be-list` 对象，保留各产品自己的 APPNAME 和后端选择；没有给共享归档固定产品身份。
- `cmake/SuiteCLI.cmake` 与 `apps/plink/`、`apps/pscp/`、`apps/psftp/CMakeLists.txt`：固定 vendor 原根独立 ExternalProject 配置，明确构建产物和选中目标，继承编译器/工具链/架构/运行库及标志，保留发布断言，只安装被选中的 CLI，不执行上游全量安装。
- 根在新套件功能及 `BUILD_TESTING` 开启时注册 Python `suite-fixture`，标签为 `runtime;suite;isolation`；此夹具由另一个代理实施，本文不声称已运行其测试。

没有修改上游源码、现有 `pg_*` 产品接口、PuTTYgen GUI/打包、其它子任务产品文件或任务状态。

## 实际环境

本机 Arch Linux x64，GCC 16.2.1，CMake 4.4.4，Ninja；系统 Qt 6.12.0。不是目标 Ubuntu 22.04/24.04 或 Windows 环境，也不是锁定 Qt 6.8.3 验收。

全部构建、生成头、测试安装与日志位于 `.tmp/build/suite-build/`。没有连接个人代理或运行会话/主机密钥写入；CLI 运行仅为 `-V` 构建身份检查。

## 已执行命令与结果

1. 全部组件关闭：退出 0，未调用 Qt 查找；构建为 `ninja: no work to do`。

   ```bash
   cmake -S . -B .tmp/build/suite-build/off -G Ninja -DPUTTY_BUILD_PUTTYGEN=OFF -DCMAKE_DISABLE_FIND_PACKAGE_Qt6=ON
   cmake --build .tmp/build/suite-build/off
   ```

2. 三 CLI 的 Release、纯命令行配置与构建：退出 0；独立上游缓存为 `PUTTY_GTK_VERSION=NONE`、`PUTTY_GSSAPI=DYNAMIC`。外层未查找 Qt。上游实际编译命令保留 `-O3` 且去掉 `-DNDEBUG`。

   ```bash
   cmake -S . -B .tmp/build/suite-build/cli -G Ninja -DPUTTY_BUILD_PUTTYGEN=OFF -DPUTTY_BUILD_PLINK=ON -DPUTTY_BUILD_PSCP=ON -DPUTTY_BUILD_PSFTP=ON -DCMAKE_DISABLE_FIND_PACKAGE_Qt6=ON -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX=/home/user/projects/putty/.tmp/build/suite-build/cli-install
   CMAKE_BUILD_PARALLEL_LEVEL=4 cmake --build .tmp/build/suite-build/cli
   cmake --install .tmp/build/suite-build/cli
   ```

   安装清单恰有 `bin/plink`、`bin/pscp`、`bin/psftp` 三项。三程序 `-V` 均退出 0，报告 Release 0.85、固定提交 `a3cce3a48d4ab12e0fccb32998b9042d4f7c3ff1`。修改后的最终编译标志传播也已重新配置/构建/安装通过；日志为 `cli-configure.log`、`cli-build.log`、`cli-install.log`。

3. Ninja Multi-Config、仅 PSFTP：Release 构建/安装退出 0，程序路径为 `cli-multi/suite-cli/psftp`，安装清单只有 `bin/psftp`；没有生成 Plink、PSCP 或原版 PuTTYgen。此项验证多配置路径接线，不能代替 Windows 资源和 MSVC 验收。

   ```bash
   cmake -S . -B .tmp/build/suite-build/cli-multi -G 'Ninja Multi-Config' -DPUTTY_BUILD_PUTTYGEN=OFF -DPUTTY_BUILD_PSFTP=ON -DCMAKE_DISABLE_FIND_PACKAGE_Qt6=ON -DCMAKE_INSTALL_PREFIX=/home/user/projects/putty/.tmp/build/suite-build/cli-multi-install
   CMAKE_BUILD_PARALLEL_LEVEL=4 cmake --build .tmp/build/suite-build/cli-multi --config Release
   cmake --install .tmp/build/suite-build/cli-multi --config Release
   ```

4. 完整核心与密钥库同树：临时宿主 `.tmp/build/suite-build/core-source/CMakeLists.txt` 依次加载原 `UpstreamPuTTY.cmake` 和新 `SuitePuTTY.cmake`，调用 `suite_add_putty_core()`。Release 配置/构建退出 0；`libpg-putty.a` 为 94 个归档成员，`libsuite-putty.a` 为 253 个成员。完整核心实际编译使用 `-UNDEBUG` 抵消发布配置的 `-DNDEBUG`；原版安全断言后备检查未报错。

   ```bash
   cmake -S .tmp/build/suite-build/core-source -B .tmp/build/suite-build/core -G Ninja -DCMAKE_BUILD_TYPE=Release
   cmake --build .tmp/build/suite-build/core --parallel 4
   ```

   `core/putty-config/cmake.h` 继续定义 `NO_GSSAPI`、`NOT_X_WINDOWS`；`core/suite-config/cmake.h` 两者均未定义，`NO_IPV6` 未定义且 `HAVE_GETADDRINFO=1`。这是双配置编译证据，实际完整运行时链接与行为由运行时代理的测试继续验证。

5. 尚未实现的 PuTTY GUI：配置真实返回 1，日志明确提示组件尚未实现。诊断保存在 `unsupported-gui.log`。

6. 根 `PUTTY_BUILD_RUNTIME=ON` 集成：Debug 配置通过，完整 `suite-putty` 归档与运行时 C 适配编译通过；运行时 C++ 首次构建因 `runtime.cpp` 缺少 `<climits>` 导致 `INT_MAX` 未定义而退出 1，已向文件所有者反馈。日志为 `runtime-configure.log`、`runtime-build.log`。不将该首次失败记为运行时构建通过，后续结果由运行时实施报告覆盖。

7. `git diff --check` 和新增构建文件行尾空白检查通过。本代理未执行 CTest，不能将未运行或空集合视为运行验收。主代理另行运行的 PuTTYgen 回归不在此重复冒充新证据。

## 验收映射与后续

- AC1/R1：已经证明两套配置和归档能同树编译，以及 CLI-only/全关闭不需 Qt；完整 C/Qt 宿主实际链接和进程运行证据仍依赖运行时测试。
- R5：六组件开关、CLI 独立构建、精确安装及多配置路径已实现并在本机验证；Windows、目标 Ubuntu、PuTTYgen/Arch 回归由主代理收齐。
- AC2–AC4：本文件没有 fd/HANDLE 生命周期、真实持续网络、测试身份隔离、平台原生运行或现有全部模式回归通过证据，不标完成。
- 尚未验证：Windows/MSVC 与真实资源输出、Qt 6.8.3、目标 Ubuntu 原生/容器、X11/Wayland/真实桌面、CLI C01–C05 行为矩阵、套件发布包。单个静态归档中未被宿主实际引用的产品前端符号还需后续 PuTTY/Pageant 适配链接验证。
