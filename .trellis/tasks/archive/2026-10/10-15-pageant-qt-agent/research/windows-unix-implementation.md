# Windows Pageant 可选 AF_UNIX 后端实施

日期：2026-10-15。本轮仅补核心的可选监听后端及专用原生测试；没有修改 Windows 产品入口、GUI、CMake、工作流或 vendor，没有提交。P11 的完整参数解析、I12 的 WSL 1／OpenSSH 认证仍未完成。本机 Arch Linux x64、GCC 16.2.1、Qt 6.12，没有 Windows SDK 或可运行的 Windows，因此不将源码接线或 Linux 回归记为原生 Windows 通过。

## 修改边界与契约

- `apps/pageant/src/core/pa_agent.c`：保留命名管道 `listener`，另建独立 `unix_listener`，通过既有 `pa_options.socket_path[108]` 开启；空串保留双 IPC。两者各自拥有 `Plug`、`Socket` 和 `pageant_listen_state`，共用唯一核心线程的同一密钥库及口令／请求状态。每个监听器均先 `pageant_listener_shutdown_connections` 再 `pageant_listener_free`，包括第二监听建立失败的回退路径。
- Windows 沿固定上游 `windows/pageant.c`／`windows/network.c`，使用 `sk_newlistener_unix`、上游 ACP 输入原字节及 `sun_path` 的 108 字节容量（最多 107 字节加 NUL）。没有宣称 UTF-8／任意中文 Windows 路径可用，没有修改 `pa_options` 布局；`client_only` 与非空监听路径的矛盾组合直接失败。
- 与原版先 `remove` 不同：拒绝所有已经存在的目标。微软官方说明 Winsock 内核实际按 UTF-8 解读 `sun_path`（不是 ACP）。创建前对同一组输入字节做严格 UTF-8 转换；非法编码直接拒绝，不将任意 ACP 中文路径冒充支持。用转换结果调用 W 文件 API 核验和打开实际内核路径，传给 Winsock 的字节不变。成功监听后，以 `FILE_FLAG_OPEN_REPARSE_POINT`、`DELETE` 和 `FILE_READ_ATTRIBUTES` 打开具体端点，核验 AF_UNIX reparse tag、非目录及单硬链接。保存该对象句柄，关闭网络监听／连接后以 `FileDispositionInfo` 标记该对象删除，再关闭句柄；不在收尾重新按路径 `DeleteFile`，避免误删后来替换的路径对象。
- 若监听成功后无法获取／核验端点句柄，启动失败并关闭监听，但不盲删无法确认的文件。极端的 bind 后底层错误或权限变化可能留下端点；该分支优先保留未知对象，未将其描述为已证实无残留。正常关闭／受控第二监听失败无残留仍须 Windows CI 实测。
- 原命名管道 ACL、原窗口 owner 校验、单实例发现、私有有界客户端查询及 MAP 的无存储／无客户端 PRNG 审计保持。可选 Unix socket 的访问权限沿 Windows 文件系统／父目录权限，专用测试使用下述私有父目录；跨完整性级别和产品任意路径 ACL 不是本轮通过项。

## 专用原生检查

`apps/pageant/tests/windows/native_ipc.cpp` 保留原 15 项检查，追加 8 项，共 23 项。原生宿主不读写注册表、种子或私钥文件；固定 RFC8032 向量及加密 PPK 仍以内存协议使用。

| 新检查 | 实际宿主行为 |
| --- | --- |
| `unix-list` | 新代理通过 AF_UNIX 枚举空库，并与命名管道／WM_COPYDATA 返回同一公开身份列表 |
| `unix-cross-transport-keys` | 管道添加→Unix／窗口枚举、Unix 删除→管道／窗口空库、Unix 添加→管道／窗口枚举、窗口删除／添加→Unix 可见、管道删除→三端空库 |
| `unix-signature` | AF_UNIX 签名包含 NUL 的中文／二进制消息，使用独立校验入口核对真实 Ed25519 签名 |
| `unix-existing-file-preserved` | 私有根内的预存普通文件使启动失败；内容保持，runner 还核对文件身份、哈希及真实私有 ACL |
| `unix-listener-failure-cleanup` | 缺失父目录令第二个真实 Winsock 监听建立失败；另验非法 UTF-8 原字节启动被拒绝；核心关闭后首条命名管道与兼容窗口也必须不存在 |
| `unix-partial-close` | Unix 半包与原管道半包、窗口在途口令请求同时存在，关闭后 Unix 连接必须 EOF／已重置，不能超时 |
| `unix-pending-close` | 单独重建同一私有端点，真实加密身份的 Unix 签名产生口令提示；异步关闭后在途签名失败／断开，过期回复被拒绝 |
| `unix-endpoint-cleanup` | 两次正常关闭后端点均不存在；保留原管道／窗口／启动锁收尾检查 |

原生测试给自己的原始 Winsock 客户端持有独立 `WSAStartup` 引用，避免核心退出的 `WSACleanup` 使客户端提前被测试进程自行关闭。所有 Unix 请求的发送／接收仍有 8 秒系统超时，runner 总 70 秒、CTest 90 秒未放宽。

`run_ipc_test.py` 复用主会话抽出的 `tools/suite/windows/private_data.py::PrivateData`，创建并核验本轮目录的当前用户／SYSTEM 两条受保护 DACL，固定祖先目录句柄；只在项目 `.tmp/` 的随机本轮根内创建 socket 和预存测试文件。路径在交给宿主前先按真实 Windows `mbcs` 严格往返，并核实这些字节按内核 UTF-8 解码也对应完全相同的私有路径，再校验字节长度。宿主结束后预存文件必须未改、端点必须消失、根必须为空，再清理本次创建的根；不会在创建失败时清理未知既有目录。

报告精确要求全部 23 项各一次，保留无存储状态与 MAP 拒绝；只有原 15 项的历史报告必须失败。报告明确区分上游 ACP 输入字节和内核 UTF-8 解释，记录三种传输、107 字节界限及私有父目录 ACL，不移除原名跨版本、权限、桌面、OpenSSH 和真实存储未验列表。

## 本机验证与证据边界

- `python3 -B apps/pageant/tests/windows/test_runner.py`：4／4 通过，包括拒绝原 15 项双 IPC 历史报告。
- 两个 Python 文件按 Python 3.11 AST 解析通过；`git diff --check -- apps/pageant` 通过。
- `.tmp/build/suite-pageant` 增量构建成功。
- 首次沙箱内 `ctest -L pageant`：10／11 失败，统一在真实本地网络不可用时启动断言，不能当代码通过。相同专用宿主／相同 `.tmp/` 隔离环境在沙箱外 GDB 正常产生 endpoint／READY 并干净退出；诊断保存在 `.tmp/pageant-unix-linux-debug.log`，未修改 ptrace／系统设置。
- 允许真实本地 socket／受控 SSH 后，`ctest --test-dir .tmp/build/suite-pageant --output-on-failure -L pageant`：11／11 通过、零跳过、12.09 秒，包含现有核心互操作、原 CLI、8 项离屏 GUI 和 Windows runner 合同。实际执行记录已固定保存到 `.tmp/pageant-unix-linux-ctest.log`。这证明现有 Linux 行为保持，不证明 Windows 新分支。
- 新增 Windows SDK 编译、真实 AF_UNIX、句柄端点删除、全部 23 项及超时上界：**等待仓库 Windows CI**。Windows 10／11 桌面人工验收由用户执行；WSL 1、OpenSSH 完整认证与参数入口另行补证。

## 来源

- 固定本地 PuTTY 0.85：`vendor/putty/windows/pageant.c` 的可选 Unix socket 入口、`vendor/putty/windows/network.c` 的 AF_UNIX listener 和 `sun_path` 复制、`vendor/putty/pageant.c` 的独立连接所有权。
- Microsoft：[AF_UNIX comes to Windows](https://devblogs.microsoft.com/commandline/af_unix-comes-to-windows/)；[SetFileInformationByHandle](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-setfileinformationbyhandle) 的 DELETE 权限和按句柄删除；[Reparse Tags](https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-fscc/c8e77b37-3909-4fe6-a4ea-2b9d423b1ee4) 的 AF_UNIX 标签。

自检补充：已读取 trellis-check 并按文件所有权检查；新注释和研究均为中文，未改变 ABI、超时、MAP 允许边界或产品前端。原生失败诊断只追加固定源码行号，不输出私密输入。
