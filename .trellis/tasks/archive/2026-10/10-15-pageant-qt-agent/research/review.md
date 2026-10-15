# Pageant Linux 核心与 Qt 外壳独立审查

日期：2026-10-15。审查任务由主会话显式指定为 `10-15-pageant-qt-agent`，没有修改父/子任务状态、提交或推送。依据本任务 PRD/design/implement/check.jsonl、核心/GUI 实施记录及所列规范，直接审查并自修；没有再派检查或实施代理。

## 发现（已修复）

### R1：口令按字符限长，回复被拒后窗口仍关闭

- 文件：`apps/pageant/src/app/agent_controller.h/.cpp`、`src/ui/agent_window.h/.cpp`、`tests/gui_test.cpp`、`tests/CMakeLists.txt`。
- 原因：`QLineEdit::setMaxLength(65536)` 限制 UTF-16 长度，协议限制为 65536 个 UTF-8 字节。原 `reply()` 不返回 `enqueue()` 失败状态，而 `finished` 在发送前已经关闭窗口。30000 个中文字形成 90000 字节时，核心请求没有得到回复，添加流程仍占用忙状态。控制器 1024 命令队列满时也有相同问题。
- 真实初始失败：独立构建的新 `oversizedPassphraseKeepsPrompt` 使用加密 PPK 和真实代理核心，确认原窗口已关闭。日志 `.tmp/suite-final-review-temp/pageant-oversized-before.log`，CTest 退出 8，断言 `pending && pending->isVisible()` 失败。
- 修复：内部 `reply()` 返回排队结果；专用口令对话框在 `done()` 中先提交，成功才关闭。UTF-8 超限或队列满保留窗口、输入和可重试的请求；错误提示随语言切换。取消不携带输入秘密，按钮、Esc 和窗口关闭统一经过提交门。核心主动撤销/退出先解除提交函数，再关闭窗口，避免误发旧回复。成功关闭加一次性保护；口令字节临时副本继续清零。
- 回归：超长中文输入保持提示和忙状态；改为正确口令后真实加载成功；无 GUI 事件处理期间填满 1024 命令，再验证失败回复保留窗口、完成通知消化后可重试成功；超长输入取消后添加状态解除。原删除中提示、退出中提示和错误口令重试继续通过。
- 审查驱动修正：首次组合运行中的队列重试把带点击副作用的表达式交给 `QTRY_VERIFY` 重复求值，导致第二次点击已关闭对话框；已加关闭状态检查，保留该次日志 `pageant-tests-final.log`。它不作为最终通过证据。最终日志见下表。

## 实际路径复核

- `main.cpp` 在创建 QApplication/Runtime/Qt 工作线程前分流原 CLI。参数解析、fork、`--exec` 等仍复用固定上游 Unix 源；`--wrap=uxsel_input_add/remove` 仅按模式路由宿主通知，保留真实 uxsel/poll 和协议解析。生产原 CLI 前缀未改，服务测试使用编译注入项目 `.tmp` 的独立入口。
- C 代理持有上游私钥/监听器/请求；GUI 只持协议版本、完整公开 blob、注释、指纹和状态。多证书不按指纹合并。逐一检查 SSH1/SSH2 添加和明文 PPK 扩展的私钥复制/临时输入释放，0005 所修所有权与 `pageant_add_*` 的实际复制一致。
- 请求编号由运行时一次性消费；错误载荷也取消对应提示。删除/清空先撤提示，退出停止监听、关闭已接收连接、注销客户端及回复队列、清缓存口令和所有密钥。0005 同时覆盖全局 GUI 等待链中的签名引用、moribund 私钥和后续提示解锁；真实删除、断开、失败提示、退出及第二把密钥恢复用例均执行。
- 操作结果在有界队列保留；公开快照合并并限制 2 MiB；背压不伪造完成。Qt 原命令在完成前保留，失败和关闭清除敏感缓冲。新增回复提交门补齐了 UI 一端的背压处理。
- 普通/加密添加、删除、清空、重加密及串行提示均走真实核心。中英切换保留 blob 身份、表格选择和活动口令。状态和密钥注释使用纯文本显示。无托盘关闭保持可见控制入口；托盘丢失恢复窗口的本轮证据仅为注入可用性状态的离屏用例。
- 安装规则仅登记同一个 `bin/pageant` 和 `pageant --gui` 桌面入口，未额外交付 GUI/辅助程序。本轮没有重复执行实施者的安装烟雾测试，也没有将链接到本机 Qt 的安装当作独立发布包。
- 0004/0005 均按 `--directory=vendor/putty` 反向应用检查成功，补丁与当前真实源对应；新增注释为中文。初次从仓库根漏传 `--directory` 的路径检查失败已纠正，不作为补丁失败。

## 独立验证

环境为本机 Linux x86_64、GCC 16.2.1、系统 Qt 6.12.0，**不是目标 Ubuntu/固定 Qt 6.8.3**。独立目录为 `.tmp/build/pageant-final-review` 和 `.tmp/build/pageant-final-review-asan`，未写实施者构建目录。所有真实代理/SSH、私钥、配置和 socket 均由公共隔离夹具创建在项目 `.tmp`；未启动生产默认原 CLI 服务或访问个人代理。

| 检查 | 本轮结果 | 证据 |
| --- | --- | --- |
| Debug 配置/产品与测试构建 | 通过，退出 0 | `.tmp/suite-final-review-temp/pageant-build-before.log`、`pageant-build-test-final2.log` |
| 普通完整 Pageant CTest | **10/10，0 skip，11.22 秒** | `pageant-tests-final2.log`；构建目录 `Testing/Temporary/LastTest.log` |
| 核心互操作内部计数 | **17/17，3.834 秒** | 普通 `LastTest.log` 中 `Ran 17 tests` / `OK` |
| GUI 场景 | **8/8 独立进程**，包括新增回复边界回归 | 普通 `LastTest.log`，每场景 0 skip |
| ASan/UBSan/LSan 完整 Pageant CTest | **10/10，0 skip，15.19 秒** | `pageant-asan-tests-final.log`；ASan 构建目录 `LastTest.log` |
| ASan 核心互操作内部计数 | **17/17，5.584 秒** | ASan `LastTest.log` 中 `Ran 17 tests` / `OK` |
| 自有 C/C++ 严格语法/类型检查 | **13 条实际编译记录全部退出 0**，覆盖 11 个不同自有源 | `pageant-strict-sources.log`，复用 compile_commands 并加 `-fsyntax-only -Wall -Wextra -Werror` |
| Python 语法 | **3/3 AST 通过** | 同上，未生成 pycache |
| 补丁和空白检查 | **0004/0005 reverse check、git diff --check 通过** | 真实工作树命令，退出 0 |

Sanitizer 构建参数为 `-fsanitize=address,undefined -fno-omit-frame-pointer`；运行使用 `ASAN_OPTIONS=detect_leaks=1:abort_on_error=1:malloc_context_size=12`、`UBSAN_OPTIONS=halt_on_error=1`。没有 suppressions，没有关闭泄漏检测，没有将诊断过滤成通过。上游既有 GCC16 const/宏告警与自有源严格检查分开；Qt offscreen 的 raise/托盘能力警告不作为实体桌面通过。

## 发现（未修复）与完成边界

本轮覆盖的 Linux 当前路径除 R1 外未发现需阻止交付该阶段的剩余确定性缺陷。以下为已存在且仍须完成的任务范围，不能将本次审查写成全任务完成：

1. Windows Pageant host、命名管道/WM_COPYDATA、owner/ACL/长度、单实例及旧客户端互操作尚未实施或原生验证；本轮不修改公共 runtime/CMake，也不以 Linux 用例推断 Windows。
2. 目标 Ubuntu22.04/24.04、固定 Qt6.8.3、X11/纯 Wayland、真实托盘出现/消失和无 SDK 包依赖闭包未在本轮执行。原 CLI 的上游提示仍以 DISPLAY 判断 GUI，纯 Wayland 原 CLI askpass 有明确适配缺口。
3. GUI 指纹格式切换、同包 PuTTY 新/保存会话入口、完整原 CLI 生命周期/TTY/`-X`/永久/foreground/debug 参数矩阵、独立原版双向对照、真实第二跳转发、证书服务器认证、OpenSSH ssh-agent 逆向和完整发布包验证仍按实施记录补齐，未删需求。
4. 当前产品每进程创建一个 Pageant 核心，GUI 测试每场景独立进程。上游 `pageant_init()` 的全局树没有公开完整 cleanup API；本轮未把“同进程反复关闭并重建 Pageant 核心”当作已验能力，也没有自行扩展上游公共初始化接口。

主会话后续可在适用规范中补充本轮获得的契约：异步口令对话框必须以字节协议边界验证，且完成/取消在实际排队成功前不能销毁唯一响应入口。规范文件所有权属于主会话，本轮仅在研究记录中明确，避免覆盖并行修改。
