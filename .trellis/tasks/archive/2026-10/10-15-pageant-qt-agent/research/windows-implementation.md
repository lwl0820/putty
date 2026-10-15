# Windows Pageant 双 IPC 实施记录

日期：2026-10-15。阶段：Windows 平台宿主、Qt 产品入口、双 IPC 接线及独立原生测试入口。本机为 Arch Linux x64、GCC 16、Qt 6.12；没有 Windows SDK。本文件不将源码接线或 Linux 回归记为 Windows 通过，不勾选整个任务完成。

## 修改边界

只修改 `apps/pageant/` 及本记录。保留 Linux 独立检查 `review.md` 的 UTF-8 字节限制、队列满保留口令框、重试编号、关闭和取消修复。没有修改 vendor 0004/0005、公共 runtime、根 CMake、Suite*、工作流或其它组件。

- `src/core/pa_windows.[ch]`：Windows 平台和旧 IPC 搬运线程，生产原名、测试编译隔离名。
- `src/core/pa_agent.[ch]`：同一核心线程接上游命名管道、旧 IPC 客户端及现有提示/公开快照；Windows 二次启动的客户端模式。
- `src/core/pa_host.c`：Windows 沿原版 Pageant 的 `noise_ultralight` 空操作策略，默认平台函数用上游 Windows 实现。
- `src/app/main_windows.cpp`、组件 CMake：Qt Windows GUI 入口，原启动互斥/发现、PPK 启动添加；共享原 GUI 管理和托盘，无 Unix fork 源进入 Windows 目标。
- `src/ui/agent_window.cpp`：Windows 只更换地址标签为命名管道；Linux 标签及全部审查修复保持。
- `tests/windows/`、测试 CMake、README：真实 WinAPI 测试宿主、编译内固定测试向量、无存储拒绝桩、runner 和门禁测试。

## 生产数据流和安全边界

1. Windows 入口在 QApplication 前调用原 DLL 搜索保护与 DIT；参数解析后在 GUI 所有者线程持有原 `agent_mutex_name()`/`lock_interprocess_mutex()` 互斥。发现规则与上游一致，同时看原管道和 `FindWindowA("Pageant", "Pageant")`。
2. 首次启动由唯一 runtime 核心线程执行 `pageant_init`。管道直接用 `agent_named_pipe_name()` 和 `new_named_pipe_listener()`；后者保留原私有 ACL、首实例排他及本机连接限制。两条 IPC 都建立成功后才发 READY，由持锁 GUI 线程解锁。
3. 旧兼容窗口是专用辅助线程中的隐藏顶层窗口，类名/标题保持 `Pageant`，不是 Qt 窗口，也不是 runtime 网络 HWND_MESSAGE。原版客户端仍可以 `FindWindow` 找到它。
4. 映射打开、当前用户 SID/历史进程 owner SID、实际映射大小检查按固定 `windows/pageant.c` 提取；补零长度 COPYDATA、空指针、结尾和嵌入 NUL 检查。请求限制为原客户端上限 262144 字节以内，不在这里解释代理协议。
5. 兼容线程复制有界字节后设置自动复位事件；`add_handle_wait` 由 runtime 的 Windows HANDLE 桥在核心线程调用 `pageant_handle_msg`。原 `PageantClient` 返回响应，辅助线程写回映射。GUI 和兼容窗口均不执行签名或密钥操作。
6. 旧 IPC 使用同一 `prompt`/一次性 request_id/代次路径，不携上游指针进入 GUI。核心关闭先停止 IPC 等待并使在途映射得到失败，再取消提示、注销 legacy client、关闭监听和 accepted 连接、清除身份。辅助线程同时在 stop HANDLE 上等待；核心析构可以等它退出，GUI `Runtime::close()` 不同步 join。
7. `noise_ultralight` 的空操作直接对应固定 `windows/pageant.c:1448` 的明确策略“Pageant 不使用随机数”。它阻止网络/句柄 I/O 抽入客户端 `windows/noise.c` 和 `sshrand.c`。`pageant.c` 自带 `random_read` 致命拒绝实现继续保留，没有创造伪随机字节。
8. Windows PPK 路径按 C 边界 UTF-8 解码为 UTF-16，再调用 `filename_from_wstr`，避免当成本地 ACP。

二次启动无文件时提示已运行后退出。带文件时 `client_only` 不调用 `pageant_init`、不建立监听、不清除对端；原 `pageant.c` 客户端管理操作只在核心线程执行，按原管道优先、旧 IPC 回退向已有代理添加。公开快照改为按枚举回调计数，避免客户端模式访问未建立的本地树。当前 Windows 参数接入 `--gui`、`-keylist`、裸 PPK、`-encrypted`/`-no-decrypt`/`-nodecrypt`/`-no_decrypt`，按各文件出现位置保留加密加载标志。`-c`、`-openssh-config`、`-unix`、ACL 限制和其余参数仍未接入，不能宣称完整 Windows CLI 已完成。

## 原生测试入口

目标 `pageant-windows-ipc-tests` 从同一适配源码建立独立库。测试目标编译 `PA_WINDOWS_TEST_NAMESPACE=SuitePageantTest-<随机32hex>`，runner 每次再传随机 scope；测试专用 setter 仅在该编译变体存在，且要求创建线程前一次设置。生产没有环境变量改名开口。每次测试的管道、窗口、映射和互斥都带该独立命名空间。

宿主只建 QCoreApplication，不建 Language/Qt GUI，不读取个人 PPK、配置、agent 或注册表。`no_storage.c` 强符号禁止种子和设置打开；runner 在启动前审计 MSVC `.map`，拒绝 `sshrand`、原 `storage`、Windows `noise` 对象及 random_ref/reseed，要求原 Pageant 核心和持久化拒绝入口存在。所有进程 cwd/TMP/TEMP/TMPDIR、链接图、报告在项目 `.tmp`。

`pageant-windows-ipc` 的 12 项实际断言组：

| 组 | 原生操作 |
| --- | --- |
| startup-discovery | 首次不存在、建立后同二进制独立子进程持原锁发现既有服务 |
| dual-list | CreateFile/ReadFile 管道与 SendMessage/映射分别列空列表 |
| pipe-add-wm-list | 管道标准 ADD_IDENTITY 添加，旧 IPC 取相同真实公钥 |
| dual-signatures | 两条入口签二进制/中文消息，用独立公开密钥验签调用验证 |
| malformed-copydata | 空载荷和未终止映射名拒绝 |
| malformed-mapping | 长度 UINT32_MAX 返回 agent failure |
| cross-transport-remove | 旧 IPC 删除后管道列表为空 |
| deferred-cancel | 有效加密 PPK 的旧 IPC 签名触发串行提示，取消返回失败 |
| deferred-decrypt | 管道签名提示正确口令，真实解密并验签 |
| pending-close | 重加密后旧 IPC 在途提示退出，等待者失败、旧回复拒绝 |
| partial-pipe-close | 活动管道只写两字节后退出，确认对端 broken pipe |
| endpoint-cleanup | runtime 关闭后窗口/管道发现均消失 |

加密 PPK 是固定 RFC8032 Ed25519 公共测试向量，测试口令固定为 `suite-passphrase`；构建内嵌后仅经原 add-ppk 扩展添加，不创建运行密钥文件。生成过程只用项目 `.tmp`。已在 Linux 用同一原核心验证该向量可延迟提示、成功解密，并以 Python cryptography 独立 Ed25519 验签；这条证据只证明测试数据有效，不能证明 WinAPI 已运行。

原生命令已写入组件 README：启用 PAGEANT 和 BUILD_TESTING，构建 `pageant` 与 `pageant-windows-ipc-tests`，执行 `ctest -R '^pageant-windows-' -C Debug`。runner 固定白名单二进制，非 Windows 直接失败；报告 `.tmp/windows-ci/reports/pageant-ipc.json` 明确区分未验项。

## 已执行验证

1. Linux 增量构建 `pageant`、`pageant-core-host`、`pageant-prompt-failure`、`pageant-gui-tests`、`pageant-entry-tests` 通过。
2. `ctest --test-dir .tmp/build/suite-pageant --output-on-failure -L pageant`：**11/11，零跳过，11.23 秒**。其中核心互操作仍 17 场景，原 CLI 1 组，GUI 8 独立进程场景，新 runner contract 1 组。
3. `cmake --build .tmp/build/pageant-final-review-asan --target ...` 后同标签 CTest：**11/11，零跳过，15.42 秒**，保留 ASan/UBSan/LSan。日志分别 `.tmp/pageant-windows-stage-linux-tests.log`、`.tmp/pageant-windows-stage-linux-asan.log`。
4. `python3 -B apps/pageant/tests/windows/test_runner.py`：**3/3**，验证有效/无效报告、缺项/重复项、原存储/PRNG 链接图拒绝及非 Windows 不启动二进制。这不是 WinAPI 通过。
5. 新 Python AST 2/2、`git diff --check -- apps/pageant` 通过。未提交、未 push。
6. `clang-cl /nologo /Zs /TC ...` 探针明确失败于缺 Windows CRT `stdio.h`；本机没有 SDK，未得到 Windows 源码编译或链接结论，MSVC 和上述 12 项原生测试均 **待 CI**。

## 仍需独立检查及原生环境补齐

- 原名原版先启动/新版先启动、原版客户端对新版代理、新版对原版；随机改名宿主不能作为该证据。
- 真 Windows 10/11、MSVC、Qt Windows 插件/翻译部署、实体托盘和无托盘、OpenSSH 实际认证、跨完整性级别和错误 owner SID/ACL。
- 二次启动走上游同步 `agent_query_synchronous`，GUI 不等待，但失联或恶意旧代理不响应时的无限等待/取消边界仍需原生审查。当前测试专用宿主不启动这一远端客户端分支，不能把在途本地 IPC 退出通过替代它。
- 完整原参数、可选 AF_UNIX/WSL1、显式 OpenSSH 配置写出、同包 PuTTY 会话入口与真实第二跳。
- 本轮未扩大 Windows 存储/PRNG；私有 hive 已验不表示此产品测试已接原存储。CI 由主会话在独立检查后接入。

## 独立检查后的变更

独立检查发现二次启动的原同步查询会阻塞核心关闭，已在组件私有宿主覆盖同步传输并添加失败锁存，详见 `windows-review.md`。原生检查由上述 12 项增至 15 项；新增独立进程的管道部分写入／逐字节响应、悬挂管道关闭和悬挂旧窗口关闭。MAP 审计同时拒绝原 `aqsync`／`agent-client` 对象，避免测试误走未隔离的生产名称。上述第 6 条及原生未执行边界仍然有效，本机 Linux 检查不能作为 Windows 通过。
