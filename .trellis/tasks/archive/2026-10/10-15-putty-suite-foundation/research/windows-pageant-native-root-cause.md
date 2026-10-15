# Windows Pageant 创建失败后的公共运行时断言

## 原生定位证据

提交 `f2f7e15` 的 Windows 工作流 `38682442363`、主作业 `116249773392` 在首个“已有文件拒绝”负例中失败。该负例先经过 AF_UNIX 启动阶段 `1,2`，模块自行回收后经过析构阶段 `30,31,32,33,38,39`，随后以 `abort`、退出码 `134` 结束。证据文件位于项目 `.tmp/suite-validation/windows-pageant-unix-diagnostic-ci.log` 和同目录 `pageant-unix-diagnostic.exe.map`。

原生宿主报告的当前 EXE 相对地址与同次构建 MAP 对照如下：

| RVA | 同次 MAP 对应函数 |
| --- | --- |
| `0x30802` | `findrelpos234 + 0x92` |
| `0x2ffae` | `del234 + 0x1e` |
| `0x21bae` | `pr_core_destroy + 0xfe` |
| `0x21a94` | `pr_core_create + 0x2a4` |
| `0x208c2` | `putty_runtime::Worker::start + 0x152` |

该栈没有 `winselgui_response`。源码对应的实际路径为：模块在创建失败时自行销毁后返回 `NULL`；`pr_core_create` 调用 `pr_core_destroy`；后者仍无条件执行 `expire_timer_context(core->module_context)`；真实 `timing.c` 向计时上下文树执行 `del234(..., NULL)`，触发 `tree234.c` 的 `!(equal_permitted && !e)` 断言。此错误属于公共创建失败路径，Linux 上模块返回空上下文也会触发。

## 最小修复与边界

`libs/putty-runtime/src/core.c` 仅在模块上下文非空时执行模块析构，以及对应模块上下文的回调和计时器最终清扫。空上下文不表示已成功创建的模块，也不能作为该模块的清扫查询。核心拥有的请求、工作包装器、计时器清扫和平台网络收尾保持原顺序；唯一核心锁仍在收尾结束时释放。

本修复没有改变 Pageant 的 AF_UNIX 监听、端点归属验证或删除算法，没有修改上游计时器／树／Windows 网络消息实现。首个负例之后的 AF_UNIX 检查尚不能据此报告通过。

## 局部回归

新增跨平台 CTest `runtime-create-failure`，标签为 `runtime;no-storage`，Windows 再附 `windows-native`。宿主链接生产 `putty-runtime`、`suite-putty` 及现有致命拒绝存储入口，不使用 socket、Conf、客户端 PRNG 或持久化数据。

- 固定执行失败→成功→失败→成功，共四次创建、两次模块析构。
- 每次创建中重入均返回 `PR_BUSY` 和空结果；失败结束后允许下一次成功创建。
- 失败结果为 `PR_PLATFORM_ERROR`、输出句柄为空，不调用模块析构。
- 创建期间登记的请求被取消，核心回调和长计时工作包装器被完整清理。
- 全局 `NULL` 上下文回调在失败后仍能执行，避免把它误当作空模块回调删除。
- 成功模块在析构中新登记的原始回调与长计时器会被最终清扫；真实 `run_timers` 验证运行中存在计时器，结束后无残留计时器／回调。

Linux Release 链接 MAP 已确认包含生产 `callback.c.o`、`timing.c.o`、`tree234.c.o`；没有生产 `storage.c.o`、`sshrand` 或 `crypto/prng.c.o`，四个种子／设置入口来自测试的 `no_storage.c.o`。Windows 使用同一宿主并生成原生 MAP，强制保留四个致命拒绝入口；SDK 编译及执行仍待仓库 CI。

## 本机实际结果

环境为 Arch Linux、GCC/G++ `16.2.1`、系统 Qt `6.12.0`，不代替目标 Ubuntu 或 Windows 平台结果。

| 检查 | 结果 |
| --- | --- |
| `.tmp/build/runtime-create-failure` Release 窄回归 | 1/1 通过，约 `0.01 s` |
| 同构建 `runtime-agent-core-link` | 1/1 通过，约 `0.11 s` |
| 同构建 `runtime-lifecycle` | 真实回环环境 1/1 通过，`1.07 s` |
| `.tmp/build/runtime-create-failure-asan` C/C++ ASan+UBSan、默认 LSan | 1/1 通过，约 `0.02 s` |
| 精确旧／新源码差分 | 旧源码 `SIGABRT`，新源码退出 `0` |
| `git diff --check` | 通过 |

默认沙箱中 `runtime-lifecycle` 因不允许创建 socket 而在 `listener.fd >= 0` 失败，提升后真实回环通过。默认沙箱的 ptrace 限制使 LSan 自身失败，提升后保持默认泄漏检查并通过；没有关闭泄漏检测来取得通过。

旧／新差分只在项目 `.tmp/suite-validation/runtime-create-failure/` 编译旧 `HEAD` 的 `core.c` 文本，复用同一回归对象和生产 `suite-putty`，没有回退共享源码。旧源码 SHA-256 为 `22aa0aba8cf7bf272966bf15c665d13916ece5de26173fee65eee08339255906`。旧进程返回 `-6` 并命中 `!(equal_permitted && !e)`，新进程返回 `0`；报告为该目录 `old-new-comparison.json`。

## 后续验证

主会话接入 Windows 及 Linux 严格测试清单后，需用同一提交重新运行 Windows 原生 Pageant 检查和本窄回归。该原生结果、AF_UNIX 后续检查及目标桌面状态由主会话补充；本记录不提前宣称它们通过。
