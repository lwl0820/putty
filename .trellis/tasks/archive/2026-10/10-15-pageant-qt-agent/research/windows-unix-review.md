# Windows Pageant AF_UNIX 增量独立审查

日期：2026-10-15。审查范围为 `pa_agent.[ch]`、Windows 原生 IPC 宿主及其 Python 运行器；未修改产品入口、GUI、CMake、工作流、公共 PrivateData 或第三方源码。共享工作区中其它代理的修改保持原样。本机为 Arch Linux x64、GCC 16.2.1、Qt 6.12，无 Windows SDK／MSVC 或可执行的 Windows。

## 已修复问题

### 端点类型不能证明本次监听归属

原实现先 bind，再通过路径打开 AF_UNIX reparse point，检查标签及单硬链接后保存句柄。此检查可以拒绝普通文件，却不能证明打开的是本次 bind 创建的端点：另一个进程可能在两步之间替换成自己的 AF_UNIX 端点，随后析构会删除替代对象。

现在打开端点时不共享 DELETE，固定当前路径对象；随后枚举上游公开的原生套接字列表，以 `getsockname` 定位本次 AF_UNIX 监听，建立独立非阻塞自连接，并从该监听直接 accept。连接两端的 `SIO_AF_UNIX_GETPEERPID` 均须成功且 PID 为当前进程，整个连接／接收／检查的等待预算为 250 ms。核心在 create 内不执行 Qt／上游事件循环。只在以上实际归属检查完成后保存可删除句柄；能力不可用、超时、错误 PID 或对象不符均关闭临时句柄并拒绝启动，不按路径删除未知对象。

GETPEERPID 的 PID 缓冲先清零。旧 Windows 驱动存在成功写入 PID、`bytesReturned` 却为 0 的已报告偏差，故不以返回长度必须为 4 作为门；成功状态与真实 PID 仍必须同时匹配。

直接 accept 的测试连接可能继承监听器的 WSAAsyncSelect 注册。关闭前撤销其注册，随后使用上游 `done_with_socket` 消息屏障，避免其已排队消息错误投递给复用句柄。监听器本身的过期 FD_ACCEPT 在上游无连接时按 WSAEWOULDBLOCK 返回，不销毁监听器。

### 增加可复现的跨进程替换情景

专用随机命名空间编译变体在 bind 后、打开文件句柄前调用窄测试 hook；生产目标没有该入口。原生宿主预先启动独立子进程，由 hook 删除本次夹具刚创建的端点并通知子进程绑定同一路径。随后必须满足：

- 核心启动失败、异步关闭完成，首条命名管道及兼容窗口不存在；
- 子进程替代端点仍存在且能连接，实际 GETPEERPID 为该子进程，不能被核心收尾删除；
- 停止该子进程后，夹具仅通过已固定的对象句柄删除它自己的替代端点。

本情景归入 `unix-listener-failure-cleanup`，保留原 15 项及新增 8 项，共 23 项，不放宽 70 秒 runner／90 秒 CTest 上限。运行器报告新增已使用的归属核查方法和替代端点保留标记；失败仍不生成成功报告。

## 已核对路径

- 两个 listener 分别拥有 Plug、Socket 和连接链，共用唯一核心线程的同一 Pageant 密钥库；部分初始化失败与正常析构均逐个先关闭在途连接、后释放 listener。
- `client_only` 与非空 socket_path 的矛盾组合直接拒绝；结构布局不变，空路径继续保持原命名管道及 WM_COPYDATA。
- 新增 Windows 源文件的头顺序为 winsock2／afunix／windows；核心经 putty.h 的平台头获得 Winsock 2，AF_UNIX 使用构建探测。当前官方 SDK 的 AF_UNIX 标签及 GETPEERPID 宏定义已核查，实际目标 SDK 编译仍待 CI。
- ACP 原字节和内核 UTF-8 路径解释分开记录，严格转换给 W 文件 API；不会将任意非 ASCII ACP 路径宣称为已支持。
- 三传输跨端添加／列出／删除、含 NUL 的中文消息签名与独立 Ed25519 验签均走实际原生宿主路径；23 项报告仍精确拒绝缺项、重复和旧 15 项历史报告。
- MAP 的持久化／客户端 PRNG 拒绝、PrivateData 的受保护私有父目录及预存文件身份／内容校验保持。

## 验证

| 检查 | 实际结果与边界 |
| --- | --- |
| `git diff --check -- apps/pageant` | 通过 |
| 两个 Windows Python 文件按 Python 3.11 AST 解析 | 通过；不执行 WinAPI |
| `python -B apps/pageant/tests/windows/test_runner.py` | 4／4 通过 |
| `cmake --build .tmp/build/suite-pageant --parallel 4` | 通过；Linux 构建不编译 Windows 分支 |
| `ctest --test-dir .tmp/build/suite-pageant --output-on-failure -L pageant` | 11／11 通过、零跳过、9.87 秒；真实本地 socket／受控 SSH 需沙箱外执行；日志 `.tmp/pageant-unix-review-ctest.log` |
| 原样 C 归属探针＋POSIX 系统调用适配 | 严格 C11、`-Wall -Wextra -Werror` 编译通过；正常归属、错误 PID、能力失败、跨进程替换保留及 250 ms 上界，共 5 项通过；源码／二进制均位于 `.tmp/pageant-unix-review/`；此项仅验证控制流及 Linux 真实 AF_UNIX，不能替代 Windows |
| Windows MSVC 编译及 23 项原生检查 | 未执行，等待仓库 Windows CI |

没有配置独立 Python 类型检查器；AST 及运行器检查通过。Windows C／C++ 类型和 SDK 接线尚无本机证据，不能记为通过。

## 未验边界与后续

1. Windows Server CI 仍须实际验证：端点 DELETE 访问／共享模式是否被 afunix.sys 接受、自连接／直接 accept、GETPEERPID、旧 FD_ACCEPT 消息、替代端点保留及正常端点删除；这些属于本增量原生验收门。
2. 官方资料没有提供 GETPEERPID 的精确最低 Windows 版本表。Windows 10 1809 的能力仍须目标实机验证；不支持时安全拒绝该可选后端，不降级到未经证明的删除。Windows 10／11 桌面按用户选择由用户人工验收。
3. bind 后系统错误、文件句柄权限失败或归属核查失败时，未获得删除权的对象可能保留。此分支优先拒绝误删，不能宣称所有底层异常均无残留。
4. 本增量没有接 Windows `-unix` 产品参数；原名跨版本单实例、跨完整性级别、完整 OpenSSH／WSL 认证与桌面行为仍保留缺口，不能据此完成 Pageant 子任务。

本范围可交主会话提交并进入 Windows CI 检查点；只有原生检查实际成功后才能记为 Windows AF_UNIX 通过。规范同步由主会话负责，建议补充“端点类型不是归属证明、获得清理权前必须固定对象并完成真实监听归属核查”的契约。

## 官方来源

- [Microsoft AF_UNIX 说明](https://devblogs.microsoft.com/commandline/af_unix-comes-to-windows/)：UTF-8 sun_path、文件权限、reparse point 和应用删除职责。
- [官方 Windows SDK afunix.h](https://raw.githubusercontent.com/microsoft/win32metadata/main/generation/WinSDK/RecompiledIdlHeaders/shared/afunix.h)：GETPEERPID 宏与 ULONG PID。
- [官方 Windows SDK winnt.h](https://raw.githubusercontent.com/microsoft/win32metadata/main/generation/WinSDK/RecompiledIdlHeaders/um/winnt.h)：AF_UNIX reparse 标签。
- [Microsoft WSL 问题 4676](https://github.com/microsoft/WSL/issues/4676)：Windows 1903／1909／2004 返回长度为零的复现报告；该问题报告不等于最低版本支持承诺。
- [SetFileInformationByHandle](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-setfileinformationbyhandle)：按对象句柄设置删除标记及 DELETE 权限。
