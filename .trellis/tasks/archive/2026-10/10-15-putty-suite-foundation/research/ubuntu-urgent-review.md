# Ubuntu 紧急数据回归复核

## 范围与结论

2026-10-15，主会话派发基础任务的局部复核。核查真实 Ubuntu 22.04／Qt 6.8.3 CI `38667664980`、作业 `116203208161` 中 `runtime-lifecycle` 的失败；原日志为 `.tmp/suite-validation/linux-ubuntu22-storage-ci.log:2324`。同一提交的 Ubuntu 24.04 通过，前一提交两版也均通过。

根因是测试对上游内联 OOB 语义的错误时序假设。没有发现需要修改产品事件桥的证据。本轮仅修改 `runtime_test.cpp`、`test_module.c/.h`，不改 `runtime.cpp`、`core.c`、上游源码、构建或 CI，不提交代码，也不覆盖并行组件改动。

## 源码证据与确定性复现

测试模块通过 `sk_new(..., oobinline=true, ...)` 打开真实 socket。上游 `unix/network.c` 的 `net_select_result` 在 `SELECT_X` 分支设置 `oobpending`；`SELECT_R` 通过 `SIOCATMARK` 判定当前位置。`plug_receive` 的 `urgent=1` 表示紧急标记之前的数据，并不表示内联的紧急字节本身。若 `before` 已读完，当前位置已经到达标记，`!` 可以按 `urgent=0` 正常交付。原测试却无条件同时要求完整的 `before!after` 和 `urgent` 状态，因而依赖核心与发送方的调度顺序。

为确定性复现，只在原流程发送 `before` 后等待其完整收到，再发送 OOB `!` 和 `after`，并把最终数据断言与 `urgent` 断言分开。真实数据断言通过，紧接着的 `state(events, "urgent")` 失败，用例耗时 0.47 秒。证据见 `.tmp/suite-validation/runtime-urgent-baseline.log`。没有依赖概率重跑，也没有提高超时。

同时核对了“读通知先到时禁用同 fd 的异常通知器”这一怀疑：`core.c` 的 `pr_core_fd_ready` 已在实际分发前执行一次非阻塞 `poll(POLLPRI)`，补入异常标志，再按 X、R、W 顺序逐项核对注册身份、当前兴趣和关闭状态。因此，单看 Qt 通知器禁用动作不能推断异常被丢弃；现有 C 层探测就是对应保护。本轮不为尚无证据的产品缺陷重构事件桥。

## 修复后的真实回归

1. 前置数据已读空：等待 `before` 完整收到后发送 `!`／`after`，要求完整内联数据一致，并明确没有 `urgent=1`。
2. 前置数据仍未读：由测试专用命令冻结同一个真实上游 socket，发送 `before`／OOB `!`／`after`；另一测试命令通过该 fd 的 `poll(POLLIN|POLLPRI)` 确认前置数据和紧急标记均已到达后才解冻。要求完整数据一致且确有 `urgent=1`。未就绪时显式重试，整体有界。
3. 异常与读同时就绪：另建真实 loopback TCP 对，使用非内联 OOB，在 Qt 注册之前证明 R 和 PRI 均就绪；测试模块通过正式 `uxsel`／Qt 运行时注册两种兴趣。回调要求 `SELECT_X` 先于首次 `SELECT_R`，通过真实 `recv(MSG_OOB)` 读到唯一 `!`，并验证普通流恰为 `beforeafter`。不是用模拟标志替代真实 socket，也不是仅检查计数。

新增探针只在 Unix 测试中编译，不使用产品接口暴露 fd。测试模块和核心仍由唯一核心线程持有，退出撤销 uxsel、关闭其私有 fd 并释放上下文。既有 fd 读取回调内关闭、迟到注册、HANDLE 复用等回归均保留。

## 实际验证

本机 Arch Linux x64，GCC 16.2.1，Qt 6.12.0；这些结果不能冒充目标 Ubuntu／Qt 6.8.3 的原生 CI。

| 检查 | 实际结果 | 证据 |
| --- | --- | --- |
| 确定性旧断言负例 | 完整数据通过、`urgent` 断言失败，0.47 秒 | `runtime-urgent-baseline.log` |
| 修正后的单次生命周期 | 1/1，通过，1.13 秒 | `runtime-urgent-fixed.log` |
| 有限重复生命周期 | 连续 10 次通过，总计 12.03 秒 | `runtime-urgent-repeat.log` |
| 两个修改源使用实际编译参数加 `-fsyntax-only -Wall -Wextra -Werror` | 2/2 退出 0 | `runtime-urgent-strict.log` |
| ASan／UBSan／LSan，显式 `detect_leaks=1`、`halt_on_error=1` | 生命周期与 HANDLE 注册 2/2，通过，1.29 秒 | `runtime-urgent-asan.log` |
| 重新构建后运行全部 `runtime` 标签 | 5/5，通过，无跳过；包含夹具、生命周期、HANDLE、完整核心链接、代理核心链接，4.43 秒 | `runtime-urgent-all.log` |
| 修改文件空白检查 | `git diff --check` 退出 0 | 本轮命令结果 |

上述日志均位于项目 `.tmp/suite-validation/`，真实运行均通过项目私有隔离运行器；构建目录为 `.tmp/build/suite-runtime` 和 `.tmp/build/suite-final-review-asan`。

## 未执行与规范同步

本轮未执行两版目标 Ubuntu／Qt 6.8.3 和 Windows 原生运行。后续由主会话提交并触发仓库 CI；Windows 的原生命周期组仍保持原有隔离跳过边界，不能因 Unix 测试修复而放行。

建议主会话在其拥有的 `suite-runtime.md` 中补充本次已证实的契约：内联 OOB 的 `urgent=1` 标记紧急位置之前的数据；测试必须分别控制前置数据已读空和仍待处理的状态，异常优先顺序另以真实同时就绪的 R／PRI 验证。该建议不改变产品设计、公共接口或验收范围。
