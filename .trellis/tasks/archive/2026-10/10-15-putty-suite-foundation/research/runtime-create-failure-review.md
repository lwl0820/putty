# 模块创建失败清理与 CI 必需回归局部复核

## 范围与结论

本轮独立检查 `libs/putty-runtime/src/core.c`、`tests/CMakeLists.txt`、新增 `tests/create_failure_test.c`、运行时规范第 5／6 节、`windows-pageant-native-root-cause.md`，以及 Windows／Linux CTest 运行器和对应两份公共合同测试。已读取本任务 `check.jsonl`、所列规范和研究、PRD／design／implement 及父任务设计／计划。

没有发现需要追加修改的生产代码问题。`create` 返回空模块时，公共核心仍完整取消请求、释放包装工作项、清理自身回调和平台资源并释放唯一核心锁；只有非空模块上下文执行模块析构及模块回调／计时器最终清扫。正常析构新增工作之后的最终清扫顺序保持不变，没有改上游源码或 Pageant AF_UNIX 算法。

同次 Windows 诊断日志和 MAP 已核对：`0x30802` 为 `findrelpos234 + 0x92`，`0x2ffae` 为 `del234 + 0x1e`，`0x21bae` 为 `pr_core_destroy + 0xfe`，`0x21a94` 为 `pr_core_create + 0x2a4`。该失败属于空模块计时上下文清理，不能归因为未出现在栈上的网络响应函数。

## 已自修的问题

- 文件：`tools/suite/ci_tests/test_linux_ci.py`。
- 问题：新增窄回归的专用 Linux 门禁测试只明确覆盖缺失、`skipped` 和 `failure`，没有覆盖该用例重复注册、重复 JUnit、`error` 或三种未执行 `status`；这不足以完整验证本轮承诺的拒绝矩阵。
- 修复：在现有方法内补齐重复注册，以及 JUnit 重复、`error`、`notrun`／`disabled`／`skipped` 状态负例。生产校验算法未变。

## 核对的行为

- 新 C 宿主使用生产核心与真实上游 `callback.c`、`timing.c`、`tree234.c`，执行失败→成功→失败→成功，共四次创建、两次析构。
- 创建中重入返回 `PR_BUSY`、结果句柄为空；创建失败返回 `PR_PLATFORM_ERROR`、不析构空模块，并能成功创建下一核心。
- 四次创建登记的请求均被取消，核心级直接回调、排队工作及长计时包装器均清理；全局空上下文回调仍执行。
- 成功模块析构期间新增的原始回调和计时器最终被清扫；运行中的真实计时器存在，结束后无残留回调／计时器。
- Linux MAP 确认真实核心／回调／计时器／树对象，无生产 `storage.c.o`、`sshrand.c.o`、`prng.c.o`；四个种子／设置入口来自既有致命拒绝的 `no_storage.c.o`。宿主不创建 socket、Conf 或客户端 PRNG。
- CTest 精确注册一项 `runtime-create-failure`，Linux 标签为 `runtime;no-storage`、期限 15 秒且无跳过返回码；Windows 构建再追加 `windows-native`，MSVC 保留四个致命拒绝入口及 MAP。Windows 运行器恰好要求三项原生结果，Linux 运行器要求此新增回归。
- 两平台合同测试均拒绝该回归缺失、重复、失败、错误、跳过和未执行状态。规范同步描述已落地的失败路径，没有把 Windows 尚未执行写为通过。

## 独立验证

环境：Arch Linux、GCC／G++ 16.2.1、系统 Qt 6.12.0；不是目标 Ubuntu 或 Windows 结果。

| 检查 | 实际结果 |
| --- | --- |
| Release 构建三个相关宿主 | 成功，Ninja 确认无需重编译 |
| `runtime-create-failure`、`runtime-agent-core-link` | 2／2 通过，分别约 0.01／0.11 秒 |
| 真实回环 `runtime-lifecycle` | 1／1 通过，1.07 秒 |
| ASan＋UBSan、默认 LSan 的 `runtime-create-failure` | 1／1 通过，0.02 秒 |
| 旧／新二进制差分独立复跑 | 旧源码 `SIGABRT` 命中 `!(equal_permitted && !e)`，新源码退出 0 |
| Windows 公共合同测试 | 21／21 通过，无跳过 |
| Linux CI 合同测试 | 5／5 通过，无跳过 |
| 三个 C 文件原编译参数加 `-Wall -Wextra -Werror -fsyntax-only` | 通过 |
| 四个 Python 文件 Python 3.11 AST 与 `compile()` | 通过 |
| 已跟踪局部改动 `git diff --check`，新增 C／研究文件空白检查 | 通过 |

回环和 LSan 按已有授权在沙箱外运行；前者需要本地 socket，后者需要检测器可用的进程环境，没有关闭泄漏检查。旧差分进程关闭 core dump，全部比较输入与产物留在项目 `.tmp/`。旧源码 SHA-256 为 `22aa0aba8cf7bf272966bf15c665d13916ece5de26173fee65eee08339255906`。

仓库没有为该局部范围配置独立 Python linter／静态类型检查器；本轮使用严格 C 编译警告、C 类型／接口编译、Python 语法编译和实际合同测试，不将未配置工具报告成已执行。

## 未修复问题与待验边界

没有遗留的已发现代码问题。Windows SDK／MSVC 原生编译、新窄回归原生执行，以及修复之后 Pageant 的全部 23 项 AF_UNIX 检查尚待同一提交的仓库 CI。Windows 10／11 桌面验收和完整套件任务也未在本轮完成；本报告只批准这一局部改动进入下一轮原生验证。
