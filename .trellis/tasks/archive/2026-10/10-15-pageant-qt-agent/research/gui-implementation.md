# Pageant Qt 窗口与 Linux 原入口阶段

日期：2026-10-15。仅记录本轮实际实现和实测，不表示 Pageant 全平台或全部对照项完成。核心及上游最小修复证据见 `core-implementation.md`。

## 交付路径和职责

- `apps/pageant/src/app/agent_controller.*`：持有唯一核心 Runtime，命令和公开事件编解码、队列背压、逐个文件添加、一次性口令回复、关闭完成及致命错误状态。待发送命令限 1024 条/4 MiB，已接受命令保留到完成，敏感命令字节在消费/关闭时清除。公开身份仍是协议版本与完整公钥 blob。
- `src/app/language.*`、`src/ui/agent_window.*`：中文默认和 English 切换，列表/多选、普通及加密添加、删除/清空、所选及全部重加密、刷新/复制端点、异步串行口令框、退出取消。语言切换保留条目、选择、口令输入和当前操作状态；原核心错误详细文本保留原义。
- `src/app/main.cpp`：仅显式 `--gui` 创建 QApplication/核心线程，验证当前 UID 的私有 XDG_RUNTIME_DIR，并在随机私有子目录创建代理 socket。无参数或其它参数在 Qt/线程之前进入原 `pageant_cli_main`。致命核心错误保留可关闭提示并返回非零。
- `src/core/pa_cli_prompt.c`：原 CLI 的通知路由和独立 askpass 前端。Linux 链接 `--wrap=uxsel_input_add/remove`，CLI 使用原 poll 空通知、GUI 使用冻结公共 runtime 的真实通知；不改变原 uxsel 表、参数解析或网络业务。原 CLI 需要 GUI 口令时，单线程父进程 fork+exec 同一程序的独立 Qt askpass 分支，父进程只读取定义的口令管道，从未在创建 Qt 工作线程后 fork。
- `resources/pageant.desktop` 与组件安装：`bin/pageant`、`share/applications/pageant.desktop`，桌面入口调用 `pageant --gui`。没有新建第二个 GUI 程序。

托盘可用时关闭列表隐藏；无托盘时关闭显示保持运行/退出，保持运行保留可见窗口；托盘服务消失后重新显示列表。托盘菜单使用相同真实操作，退出等待核心断开活动连接并清除密钥。没有新增未经验证的 PuTTY 会话入口。

本轮只改 `apps/pageant/` 及本任务研究记录；没有进一步修改已经稳定的 vendor 补丁、根 CMake、Suite*、公共 runtime 或 tools/suite。共享代码在其它代理修改后已随构建重新链接。

## 普通构建与 CTest

环境：本机 Arch Linux x64、GCC 16、Qt 6.12.0。既非发布锁 Qt 6.8.3，也非 Ubuntu 原生或 Windows 证据。

```bash
cmake -S . -B .tmp/build/suite-pageant -G Ninja -DCMAKE_BUILD_TYPE=Debug \
  -DPUTTY_BUILD_PUTTYGEN=OFF -DPUTTY_BUILD_PAGEANT=ON -DBUILD_TESTING=ON
cmake --build .tmp/build/suite-pageant --target pageant pageant-core-host \
  pageant-prompt-failure pageant-gui-tests pageant-entry-tests suite-test-keygen-build -j 4
ctest --test-dir .tmp/build/suite-pageant --output-on-failure -L pageant -j 3
```

最终 **9/9 CTest 通过，3.81 秒**，日志 `.tmp/build/suite-pageant/pageant-tests.log`；不是空测试集合。

| CTest/场景 | 真实验收内容 |
| --- | --- |
| pageant-core-interop | 内含 17 个真实核心用例，含 OpenSSH 验签及 agent-only SFTP；详见核心记录 |
| pageant-original-cli-entry | 无 DISPLAY/WAYLAND_DISPLAY 且 Qt 平台故意不存在时，版本/帮助、无参数、缺 --exec 参数、非法生命周期组合、--gui 多余参数的状态；实际 --gui 启动；原 --exec 服务与原客户端/OpenSSH 双向身份、签名验签、删除和随子进程退出 |
| defaultLanguageAndSelection | 默认中文，语言及状态文案切换，公开身份和选择保留，语言偏好隔离 |
| realAddDeleteAndClearActions | 从真实文件对话框添加 PPK、按钮删除、加密添加和清空，枚举状态与核心一致 |
| serialPassphrasesAndReencrypt | 两文件逐项添加，错误口令重试、取消后下一文件、单口令框、语言切换保留输入；单项/全部重加密；OpenSSH 真实签名触发提示并成功 |
| noTrayVisibleControlAndRecovery | 注入托盘可用性验证关闭/保持运行、隐藏及托盘消失恢复逻辑；这不是实体桌面托盘运行证据 |
| exitCancelsPromptAndSocket | 提示中退出，等待 Runtime.closed，提示消失、socket 删除 |
| clearDuringPromptDismissesDialog | 添加提示中清空，撤销窗口和添加状态，随后添加正常，不留下失效提示 |
| originalCliControlsGuiAgent | 原 CLI 在故意无 Qt 平台的子进程中对 GUI socket 执行 -a/-l/-L/-d/--encrypted/-D，GUI 快照同步 |

所有 key、配置、socket 和日志只在项目 `.tmp`。`pageant-entry-tests` 编译同一入口和原 CLI 源，仅通过已批准 0004 注入本构建独占 `.tmp/pa-<hash>/agent` 前缀；生产目标仍保留原 `/tmp/pageant`。公共 guard 没有放宽，测试直接调用已证明显式位置的新模块或测试前缀变体，不自动启动未知 Pageant 构建。

## Sanitizer、安装与视觉检查

Sanitizer 构建沿核心阶段的 `.tmp/build/suite-pageant/asan`：C/C++ 与链接使用 `-fsanitize=address,undefined -fno-omit-frame-pointer`。最终核心 17/17 通过；最终 7 个 GUI 场景各在独立进程通过，均为 `ASAN_OPTIONS=detect_leaks=1:abort_on_error=1:malloc_context_size=12 UBSAN_OPTIONS=halt_on_error=1`，无抑制文件。GUI 被测模块和核心有 sanitizer；测试用 PuTTYgen 及外部原 CLI 客户端用普通构建，不将此记成所有外部工具的 sanitizer 验收。

可复现单个 GUI 场景：

```bash
ASAN_OPTIONS=detect_leaks=1:abort_on_error=1:malloc_context_size=12 \
UBSAN_OPTIONS=halt_on_error=1 \
python3 -B apps/pageant/tests/run_gui_test.py \
  --binary .tmp/build/suite-pageant/asan/apps/pageant/tests/pageant-gui-tests \
  --puttygen .tmp/build/suite-pageant/suite-test-keygen/puttygen \
  --cli .tmp/build/suite-pageant/apps/pageant/pageant \
  --case serialPassphrasesAndReencrypt
```

日志是 `asan-final-core.log` 和 `asan-gui-<场景名>.log`，均在 `.tmp/build/suite-pageant/`。Qt offscreen 的 raise/托盘插件能力警告与真实桌面证据分开；没有忽略测试失败或 sanitizer 报告。

组件安装命令：

```bash
cmake --install .tmp/build/suite-pageant \
  --prefix /home/user/projects/putty/.tmp/build/suite-pageant/install --component pageant
```

核对仅安装两个预期文件；已安装程序在无图形条件下 --help 成功，真实 --gui 创建私有 XDG 子目录 0700 和 socket 0600，OpenSSH 添加/列表/清空、已安装 CLI 列表成功。启动探针独立进程最后终止；正常退出证据来自 Qt 场景，未将探针终止冒充正常退出。日志 `pageant-install.log`、`install-smoke.log`。未验证无 SDK 发布包依赖闭包。

中文实际窗口截图已查看，表格、菜单、按钮、状态及 socket 均可见，文字无方框：`.tmp/pageant-ui-evidence/cd6c3e51f1d0413487d28b567d179998.png`。这是离屏截图，不能替代真实桌面验收。

## 对照项状态与剩余边界

| 对照项 | 本轮结果和仍缺内容 |
| --- | --- |
| P01/P04/P05/P06/P07 | 本机公开列表、真实添加/延迟提示/重加密/删除及竞态有证据；目标平台完整矩阵待验 |
| P02/P08/P09/P10 | 核心已有 Ed25519/ECDSA/RSA/SSH1 添加和多证书、部分协议/扩展证据；不宣称全部算法/全部负例完成 |
| P03 | GUI 指纹格式切换尚未实现；当前 SSH2 SHA256、SSH1 可用 MD5，身份不依赖指纹 |
| P14/P15/P16/P17 | 原解析和业务源码保留，本轮已跑基础客户端、文件身份、--exec 和入口错误；完整参数、TTY/-X/永久/foreground/debug、指纹选择及 askpass 矩阵待补 |
| P18 | 默认中文/English、选择/提示/状态保持、无托盘控制逻辑已通过离屏；实体托盘/X11/纯 Wayland 待验 |
| P11/P12/P13、I04/I05/I12 | Windows host、双 IPC、旧窗口映射 owner/长度/权限、单实例、WSL 和托盘会话入口未实现或未验；Windows CI 本阶段保持 Pageant 关闭 |
| I06/I10 | 本机 OpenSSH 枚举/增删/签名和 agent-only SFTP、延迟并发/删除/退出有证据；目标 Ubuntu 及转发场景仍待补 |
| I01/I02/I03/I07/I08/I09/I11/I13/I14 | 四客户端完整新旧互操作、真正独立原版对端、真实第二跳转发、开关隔离、证书服务器认证、OpenSSH ssh-agent 逆向及发布包/实体桌面仍待补 |

GUI 提示不依赖 DISPLAY 字符串，可使用 Qt Wayland 后端；但纯 Wayland 实际运行尚未验证。原 CLI 的上游 `askpass` 仍以 DISPLAY 判断 GUI 可用性，因此“原 CLI 纯 Wayland GUI 提示”仍有明确适配缺口，没有通过改假 DISPLAY 宣称完成。翻译加载优先同目录 `translations/qtbase_zh_CN.qm` 再读 Qt 安装目录，发布任务仍需部署官方资源。

没有更改父/本任务复选框或任务状态，没有提交/push。后续由独立审查复核本轮 Linux 范围，再接 Windows 平台 host/双 IPC 阶段；基础 HKCU 隔离真验前仅编译，不接触个人存储。
