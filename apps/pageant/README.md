# Pageant Qt 密钥代理

Linux 桌面入口是同一程序的 `pageant --gui`；无参数和其它参数保留原版 Unix Pageant 的解释与退出状态。安装组件为 `pageant`，包含 `bin/pageant` 和启动 `--gui` 的桌面入口。

窗口默认中文，可在语言菜单切换 English，偏好只保存语言。密钥列表展示原代理公开身份，选择按 SSH 协议版本和完整公钥 blob 保留；窗口不接触上游私钥指针。添加、加密存储添加、删除、清空、单项及全部重新加密均调用原 `pageant.c`。口令框异步串行显示，错误口令允许重试，取消、删除和退出撤销旧请求。

“添加加密密钥”保留可延迟解密的原 PPK；使用时提示口令。“重新加密”清除可重新加载的明文私钥，不修改磁盘文件，也不修改其口令。原 Pageant 不直接加载 OpenSSH 私钥文件；可先用 PuTTYgen 转换为 PPK，OpenSSH `ssh-add` 的协议添加仍可用。

有托盘时关闭列表会隐藏窗口，可从托盘恢复；无托盘时关闭提供保持运行或退出，保持运行会保留窗口。托盘服务消失后会重新显示窗口。退出等待核心撤监听、关闭已接收连接、取消提示并清除内存身份。

GUI 在当前用户独占的 `XDG_RUNTIME_DIR` 中创建私有随机子目录及 0600 socket，并在列表中展示可复制的 `SSH_AUTH_SOCK` 地址。它不改 shell 或 SSH 配置。原 CLI 保留原 `/tmp/pageant` 前缀，项目测试只启动编译注入 `.tmp` 前缀的专用变体；环境 `TMPDIR` 不能代替该编译隔离。

## 验证

在根工程启用 `PUTTY_BUILD_PAGEANT=ON` 和 `BUILD_TESTING=ON` 后运行：

```bash
cmake --build .tmp/build/suite-pageant --target pageant pageant-core-host \
  pageant-prompt-failure pageant-gui-tests pageant-entry-tests suite-test-keygen-build
ctest --test-dir .tmp/build/suite-pageant --output-on-failure -L pageant
```

核心测试使用真实 OpenSSH 添加、列表、签名验签和受控 SFTP 认证；Qt 测试使用真实核心与密钥操作，默认离屏。原 CLI 服务测试仅使用专门编译的项目 `.tmp` 前缀。所有测试清除个人代理变量，密钥、配置、socket 和截图位于 `.tmp`。

当前证据覆盖本机 Linux 核心、Qt 离屏和原 CLI；不代表目标 Ubuntu、X11、纯 Wayland 或真实桌面托盘已经验收。Qt 标准对话框翻译优先读取同目录 `translations/qtbase_zh_CN.qm`，再读取 Qt 安装目录，发布包仍须提供该资源。Windows 双 IPC、同包 PuTTY 新/保存会话入口、完整原参数矩阵和真实第二跳代理转发留在后续阶段。
