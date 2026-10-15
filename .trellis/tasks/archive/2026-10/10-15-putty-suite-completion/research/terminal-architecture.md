# 研究：PuTTY Qt 终端与配置架构

- 查询：将 PuTTY 0.85 的完整终端、连接、认证和配置能力接入 Qt 的契约及任务边界。
- 范围：混合；以仓库固定上游源码为实现基线，Qt 官方 6.8 文档仅核对适配机制。
- 日期：2026-10-15

## 发现

### 已确认范围与研究状态

按主会话转达的用户批准：PuTTY、Pageant 采用 Qt；Plink/PSCP/PSFTP 保留 CLI；集成已有 PuTTYgen。Windows 10 1809+/11 x64、Ubuntu 22.04/24.04 x64，Linux 覆盖 X11/Wayland；GUI 中英双语默认中文；原版 0.85 功能与配置、代理、参数兼容；不新增多标签。父 PRD 已同步用户确认的范围。本文仅为静态研究，未构建、未运行，也没有完成任何功能验收。

### 找到的文件

| 路径 | 职责 |
| --- | --- |
| `vendor/putty/putty.h` | Backend、Seat、TermWin、LogPolicy、Conf、终端与行规程公开接口 |
| `vendor/putty/terminal/terminal.c`、`terminal/terminal.h` | 终端状态机、屏幕/回滚、输入、打印、计时与释放 |
| `vendor/putty/terminal/bidi.c`、`terminal/lineedit.c`、`ldisc.c` | 双向文字、终端本地行编辑、发送与本地回显 |
| `vendor/putty/config.c`、`dialog.h`、`dialog.c` | 通用配置树、控件描述、事件处理与布局辅助 |
| `vendor/putty/conf.h`、`utils/conf.c`、`settings.c` | 配置项元数据、类型化配置对象、存储版本映射与默认值 |
| `vendor/putty/windows/window.c`、`unix/window.c` | 原版平台终端前端，可对照生命周期、菜单、输入与重配置 |
| `vendor/putty/windows/config.c`、`unix/config-unix.c`、`unix/config-gtk.c` | 平台和图形工具包特有配置扩展 |
| `vendor/putty/windows/storage.c`、`unix/storage.c` | 原版会话、主机密钥、CA、随机种子存储 |
| `vendor/putty/callback.c`、`timing.c` | 进程级顶层回调和计时调度 |
| `vendor/putty/windows/select-gui.c`、`handle-wait.c`、`unix/gtk-common.c` | 原版平台事件循环桥接参考 |
| `vendor/putty/test/test_terminal.c`、`test/test_conf.c` | 上游终端及配置测试基础；不能代替实际 Qt 前端验证 |
| `cmake/UpstreamPuTTY.cmake` | 当前只服务 PuTTYgen 的最小密钥库，不能直接当完整客户端核心 |

### 建议的数据流和所有权

建议自有 `apps/putty/` 分成 C 适配、应用控制器、Qt 控件与平台桥。不要用 QTextEdit 自行解释转义序列，直接复用上游 Terminal。上游对象留在 C 适配内，对 C++ 只暴露自有句柄、类型化事件和无私有材料的状态投影；该原则与现有密钥核心规范一致。Qt 控件最终调用适配层，不直接修改 Terminal 内部字段。

输出路径为 Backend → Seat.output → term_data → Terminal → TermWin 绘制/剪贴板/窗口请求；输入路径由 Qt 键盘/IME/鼠标适配进入 term_keyinput[w]、term_mouse、term_do_paste，最终经 Ldisc → Backend。参考 `putty.h:2015`、`:2039`、`:2046`、`:2210`。特殊键先使用上游 `format_arrow_key`、`format_function_key`、`format_small_keypad_key`、`format_numeric_keypad_key`（`putty.h:2060` 起），不能固定发送 xterm 转义而忽略应用键盘模式。

单个会话拥有 Conf、unicode_data、Terminal、Backend、Ldisc、LogContext 与三个 vtable 宿主。初始化参考 `windows/window.c:490`、`:626`、`:628`、`:293`、`:321`、`:326`；Unix 同类参考 `unix/window.c:5345`、`:5713`、`:5232`。TermWin 必须在 term_init 前可用，因为调色板回调可能在初始化内部调用（`putty.h:1690`）。

连接释放与窗口释放分开：`unix/window.c:2426` 先释放 Ldisc、Backend，解除 Terminal 后端；`:2449` 以后才释放终端和其他对象；最后删除前端回调（`:2511`），因为清理后端仍可新增回调。`terminal/terminal.c:2150` 自行过期终端计时并删除终端回调。必须额外撤销 Qt 排队事件、文件描述符通知及认证对话框持有的上下文，防止退出后回调使用旧地址。

### 四个必须实现的边界

| 边界 | 上游契约 | Qt 适配重点 |
| --- | --- | --- |
| TermWin | `putty.h:1606`、`:1609` | 文本/光标/可信标记、字宽、颜色与真彩、行属性、回滚条、铃声、剪贴板、标题、窗口状态和背压 |
| Seat | `putty.h:1085`、`:1088` | 输出/横幅、认证、主机密钥和弱算法确认、特殊命令、远端退出/断开区别、显示信息、防伪信任状态 |
| LogPolicy | `putty.h:2073` | 事件日志、覆盖/追加/取消询问、日志错误、详细输出 |
| 配置桥 | `dialog.h:22`、`:99`、`:559` | 每类控件、事件分派、上下文、批量刷新、动态标签/列表、焦点和对话框完成 |

TermWin 绘制操作必须处于 setup_draw_ctx/free_draw_ctx 配对区间内，包含 char_width（`putty.h:1611`）。建议先绘制到自有缓存再由 paintEvent 展示，或设计受控的绘制上下文；不能在任意核心回调中直接对未激活 QWidget 开始绘制。Qt 逻辑像素与设备像素、cell 坐标、滚动偏移应有独立转换并测试高 DPI。

远端 request_resize 无论接受还是拒绝，最终都必须调用 term_resize_request_completed；term_size 在 resize 后同步实际尺寸（`putty.h:1657`）。Wayland 下不支持的窗口请求要安全完成/拒绝，不能悬挂终端状态。

Seat 输出返回积压长度；TermWin.unthrottle 连接 backend_unthrottle（`putty.h:1090`、`:1703`）。持续输出不能无限排队字符串或占满 GUI 事件循环。notify_remote_exit 与 notify_remote_disconnect 不同：主通道结束后转发可能仍存在（`putty.h:1150` 起），不能直接在任一通知里立即释放后端。

主机密钥和弱算法确认支持异步回调（`putty.h:1215` 起），日志 askappend 也支持异步（`:2081`）。建议非阻塞对话框配合显式挂起状态；回调仅完成一次，关闭、取消、断线时不得默认接受。Seat.set_trust_status 与 TermWin.draw_trust_sigil 用于防认证提示伪装，不能用空实现再声称具有信任可视能力。认证输入复用 `term_get_userpass_input`（`:2044`）可保留核心行编辑，但仍需确保可信来源可辨识、口令不回显。

### 平台事件循环契约

必须采用单进程一个运行时/事件桥，所有 PuTTY 核心调用串行运行于唯一所有者线程；所有者取 GUI 线程还是专用核心线程，需要以下技术验证门确定。`callback.c:17`、`:19` 与 timing.c 使用进程静态队列；把每个连接放到任意独立 QThread 会共享这些状态。PuTTYgen 可执行文件中的工作线程模式不能机械照搬。长时间密码计算的 GUI 响应性需实测，若必须后台执行，应先明确整个运行时的唯一归属及不可跨线程 Terminal 调用的架构。

- 顶层回调：`request_callback_notifications` 注册通知；每次 `run_toplevel_callbacks` 只执行一个回调（`callback.c:115`），必须在 pending 未空时继续安排下一轮。不要写成无界 while 循环饿死 GUI。异步确认期间需要防止嵌套事件循环重新进入核心；原版 Windows 特别通过排队规避 recv 重入（`windows/select-gui.c:98`）。
- 计时：实现 `timer_change_notify`，配单次 Qt 定时器；`run_timers` 返回的 next 是后续绝对 tick（`timing.c:167`、`:202`）。不能把 next 当持续毫秒数直接传入；沿用上游溢出比较方法，并防 run_timers 内重新修改计时器（`unix/gtk-common.c:165`）。
- Unix：实现 uxsel_input_add/remove，按 R/W/X 更新通知，X 先于读（`unix/gtk-common.c:74`）；候选为 QSocketNotifier。删除或重配 fd 时撤销旧通知，明确关闭/HUP 与 fd 复用行为。
- Windows：优先保留 upstream do_select → WSAAsyncSelect → HWND 消息 → winselgui_response → 顶层回调链（`windows/select-gui.c:21`、`:31`、`:71`、`:89`），由 Qt 原生消息桥承接隐藏/终端 HWND；已关闭 socket 的 moribund 队列不可省略。另将 `get_handle_wait_list`/`handle_wait_activate`（`windows/handle-wait.c:112`、`:128`）接入 QWinEventNotifier 或专用有界适配，串口/管道/本地代理不只依赖套接字。Qt 直接替代 do_select 是另一条风险更高的实现路线，须证明 FD_CONNECT/WRITE/OOB/CLOSE 等语义一致后才可采用。

### 线程模型补充：实施初期必须通过的技术验证门

新增源码证据：`ssh/userauth2-client.c:1228` 在认证协程里直接同步调用 ppk_load_f；`sshpubk.c:638` 执行 Argon2，私钥格式可携带较高 KDF 工作量；`ssh/userauth2-client.c:1393` 与 `:1416` 同步调用 GSSAPI acquire_cred/init_sec_context。`sshrand.c:35` 的 global_prng、`callback.c:17` 的队列和 timing.c 全局状态意味着不能把这些调用随意交给另一个线程并继续运行原核心。它们确实可能耗时，但实际耗时/网络依赖和可中断性尚未测量。

TermWin 的同步性是硬契约：`terminal/terminal.c:1560` 在 setup_draw_ctx 成功后立即 do_paint，再 free_draw_ctx；`:6216` 在绘制比较中同步读取 win_char_width 决定 ATTR_NARROW。因此“核心线程遇到字宽就阻塞调用 GUI”会与 GUI 等待核心/关闭形成反向锁；“全量预缓存若干常用字符”也不能涵盖任意远端 Unicode、回退字体和新 DPI。

**推荐优先验证：专用核心运行时线程 + 同线程 CPU 字体度量/绘制 + GUI 异步呈现。** Qt 官方允许在工作线程对 QImage 绘制、不允许对 QWidget 绘制，单一绘图设备同一时刻只由一个线程使用；QFontMetricsF 标注可重入，但必须用线程私有实例。相关依据：[Qt 线程模块](https://doc.qt.io/qt-6.8/threads-modules.html)、[QFontMetricsF](https://doc.qt.io/qt-6.8/qfontmetricsf.html)、[QImage](https://doc.qt.io/qt-6.8/qimage.html)。这些只证明可用机制，不证明当前平台字体测量与原版网格语义天然一致。

候选实现约束：

1. GUI 初始化应用和字体系统，向核心提交不可变渲染配置（字体族、样式、字号/像素大小、DPI/DPR、配色、代次）。核心只用自己的 QFont/QFontMetricsF/QImage；字宽缓存按字体实例/回退规则/代码点/DPI/渲染代次区分，cache miss 在核心线程同步使用同一离屏绘制设备计算，绝不回调 GUI 等待。QFontMetricsF 基于明确离屏设备，避免隐含屏幕指标不同；热切换字体/DPI 时先构造新代次再使 terminal invalidate 重画。
2. TermWin.draw_text/draw_cursor 深复制上游短命 wchar_t 缓冲和样式，形成带 session id、session generation、render generation、frame sequence 的不可变命令批次；free_draw_ctx 完成一批。最保守路线在核心线程直接绘到私有 QImage，交付不可变图像/脏区；GUI paintEvent 只呈现，不重测字宽。若改为 GUI 重放命令，必须证明与核心度量使用相同字体回退、裁剪/缩窄、DPI 和文本布局，否则优先保留 QImage 路线。
3. 发布图像后核心必须保持该实例只读，后续绘制使用分离/新缓冲；不能让 GUI 正在读图像时核心改同一像素。帧队列有界并可合并旧帧，但最后状态必须完整；丢增量帧时需同步全帧或重建，不能只丢命令造成画面残留。
4. GUI 到核心的输入、尺寸、焦点、配置、认证结果均排队，核心到 GUI 的剪贴板/窗口请求也排队。Clipboard/窗口尺寸/认证均使用请求 id 和 generation；核心回调不得访问 QWidget。Seat 的显示字符串/像素尺寸/光标等同步查询从核心拥有的元数据镜像读取。resize 完成由 GUI 回执触发 term_size/term_resize_request_completed。认证允许异步的接口按上游 callback 挂起；终端提示输入也在核心排队处理。
5. dlg_* 适配不能在核心线程同步读取 QWidget。采用核心所属的控制值/上下文镜像：GUI 发控件值变更事件，核心先更新镜像再运行上游 handler；dlg_* getter/setter 在镜像上同步完成并生成 GUI 更新批次。配置用独立 Conf 草稿，严格保留刷新抑制和 item id。另一选择是在 GUI 保有完全独立、不与运行时并发的配置进程/纯模型，但不能让同一 Conf/全局状态跨线程共享。
6. Unix notifier、计时器归属核心 QThread；Windows 消息 HWND 与 handle notifier 同样归属核心线程的消息循环，或者由 GUI 只转发原始事件而不直接 select_result。原版 HWND API、SSPI、agent client、打印以及 Win32 线程亲和性必须在 Windows 原生验证，不能只证明 Linux 工作线程 QImage 成功。
7. GUI 关闭是异步状态机，不能 wait()/join() 等待正在跑 KDF/GSS 的核心，也不能强杀 QThread 释放正在使用的 PRNG。取消先记录原子/安全待处理意图，在上游同步调用返回的第一个安全点停止并清理。若要求 GSS 阻塞时有硬性退出上界，则需可隔离的子进程运行时或有证明的原生超时/取消；单线程排队取消本身不能给出该保证。

**技术验证门建议设在“共享基础”的事件桥契约与“Qt 终端”的第一步之间，未通过前不铺开完整控件和功能。** 用最小 TermWin、真实 SSH 加密 PPK/GSSAPI 路径、带 CJK/组合字符/字体回退/高 DPI 变化的帧完成纵切。测量 GUI 心跳延迟、核心调用线程一致性、长 KDF 时窗口可重绘/取消可见、持续输出时输入可响应、关闭/重配置与过期帧回调不死锁；两平台都跑。耗时上限要在设计/实施文档写成明确指标再验收，当前不虚构已批准阈值。

若工作线程字体或 Windows 平台核心 API 验证失败，备选是“GUI 线程运行核心 + 针对认证/耗时计算的可审计异步上游补丁”，必须挂起整个对应状态机、把纯计算输入复制到隔离计算任务后再回到唯一所有者线程提交，不能让 ppk_load/GSS 与运行时共享状态并发。该路线修改面明显更大；如任意平台无法满足，应回到设计评审而非删掉 GSSAPI/加密私钥或长期阻塞 GUI。

### 原版配置、存储和双语映射

Conf 是配置唯一业务模型，`conf.h` 列举项和存储元数据；`ConfKeyInfo` 包含类型、默认值、保存关键字和枚举映射（`putty.h:1843`），`settings.c:543` 已自动处理简单项，复杂映射另行处理。以 do_defaults/load_settings/save_settings/get_sesslist 构成原版配置入口，不另写 JSON/INI 简化版。不要假定每个 Conf 字符串都是 UTF-8：`putty.h:1798` 的 STR、`:1820` 的 STR_AMBI、Filename 与 FontSpec 各有转换和所有权契约。Windows/Unix FontSpec 不同（`windows/platform.h:73`、`unix/platform.h:64`），Qt 字体不能直接存为 QFont.toString 再期望原版能读取。

建议以 setup_config_box（`config.c:1783`）生成控制描述，再实现 Qt dlg_* 桥：文本、编辑/组合框、单选、复选、按钮、列表/下拉/排序列表、多列、文件/字体选择、延迟 tab 顺序。`dialog.h:99` 中 REFRESH/ACTION/VALCHANGE/SELCHANGE/CALLBACK 都有真实使用。`dlg_update_start/done` 必须抑制程序设置值产生的伪用户事件，`config.c:1727` 已说明否则刷新代理列表会覆盖旧值。列表必须保留 item id，不能以翻译后显示文本作为值。颜色选择在处理器返回后运行并发 CALLBACK（`dialog.h:633`），焦点、私有上下文和 dlg_refresh(NULL) 全刷新同样不能缺。

通用 config.c 不足以覆盖平台选项。`windows/config.c:33` 扩展 AltGr、窗口行为、铃声、RTF、字体质量、窗口缩放、X authority 等；`unix/config-unix.c:12` 扩展本地代理与打印；`unix/config-gtk.c:20` 含左回滚条、普通/粗体/CJK 字体和 UTF-8 locale 等。Qt 不直接链接依赖 HWND/GTK 的 UI 处理函数；提取同等配置描述到自有平台扩展并逐项对照。GTK 原始字体名与 Qt 字体格式、Windows OEM/逻辑调色板等必须设置专项兼容验证。

会话中设置以 conf_copy 为草稿，调用 term_pre_reconfig，确认后依次更新日志、ldisc、terminal、backend；取消释放草稿而不修改运行连接（`unix/window.c:4804`、`:4829`、`:4833`、`:4839`、`:4843`、`:4847`）。`config.c:1928` 已禁止会话中加载另一完整配置。界面语言切换只换标签/菜单/错误提示，不重建 Conf、控件上下文、未提交输入或连接。初始控件文本和处理器动态产生的列表/错误均需翻译覆盖；不得把核心协议标识、会话名、主机、指纹、脚本、服务器文字拿去翻译。

Windows 按 `windows/platform.h:190` 的 HKCU Software\SimonTatham\PuTTY 存储；Sessions、SshHostKeys、SshHostCAs 的实现分别见 `windows/storage.c:23`、`:284`、`:24`。Unix 优先 PUTTYDIR，再已存在 XDG 路径，然后旧 ~/.putty 等，是否新建 XDG 默认路径还取决于 XDG_DEFAULT（`unix/storage.c:81`）。子路径另支持 PUTTYSESSIONS、PUTTYSSHHOSTKEYS、PUTTYRANDOMSEED、PUTTYSSHHOSTCAS（`:164` 至 `:205`）。语言/窗口偏好可用自有 QSettings，但不能用其替代这些原版配置。

### 必要子任务边界与依赖建议

| 建议边界 | 交付及独立验证 | 显式依赖 |
| --- | --- | --- |
| 公共完整客户端核心与平台事件桥 | 核心目标、真计时/回调、网络/句柄、存储、无 GTK 链接，真实连接/关闭回归 | 先定义与现有 pg-putty 的目标、符号和配置头隔离 |
| 终端控件与输入渲染 | TermWin、键盘/IME/鼠标/剪贴板、尺寸、绘制/字宽矩阵 | 核心接口可先用假后端，最终依赖事件桥 |
| 配置与会话兼容 | dlg_* 全控件映射、原版存储/字体、双语、运行中重配置、CA 编辑入口 | 公共核心的 Conf/平台存储可调用；不依赖 SSH GUI 已完成 |
| PuTTY 会话与完整协议集成 | Seat、LogPolicy、认证/安全确认、菜单、完整后端、代理转发、生命周期 | 事件桥、终端控件、配置兼容三项通过 |
| 套件集成与平台验收 | 六组件协同、Pageant/共享连接/CLI 会话、发布包目标平台矩阵 | PuTTY、Pageant、CLI 和 PuTTYgen 各自交付；不能由只编译替代 |

若实现分批落地，可先形成内部可运行纵切，再逐项补全；每阶段不得将 SSH 最小登录示范宣称为“PuTTY 0.85 完整功能”。详细验收矩阵见同目录 `terminal-parity.md`。

### 外部参考

- 项目锁定 Qt 6.8.3：`tools/qt/qt-sources.json:2`。本次 Context7 查询使用 Qt 6.8 官方文档集合；网页当前展示该支线的维护版，实施应以本地 6.8.3 头文件再核对。
- [QSocketNotifier](https://doc.qt.io/qt-6.8/qsocketnotifier.html)：文件描述符通知、同 fd 同类型只装一个、读写分别注册；Windows 会将 socket 设成非阻塞。
- [QWinEventNotifier](https://doc.qt.io/qt-6.8/qwineventnotifier.html)：Windows 事件句柄通知，需额外适配动态 handle wait 集合。
- [QAbstractNativeEventFilter](https://doc.qt.io/qt-6.8/qabstractnativeeventfilter.html)：Windows MSG 桥接机制；不能据此宣称上游 socket 生命周期已适配。
- [Wayland 与 Qt](https://doc.qt.io/qt-6.8/wayland-and-qt.html)：跨平台后端背景，窗口管理、剪贴板和 X11 集成须在真实后端验证。

### 相关规范

读取 `.trellis/workflow.md`：本阶段仅研究，复杂任务规划稿经评审后才能开始。读取 `.trellis/spec/frontend/index.md`、`directory-structure.md`、`state-management.md`；后者目前是 PuTTYgen 专用，不直接把密钥工作线程模式当套件核心约束。读取 `.trellis/spec/backend/index.md`、`directory-structure.md`、`error-handling.md`、`private-file-permissions.md`、`build-tools.md`；已有上游对象不跨 C++ 接口、敏感内容清除、目标平台证据分离、`.tmp/` 隔离可沿用。其它 frontend 质量/组件与 backend 数据库文档大部分为模板，不具备新终端可执行契约。跨层 guide 也含 Trellis 自身示例，应仅采用跨层数据流检查方法。

## 注意事项 / 未找到

1. 尚无自有 Qt 终端、完整客户端 C API、Qt dlg_* 或事件桥，因此本文中的模块拆分和线程选型均为建议，必须通过原生构建、真实认证/转发/串口与重入销毁测试验证。
2. 现有 `cmake/UpstreamPuTTY.cmake:9`、`:10` 强制 NO_GSSAPI/NOT_X_WINDOWS，`:29` 使用 no-timing stub。不能直接沿用这些选项后宣称具备完整 GSSAPI、X11 或计时；应保留 PuTTYgen 最小库隔离，新核心独立配置。
3. 上游 `windows/storage.c:187` 明确保留 cpath/CP_ACP 路径以兼容旧配置，原版对系统代码页外字符的会话路径保存可能有损。这与 Qt 文件对话框能打开该路径不是同一能力；不能在兼容工作中悄然改变存储格式。
4. Unix `close_settings_w`（`unix/storage.c:286`）不报告 fclose 错误，而 `save_settings` 返回 NULL 表示无先前错误（`settings.c:519`）。若要求可靠落盘，需增加自有可报告适配或受控补丁并保留格式；此问题须作为实施风险，不能仅检查 save_settings 的 NULL。
5. 原版 settings.c 以已知 Conf 项保存，不能从“原版兼容”推导“保留任意未来版本未知字段”。目标是固定 0.85 的适用配置语义，不无证据承诺未来格式。
6. Wayland 任意移动窗口/置顶、全局窗口 ID、主选择剪贴板和远程控制请求受实际 compositor 能力影响。须按原版对应平台与实际协议能力记录限制，不能静默 no-op 所有钩子，也不能凭 XCB 结果宣称 Wayland 通过。
