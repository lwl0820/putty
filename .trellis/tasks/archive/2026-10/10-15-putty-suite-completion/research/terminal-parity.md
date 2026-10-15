# 研究：PuTTY 完整功能与配置验收矩阵

- 查询：为 Qt PuTTY 0.85 保真迁移定义可追溯、可验证的功能矩阵及证据要求。
- 范围：内部源码与项目规范；矩阵是实施验收建议，当前没有运行结果。
- 日期：2026-10-15

## 发现

### 使用方式与证据维度

每行实施时应建立记录：功能编号、0.85 来源、适用平台、配置控件/Conf 映射、核心能力、可重放用例、预期结果、构建和运行环境、日志或截图路径、实际结果/限制。以“源码存在”“配置可见”“编译成功”“离屏 Qt Test”“真实网络互操作”“真实目标桌面操作”分别记账。此文是候选分组，完整清单需实施时枚举 controlbox 与全部适用 Conf 项/平台扩展建立逐项追踪，不以这些分组代替逐控件审计。

基准平台格：Windows 10 1809+ x64、Windows 11 x64；Ubuntu 22.04 x64 的 X11/Wayland、Ubuntu 24.04 x64 的 X11/Wayland。自动核心/逻辑测试可共用，部署包运行和桌面输入/剪贴板/IME需逐平台留证。不得把虚拟离屏测试当作 X11、Wayland 或 Windows 桌面通过。测试配置和服务夹具置于项目 `.tmp/`；Windows 必须隔离实际原版注册表存储，光改 QSettings/XDG 不够。

### 功能与验收表

| 编号 | 能力与上游来源 | 必须验证的正常/错误路径 |
| --- | --- | --- |
| PT-01 | 完整后端，`be_list.c:56`、`:62`、`:71`、`:79`、`:82`、`:83`、`:84`、`:92` | SSH、Telnet、Rlogin、SUPDUP、Raw、Serial 与 SSH connection 模式按上游适用接口保留；每类至少真实或受控对端握手、收发、断开、非法配置；未交付 PuTTYtel 不等于从 PuTTY 移除 Telnet |
| PT-02 | 配置默认/加载/保存/删除，`config.c:826`、`:1783`、`:1902`；`settings.c:519`、`:693`、`:1054`、`:1081` | 原版创建→Qt 使用/修改→原版重读；反向同样执行；Default Settings、特殊字符会话名、主机/端口/协议切换、取消、读写失败；不因未编辑某页而丢字段 |
| PT-03 | 配置控制树覆盖，`dialog.h:22`、`:99`、`:559` | 所有控件类型、排序算法列表、id 保持、组合框、文件/字体选择、动态启用/标签、批量刷新、焦点/键盘导航；无 handler 或未实现 dlg_* 时明确失败，不能静默忽略 |
| PT-04 | 配置枚举/映射，`conf.h`、`putty.h:1843`、`settings.c:543` | 已知存储项逐项 round-trip；数字与存储枚举区分；端口转发/环境/TTY 等映射表、密码/命令字符串不翻译；Windows/Unix 特有项单列 |
| PT-05 | Windows 原版存储，`windows/platform.h:190`、`windows/storage.c:23`、`:284`、`:24`、`:187` | Sessions/SshHostKeys/SshHostCAs，百分号转义、默认会话、原版路径/FontSpec；中文代码页和代码页外字符区分记录；不能以自有 QSettings 代替 |
| PT-06 | Unix 原版存储，`unix/storage.c:81`、`:164`、`:181`、`:205` | PUTTYDIR 和各子路径覆盖；已存在 XDG 与旧 ~/.putty 的选择；新环境 XDG_DEFAULT 行为；权限、文件不可写、目录异常、特殊会话名；用独立账户/容器避免读取个人目录 |
| PT-07 | 字体、编码与渲染，`putty.h:1611`；`unix/unicode.c:264`、`windows/unicode.c:692` | ASCII、中文宽字、组合字符、非 BMP、双向文字、DEC 线绘、粗体/下划线/闪烁/反色、ANSI/256/真彩、双宽双高行、不同字体、回退字体；窗口缩放/多屏高 DPI 后光标和命中格一致 |
| PT-08 | 终端缓冲/状态机，`terminal/terminal.c:2046`、`:2183`、`:7876` | 大量输出、回滚历史、备用屏幕、光标保存恢复、全屏 TUI、清回滚/重置、终端查询响应、断线保持屏幕；与固定上游核心对同输入比对状态和可见行为 |
| PT-09 | 键盘/IME，`putty.h:2046`、`:2060`；`terminal/terminal.c:7807`、`:8144` | 应用光标/数字键盘、功能/编辑键、Ctrl/Alt/AltGr/Compose、Backspace/Delete、Enter、快捷键冲突、中文预编辑/提交和焦点切换；核对对端真实字节而非仅 QTest 发事件 |
| PT-10 | 鼠标/选择/剪贴板，`putty.h:2022`、`:2029`、`:2035`；`terminal/terminal.c:7116`、`:7204` | 选择/扩选/矩形/双击/三击、回滚与滚轮、raw mouse 与修饰键、Copy All、多行/括号粘贴、primary/clipboard、自定义粘贴动作、失去剪贴板所有权；Windows RTF、Linux X11/Wayland 分测 |
| PT-11 | 窗口/标题/铃声/打印，`putty.h:1642`、`:1657`、`:1671`；`terminal/terminal.c:3255` | 本地/远端 resize 完成、行列与字体缩放、最小/最大/全屏、标题与禁止远控选项、可见/声音铃声、打印流结束；Win WAV/任务栏提示等平台功能专项；Wayland 不支持的管理请求要有明确处理与证据 |
| PT-12 | 认证与防伪，`putty.h:1088`、`:1128`、`:1334`、`:2043` | 用户名/密码、加密私钥口令、公钥/证书、keyboard-interactive 多提示/取消、Pageant；服务器恶意横幅/伪造提示被区分，可信提示不混入非可信输出，口令不回显、不进事件日志 |
| PT-13 | 主机密钥/CA/弱算法，`putty.h:1215`、`:1252`；`config.c:2794`、`:2814`、`:2857` | 首次接受并保存、仅本次、拒绝、变更告警、手工 pin、CA 证书信任、弱密钥/算法告警、取消与断线回调只完成一次；原版缓存与 Qt 互认 |
| PT-14 | SSH 选项与高级认证，`config.c:2677`、`:2750`、`:2867`、`:2888`、`:2930`、`:2955` | KEX/cipher/hostkey 顺序及警告线、重协商、压缩、SSH 版本选项、auth 插件、GSSAPI 及委派；缺动态依赖应记录真实可用性与提示，不因构建开关全禁用而跳过 |
| PT-15 | 代理，`config.c:1722`、`:2598`；`windows/config.c:358`、`unix/config-unix.c:34` | SOCKS4/5、HTTP CONNECT、Telnet 命令、local command、SSH proxy TCPIP/exec/subsystem，代理认证、DNS 位置、绕过列表、错误/取消；真实路由结果与 upstream 一致 |
| PT-16 | 转发、共享与显示，`config.c:2713`、`:3015`、`:3067`、`:3088` | L/R/D、IPv4/IPv6、绑定范围、代理转发、X11 cookie/Xauthority、SSH TTY/env、共享连接 upstream/downstream、远程命令；主进程退出时剩余转发生命周期正确；Linux Wayland 仍按 DISPLAY/XWayland 条件单测 X11 转发 |
| PT-17 | 日志与菜单，`putty.h:2073`；`unix/window.c:5660` | Event Log、会话日志各模式、追加/覆盖/取消/无法写、敏感包省略；New/Duplicate/Restart、Change Settings、Special Commands 动态菜单、Copy All、Clear Scrollback、Reset；不因双语导致协议/服务器文字改变 |
| PT-18 | 会话中重配置，`unix/window.c:4804`、`:4829`、`:4833`、`:4839`、`:4843`、`:4847` | 修改字体/颜色/窗口/日志/可重配转发即生效；取消保持旧配置；禁用不可运行中改项；重配置期间断线/关闭不访问失效指针；语言切换保留未提交输入 |
| PT-19 | 平台事件及压力，`callback.c:115`、`timing.c:167`；`windows/select-gui.c:98` | 无网络时计时照常、持续输出时界面可响应、真实高成本 PPK KDF/GSSAPI 阻塞时 GUI 心跳与关闭状态、字体/DPI 代次及丢帧恢复、连接失败/半关闭/OOB、fd/socket 重用、句柄列表变化、pipe/serial/local proxy、异步提示中取消、重入/重复退出通知、快速连接关闭无泄漏/悬挂 |
| PT-20 | 参数兼容，`windows/putty.c:57`、`:131`；`cmdline.c` | -load、协议/端口/用户/私钥/转发/代理/日志等完整适用参数，与原版解析顺序/覆盖优先级一致；两平台 GUI 启动参数、非法参数和退出行为分别对照；不能让 Qt 参数解析先吞掉 PuTTY 参数 |
| PT-21 | 六组件互操作 | Qt 保存会话→Plink/PSCP/PSFTP -load；PuTTYgen 生成密钥→Pageant→PuTTY/CLI 认证；共享连接跨 GUI/CLI；传输哈希与远端退出码；包内运行不要求开发机 Qt/环境变量 |
| PT-22 | 双语与可用性 | 初次中文，切换英文后连接/密钥/草稿保留；配置全部标签、动态列表、错误提示、文件/字体/颜色标准对话框、快捷键可用；help/菜单不含开发任务文字；服务器文字保持原样 |

### 必须单列的高风险差异

- “原版完整功能”需要覆盖 Windows 配置扩展以及 Unix 配置扩展，不能只枚举 `config.c`；平台特有 FontSpec、OEM 模式、字体质量、RTF、铃声、打印等均有可观察结果。
- Wayland 与 X11 的原生窗口能力不同；只有明确适用条件并实测后的平台限制才可以标记“不适用”。不能将同一个未实现控件在所有平台一并判不适用。
- SSH 连接共享、代理链认证、主进程退出后端口转发仍存活，都会穿过 Seat 与事件桥，应该放在集成阶段的必需用例而非仅单元测试。
- Windows 原版注册表和 Unix 原版目录在测试中必须隔离；不能因测试用了 QSettings IniFormat 就认为上游 storage.c 也已隔离。
- `test/test_terminal.c:39` 的 mock 绘制函数为空，`:68` 的 setup_draw_ctx 返回 false，因此上游测试通过不能证明 Qt 绘制、剪贴板、IME 或键盘适配正确。
- 不应承诺存储格式超出 0.85 能力：Windows cpath/代码页边界以及任意未知字段保留另有约束。可靠写入错误报告也需实施改造，见架构文档注意事项。

### 建议的验证层次

1. 固定上游 0.85 核心基线：接入有意义的终端/配置测试，保存夹具与来源；新增用例只验证真正跨边界风险，例如控制刷新误写、异步释放、配置 round-trip。
2. Qt 控件逻辑：假 TermWin/Seat/Backend、配置控制树覆盖、编码/事件/id/取消/双语状态测试；验证真实数据流而非镜像实现细节。
3. 受控互操作：隔离的 SSH/代理/串口伪终端/协议对端；核对网络字节、退出码、缓存与配置副作用、共享和转发生命周期。
4. 原生桌面：各平台输入、IME、clipboard/selection、字体高 DPI、全屏、托盘相关协作、原生句柄/串口和发布包启动；保留截图、日志、版本和缺口。
5. 全套件回归：现有 PuTTYgen CTest 与已记录边界保持；CLI-only 配置不依赖 Qt；父任务完成需所有范围项有证据或用户批准的范围修订。

### 外部参考与相关规范

固定上游版本来源为 `vendor/来源.md` 与仓库 `vendor/putty/`，以固定源码为准；Qt 适配 API 的官方来源及版本限制记录于 `terminal-architecture.md`。相关规范为 `.trellis/spec/frontend/directory-structure.md`、`state-management.md`、`.trellis/spec/backend/directory-structure.md`、`error-handling.md`、`private-file-permissions.md`、`build-tools.md`；其中密钥专用契约只适用于其原有范围，新终端尚需实施时补充针对性规范。

## 注意事项 / 未找到

尚未创建可执行测试夹具、GUI 控件映射清单和原生平台证明，亦未运行构建或测试。本表不能被当作通过记录；每个后续子任务应将相关编号变成独立验收条目，写清依赖并保留未通过状态。原版用户手册各章节可作为后续全功能清单的补充，但不能替代源码/运行结果。
