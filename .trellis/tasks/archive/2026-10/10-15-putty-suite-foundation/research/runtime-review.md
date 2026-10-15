# 运行时只读追加审查

日期：2026-10-15。由主会话在构建专项检查后追加授权。读取公共 C/Qt 头、`src/core.c`、`src/runtime.cpp`、测试模块及生命周期用例；本代理只新增此前分配的两个链接宿主源，既有运行时文件由实施所有者修改。

## 发现与当前状态

### R1：事件超过限额时先复制、后拒绝

- 位置：读取时 `runtime.cpp` 的 `Worker::eventHost` 第 99–101 行与 `enqueue` 第 107–109 行。
- 事实：只在 `source->size > INT_MAX` 时提前返回；其余事件先构造完整 `QByteArray`，随后才检查 `limits.eventBytes` 及剩余额度。
- 影响：即使配置 4 MiB 的事件字节上限，模块给出较大事件时仍会先临时分配、复制最高接近 2 GiB 的材料，才返回背压。有限队列不能因此保证此边界的内存受限。
- 建议：在复制前按事件大小与当前限额拒绝，保持成功入队前再次确认或在同一受锁分支完成复制/入队；不能因为修复而吞掉原背压结果。
- 原审查时状态：已报主会话协调，尚未由本代理修改或复验最终修复。最终状态见下方追加复核。

### R2：Windows HANDLE 只核对数值，没有等待注册代次

- 位置：读取时 `runtime.cpp::refreshHandles` 第 167–188 行；`core.c::pr_core_handles/pr_core_handle_ready` 第 395–421 行。
- 事实：Qt 通知器和回调以 HANDLE 数值作为映射键；激活时只核对相同数值仍映射到原通知器，C 层重新按相同数值查找当前 `HandleWait` 后直接激活。上游 `HandleWait` 自身另有注册对象，但桥只传递 HANDLE。
- 触发路径：旧注册的激活已进入 Qt 消息队列；同轮模块操作删除等待项并关闭 HANDLE，再创建复用相同数值的新 HANDLE。刷新时数值仍存在，旧通知器不会被撤销；旧激活可能交给新 `HandleWait`，即使新注册没有就绪。
- 对照：Unix fd 已额外携带注册编号并逐项检查；Windows 当前只有相同句柄值检查，不能据此证明复用安全。
- 建议：桥同时携带等待注册身份/代次；旧注册撤销后禁用旧通知器，再注册新等待对象。需要 Windows 原生用例覆盖同值复用、已排队激活、主动关闭及手动/自动复位事件。
- 原审查时状态：已报主会话。没有 Windows SDK/原生环境，本代理不编造复用用例通过；该平台缺口保留。最终源码和算法复核见下方。

## 已核对的正确路径

- 一进程单核心锁在第二个活动核心时返回 `PR_BUSY`。模块入口、回调及计时都由所属线程执行；代次、请求所属对象和重复回复分别核对。
- 命令和事件限制项数及字节数；普通事件为命令完成预留一个队列槽。命令仅在仍有完成槽时弹出，完成事件不携带数据，因此不会因普通事件恰好填满容量而静默丢失。
- `Runtime` 析构不阻塞工作线程；独立状态继续持有核心，前端指针撤销后异步清理。排队投递用对象上下文，事件槽立即删除门面时 `drainEvents` 在每次发信号后检查 `QPointer`。
- 关闭先停止外部通知和新输入，再取消请求、析构模块，最后清扫析构中新排队的模块/宿主回调与计时包装器。模块负责销毁自己所有上游子对象；只清扫模块地址不能代替子对象的内部清理。
- Unix fd 重配禁用旧通知器，注册编号防止旧队列操作落到复用对象；OOB 在读取前重新检测，按异常、读、写顺序执行，并在每项前核对注册仍有效。
- Windows 网络保留原版 WSAAsyncSelect、隐藏 HWND、顶层回调与 moribund socket 过滤；HANDLE 和 socket 走不同路径。没有从 Linux 静态源码推导它们已经通过 Windows 运行验收。
- 通用层不再初始化客户端 PRNG。Pageant 单独链接目标及原版随机读取致命策略已有最小实跑证明，见 `build-review.md`。

## 执行与边界

本机增量构建通过；允许受控回环监听后，Linux `runtime-lifecycle` 为 1/1 通过（1.18 秒）。现有用例覆盖真实 256 KiB 双向字节、消费者暂停及恢复、OOB、连接关闭与重开、计时/回调取消、请求错对象/重复、析构新回调、同步操作期间异步关闭、门面提前析构和槽内删除。

该用例不等同于实际 GSSAPI/KDF、完整 TermWin 绘制、十秒 GUI 心跳门、Windows HWND/HANDLE 生命周期或桌面验收。R1/R2 的最终修复与验证尚需主会话追加，不能仅以本机 CTest 通过消除。

## 最终独立复核与 R3 自修

同日由主会话再次派发，活动任务显式为本基础任务。按 `trellis-check` 和 `trellis-before-dev` 重新读取任务、已有发现及 `suite-build/suite-runtime` 契约。R1/R2、vendor 补丁和构建文件保持只读；确认额外 R3 后获得主会话授权，只修改 `core.c`、`test_module.c/.h`、`runtime_test.cpp` 的对应局部，没有覆盖并行终端或 CLI 文件。

### R1 结论：已修复

`Worker::eventHost` 在同一把队列锁内先比较单条大小、项数和剩余字节，再构造 `QByteArray` 并入队。拒绝分支没有读取 payload。`eventCopyLimit` 暂停消费者，在 64 字节限额下先接收 48 字节，再将不可读页作为 32 和 128 字节载荷，分别覆盖超剩余额度及超总限额。只观察到合法的 48 字节事件，两个拒绝均保持背压状态；本轮普通及 sanitizer 生命周期测试实际执行了该路径。

### R2 结论：源码及真实算法已修复，Windows 原生待验

- 生产 `HandleWait` 使用进程内单调 `uint64_t registration`；等待快照保存树索引和编号。激活先在当前树查找索引，再比较编号，完全不解引用旧等待对象。回调自注销后没有继续访问旧对象。
- 生产 `windows/handle-wait.h` 由原 `platform.h` 引入；原公开函数签名保留，原生三处等待循环调用方式不变。测试使用此真实头及真实 `handle-wait.c`，没有复制算法。64 项上限仍由断言显式保护。
- `pr_handle_watch` 携带句柄和编号；C 激活同时核对两者，Qt 映射按编号区分注册，同值复用时禁用并延后释放旧通知器，迟到捕获仍被 Qt/C 双重核对拒绝。
- `0003` 覆盖修改的两源及新头，反向校验成功；`0002` 同样与当前源码一致。熵补丁保留强随机、进程列表和种子，新增结构清零，没有环境字符串拼 shell。
- 本轮 `runtime-handle-registration` 实跑通过。该结果只证明等待注册算法；不能替代 Windows 原生编译、已排队 Qt 激活、自动/手动复位事件和 HWND 关闭验证。

### R3：读回调关闭后仍处理同批写通知，已自修

- 位置：`core.c::pr_core_fd_ready`。原代码只在批次入口核对 `closing`；X/R/W 后续项只重查 fd 注册。因此读回调调用 `pr_core_begin_close` 后，同批写回调仍会进入模块。此入口也没有其它分发入口已有的 `dispatch_depth` 保护。
- 真实复现：独立 C host 创建 `socketpair`，发送一个字节，使被测端读写同时就绪；按真实注册编号交付 R/W。同一读回调发起关闭，旧实现输出 `read=1 write_after_close=1` 并退出 3。源及初次结果保存在 `.tmp/suite-final-review-temp/fd-close-probe.c`、`fd-close-probe.log`。
- 修复：在每项通知前同时核对 `closing`、原注册编号和该项 interest；整个 fd 分发进入/退出 `dispatch_depth`，所有分支均平衡，模块回调不能绕过顶层销毁断言。保留原 X/R/W 顺序及 OOB 探测，没有改公共接口。
- 回归：`runtime_test_fd_close` 使用真实 socketpair，先用 `poll` 确认同时读写就绪，再确定地交付同一批 R/W；读回调实际读取字节并关闭，断言写回调为零，然后在顶层销毁。它纳入已有 `runtime-lifecycle`，不依赖 Qt 偶然合并通知；测试函数仅在 Unix 编译。
- 同一独立探针重链修复后的核心，输出 `read=1 write_after_close=0`，退出 0；结果保留 `fd-close-probe-after.log`，没有覆盖初次失败。

### 本轮验证

环境：Arch Linux x64，GCC/G++ 16.2.1、CMake 4.4.4、Qt 6.12.0。独立目录没有使用主会话或产品代理构建树。

| 检查 | 实际结果 |
| --- | --- |
| `.tmp/build/suite-final-review`，Release，四个正式运行时宿主构建 | 退出 0 |
| R1/R2 最终源码的 `ctest ... -L runtime` | 5/5、0 跳过、4.18 秒，包含公共隔离、生命周期、真实等待算法及两个链接宿主 |
| R3 修复后的增量构建与 `runtime-lifecycle` | 1/1、1.20 秒、退出 0 |
| `.tmp/build/suite-final-review-asan`，Debug、ASan＋UBSan、含 R3 的生命周期 | 1/1、1.31 秒、退出 0，无 sanitizer 诊断 |
| 七个自有 C/C++ 源的原编译参数加 `-fsyntax-only -Wall -Wextra -Werror` | 全部退出 0，包含 R3 改动；日志 `strict-self-sources-final.log` |
| `run_runtime_test.py` AST 语法检查、`git diff --check` | 通过 |
| `0002/0003` 的 `git apply --reverse --check --directory=vendor/putty` | 均退出 0 |
| Pageant 最终可执行文件 `nm` | 只有代理 `random_read`，没有 `random_ref` 或 `global_prng` |

构建日志在 `.tmp/suite-final-review-temp/`，CTest 原记录在对应构建目录 `Testing/Temporary/`。本轮上游未修改文件仍有 GCC 16 的 const 丢弃及 `_GNU_SOURCE` 重复定义告警；自有七源严格检查无告警。仓库没有额外独立 lint/type-check 命令，本次以空白、Python AST、严格 C/C++ 语法/类型检查及真实链接作为对应检查，不称上游全体零告警。

定点只读检查 `SuiteTestSupport.cmake`：仅 Linux 且非交叉编译注册测试；编译器、工具链/sysroot 和标志传播到独立上游，去掉 `NDEBUG`；产物、导入位置、CTest 的 `$<TARGET_FILE>` 按配置一致。独立 `.tmp/build/suite-final-review-cli-multi` 禁 Qt 配置成功，测试 keygen 的 Release 真实构建成功，生成的 Debug/Release 用例各自指向相应目录；工具明确没有安装步骤。此项没有代替 CLI 完整功能测试、跨工具链实测或 Windows 构建。

主会话在 `platform-validation.md` 另记固定 Qt 6.8.3 的两个 Ubuntu 结果；其 R1/R2 日志不由本代理冒称执行，R3 影响的目标回归由主会话随后复跑。没有剩余已确认的本轮代码缺陷。Windows 原生与隔离、真桌面、长 KDF/GSS、完整产品/发布包仍维持原未验边界；基础任务和父任务没有标记完成。
