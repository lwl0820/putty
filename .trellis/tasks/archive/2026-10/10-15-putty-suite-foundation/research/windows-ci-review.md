# Windows CI 与无存储原生宿主独立审查

日期：2026-10-15。活动任务：`10-15-putty-suite-foundation`。

## 结论

已定向审查 `.github/workflows/putty-suite.yml`、`tools/suite/windows/`、`libs/putty-runtime/tests/windows/`，并核对主会话所有的测试 CMake、共享运行时及固定上游事件路径。未发现需要在本次范围内修复的确定性代码缺陷；未改动产品、公共运行时或构建接线。本轮只新增本审查记录，没有提交或推送。

本地可运行的脚本检查通过，可进入真实 Windows CI 首次执行。此结论不表示 MSVC 编译、链接图或 Win32 用例已经通过，也不表示基础任务完成。

## 实际路径核对

- HANDLE 宿主使用真实 `CreateEventW`、`SetEvent`、`ResetEvent` 和 `WaitForSingleObject`；分别核验手动与自动复位。旧快照场景在真实等待完成后注销原项，用同一仍存活的 HANDLE 重注册；同时检查注册编号变化及旧快照不能激活新项。回调内自注销后再次激活不再执行。
- Qt 宿主使用正式 `Runtime`，没有另造平台事件循环。核心线程注册两个真实事件，运行时创建 `QWinEventNotifier`；模块发出事件和析构均检查 Win32 线程编号。
- TCP 通过上游 `sk_new`，沿隐藏 HWND、`WSAAsyncSelect`、`winselgui_response` 和顶层回调交付数据；256 KiB 数据经 Qt 对端回显后按字节比较。非 inline OOB 通过真实 `send(..., MSG_OOB)` 发送，只有 `urgent == 2` 被计为紧急数据，并单独检查后续普通数据。本地关闭、重开、远端关闭和已设置事件中的核心关闭均有实际状态断言。
- 事件宿主没有客户端 PRNG 初始化、会话入口或 CLI 调用；网络轻噪声入口是本宿主专用空实现，seed/settings 入口直接终止。核对 CMake 为事件宿主生成 MSVC map，并用四个 `/INCLUDE` 保留拒绝入口。运行前审图拒绝真实 `sshrand`、`noise`、`storage` 对象和客户端随机入口。
- 两个原生 CTest 没有跳过返回码。运行器先核对标签选择恰好包含两个指定测试，再核对实际 JUnit 中二者存在、无失败、无跳过；非零 CTest 状态继续失败。`runtime-lifecycle` 的 Windows 存储隔离缺口仍返回 77，未将其改成通过。
- CLI-only 在禁止 Qt 查找时配置构建；多配置先验证缺少 Release 时安装失败，再分别核对 Debug/Release 安装文件及对应摘要。完整 Release 核对三个 CLI 的精确安装清单及有效断言标志，Ninja 只输出展开响应文件后的编译命令，没有执行 CLI 的版本或帮助入口。
- 工作流固定 Windows Server 2022、MSVC x64、Qt 6.8.3、CMake 3.30.9、Ninja 1.11.1.1；Qt 缓存键包含来源锁及构建脚本摘要，没有修改既有 PuTTYgen 工作流。PowerShell 的各构建、安装和核查调用均传播退出状态。
- 报告及子进程临时目录限制在项目 `.tmp/`，拒绝上级跳转、符号链接和 Windows reparse point；子环境不修改 HOME/USERPROFILE/APPDATA，不把 CI 标记当注册表授权。上传仅含指定 JSON/XML、两个原生测试 EXE 和三个 CLI EXE，不递归收集运行根、密钥、hive 或缓存。

## 本机验证

环境：Linux x64、Python 3.14.8。系统存在 Clang/clang-cl，但没有 Windows SDK、MSVC、MinGW 或 Wine；未尝试用平台替身冒充原生编译。

| 检查 | 实际结果 |
| --- | --- |
| `python -B -m unittest discover -s tools/suite/windows/tests -v` | 13 项通过、0 失败、0 跳过，0.188 秒 |
| `python -B -m unittest discover -s tools/qt/tests -v` | 9 项通过、0 失败、0 跳过，0.491 秒 |
| Windows 工具 Python AST | 5 个文件解析通过 |
| 工作流 YAML | BaseLoader 解析通过；核对三个触发入口、只读 contents 权限、目标 runner 和 13 个步骤 |
| `git diff --check` | 退出 0；新增审查记录另行核对无行尾空白 |

工程没有为这组文件配置独立 Ruff/Mypy/Actionlint 命令，本机也未安装这些工具。因此上述 AST/YAML/空白检查是实际静态证据，不写成已经运行专用 lint 或 C/C++ 类型检查。C/C++ 类型、Windows SDK 声明和实际链接闭包必须由新 CI 的 MSVC 构建验证。

## 未执行项及建议

- 新工作流尚未在 Windows 运行；两个原生宿主的 10 个具名检查和实际 map 审查均待 CI，不能从本机 22 项 Python 成功推断通过。建议主会话选择已审核文件提交并触发这份工作流，再根据真实诊断修复。
- HKCU/随机种子、原版 CLI 子进程隔离、Windows SSH/代理互操作仍未实施验收。这属于已有隔离门及后续工作，不在本次无存储宿主内扩成持久化运行。
- 本宿主不替代 Windows 下完整生命周期、背压与存储组合，也不覆盖终端渲染、长 KDF/GSS 或 Pageant IPC。共享任务仍应保持进行中。
- Windows Server runner 不替代 Windows 10 1809+/11 的真实桌面验收；用户已选择自行验收桌面交互，后续结果分别记录。

现有规范已经要求平台证据分开、未验证 HKCU 时拒绝存储、队列/事件代次与真实链接；本轮未产生需要新增 `.trellis/spec/` 契约的实现变更。
