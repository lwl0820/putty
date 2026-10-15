# Windows AF_UNIX 原生断言定位

日期：2026-10-15。实施边界为 Pageant 核心中仅测试宏启用的固定阶段钩子、Windows IPC 专用宿主及其 Python 运行器；不改 vendor、公共运行时、产品监听／清理算法、产品入口、构建和工作流。其它代理的实时修改保持原样，未提交。

## 问题与证据边界

`f7d913f` 的 Windows 运行 38679891218、job 116241793352 已通过完整 SDK／MSVC 编译，但 Pageant 原生测试在约 17.30 秒出现 `tree234.c:536` 的空查询断言。`08b5cc3` 后续运行在约 17.59 秒再次出现同一断言。对应日志为项目 `.tmp/suite-validation/windows-pageant-unix-first-ci.log` 和 `windows-ppk-seed-fix-ci.log`。日志只有断言，无栈和阶段，尚不能确定调用点。

原独立审查 `windows-unix-review.md` 对过期 `FD_ACCEPT` 的说明不准确：固定 vendor 的 `windows/network.c` 仅在 `WSATRY_AGAIN` 时中止该分支，`WSAEWOULDBLOCK` 会继续传递 `INVALID_SOCKET`。`windows/select-gui.c` 的 `find234(moribund_sockets, (void *)wParam, NULL)` 位于后续 `select_result` 的零值检查之前。这是源码事实，不足以证明本次原生断言由其中某条路径造成；本轮没有据此修改上游。

## 诊断增量

- 专用宿主在 QApplication 之前注册 SIGABRT 和未处理异常诊断。异常只输出固定故障类型、数字代码及当前固定阶段；捕获最多 24 帧，仅保留当前 EXE 内相对于模块基址的 RVA，不输出模块路径、绝对地址或任意异常文字。
- SIGABRT 在写出诊断后以 134 立即结束专用宿主，不执行析构，也不把断言变成可恢复错误。生产目标不使用此行为。Microsoft CRT 文档明确支持通过 SIGABRT 处理器执行自定义诊断和终止，见 [abort](https://learn.microsoft.com/en-us/cpp/c-runtime-library/reference/abort?view=msvc-170) 及 [_set_abort_behavior](https://learn.microsoft.com/en-us/cpp/c-runtime-library/reference/set-abort-behavior?view=msvc-170)。
- 顶层阶段覆盖已有文件拒绝、缺失父目录拒绝、非法编码拒绝、跨进程替换拒绝、正常三传输启动／签名和关闭。C 核心的编号仅由 `PA_WINDOWS_TEST_NAMESPACE` 编译启用，Linux 和生产 Windows 目标均为空操作。
- runner 在普通失败和 70 秒超时中只转录白名单阶段及数字字段；最多检查前 16 KiB 中的末 128 行，最多导出 64 行。输入超限时丢弃截断的尾行，避免半行数字被误作完整 RVA。普通 CHECK 只报告源码行号，不再传播表达式或异常详情。
- 原 15 项双 IPC 与新增 8 项 AF_UNIX 仍精确要求共 23 项；250 ms 归属探针、70 秒 runner、90 秒 CTest 均保持。新增阶段只提供定位，不能算新原生通过证据。

### C 核心子阶段表

| 编号 | 已抵达的位置 |
| --- | --- |
| 1、2 | 可选监听入口、现有文件属性查询前 |
| 3、4、5、6、7 | listener 分配前、原生监听创建前／后、交付 Socket 前／后 |
| 8、9、10 | 替换 hook 前、固定对象打开前、归属验证前 |
| 11、12、13 | 原生监听枚举前、找到本次监听后、自连接前 |
| 14、15、16、17 | 等待监听、直接 accept 前、等待连接完成、核对双方 PID 前 |
| 18、19、20、21、22 | 撤销 accept 通知前、close 前、消息屏障前、probe close 前、保存已核实对象前 |
| 30、31、32、33 | destroy 入口、主管道连接关闭前、主管道 listener 释放前／后 |
| 34、35、36 | AF_UNIX 连接关闭前、AF_UNIX listener 释放前／后 |
| 37、38、39 | 已核实端点删除前、平台 listener 收尾后、模块状态释放后 |

100–112 为顶层具名阶段；故障行保存最后一次抵达的编号，前面的具名阶段用于判定本次情景。实际 RVA 可对该次构建的 `pageant-windows-ipc-tests.exe.map` 符号化；不能用其它提交的链接图推断地址。

## 本机验证

本机 Arch Linux x64／GCC 16.2.1／Qt 6.12，无可执行的 Windows、SDK 或 MSVC。

| 检查 | 实际结果 |
| --- | --- |
| Windows runner 回归 | 独立检查后 7／7 通过，包含未知日志／路径／超额输入过滤、截断尾行拒绝、错误和超时数值诊断保留 |
| 两个 Python 文件按 Python 3.11 AST 解析 | 通过 |
| `cmake --build .tmp/build/suite-pageant --parallel 4` | 通过；不编译 Windows 分支 |
| 沙箱内 Pageant CTest | 10 项在套接字启动阶段失败，均有同一空查询断言；不是 Windows 原生证据 |
| 允许受控本地套接字后的 Pageant CTest | 11／11 通过，零跳过，约 10 秒；日志 `.tmp/pageant-unix-native-diagnostics-ctest.log` |
| `git diff --check -- apps/pageant` | 通过 |
| 本增量 Windows 编译、诊断处理器及原生 23 项 | 未执行；等待仓库 CI |

本增量可交独立 checker 审查并提交进入诊断检查点。必须等真实 Windows 阶段和调用栈后再选择产品／上游修复；Windows 10 1809 能力、Windows 10／11 桌面及完整 `-unix` 产品入口仍未验收。
