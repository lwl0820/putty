# Windows Pageant 双 IPC 独立审查

日期：2026-10-15。任务由主会话显式指定为 `10-15-pageant-qt-agent`。本轮只修改 `apps/pageant/` 和本任务研究记录，没有修改 vendor、共享运行时、根构建或 CI，没有提交、推送或改变完成状态。保留既有 Linux 和口令背压修复，没有再派实施／检查代理。

## 发现（已修复）

### R1：二次启动可被既有代理永久阻塞，关闭无法安全收尾

- 文件：`src/core/pa_windows.[ch]`、`src/core/pa_agent.c`、`tests/windows/native_ipc.cpp`、`verify.c`、runner、README。
- 确定路径：`client_only` 不初始化本地树，初次公开快照经 `pageant_enum_keys` 进入 `pageant_client_op_query`，再调用原 `agent_query_synchronous`。原 Windows 传输包含无限 `ReadFile` 和 `SendMessage`；同用户的既有代理只要连接后不答复，就能阻塞唯一核心线程。GUI 仍可响应，但新入口等待 `closed` 才退出，核心始终无法抵达安全关闭点。当前 Runtime 门面析构没有同步 join；不把这一问题误写成 GUI 析构 join。
- 与主会话确认后，在组件私有 Windows 宿主提供强符号同步查询入口。共享 SSH 客户端、协议解析和密码实现保持原有来源。管道优先，失败后以同一剩余预算尝试旧窗口；连接、完整请求、完整响应前缀与正文共享单次 8000 ms 预算。
- 本地管道使用同步 `PIPE_NOWAIT`，完整累计短读／短写和零进展重试，检查断开、长度上限、当前用户 owner SID 与本地服务进程；没有挂起 OVERLAPPED 或强杀签名线程。旧 IPC 使用 `SendMessageTimeoutA`，设置 `SMTO_BLOCK | SMTO_ABORTIFHUNG | SMTO_ERRORONEXIT`，禁止同线程队列重入，映射按原安全工具建立当前用户私有 ACL。
- 原枚举最多连续尝试 SSH1、扩展及 SSH2，且所有传输都失败时仍可能返回成功的空列表。新增进程内传输失败锁存，使后续请求立即失败；Qt 快照将该状态投射为 `PA_FAILURE`。当前二次启动是一次性客户端进程，重新启动才重连，没有承诺故障后的同进程自动恢复。
- 原生回归新增三个独立进程组：128 KiB 请求跨 4 KiB 管道缓冲并收取逐字节响应；已接收请求但不响应的管道；已进入 WndProc 但不返回的旧窗口。后两组在对端确认实际收到请求后才通知 GUI 关闭，断言 close 返回低于 500 ms、GUI 心跳最大间隔低于 500 ms、10 秒内安全关闭且确实记录传输失败。子进程在随机编译前缀及本次 scope 下运行，只回收本测试的对端。
- MAP 拒绝原 `aqsync`／`agent-client` 对象，要求私有有界入口和失败状态符号，防止测试客户端走原名个人代理。runner 要求新增检查与原 12 项各出现一次，共 15 项。

实现选择依据：微软说明 [PIPE_NOWAIT 的空读、满写和立即返回语义](https://learn.microsoft.com/en-us/windows/win32/ipc/named-pipe-type-read-and-wait-modes)；本路径是核心线程内有界同步客户端，没有把它当后台异步机制。[CancelIoEx 文档](https://learn.microsoft.com/en-us/windows/win32/api/ioapiset/nf-ioapiset-cancelioex) 要求取消完成前保留 OVERLAPPED 和缓冲，因此本轮未以取消后立即释放冒充安全超时。[SendMessageTimeout 文档](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-sendmessagetimeouta) 说明标志及同队列超时例外；本实现不启用可延长超时的 `SMTO_NOTIMEOUTIFNOTHUNG`。

8 秒只限制单次传输等待。它不是合法但缓慢的整个命令队列、密钥 KDF 或启动互斥锁的统一退出上界；没有新增全局取消意图，也没有声称不可取消的计算被终止。

## 实际路径复核

- 生产复用原 `agent_mutex_name()`、`agent_named_pipe_name()`，启动发现同时看命名管道和 `Pageant` 隐藏顶层窗口。锁在 GUI 所有者线程取得／释放，首次保持到两个入口就绪；Linux 仍在 Qt／线程创建前分流原 CLI。
- 命名管道直接复用上游监听器和私有安全描述符。旧窗口映射按当前用户 SID／历史进程 owner SID 校验，检查实际映射容量、消息长度、映射名终止与嵌入 NUL。检查失败没有进入代理协议。
- 兼容窗口线程只复制有界字节并等待事件；`add_handle_wait` 将请求交回唯一核心，签名和上游对象不在窗口或 GUI 线程执行。同步旧请求在该窗口串行处理。关闭先设置 stop，解除消息／回复等待并回收窗口线程，再撤提示、注销客户端、关闭管道连接和清除身份；GUI `close()` 不等待该辅助线程。
- Windows `noise_ultralight` 空操作与固定原 `windows/pageant.c` 对应；原 `pageant.c` 的致命 `random_read` 保留，不创建客户端 PRNG。测试持久化强拒绝桩及 MAP 同时限制原 storage、noise、sshrand 抽取。
- 测试生产名在编译变体中改名，每次再加随机 scope；没有生产环境变量改名入口。所有 runner 路径通过公共项目 `.tmp` 检查，二进制名称白名单、无存储环境、唯一 JSON、15 项全量结果和运行根空目录检查仍保留。原名跨版本兼容继续单列未验。
- 新入口只接当前声明的 Windows 参数，不将其冒充完整 Windows Pageant 命令行。未混入原 Win32 GUI／GTK 源；Windows 产品路径按 UTF-8 转为 UTF-16 PPK 文件名。

## 独立验证

本机为 Arch Linux x86_64、GCC 16.2.1、Qt 6.12.0，没有 Windows SDK。本轮 Linux 普通构建目录为 `.tmp/build/pageant-final-review`，没有启动生产默认 CLI 服务或访问个人代理。

| 检查 | 本轮结果 | 证据 |
| --- | --- | --- |
| Linux 产品及受影响测试增量构建 | 通过，退出 0 | `.tmp/pageant-windows-review-build.log` |
| 自有 C/C++ 严格语法／类型检查 | 13 条实际编译记录全部通过，增加 `-fsyntax-only -Wall -Wextra -Werror` | `.tmp/pageant-windows-review-strict.log` |
| Linux 完整 Pageant CTest | **11/11，0 skip，14.29 秒** | `.tmp/pageant-windows-review-linux-tests.log` |
| 核心内部场景 | **17/17，5.681 秒** | 构建目录 `Testing/Temporary/LastTest.log` |
| GUI 场景 | **8/8 独立进程，0 skip**，含超长口令及原 CLI 控制 | 同上 |
| Windows runner 门禁 | **3/3，0 skip**，覆盖 15 项完整报告、拒绝原无界客户端链接及非 Windows 禁运行 | 同上及直接 `python3 -B apps/pageant/tests/windows/test_runner.py` |
| Python AST、差异空白检查 | 全部通过 | 实际 AST、`git diff --check -- apps/pageant`，退出 0 |
| Windows C/C++、MSVC 链接及真实 WinAPI | **未执行，待仓库 CI** | 本机无 SDK，不以 Linux 编译／runner 模拟代替 |

本轮新修复只落在 Windows 分支及 Windows 专用测试。实施者此前的普通／ASan／UBSan／LSan 11/11 记录保留于 `windows-implementation.md`；本轮没有无必要重复整个 sanitizer、PuTTYgen 或三个 CLI 测试，也不把此前 Linux sanitizer 当新 Windows 传输通过。

## 发现（未修复）与交接

1. **原生证据缺口**：Windows 新代码尚无 MSVC 编译、链接或 WinAPI 执行结果；不能以本审查宣布 Windows 阶段通过。需 CI 构建 `pageant`、`pageant-windows-ipc-tests`，运行 `pageant-windows-ipc` 和 `pageant-windows-runner-contract`，并强制收取通过的 15 项 `.tmp/windows-ci/reports/pageant-ipc.json`。现有 CTest TIMEOUT 90 秒、runner 70 秒覆盖两个单次 8 秒负例，无需放宽以掩盖挂起。
2. **本轮未实施的产品项**：完整原参数、可选 AF_UNIX／WSL1、显式 OpenSSH 配置写出、同包 PuTTY 会话入口、真正第二跳及 OpenSSH 认证，按原任务继续；属于阶段范围外，没有擅自删除需求或扩写实现。
3. **不可由更名对端证明的验收**：专用身份下原名原版／新版先后启动、双向独立原版客户端、真实 Windows 10／11 桌面、托盘、跨完整性级别及错误 owner／ACL 场景均待验。当前 Windows 测试没有原存储隔离宿主，不扩大为原存储已通过。
4. **关闭边界**：保留唯一核心与不可强杀 KDF／签名的设计；本轮修的是失联对端无限传输等待。原启动互斥等待及任意完整工作批次没有新增硬截止时间，是主会话确认保留的边界。
5. **规范同步建议**：共享规范所有权在主会话。建议原生通过后补充“Pageant 二次启动私有同步传输必须有统一预算；失败枚举不能展示成功空库；Windows 测试双端改名和 MAP 禁原客户端”的可执行契约。目前只在本研究记录，避免将未运行平台写成已验惯例。

可提交点：本轮 `apps/pageant/` 全部实现、测试与 README 已稳定，不含他人组件改动；研究记录可由主会话选择单独纳入。未提交，未改变完成门。
