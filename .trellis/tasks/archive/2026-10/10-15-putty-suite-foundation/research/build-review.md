# 共享构建独立检查

日期：2026-10-15。任务为 `10-15-putty-suite-foundation`；本轮先检查根入口、`SuitePuTTY.cmake`、`SuitePuTTYSources.cmake`、`SuiteCLI.cmake` 及三个 CLI 的 CMake 入口，随后按主会话授权检查运行时生命周期。临时产物位于项目 `.tmp/`；本报告不表示基础任务或六组件已完成。

## 实际修复与复验

本代理保持共享构建文件只读，发现交主会话修复；只新增经明确分配的两个 C 测试源，没有覆盖运行时实施代理文件。

### B1：Unix 日志时间函数漏入源清单

- 原问题：`suite_unix_utils_sources` 没有上游 `unix/CMakeLists.txt` 加入的 `utils/ltime.c`。归档构建成功，但 `logging.c.o` 只有 `U ltime`，实际链接失败。
- 证据：`.tmp/build/suite-review-build/ltime-symbols.txt`、`ltime-link.log`，最小调用链接返回 1，诊断为未定义 `ltime`。
- 修复：主会话已补入该源。本代理增量重建完整核心，并以真实日志调用链接、写入与读取成功复验。

### B2：通用 null 方法整体遗漏

- 原问题：共享源清单漏了 `vendor/putty/stubs/CMakeLists.txt` 纳入通用库的八个 `null-*` 文件。它们是与真实实现并存的方法，不是关闭网络或计时的 `no-*` 替代实现。
- 影响：真实 SSH、网络、密码模块缺少 `nullkey_*`、`nullcipher_next_message`、`nullmac_next_message`、`nullplug`、`nullseat_*`、`nullsock_endpoint_info` 等符号；只构建静态归档不会发现。
- 修复：主会话已补齐八源。逐项对照通用 87 个 utils 源和八个 null 源，没有继续发现漏项。完整后端表、Terminal、真实 settings/storage/logging 的最终链接成功。

### B3：客户端与代理的随机函数混在同一归档

- 原问题：`pageant.c` 自带禁止随机读取的 `random_read` 致命分支，而客户端 `sshrand.c` 提供真实 PRNG 的同名函数。最初通用运行时还直接引用 `random_ref`，Pageant 使用通用运行时时会拉入客户端实现。
- 证据：`.tmp/build/suite-review-build/full-core-link.log` 保留双实现重复定义诊断；此组合探针用于发现边界，不能把“两个互斥产品必须同进程”当作新需求。
- 修复：主会话将 `pageant.c` 独立为 `suite-pageant-core`，公开依赖 `suite-putty`；运行时代理移除通用层对 `random_*` 的直接调用。客户端模块拥有 PRNG，Pageant 保留原版禁止随机读取的契约。
- 复验：独立 Pageant 模块链接当前公共 C 核心及两个静态目标，通过初始化、空密钥计数与关闭；符号表只有 Pageant 的 `random_read`，没有客户端 `random_ref` 或 `global_prng`。Unix `random_add_noise` 空方法遵循原 `unix/noaskpass.c` 的前端策略，不覆盖随机字节函数。没有编入 `unix/pageant.c`，没有启动监听器或个人代理。

### B4：多配置安装会误收另一配置产物

- 原问题：所有 `CMAKE_RUNTIME_OUTPUT_DIRECTORY_<CONFIG>` 指向同一目录，导入位置与安装源同样不分配置。
- 实际复现：独立 Ninja Multi-Config 树只构建 Debug PSFTP，随后 `cmake --install --config Release` 返回 0，并复制该 Debug 程序；文件 SHA256 一致，Release 对象尚不存在。
- 证据：`.tmp/build/suite-review-build/multi-config-results.json` 与 `multi-debug-build.log`、`multi-release-install.log`。
- 修复状态：主会话接管该复现构建树，正在将多配置输出、声明产物、导入位置与安装源统一改为按配置的目录。本代理不再操作移交后的构建树；最终复验结果由主会话追加，不能把原失败结果改记为已通过。

主会话复验补记：已将上述三个位置统一为 `suite-cli/$<CONFIG>/`，并传播配置清单。只构建 Debug 后安装 Release 返回非零，安装目录没有误收 Debug；分别构建和安装 Debug、Release 后，两个安装文件的 SHA256 各自与对应配置产物一致，且两个配置的摘要不同。六份配置、构建和安装日志位于 `.tmp/suite-validation/multi-*.log`。此补记覆盖 B4 的最终修复证据，保留前述实际失败历史。

## 实际执行的验证

环境为本机 Arch Linux x64，GCC 16.2.1、CMake 4.4.4、Ninja、Qt 6.12.0；不代替 Ubuntu 22.04/24.04、Qt 6.8.3 或 Windows 验收。

1. `PUTTY_BUILD_PUTTYGEN=OFF` 且 `CMAKE_DISABLE_FIND_PACKAGE_Qt6=TRUE` 的全关闭配置与构建均返回 0。
2. 仅 PSFTP、禁用 Qt 查找、Ninja Multi-Config 的配置和 Debug 真实编译返回 0。额外覆盖了不同配置安装的负例，见 B4。
3. 复用经主会话移交的 `.tmp/build/suite-build/core` 增量构建，真实包含 Unix 时间函数、八个 null 方法、补充熵补丁及独立代理目标，返回 0。
4. 检查同树生成头：密钥头仍定义 `NO_GSSAPI`、`NOT_X_WINDOWS`；完整核心不定义这两项，`HAVE_GETADDRINFO=1`，没有继承密钥裁剪配置。完整核心带 `-UNDEBUG`，正式探针以 `-O3 -DNDEBUG -UNDEBUG` 验证断言实际执行。
5. 新增 `libs/putty-runtime/tests/core_link_smoke.c`：完整 SSH/串口/其它协议后端表和 Terminal 的真实链接闭包；实际 `do_defaults/save_settings/load_settings` 往返主机名及端口，真实 `logtraffic/logevent/logflush/log_free` 写入并读取 UTF-8 日志。公共隔离探针后运行返回 0；没有完整覆盖的直接启动返回 1。其测试前端方法只提供原本由产品前端拥有的默认值、LogPolicy 与通知策略。
6. 新增 `libs/putty-runtime/tests/agent_core_link_smoke.c`：后端全关的 Pageant 身份，真实公共 C 运行时创建与销毁原版代理核心，返回 0，隔离随机种子保持为空。此用例验证链接/初始化，不验证代理协议、签名或 IPC。
7. 两个新增自有 C 源分别以 `-Wall -Wextra -Werror` 编译成功，再链接上游源；没有为上游既有未使用参数告警关闭自有源检查。正式 CMake/CTest 注册由主会话负责。
8. 增量构建 `putty-runtime-tests` 返回 0。默认沙箱首次 CTest 因禁止本机回环监听，在 `server.listen` 失败；自动审核允许本次隔离回环服务后，`ctest --test-dir .tmp/build/suite-runtime --output-on-failure -R '^runtime-lifecycle$'` 为 1/1 通过，耗时 1.18 秒。没有将沙箱失败当产品缺陷，也没有把它隐藏成首次通过。
9. `git diff --check` 通过。仓库未配置独立 CMake lint 或额外类型检查命令；CMake 配置/生成与实际 C/C++ 编译承担本次语法、类型和链接检查。

原始日志、探针与 JSON 在 `.tmp/build/suite-review-build/`。公共隔离器逐层验证 0700 私有目录；试图把隔离根放在普通 0755 的构建目录下会被拒绝，本轮改用 `.tmp/suite-review-core`、`.tmp/suite-review-agent` 的独立私有父目录，没有放宽权限规则。

## 其它检查结论与未执行项

- ExternalProject 在未显式替换生成器时会继承生成器、平台、工具集和实例；本机 CMake 实现已核对，不将没有重复写 `-A/-T` 误报为架构丢失。MSVC 资源、架构和运行库仍需原生构建实证。
- 根组件开关没有通过空目标伪装尚未实现的 GUI。CLI 独立上游配置关闭 GTK，保留动态 GSSAPI，并只构建/安装所选择的 CLI；完整 GUI 核心保留真实网络、计时、GSSAPI、串口、共享连接、X11 及原版存储源。
- 本机完整链接证明了 Linux 客户端及代理的两种源闭包；GUI `dlg_*`、平台界面策略及 GTK 特有配置等由后续 Qt 适配提供，当前不冒充完整产品行为已实现。
- Unix 补充熵补丁保留 `/dev/urandom`、原进程列表和保存种子；新增结构先清零，仅在成功取得时间/资源数据后追加，没有把环境变量拼入 shell。补丁与来源记录一致。密码安全和目标系统的完整行为矩阵仍按父任务验收执行。
- 原生 Windows、Qt 6.8.3 与目标 Ubuntu 构建、所有既有 PuTTYgen/Arch 回归、X11/Wayland 真桌面、完整 CLI C01–C05 及六组件发布包未由本代理复跑；主会话的独立结果不得混称为本轮执行。
- 运行时追加审查见 `runtime-review.md`。Windows HANDLE 复用等未解决项须单列，任务维持进行中。
