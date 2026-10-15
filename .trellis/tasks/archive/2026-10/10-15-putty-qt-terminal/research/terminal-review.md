# Linux 终端纵切独立检查与自修

日期：2026-10-15。检查范围为首轮真实 Terminal／Backend／Seat、CPU 离屏帧、PPK／GSS 响应性和隔离夹具，以及上游补丁0006、0007。后续获主代理明确授权，扩展到 SSPI MIC 单函数、统一输入队列和补丁0008。未改共享运行时、根构建、Pageant 或 Windows 工具；未提交或推送。

本检查不表示完整 PuTTY 产品完成。Windows 原生运行、X11／Wayland 桌面、完整配置／输入功能仍按任务技术门验收；本报告后续补充了两个目标 Ubuntu 的真实执行证据。

## 已修复发现

### F1：MIC 错误状态未检查，可能读取或释放未初始化地址

- 位置：`vendor/putty/ssh/userauth2-client.c` 的 `ssh2_userauth_gss_packet` 及两处调用者；`vendor/putty/windows/gss.c` 的 `ssh_sspi_get_mic`。
- 证据：GSS 空 context、SSPI 查询或签名失败可能不写输出，原调用者忽略返回值并直接读取 `mic.value/length`。SSPI 签名失败还遗漏其刚分配的缓冲；查询成功但最大签名长度为0时错误返回成功而不提供输出。
- 修复：MIC 输出清零，非成功或空 MIC 返回失败；两种认证调用者均明确 `ssh_sw_abort`，不把 NULL 放入队列。失败时已返回的缓冲仍通过所属库释放。SSPI 输出先清零，零长度返回失败，签名失败归还临时分配。正常 MIC 复制后释放的0007保持独立。
- 补丁：`vendor/patches/0008-gss-mic-failure.patch`，基于已应用0007的源码生成；0006、0007、0008反向检查均通过。
- 回归：`terminal-gss-mic-failures` 编译生产函数原文，实际验证两种认证包的成功复制／释放、不写输出失败、空成功和带缓冲失败；失败不能生成任何包。修复前同一探针触发 marshal 断言，退出-6。`terminal-sspi-mic-failures-shim` 编译真实 SSPI 函数原文并注入系统调用结果，覆盖空 context、查询失败、零最大长度、签名失败及成功所有权，修复前退出1；修复后两项均通过。
- 平台边界：SSPI 回归是窄类型／调用夹具，不等于原生 Windows API 已执行。真实 GSS 认证及 ASan 回归另外保留，没有用故障夹具代替正常认证。

### F2：合法宽帧被消费者4096像素的单边上限拒绝

- 位置：`apps/putty/src/app/terminal_controller.cpp` 的帧验证。
- 证据：渲染器允许240列、2行及不超过4M总像素的图像；144 DPI／DPR2 下宽度超过4096仍合法。消费者原来拒绝该帧，已接受的初始配置因此没有可显示图像。
- 修复：消费者按同一4M总像素上限验证，仍核对正尺寸、stride、字节长度和 DPR。
- 回归：`terminal-render-bounds` 启动真实 Terminal、核心字体和 QImage；修复前 `errors.isEmpty()` 失败，修复后宽帧实际呈现。夹具和随机种子全部在项目私有根。

### F3：方向键绕过文本队列，在核心拥塞时静默丢失

- 位置：`terminal_controller.*` 的 `sendKey/sendInput/pumpInput`。
- 证据：方向键曾直接提交，窗口忽略返回值；真实同步认证期间16项核心队列满时，方向键被拒绝且没有保留或错误反馈。
- 修复：获主代理批准后，文本和方向键统一为有序操作队列；相邻文本合并、文本分块最多4096字节，单项等待回执。总量最多64KiB，排队操作最多256项及一个在途分块；容量拒绝发出界面错误，核心背压保留并重试。关闭清零并撤销待发输入，之后的文本及方向键均返回 `PR_CLOSING`。
- 回归：真实 PPK 解密期间填满16项核心命令，再提交256项混合文本／方向键。额外方向键明确拒绝并产生一条错误；SSH 对端逐字节验证642个线缆字节，顺序及内容完全匹配。普通构建记录451次重试，ASan记录455次；不是仅检查本地队列长度。关闭中另验证在途文本／方向键被撤销、待发字节清零。

### F4：无请求完成事件误清空字体配置

- 位置：`TerminalController::receive` 的完成事件匹配。
- 证据：`renderRequest_`／`inputRequest_` 初始为0，而 OPEN、GET_SCREEN 的完成事件请求号也是0；原分支误认为字体配置完成，把 `config_` 替换为尚未设置的全0结构，破坏代次和过期帧过滤。
- 修复：仅匹配非零在途请求号。
- 回归：真实连接完成后字体代次仍为1；配置到2后 GET_SCREEN 不得改回0；混合输入后的屏幕请求同样保持代次1。

### F5：报告路径的词法判断允许逃出项目 .tmp

- 位置：`run_terminal_test.py`、`run_terminal_gss_test.py`。
- 证据：`absolute().is_relative_to(...)` 不消除 `..`，也不解析符号父目录。
- 修复：先解析实际目标，再做项目 `.tmp/` 边界判断，拒绝发生在创建服务或受测进程之前。
- 回归：`terminal-runner-report-paths` 内两项 unittest 对两个运行器都验证上级目录和符号父目录逃逸；夹具启动被哨兵禁止，目标文件不写入。

### F6：不可变帧断言原来只验证测试自己的深复制

- 位置：`terminal_test.cpp`。
- 修复：在 DPI 更新后保留实际收到的 QImage 共享快照，后续真实输出继续时核对其像素仍不变；不先 `.copy()` 后再验证同一个副本。

## 已核对且未另行修改

- 0006在 BPP 结构释放前清除收发包队列、禁用队列回调，再清除 BPP 上下文回调；PktIn 的延迟归还保留上游所有权。真实关闭 ASan／默认 LSan 通过，未在模块析构后盲跑业务回调。
- 0007使用已有库专属 `free_mic`，在包已复制 MIC 后释放；没有误用 `free_tok`。
- 唯一核心线程拥有 Conf／Terminal／Backend／Ldisc／PRNG 和 QFont／QImage；Qt 控件只消费序列化完整帧。帧拥塞只保存最新完整图像，普通状态按类型有界合并；一次性确认有请求号及关闭取消器。
- GSS 域显式设置本次配置、缓存、keytab、重放缓存及日志；不修改 HOME、父环境或读取个人代理。真实 KDC 只监听回环，客户端只有本次 TGT，受测终端不提供 PPK 或 agent，KDC 日志实际记录 TGS_REQ。夹具4项独立真实集成检查全部通过，0跳过，8.949秒，覆盖无凭据失败和关闭回收。
- 延迟准确含义是代理收到真实 TGS 请求后等待1.5秒再转发给 KDC；不是睡眠替代 GSS，也不是专门推迟已经生成的响应。README已改为准确措辞；旧实施记录中的“响应延迟”按此澄清。
- 独立核对主代理的 Windows 单行修复时发现 `QWinEventNotifier::HANDLE` 在 Qt6.8.3 中是私有别名；已报告，主代理改用公开的 `Qt::HANDLE`，与构造参数类型完全一致。此检查仅基于锁定头文件，不表示 Windows 运行通过。

## 验证

本机 Linux x64、GCC16.2.1、Qt6.12.0、offscreen；SSH 对端为受控断网 Arch 容器。全部临时材料、构建、keytab、票据、配置、截图及报告在项目 `.tmp/`。

| 项目 | 实际结果 |
| --- | --- |
| 普通构建 `putty-qt`／终端与MIC测试 | 通过 |
| `ctest --test-dir .tmp/build/suite-terminal --output-on-failure -L terminal` | 6/6通过，42.51秒 |
| 最新补充关闭撤销断言后的受影响普通 PPK 用例 | 1/1通过，26.87秒 |
| `ctest --test-dir .tmp/build/suite-terminal/asan --output-on-failure -L terminal` | 最新源码／断言6/6通过，42.71秒；ASan、UBSan、默认LSan无诊断 |
| GSS 公共夹具显式启用的独立 unittest | 4/4通过，0跳过，8.949秒 |
| 自有 C/C++ 严格语法检查 | 7个文件 `-fsyntax-only -Wall -Wextra -Werror` 全0；队列变更后再检查控制器／终端测试全0 |
| Python | 6个文件 AST通过；报告路径 unittest 2/2通过 |
| 补丁／空白 | 0006／0007／0008反向检查、`git diff --check`通过 |

LSan 新增宽帧用例曾因沙箱无法检查线程而报自身 fatal；保持检测开启，在授权环境重跑2/2通过，最终全6项同样通过，没有抑制泄漏。未将最初默认未启用 GSS 的4项跳过记成通过；随后显式启用才取得上述真实4/4结果。

最终公开结果为 `.tmp/terminal-reports/212581c99b7f5e1b{,-gss}.json`、ASan 的 `afe4c0128ab46a22{,-gss}.json`。普通PPK负载12725ms／253次心跳／最大99ms，实际认证工作2141ms，close调用0ms／安全清理2276ms；GSS负载12027ms／237次心跳／最大113ms，真实TGS等待1.50011秒，等待中close0ms／安全清理1553ms／最大心跳50ms。ASan的PPK／GSS最大心跳分别129／104ms，均低于250ms。公开字体包含DejaVu Sans Mono、Noto Color Emoji和Noto Sans Mono CJK KR；终态截图已实际查看，FINAL／INPUT_OK、中文、组合音标和emoji可见。

## Ubuntu 22.04／24.04 实际执行复核

两个目标此前已能编译链接，但手动验收使用宿主 Release PuTTYgen 校准1500ms Argon2，生成377轮；受测核心分别为 GCC11.4／13.3 的 Debug 构建。真实状态停在同步 KDF，15秒启动限时因此到期。临时公开事件／毫秒探针确认口令输入和 KDF 开始，随后改用同配置工具取得34轮、约2秒的实际核心工作。此问题是校准配置失配，没有证据需要修改 Qt 事件桥，未修改共享运行时、提高超时或降低250ms心跳标准。临时源码探针已精确移除。

最终 PPK 校准工具与受测终端均在相应 Ubuntu 镜像中执行，使用同一个构建目录的 `suite-test-keygen/puttygen`／`putty-terminal-tests`、目标 libc 和固定 Qt6.8.3。Ubuntu22 的校准入口为 `.tmp/terminal-review/ubuntu22-keygen`，是原终端 wrapper 的私有副本；Ubuntu24 使用主代理的 `.tmp/suite-validation/ubuntu24-keygen`。两者均只挂本次私有根、使用077 umask，并在只读目标环境中执行实际二进制。第一次用 Ubuntu22 Debug 工具在宿主 libc 校准的中间成功结果只用于诊断；下表采用后续校准与终端均在目标环境的最终结果。

| 项目 | Ubuntu 22.04／GCC11.4 | Ubuntu 24.04／GCC13.3 |
| --- | --- | --- |
| 最终终端／两项MIC回归增量构建 | 通过 | 通过 |
| PPK负载时间／50ms心跳数／最大间隔 | 12534ms／248／114ms | 12564ms／249／84ms |
| 实际同步PPK认证工作 | 1991ms | 2022ms |
| PPK工作中close调用／安全回收 | 0ms／1893ms | 0ms／2074ms |
| 混合文本／方向键对端逐字节验证 | 642字节／433次背压重试 | 642字节／443次背压重试 |
| GSS负载时间／50ms心跳数／最大间隔 | 12062ms／239／91ms | 12036ms／238／117ms |
| 实际KDC服务票据等待 | 1次TGS_REQ／1.500108s | 1次TGS_REQ／1.500105s |
| TGS等待中close调用／安全回收／最大心跳 | 0ms／1558ms／51ms | 0ms／1555ms／51ms |
| 目标环境中本地终端CTest | 4/4，0.43秒 | 4/4，0.46秒 |

本地四项为真实 GSS MIC 函数故障、真实 SSPI 函数原文的窄夹具、宽帧和报告路径检查；SSPI 仍不是 Windows 原生系统调用。PPK及GSS完整负载另由宿主隔离运行器调用目标容器 wrapper，实际 SSH／KDC 对端是宿主 Arch 受控服务，不把对端镜像当作客户端平台证据。

公开结果位于 `.tmp/terminal-review/ubuntu{22,24}-final.json`、`ubuntu{22,24}-gss-final.json`、`ubuntu{22,24}-unit-final.log`。两个版本完整帧均为2080×1200、DPR2、渲染代次2，使用宿主共享系统字体；Ubuntu24终态截图已实际查看，中文、组合é、emoji、FINAL和INPUT_OK可见。未验证两Ubuntu桌面字体配置、X11／Wayland与原生窗口交互。GSS夹具README已同步默认镜像按 Arch／Ubuntu22／Ubuntu24宿主选择的事实。

## 未修复／待验

本次授权范围内没有遗留的已确认代码缺陷。完整键盘、IME预编辑、鼠标／剪贴板、配置描述、平台字体和其它协议入口仍是既定后续功能，不能把纵切当完整实现。Windows原生终端／SSPI、X11／Wayland与桌面输入仍由主任务分别执行记录；两个目标Ubuntu的Qt6.8.3真实PPK／GSS及异步关闭现已通过。第一项双平台技术门保持未完成。

公共 `.trellis/spec/` 与 `vendor/来源.md` 由主代理所有，未越权改写。建议其同步本轮实证契约：文本／键操作共用有序有界队列、非零请求号匹配、帧生产／消费使用相同总像素限额，以及0008的错误缓冲所有权；README与本报告已记录可执行事实。无需更改父产品范围或增加审批。
