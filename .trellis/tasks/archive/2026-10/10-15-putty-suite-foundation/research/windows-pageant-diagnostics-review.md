# Windows Pageant 诊断增量独立检查

日期：2026-10-15。本轮只检查 `apps/pageant/src/core/pa_agent.c`、`apps/pageant/tests/windows/native_ipc.cpp`、`run_ipc_test.py`、`test_runner.py` 及 Pageant 的 `research/windows-unix-native-fix.md`。未修改 vendor、公共运行时、产品监听和端点归属算法、工作流或其它并行实现；未提交。

已读取活动任务 `check.jsonl`、需求、设计、实施计划、父设计和实施计划，以及所列规范和研究。诊断只服务于 Windows 原生断言定位，不构成 AF_UNIX 产品验收通过。

## 已修复发现

- 文件：`apps/pageant/tests/windows/run_ipc_test.py`、`test_runner.py`。
- 问题：先截前 16 KiB 再按行过滤时，边界可能恰好截在十六进制 RVA 中间。截断的数字仍可能匹配白名单，导致公开诊断中的位置与原始位置不同。
- 修复：只有确实发生字节截断时，回退至最后一个完整 LF 行；无完整行则不转录。增加有效数字被截断、完整 CRLF 行仍保留和无效 UTF-8 行拒绝的回归，原日志白名单与 128／64 行限额保持。
- 同步 Pageant 研究的边界说明及 7 项 runner 回归数量。

## 未修复发现与验证边界

- 未发现需要进一步修改的本次局部代码问题。C 核心钩子受 `_WIN32 && PA_WINDOWS_TEST_NAMESPACE` 限定，生产 Windows 和 Linux 不输出阶段；监听、accept、归属核对及清理调用顺序保持原样，仅拆分等价的条件以插入阶段。
- 专用宿主使用固定具名／编号阶段，普通 CHECK 不再传播表达式或任意异常文字。故障诊断设置一次性原子门；最多取 24 帧，对每个地址核验所属模块，只输出当前 EXE 的数值 RVA，不输出绝对地址、模块路径、参数或内存。SIGABRT 写出诊断后终止专用宿主，不继续正常执行或析构；SEH 过滤器继续交给系统处理。真实 MSVC 编译、SIGABRT／SEH 行为和调用栈可用性仍须由同次 Windows CI 验证，本机不作通过声明。
- 原生报告仍精确要求 23 项且各出现一次，旧 15 项报告仍被拒绝；250 ms 归属验证、70 秒 runner 和 90 秒 CTest 保持。此次没有把 `tree234.c:536` 断言判为已修复，也没有据 Linux 沙箱内的相同断言推断 Windows 根因。应等待原生阶段／RVA，并仅用对应构建的链接图符号化。
- runner 的 16 KiB／128／64 限额限定公开转录；原有 `subprocess.run(capture_output=True)` 的进程输出捕获方式保持，没有新增整个子进程输出的内存上限承诺。
- 本轮仅增加测试诊断，没有形成新的产品或公共平台契约，现有 `.trellis/spec/backend/suite-runtime.md` 无需同步；Windows 10／11 桌面及完整产品参数入口仍属于后续验收。

## 验证

本机为 Arch Linux x64／GCC 16.2.1／Qt 6.12，无 Windows SDK／MSVC／可运行 Windows。

| 检查 | 实际结果 |
| --- | --- |
| 格式／差异检查 | `git diff --check` 针对四个代码文件通过 |
| 核心严格编译检查 | 使用实际 Ninja 编译参数，对 `pa_agent.c` 的 Linux 分支执行 `-fsyntax-only -Wall -Wextra -Werror` 通过 |
| Python 静态检查 | 两个变更文件按 Python 3.11 AST 解析和本机字节码编译通过；仓库未配置本范围的独立 Python 类型检查器 |
| runner 回归 | `python -B -m unittest discover -s apps/pageant/tests/windows -p test_runner.py -v`：7／7 通过，零跳过 |
| 构建 | `cmake --build .tmp/build/suite-pageant --parallel 4` 通过 |
| Pageant CTest | 允许受控本地套接字后执行 `ctest --test-dir .tmp/build/suite-pageant --output-on-failure -L pageant`：11／11 通过，零跳过，11.60 秒 |
| Windows 宿主及 23 项原生 IPC | 未执行，等待仓库 Windows CI；Linux 构建不覆盖该 Windows 分支 |

检查完成，以上范围的文件所有权已释放，可交主会话精确提交并进入 Windows 诊断检查点。
