# 研究：Pageant 功能对照、密钥生命周期与互操作验收

- 研究问题：为 Qt Pageant 建立固定 PuTTY 0.85 完整功能清单，并定义与 PuTTY、Plink、PSCP、PSFTP、OpenSSH 的平台代理认证及转发验收矩阵。
- 研究范围：内部研究（本地 PuTTY 0.85 源码和文档；Qt 平台文档见配套架构研究）
- 日期：2026-10-15

## 研究发现

### 1. 基线与证据文件

本矩阵是规划验收建议，全部为“待实施／待验证”，不表示测试已执行。平台符号：W 为 Windows 10 1809+/11 x64；U 为 Ubuntu 22.04/24.04 x64；U-G 为 X11／Wayland GUI；U-X 为需要 X11 的专属行为。每项须在其适用平台保留原版输出、错误和退出状态；“OpenSSH 兼容”不等于承诺 OpenSSH 的全部代理扩展。

| 文件 | 提供的证据 |
| --- | --- |
| `vendor/putty/pageant.c:90` | 公钥与私钥分离、证书共享私钥及去重原因 |
| `vendor/putty/pageant.c:368`、`:494` | 添加去重、共享私钥、删除最后公钥后的释放 |
| `vendor/putty/pageant.c:676`、`:801`、`:866` | 延迟解密、错误口令重试、取消及请求排队 |
| `vendor/putty/pageant.c:914`、`:1421`、`:1579` | 重加密、PPK 扩展、扩展状态列表 |
| `vendor/putty/pageant.c:2211`、`:2490` | 密钥文件载入和 GUI 可复用的枚举接口 |
| `vendor/putty/pageant.h:209`、`:248` | 管理操作状态码、已知扩展、状态标记 |
| `vendor/putty/windows/pageant.c:328`、`:575`、`:1850` | GUI 列表、指纹格式、托盘功能 |
| `vendor/putty/unix/pageant.c:1323`、`:1493` | Unix 选项解析及代理／客户端模式约束 |
| `vendor/putty/doc/pageant.but`、`doc/man-pageant.but` | 各平台功能及用户流程参考 |
| `vendor/putty/cmdline.c:658`、`:693` | 代理认证和转发开关 |
| `vendor/putty/ssh/userauth2-client.c:576`、`:1075` | 实际认证中的枚举、签名与等待 |
| `vendor/putty/ssh/agentf.c:26`、`:96` | 转发客户端请求和代理断开失败行为 |

### 2. 密钥模型与生命周期契约

#### 内存中的实体

私钥按协议版本与不含证书的公钥识别；公钥额外按完整证书 blob 区分，因此一个私钥可对应裸公钥和多个证书，见 `pageant.c:113`、`:132`、`:158`。Qt 列表不能只用“指纹字符串”或注释作为主键：默认指纹刻意忽略证书，多个证书条目可具有相同指纹（`doc/pageant.but:98`）。选择动作应使用 `pageant_pubkey` 的 blob 与 ssh_version，见 `pageant.h:220`，界面仅保存公开快照。

#### 原版存在的状态

| 状态 | 核心判断／公开标记 | 用户可观察行为 |
| --- | --- | --- |
| 普通明文持有 | 无 encrypted_key_file | 签名不提示；不能重加密；重加密全部不删除该密钥 |
| 仅加密 PPK | `HAS_ENCRYPTED_KEY_FILE` 和 `HAS_NO_CLEARTEXT_KEY` | 添加时不提示，首次使用触发口令 |
| 可重加密的明文 | 有 encrypted_key_file，已有 skey | 首次解密后后续签名不提示；重加密删除明文，下一次再提示 |
| 等待口令 | decryption_prompt_active、全局 gui_request_in_progress | 同时只有一个提示，排队中的请求不应超越或重复弹窗 |
| 最后一个公钥已删除、提示尚未返回 | moribund | 已排队请求失败，返回口令不能复活被删除密钥 |

标记定义见 `pageant.h:256`；共享私钥导致相关裸公钥／证书共同解密或重加密，见 `pageant.c:90`、`:392`、`:914`。删除单个证书不应删除仍被其他公钥引用的私钥，删除最后一个则释放并失败等待请求（`:494`、`:211`）。退出不能保存密钥明文或口令到配置。

文件添加只接受上游 SSH-1 私钥／SSH-2 PPK 类型；不接受任意 OpenSSH 私钥文件，见 `pageant.c:2230`。远端 `ssh-add` 通过标准代理协议添加明文身份是另一条路径（`:1201`），不能误判为本地文件导入格式已扩展。SSH-1 不支持加密形式添加与重加密（`:2237`、`:918`）。

延迟解密使用内存中的加密文件，见 `pageant.c:165`、`:818`；载入后移动／删除源文件，不应使后续签名依赖原路径。口令被成功用于批量添加时会暂存复用（`:2436`），批次结束须调用 `pageant_forget_passphrases()` 安全清除（`:2094`）。用户取消当前项时，之前已添加的密钥仍存在，不能把部分成功伪装成原子失败或全部成功。

### 3. 功能对照矩阵

| 编号 | 功能／适用平台 | 可观察验收与边界 | 本地原版证据 |
| --- | --- | --- | --- |
| P01 | GUI 查看列表，W／U-G | 显示算法、位数／曲线、指纹、注释、证书和解密状态；远端增删后刷新 | `doc/pageant.but:61`；`pageant.h:135`、`:231` |
| P02 | 所有原版密钥类型，W／U | RSA SSH-2、DSA、ECDSA P256/P384/P521、Ed25519、Ed448、SSH-1 RSA；签名和列表均验，不因现代 OpenSSH 拒绝旧算法就跳过核心 | `doc/pageant.but:72`；`pageant.c:426`、`:446` |
| P03 | GUI 指纹切换，W／U-G | SHA256／MD5 及含证书格式，切换不更换密钥；与 PuTTYgen／原版公开材料对照 | `windows/pageant.c:575`；`doc/pageant.but:91` |
| P04 | 普通添加、多文件添加，W／U | 加密 PPK 提示、错口令重试、取消保留其他密钥；重复密钥不重复列出；批次口令清除 | `pageant.h:195`；`pageant.c:2242`、`:2436`；`windows/pageant.c:544` |
| P05 | 加密添加与首次解密，W／U | 添加不提示，枚举可见，首次本地／转发签名才提示；正确口令后不再提示，拒绝不产生签名 | `doc/pageant.but:347`；`pageant.c:676`、`:801`、`:866` |
| P06 | 重加密单项与全部，W／U | 只移除可重加密私钥明文，不改原 PPK；普通明文密钥保留；重复重加密幂等；SSH-1 拒绝 | `pageant.c:914`；`doc/pageant.but:373` |
| P07 | 删除单项、多选、全部，W／U | 删除后枚举与签名不可用；同私钥其他证书仍可用；最后条目删除释放私钥；等待口令中删除不得复活 | `pageant.c:494`、`:801`；`windows/pageant.c:745` |
| P08 | 证书，W／U | 同私钥裸条目和多个不同证书可共存、指纹可区分；公钥身份与证书认证有效；重加密状态跨别名一致 | `pageant.c:90`、`:132`；`doc/pageant.but:98` |
| P09 | 标准代理请求，W／U | SSH-1 枚举／挑战／增删；SSH-2 枚举／签名／增删／清空；RSA SHA2 标志、未知标志拒绝 | `pageant.c:970`、`:1019`、`:1088`、`:1156`、`:1201`；`:723` |
| P10 | PuTTY 扩展，W／U | query、add-ppk、reencrypt、reencrypt-all、list-extended；未知扩展正确失败；对不支持扩展的代理显示失败，不回退为明文 | `pageant.h:248`；`pageant.c:1382`、`:2341` |
| P11 | Windows 命令行 | 文件参数、--encrypted 及别名、--keylist、--openssh-config、--unix、-c、-restrict-acl、-restrict-putty-acl、-pgpfp；顺序与错误状态对照原版 | `windows/pageant.c:1573`、`:1591`、`:1611` |
| P12 | Windows 单实例 | 原版与 Qt 版本任意先后、并发启动只产生一个服务；二次启动密钥加入已有代理；-c 在添加后执行 | `windows/pageant.c:1631`、`:1808`、`:1833` |
| P13 | Windows 托盘会话入口 | 新会话、已保存会话、帮助／关于、列表、添加／加密添加、删除全部、重加密全部、退出；启动同套件 PuTTY | `windows/pageant.c:843`、`:865`、`:1355`、`:1850` |
| P14 | Unix 客户端 CLI | -a/-d/-D/-r/-R/-l/--public/--public-openssh/-L/--test-sign/--test-sign-with-flags；标识歧义、未找到和扩展失败非零 | `unix/pageant.c:835`、`:1323`；`doc/man-pageant.but:206` |
| P15 | Unix 标识与指纹 | file:/comment:/fp:/sha256:/md5:/sha256-cert:/md5-cert:；-E/--fptype 四种格式，不能仅依文档只列的两种 | `unix/pageant.c:694`、`:1417`；`doc/man-pageant.but:244` |
| P16 | Unix 生命周期 | -X/-T/--exec/--permanent/--debug/--foreground；stdout、EOF、TTY、进程继承、X 断开均对照 | `unix/pageant.c:1218`；`doc/man-pageant.but:106` |
| P17 | Unix 其他参数 | -s/-c shell 语法、--symlink、--encrypted/--no-decrypt 别名、--gui-prompt/--tty-prompt、--askpass、-v、--help/-V/--version、-- 参数结束 | `unix/pageant.c:1323`；`doc/man-pageant.but:342`、`:363` |
| P18 | GUI 双语与无托盘，W／U-G | 默认中文，可切英文；切换保留条目、选择、在途提示与状态；有托盘时关闭列表隐藏；无托盘关闭时提供“保持运行／退出”，保持运行后必须仍有可见管理和退出入口 | 项目 `frontend/state-management.md`；配套架构研究第 6 节（父设计确定的新增适配验收） |

Unix Pageant 自有 main 不经 GTK 通用命令行入口（`unix/pageant.c:1308`），解析器中没有 `-display` 分支，未知参数直接失败（`:1441`）；不要从其他 GTK PuTTY 程序推断 Pageant 也接受其参数。Windows 页面的新会话／已保存会话入口必须纳入 PuTTY 主程序子任务的跨组件契约，不能以原 Win32 外壳已删除为理由删除功能。

### 4. 套件与 OpenSSH 互操作验收矩阵

测试必须使用固定原版 0.85 作为独立对照端，不能让同一新实现两端自测成为唯一依据；每个平台记录工具版本、二进制来源、命令、退出状态、公开指纹／签名校验和服务器日志。所有夹具、服务目录与日志写项目 `.tmp/`，口令和私钥不入报告。Qt 版本与原版的客户端、代理在同一专用测试身份下运行，采用原命名、互斥和发现规则验证互操作；以专用系统身份／隔离环境避开开发者日常代理，不能将服务端与客户端分到不同用户后宣称同用户兼容。

父设计已确定 Linux 桌面入口调用同一程序 `pageant --gui`，原 CLI 无参数继续报错，不新增独立 GUI 程序。验证应分别覆盖桌面 GUI 和无 DISPLAY／WAYLAND_DISPLAY 的原 CLI；纯 Wayland 下口令提示正常，`-X` 仍保持原 X11 生命周期。GUI 不自动修改用户 shell、SSH 配置，Windows 显式 `--openssh-config` 仅对指定文件生效。上述决定与原版证据无直接冲突；新增 GUI 行为单独验收，不混作原版 CLI 固有行为。

| 编号 | 服务端 × 客户端／平台 | 场景与成功判据 |
| --- | --- | --- |
| I01 | Qt Pageant × 新 PuTTY／Plink／PSCP／PSFTP，W／U | 相同测试用户仅配置代理公钥认证：PuTTY 真终端登录、Plink 执行命令且远端状态可核对、PSCP 上传下载摘要一致、PSFTP 批处理上传下载摘要一致；服务器确认公钥认证，不能回退密码仍算通过 |
| I02 | Qt Pageant × 固定原版 0.85 上述四客户端，W／U | 重复 I01，证明代理接口未只对新套件有效；Linux 每次明确 SSH_AUTH_SOCK，Windows 独立覆盖原管道 |
| I03 | 原版 Pageant × 新四客户端，W／U | 逆向兼容 I01；Windows 原版已先启动时新 GUI 加密添加由原代理持有，Linux 不创建隐藏新代理替代 SSH_AUTH_SOCK 指向 |
| I04 | Qt Pageant × 旧 IPC 专用客户端，W | 绕过／禁用管道路径，仅用 FindWindow + 文件映射 + WM_COPYDATA 完成枚举、签名、延迟口令；共享映射 owner 错误、短映射、越界长度均失败且服务继续 |
| I05 | Qt Pageant × Windows 原生 OpenSSH，W | --openssh-config 生成可 Include 配置，ssh 使用该 IdentityAgent 完成认证；原生 ssh-add 经显式代理环境或对应支持配置完成枚举／增删；退出后配置清空。不要把 ssh 读取 IdentityAgent 与 ssh-add 读取 config 等同，须按实测指定该工具入口 |
| I06 | Qt Pageant × Linux OpenSSH，U | SSH_AUTH_SOCK 下 ssh-add -l/-L/添加/删除与 GUI 双向同步，ssh 公钥认证成功；外部添加的明文身份不冒充可重加密 PPK |
| I07 | Qt Pageant × Plink -A × 远端 OpenSSH，W／U | 第一跳建立代理转发，远端 SSH_AUTH_SOCK 有效；远端 ssh-add 列举与本地一致；第二跳使用本地代理签名认证成功 |
| I08 | 转发远端 ssh-add × Qt Pageant，W／U | 远端添加／删除身份后本地 GUI 刷新；其他本地客户端可使用新身份；转发断开后本地代理继续可用 |
| I09 | Qt Pageant × -agent/-noagent、-A/-a，W／U | 开关隔离：关闭代理认证时不向代理请求签名；关闭转发时远端没有该转发端点；-i 对代理选钥仍匹配固定原版。-A/-a 只在 PuTTY／Plink 适用，不要求 PSCP／PSFTP 接受（`cmdline.c:695`） |
| I10 | Qt Pageant 延迟密钥 × 新／原版／OpenSSH／转发客户端，W／U | 并发首次签名只出现串行口令提示；正确、错误后重试、取消、客户端断开、提示中删除、提示中退出均结束请求，无死锁／崩溃／密钥复活 |
| I11 | Qt Pageant × 证书认证服务，W／U | CA 签发的同私钥多个证书分别枚举，签名通过独立验证；服务器接受正确证书；切换指纹含证书格式可区分；非法／过期证书在服务器拒绝不能误算代理签名故障 |
| I12 | Qt Pageant × WSL 1，W（具备环境时） | --unix socket、WSL 路径转换、ssh-add 枚举及 ssh 认证；父目录私有；WSL 2 作为原版不支持记录，不要求增加桥接 |
| I13 | 新 Unix Pageant 客户端 × OpenSSH ssh-agent，U | 标准增删枚举签名成功；--encrypted/-r/-R 不支持时非零且明文不泄漏；公开列表扩展回退到标准列表 |
| I14 | 各 GUI 后端／发布包，W／U-G | 无 SDK 安装包运行；Windows 普通／提升权限同用户；Ubuntu X11／Wayland 有托盘和无托盘；有托盘关闭列表后代理继续；无托盘关闭选择保持运行后窗口和退出入口仍可见，选择退出完整收尾；重新启动后不恢复内存密钥 |

代理接口测试应至少覆盖分片／合并请求、并行连接、非法长度、未知请求、签名未知标志、客户端中途断开和退出。流式解析上限 `AGENT_MAX_MSGLEN=262144`，见 `pageant.h:12`；接收解析的实际严格边界见 `pageant.c:1836`，不要自行假设“等于上限”可接受。监听器保持响应顺序与异步完成关联，见 `pageant.h:64`、`pageant.c:1840`。WM_COPYDATA 与流式边界分别测。

认证算法矩阵与服务端算法策略分开：DSA、SSH-1、Ed448 等在某些 OpenSSH 服务端不可用时，使用固定原版代理／独立签名验证证明代理功能，记录服务端不适用；不可直接跳过全部覆盖，也不能为了通过而全局降低用户 SSH 服务策略。

### 5. 负例、基线限制与功能误读

- `pageant.c` 主 switch 只实现列明的标准请求与 PuTTY 扩展；未发现 OpenSSH 的受约束添加、超时、每次签名确认、全局 lock/unlock、目标约束／session-bind 的实现分支。未知消息失败见 `pageant.c:1612`。这些不能因“原版完整兼容”被新增为必须实现的 OpenSSH 全特性；如需则为另行范围。
- 重加密不是修改 PPK 文件，也不是给原本明文添加的密钥自动设置口令，见 `pageant.c:926`。
- 证书共享私钥可使多个条目同时解锁，这是上游明确设计（`pageant.c:90`），Qt 不应“修复”为彼此独立口令状态。
- 列表默认指纹忽略证书，不适合作为唯一条目 ID，见 `doc/pageant.but:98`。
- Pageant 本地文件只接受原版私钥格式；已有 PuTTYgen 可将外部格式转换为 PPK，本任务集成链应覆盖转换后加载，不重复开发通用导入。
- Unix --askpass 的唯一敏感 stdout 是其定义的返回口令通道，成功 0／取消非零，见 `unix/pageant.c:1470`；不能把“所有 stdout 均不得含口令”的日志规则误套到此协议。
- `--foreground` 输出环境后关闭 stdout；所有普通 GUI 通知必须与该流隔离。--debug、-v 的 stdout／stderr 行为不同，见 `doc/man-pageant.but:177`、`:365`。

### 6. 相关规范

- `.trellis/spec/backend/directory-structure.md`：新组件独立目录、最小实际复用、组件关闭构建无需 Qt。
- `.trellis/spec/backend/error-handling.md`：C 适配边界、类型化错误、敏感数据清除、真实互操作。
- `.trellis/spec/frontend/state-management.md`：真实核心回调、语言保持状态、关闭等待、QSettings 测试隔离和后端证据区分。
- `.trellis/spec/frontend/directory-structure.md`：Qt 插件和发布包运行证据。
- 配套 `research/pageant-architecture.md`：平台接口、原生命周期、GUI 入口与无托盘建议。

### 7. 外部参考

主要功能以本地官方 0.85 源码及自带文档为准，其来源／版本见 `vendor/来源.md:3` 与 `vendor/putty/version.h:5`。Qt 能力的官方 6.8 文档、在线补丁版本限制和查阅日期见配套架构研究；本文件未将最新版 OpenSSH 的功能集合倒推成 Pageant 0.85 的实现保证。

## 限制与未验证项

- 未执行本文件任何矩阵；仅有源码研究证据。Windows 原生、两 Ubuntu 的 X11／Wayland、有无托盘、真实 SSH 认证和转发均需要分别执行并保留报告。
- 工程目前未接入 Pageant，CLI 工具与 PuTTY Qt 主程序由其他子任务实现；I01/I03/I07/I08 依赖它们可运行，不能隐式仅按任务树先后推断。
- 测试 socket 默认写系统 /tmp 的风险见配套研究，必须先实现可控目录边界再运行原版对照端。测试 SSH 服务端若自行创建远端临时目录，也需在隔离测试环境中显式重定向，避免开发验证违反用户目录约束。
- Windows 原版 -c 使用 ACP 剩余参数并有 UNICODE 注记（`windows/pageant.c:1617`），中文／空格参数的改进需保证原有分词语义；Windows --unix 中文路径同样需实测，不以 Qt 字符串支持代替。
- 原 CLI 的 GUI 口令路径需要纯 Wayland 可用性适配；Windows 提权跨上下文、父设计已确定的无托盘选择和 Linux 显式 GUI 入口均需子任务专项验证，不能把设计确定等同于运行已通过。
