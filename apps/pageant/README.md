# Pageant Qt 密钥代理

Linux 桌面入口是同一程序的 `pageant --gui`；无参数和其它参数保留原版 Unix Pageant 的解释与退出状态。安装组件为 `pageant`，包含 `bin/pageant` 和启动 `--gui` 的桌面入口。

窗口默认中文，可在语言菜单切换 English，偏好只保存语言。密钥列表展示原代理公开身份，选择按 SSH 协议版本和完整公钥 blob 保留；窗口不接触上游私钥指针。添加、加密存储添加、删除、清空、单项及全部重新加密均调用原 `pageant.c`。口令框异步串行显示，错误口令允许重试，取消、删除和退出撤销旧请求。

“添加加密密钥”保留可延迟解密的原 PPK；使用时提示口令。“重新加密”清除可重新加载的明文私钥，不修改磁盘文件，也不修改其口令。原 Pageant 不直接加载 OpenSSH 私钥文件；可先用 PuTTYgen 转换为 PPK，OpenSSH `ssh-add` 的协议添加仍可用。

有托盘时关闭列表会隐藏窗口，可从托盘恢复；无托盘时关闭提供保持运行或退出，保持运行会保留窗口。托盘服务消失后会重新显示窗口。退出等待核心撤监听、关闭已接收连接、取消提示并清除内存身份。

GUI 在当前用户独占的 `XDG_RUNTIME_DIR` 中创建私有随机子目录及 0600 socket，并在列表中展示可复制的 `SSH_AUTH_SOCK` 地址。它不改 shell 或 SSH 配置。原 CLI 保留原 `/tmp/pageant` 前缀，项目测试只启动编译注入 `.tmp` 前缀的专用变体；环境 `TMPDIR` 不能代替该编译隔离。

## Windows 适配

Windows 入口使用同一个 Qt 密钥窗口。生产命名管道沿用 `agent_named_pipe_name()`、上游私有 ACL 和首实例排他；隐藏顶层窗口保留 `Pageant` 类名和标题，接受原 `WM_COPYDATA` 共享映射 IPC，并检查映射所有者为当前用户 SID 或原版兼容 owner SID。兼容窗口由辅助线程运行，字节通过核心 HANDLE 等待交给原代理；签名和口令均不在 GUI 线程同步等待。退出先唤醒旧 IPC 等待者，再撤销请求、连接和密钥。

启动先持有原版互斥锁，发现已有代理后复用；首次启动保持锁直到两条 IPC 就绪。二次启动无动作时提示已运行并退出；带 PPK 时仅使用原核心的客户端操作向已有代理添加，结束后退出，不清空对端。当前 Windows 参数接入 `--gui`、`-keylist`、裸 PPK 和原 `-encrypted`/`-no-decrypt` 同义选项；`-c`、`-openssh-config`、`-unix`、ACL 限制选项等完整参数尚未接入。本机没有 Windows SDK，本段描述实现路径，仍待 MSVC 构建及原生行为验收。

二次启动的同步查询留在唯一核心线程。私有传输使用本地非阻塞管道和带超时的旧窗口消息；连接、完整请求写出、响应前缀及正文读取、旧窗口回退共享单次 8 秒预算。短读和短写完整累计，管道 owner 必须为当前用户，并核验本地服务进程。传输失败后本次客户端进程快速失败，重新启动才会重连；远端不可用不会显示成成功的空密钥库。GUI 关闭立即返回，挂起查询在超时安全点后收尾；8 秒是单次查询预算，不是任意密钥计算或整个命令队列的退出上界。

Windows IPC 测试是独立无存储宿主，使用随机编译前缀加每次运行随机后缀，不会探测或占用个人 Pageant。生产程序没有环境变量改名入口。runner 在执行前审计 MSVC 链接图，禁止抽入原设置、种子、客户端 PRNG 和 Windows 重噪声对象；原 Pageant 的 `random_read` 致命拒绝实现保留。测试只加载编译内固定 RFC8032 向量，不读取用户密钥、设置或注册表。测试更名传输不能代替专用身份下的原名跨版本互操作。

原生 Windows 构建及运行入口如下，Qt 路径由仓库 CI 的固定配置提供：

```powershell
cmake -S . -B .tmp/build/suite-pageant-windows -DPUTTY_BUILD_PUTTYGEN=OFF -DPUTTY_BUILD_PAGEANT=ON -DBUILD_TESTING=ON
cmake --build .tmp/build/suite-pageant-windows --config Debug --target pageant pageant-windows-ipc-tests
ctest --test-dir .tmp/build/suite-pageant-windows -C Debug --output-on-failure -R '^pageant-windows-'
```

`pageant-windows-ipc` 需原生 Windows，失败时不能跳过；其 15 项场景覆盖双 IPC 枚举、交叉管理、真实 Ed25519 签名验签、畸形输入、延迟解密的取消和成功、在途退出及端点回收，并以独立进程覆盖 128 KiB 请求的部分写入、逐字节响应、管道和旧窗口不响应时的客户端关闭。JSON 报告位于 `.tmp/windows-ci/reports/pageant-ipc.json`。`pageant-windows-runner-contract` 是可在 Linux 执行的三个 runner 门禁测试，不算原生 Windows IPC 通过。

## 验证

在根工程启用 `PUTTY_BUILD_PAGEANT=ON` 和 `BUILD_TESTING=ON` 后运行：

```bash
cmake --build .tmp/build/suite-pageant --target pageant pageant-core-host \
  pageant-prompt-failure pageant-gui-tests pageant-entry-tests suite-test-keygen-build
ctest --test-dir .tmp/build/suite-pageant --output-on-failure -L pageant
```

核心测试使用真实 OpenSSH 添加、列表、签名验签和受控 SFTP 认证；Qt 测试使用真实核心与密钥操作，默认离屏。原 CLI 服务测试仅使用专门编译的项目 `.tmp` 前缀。所有测试清除个人代理变量，密钥、配置、socket 和截图位于 `.tmp`。

当前证据覆盖本机 Linux 核心、Qt 离屏和原 CLI；不代表目标 Ubuntu、X11、纯 Wayland 或真实桌面托盘已经验收。Qt 标准对话框翻译优先读取同目录 `translations/qtbase_zh_CN.qm`，再读取 Qt 安装目录，发布包仍须提供该资源。Windows 原生双 IPC、新旧版本原名先后启动、跨权限、客户端挂起后的取消、同包 PuTTY 新/保存会话入口、完整原参数矩阵和真实第二跳代理转发仍待验收。
