# 研究：Pageant 架构、平台接口与 Qt 适配

- 研究问题：如何在固定 PuTTY 0.85 行为、配置与代理接口的前提下，为 Pageant 增加 Windows／Ubuntu Qt 图形界面，保留生命周期与命令行兼容。
- 研究范围：内部与外部混合（本地固定上游源码为主要证据，Qt 官方 6.8 文档用于 API 能力核对）
- 日期：2026-10-15

## 研究发现

### 1. 研究边界与结论

任务路径由主会话显式提供：`.trellis/tasks/10-15-putty-suite-completion`。本轮只读研究，不启动任务、不构建、不运行代理、不修改产品代码，不加载 implement/check JSONL。用户已确认 Pageant 使用 Qt；Windows 10 1809+/11 x64、Ubuntu 22.04/24.04 x64（X11／Wayland）；原版 0.85 完整功能与原平台代理接口兼容。下文分别记录原版事实与父设计已确定的适配契约。

核心结论：保留 `vendor/putty/pageant.c` 的密钥、协议和异步操作实现，替换平台 GUI 外壳并建立真实事件循环桥。Windows 保留命名管道与 WM_COPYDATA 两条入口；Unix 保留 SSH_AUTH_SOCK、多实例和显式 CLI 生命周期。Qt GUI 不应成为这些接口的新协议。

父设计决定与所查证据无直接冲突；需明确标记的适配增量是 Linux 显式 GUI 模式、纯 Wayland 口令提示和无托盘窗口选择。它们不改变原 CLI 的无参数错误、`-X` 的 X11 生命周期或 Windows 同用户发现协议。

### 2. 相关文件

| 路径 | 职责 |
| --- | --- |
| `vendor/来源.md:3`、`vendor/putty/version.h:5` | 固定官方 PuTTY 0.85 来源、校验与二进制版本 |
| `vendor/putty/pageant.c` | 公私钥存储、签名、异步请求、标准代理协议与 PuTTY 扩展 |
| `vendor/putty/pageant.h` | 前端回调、客户端、监听器、口令响应及管理 API |
| `vendor/putty/windows/pageant.c` | 原 Win32 GUI、托盘、单实例、旧 IPC、管道、可选 AF_UNIX |
| `vendor/putty/windows/agent-client.c` | PuTTY 套件的 Windows 代理发现和查询，管道优先、旧 IPC 回退 |
| `vendor/putty/windows/utils/agent_named_pipe_name.c` | 服务端与客户端共用的管道命名 |
| `vendor/putty/windows/utils/agent_mutex_name.c` | Windows 启动竞态互斥名称 |
| `vendor/putty/windows/utils/cryptoapi.c` | 管道名称后缀计算所需的 Windows 加密及摘要逻辑 |
| `vendor/putty/windows/named-pipe-server.c`、`windows/utils/security.c` | 管道监听、异步句柄、私有 ACL |
| `vendor/putty/unix/pageant.c` | Linux CLI 生命周期、管理动作、shell 输出、askpass 调度 |
| `vendor/putty/unix/agent-client.c`、`unix/agent-socket.c` | SSH_AUTH_SOCK 客户端、私有 socket 目录和清理 |
| `vendor/putty/unix/utils/make_dir_and_check_ours.c` | Unix 目录 UID／权限校验 |
| `vendor/putty/ssh/agentf.c` | SSH 转发到本地代理，沿用 agent_query |
| `vendor/putty/doc/pageant.but` | Windows Pageant 用户行为与 GUI、OpenSSH、WSL 1 文档 |
| `vendor/putty/doc/man-pageant.but` | Unix Pageant 命令、生命周期及管理动作参考 |
| `vendor/putty/windows/CMakeLists.txt:80`、`unix/CMakeLists.txt:243` | 上游平台目标及其库依赖，Unix askpass 可选 GTK |

### 3. 平台入口和发现契约

#### Windows

1. 管道名必须复用 `agent_named_pipe_name()`，其格式为 `\\.\pipe\pageant.<username>.<suffix>`，不能硬编码为 Windows OpenSSH 默认代理管道；证据 `windows/utils/agent_named_pipe_name.c:9`。后缀由 `capi_obfuscate_string("Pageant")` 生成，涉及 CryptProtectMemory 的跨进程标志和 SHA256，见 `windows/utils/cryptoapi.c:29`、`:46`、`:64`、`:73`。
2. 旧客户端以 `FindWindow("Pageant", "Pageant")` 找窗口，服务端窗口类和标题必须保持，见 `windows/agent-client.c:15`、`windows/pageant.c:35`。Qt 顶层窗口的默认类名不能代替这一专用隐藏窗口。
3. 当前套件 `agent_exists()` 同时探测管道和旧窗口，查询先管道、失败后旧 IPC，见 `windows/agent-client.c:146`、`:282`。只测新 Plink 成功不能证明旧 IPC 保留。
4. 启动互斥 `Local\pageant-mutex.<username>`，见 `windows/utils/agent_mutex_name.c:8`。互斥覆盖代理发现、服务初始化和全部监听器建立，之后才释放；见 `windows/pageant.c:1631`、`:1653`、`:1798`。不能用 Qt 自造锁替代，否则与原版并发启动仍会抢服务。
5. 原版二次启动添加密钥、运行 `-c` 后退出；不构造第二份本地密钥库。空动作二次启动显示已经运行提示，见 `windows/pageant.c:1808`、`:1818`、`:1833`。原版已运行分支未专门远程激活 `--keylist`，该细节需原生差分验证，不能凭预期改写。
6. 命名管道使用 overlapped 字节流、首实例排他和可用时拒绝远程客户端，见 `windows/named-pipe-server.c:75`；私有描述符来源于 `:211`，UID 对应 SID／owner／ACL 构造见 `windows/utils/security.c:177`、`:180`、`:218`、`:224`。适配层不能降为开放管道。

#### WM_COPYDATA 与事件循环

`windows/pageant.c:1072` 打开共享映射，`:1102` 校验用户 SID 或历史兼容 SID，`:1160` 检查实际映射区大小，`:1196` 拒绝越界长度。专用 IPC 线程处理 SendMessage，主循环负责核心异步请求；通过 `ev_msg_ready` 和 `ev_reply_ready` 交接，见 `:1205`、`:1385`、`:1414`、`:1781`。

建议保留此异步线程边界，而不是在 GUI 线程同步等待签名或在 Qt 模态口令框中阻塞原生消息。必须让回调、Windows HANDLE 等待、网络事件与口令响应继续被泵送；退出时先结束请求并唤醒等待者。新桥需校验 COPYDATA 长度非零、映射名结尾和消息长度；不要仅复制旧 WndProc 后假设所有输入天然合法。`pageant_handle_msg()` 会排队到顶层回调（`pageant.c:1635`），因此“函数返回”不表示已得到响应。

#### Windows OpenSSH 与 AF_UNIX

- `--openssh-config <file>` 在主服务建立命名管道后写 `IdentityAgent "//./pipe/..."`，正常退出将文件清空；证据 `windows/pageant.c:1714`、`:1724`、`:1937`。建议检查写出／关闭失败，禁止空成功提示；不得擅自编辑用户主配置。
- 文档明确适用 Windows 原生 OpenSSH，不保证 Git 附带的 MSYS／MinGW OpenSSH，见 `doc/pageant.but:186`、`:214`、`:220`。Git 使用原生 ssh 或 Plink 是文档已有路径（`:227`）。
- `--unix <path>` 提供 WinSock AF_UNIX 监听，见 `windows/pageant.c:1744`。上游文档面向 WSL 1，要求父目录限制访问，并明确 WSL 2 不兼容此机制；见 `doc/pageant.but:233`、`:261`、`:267`。WSL 2 不得记成实现缺陷或新增保证。

#### Ubuntu

- 客户端通过环境变量 SSH_AUTH_SOCK 定位 socket，见 `unix/agent-client.c:18`、`:128`；环境变量存在只是可尝试的依据，不证明 socket 可达（`:151`、`:165`）。
- 每个服务使用不同 PID socket，`unix/agent-socket.c:24`、`:36`；这说明原版允许同用户多实例，不应套用 Windows 单实例设计。
- socket 所在目录创建为 0700，并验证所有者和实际权限，见 `unix/utils/make_dir_and_check_ours.c:44`、`:50`；清理子进程跟随父进程管道 EOF 删除 socket，见 `unix/agent-socket.c:47`。父目录本身需安全，不能仅看最终目录是 0700，源文件 `:14` 已说明威胁边界。
- 启动 GUI 不能回溯修改调用它的 shell 环境。父设计确定 GUI 展示可复制的 SSH_AUTH_SOCK 启动信息，不自动修改用户 shell 或 SSH 配置；Windows 用户显式指定 `--openssh-config <file>` 时仅写该目标文件，该参数不是 Linux 原版选项。GUI 启动的套件子进程可显式继承该代理环境，已有独立终端需由用户设置环境。
- 上游 `--symlink` 只替换已有符号链接，不覆盖普通文件，失败报错，见 `unix/pageant.c:1151`、`:1167`。原版环境片段见 `:254`、`:275`，新 GUI 复制片段应处理空格／引号，不从原版“路径简单”假设推广到中文项目路径。

### 4. Unix 生命周期与 Qt 入口的最小兼容方案

原版 `pageant` 无参数和裸密钥无生命周期都会非零失败，见 `unix/pageant.c:1521`、`:1526`。因此无参数直接打开 GUI 是行为变化，不能称为完全保留命令行。

父设计确定同一 Linux 可执行文件保留 `pageant` 原 CLI，无参数继续按原版非零报错；桌面入口使用 `pageant --gui`。新增显式 GUI 模式先分流，原版参数、无参数错误、退出状态和输出仍走原 CLI 分支；不新增独立 `pageant-gui` 程序或交付组件。此方案避开无参数语义冲突，符合原版 `unix/pageant.c:1526` 的证据。

原 CLI 参数解析须在初始化 QApplication 之前完成，纯 CLI 模式不依赖 DISPLAY、WAYLAND_DISPLAY、平台插件或托盘。原版有 fork 和 setsid，见 `unix/pageant.c:292`；不要在已建立 Qt GUI 或多线程状态后直接复用该 fork 路径。可将原 CLI 运行器独立为 C 入口供模式分流调用；GUI 用独立的事件桥／前台服务生命周期，避免改变原 CLI 生命周期。

| 生命周期 | 原行为与证据 | Qt 适配边界 |
| --- | --- | --- |
| `-X` | 监视 X 连接，断开则退出；`unix/pageant.c:240`、`:1180`；文档 `man-pageant.but:112` | 保持 X11 含义；纯 Wayland 无 DISPLAY 时维持明确失败，不静默改为 Wayland 会话寿命 |
| `-T` | 保留控制终端、轮询终端存在；`unix/pageant.c:323`、`:1016` | 不因打开 Qt 窗口延长终端会话寿命 |
| `--exec` | 注入 socket／PID 后执行全部剩余参数，子进程结束则代理退出；`:1042`、`:1269` | 保留参数边界、子进程继承和寿命；不要把 Windows `-c` 语义映射过来 |
| `--permanent` | fork 后常驻并输出环境；`:1223` | 不与 GUI 关闭行为混用 |
| `--debug` | 前台、不 fork、stdout 输出环境与日志、stdin 提供口令；`:1230` | CLI 输出与 TTY 状态保持；不强制加载 GUI |
| `--foreground` | 前台输出环境后关闭 stdout；`:1225` | 父进程必须能读到 EOF；不能混入 Qt／翻译日志 |

原 `--exec` 在等待子进程结束时只设置结束标志，并非直接返回子进程的退出码，见 `unix/pageant.c:1037`、`:1539`。验收应与固定原版比较，不假定与 ssh-agent 完全一致。

### 5. 核心对象、口令回调与线程建议

- `pageant_init()` 初始化进程全局树并设本地模式（`pageant.c:1642`），没有公开“多实例句柄”与完整 cleanup API；不能每个 Qt 窗口调用一次，也不能把所有窗口各自建一份核心。
- 所有密钥、协议请求、顶层回调、客户端注册／注销应在一个核心事件线程串行处理。Qt 主线程仅显示公开快照和口令框；新增 C 适配层隐藏上游结构，目录建议 `apps/pageant/`，与现有组件目录规范一致。
- `PageantClientVtable` 负责日志、异步响应与请求口令（`pageant.h:31`）；使用 `pageant_register_client`／`unregister_client` 管理断开时未完成请求（`:76`）；口令结果走 success／refused（`:103`）。`ask_passphrase` 只表示已创建提示，不能在其返回前同步回调成功；Windows 原版在提交口令之前先销毁旧对话框并清空窗口指针，避免错误口令同步触发新提示时重入，见 `windows/pageant.c:188`。
- 全局口令提示锁与请求队列由核心管理（`pageant.c:200`、`:676`、`:701`）。Qt 一次只显示一个延迟解密提示；错误口令由核心发起重试（`:829`），取消失败等待请求并放行后续请求（`:866`）。GUI 不能另外发起竞争的解密流程。
- 载入管理可复用 `pageant_add_keyfile`、枚举／删除／重加密 API，公开结果码见 `pageant.h:209`；多文件批量载入完成、失败或取消收尾均清除缓存口令（`pageant.h:203`、`pageant.c:2094`）。Qt 隐式共享字节不等于安全擦除，需明确敏感缓冲的唯一所有权和清除时机。
- `pageant_listener_free()` 只关闭监听 socket（`pageant.c:1973`），不能据此认定所有已接受连接与密钥已销毁。显式退出流程应先拒绝新请求，取消口令提示、注销／关闭存量连接、清除缓存口令及全部密钥，最后终止桥与 GUI；避免强杀仍在等待的 IPC 线程。

### 6. Qt 托盘、无托盘及退出策略

Qt 6.8 的 QSystemTrayIcon 支持 Windows、Linux 的 StatusNotifierItem 和 X11 XEmbed；存在性通过 isSystemTrayAvailable 检查，GNOME 的激活事件存在限制。Qt 并未给出统一的“托盘消失”信号。官方文档：[QSystemTrayIcon](https://doc.qt.io/qt-6.8/qsystemtrayicon.html)。本仓库锁定 Qt 6.8.3，网页目前展示 6.8.9 补丁文档，不能把未检查的新补丁行为当成 6.8.3 实测结果。

父设计确定的产品状态契约：

| 状态／动作 | 行为 |
| --- | --- |
| 托盘可用，关闭密钥列表 | 隐藏窗口，代理继续；匹配 Windows 原版 `doc/pageant.but:46` |
| 无托盘启动 GUI | 显示密钥列表与明确的“退出”动作；不能启动后只剩不可操作的隐藏进程 |
| 无托盘关闭最后窗口 | 弹出“保持运行／退出”产品选择；选择保持运行则保留或恢复可见管理窗口及退出动作，选择退出则执行完整收尾；不得无控制入口后台驻留 |
| 运行中托盘丢失 | 重新检测可用性；若窗口隐藏则恢复可访问窗口，不强制聚焦；避免用户无处管理密钥 |
| 托盘恢复 | 恢复图标，保留密钥与窗口状态；不因托盘恢复重新初始化核心 |
| 显式退出 | GUI 和托盘菜单共用收尾；关闭监听／请求／密钥后退出；不是“Remove All” |
| 原 CLI 生命周期运行 | 继续由原寿命规则决定退出，不能因 GUI 可见性或托盘变化退出 |

Qt 的 quitOnLastWindowClosed 默认值为 true，常驻模式需显式管理，并由显式退出路径结束程序；参考 [QGuiApplication 属性](https://doc.qt.io/qt-6.8/qguiapplication.html#quitOnLastWindowClosed-prop)。不应只设 false 后不提供退出入口。系统通知、双击和抢焦点不能成为唯一入口；前台窗口和菜单必须可用。检测托盘变化的方式需要在 Qt 6.8.3 与两 Ubuntu 桌面验证，研究不声称已实现可靠轮询或平台通知。

### 7. 外部参考

- 官方固定基线来源与 SHA256 已记录在 `vendor/来源.md:3`，源压缩包 https://the.earth.li/~sgtatham/putty/0.85/putty-0.85.tar.gz 。本研究分析仓库固定源码，不用最新版在线 PuTTY 文档替代该行为基线。
- Qt 官方 QSystemTrayIcon 6.8：https://doc.qt.io/qt-6.8/qsystemtrayicon.html ，核对日期 2026-10-15；Context7 库 `/websites/doc_qt_io_qt-6_8` 已核对同一 API。
- Qt 官方应用退出属性：https://doc.qt.io/qt-6.8/qguiapplication.html#quitOnLastWindowClosed-prop ，核对日期 2026-10-15。

### 8. 相关规范

- `.trellis/workflow.md`：复杂任务先完成规划、设计和实施计划，评审后才能 start；研究结果持久化。
- `.trellis/spec/backend/directory-structure.md`：组件 `apps/<组件>/`，固定上游和共享构建复用，不提前泛化业务接口。
- `.trellis/spec/backend/error-handling.md`：现有 PuTTYgen C 封装的 UTF-8、错误码和敏感数据所有权模式可借鉴；pg_* 接口不直接扩作代理核心。
- `.trellis/spec/backend/private-file-permissions.md`：私有输出失败关闭、不以测试修权限掩盖产品失败；Pageant 配置输出／socket／IPC 需单独确立对应契约。
- `.trellis/spec/frontend/state-management.md`：GUI／核心线程边界、默认中文和语言切换保留状态、关闭等待；原文适用于 PuTTYgen，不能不加分析地把代理后台任务视作生成任务。
- `.trellis/spec/frontend/directory-structure.md`：平台插件部署、X11／Wayland／真实桌面证据区分与测试配置隔离。

## 限制与未验证项

- 尚无 `apps/pageant/` 自有实现，也未验证 Qt 事件桥、Windows HANDLE 桥和无托盘实际行为；这些是子任务实施与原生验收责任。
- `unix/pageant.c:236` 硬编码 `/tmp/pageant`，不能仅设 TMPDIR 就使原版代理测试进入项目 `.tmp/`。测试入口需可注入绝对目录前缀，或经过记录的极小测试适配；本轮没有运行该默认代码，也没有访问系统临时目录。
- 工程要求开发临时产物位于项目 `.tmp/`；生产安装后的运行目录策略应单独设计，不能要求终端用户必须有源码仓库。父设计已指定 GUI 使用经权限验证的 XDG_RUNTIME_DIR、原 CLI 生产模式保留原路径，开发验证在所有模式显式覆盖到项目 .tmp/。
- 父设计已确定 Linux `pageant --gui`、纯 Wayland 口令适配及“有托盘关闭隐藏／无托盘保持运行或退出”的策略，原先的备选入口与退出／取消措辞已被替换。这些是新增 Qt 适配行为；原 CLI 显式生命周期保持不变，不属于原版 CLI 已有 GUI 行为。
- Windows `--unix` 中文路径在上游有明确 UNICODE 注记（`windows/pageant.c:1607`），需原生 WinSock 实测；不能因为 Qt 接受 QString 就假定传输层已支持。
- Unix 现有 GUI 提示仅检测 DISPLAY（`unix/pageant.c:1461`、`:489`），延迟提示也据 display 选择可用性（`:1140`）；原 GTK 调用在 `:467`。纯 Wayland 下替换 GTK askpass 时必须更新 GUI 可用性探测并保持 TTY 优先／显式 --tty-prompt 行为；这与 -X 的 X11 专属生命周期是两件事，不能一并替换。
- 互操作保持 Qt 版本与原版同用户身份和相同发现规则，不以更名管道、互斥或改用另一用户来规避兼容性。测试采用专用系统身份／隔离环境，使该身份内的原版与新版本互操作，不连接开发者日常代理；Windows 原命名不可按测试场景任意重命名，否则不能证明原客户端兼容。
- 尚未运行任何签名、连接、转发、Windows／Ubuntu 测试。本地源码证明当前实现路径，不代替产品已通过的证据。
