# Pageant 核心阶段实测

日期：2026-10-15。当前为实施阶段记录，不代表整个 Pageant 或任何 Windows/目标 Ubuntu 桌面验收通过。

## 已落地

`apps/pageant/src/core/pa_agent.*` 复用固定 0.85 `pageant.c` 的监听、消息、签名、扩展及管理 API。所有上游对象只在 `putty-runtime` 的唯一核心线程使用。C 边界只传定长头和有界尾随字节；身份是 SSH 协议版本和完整公钥 blob，GUI 层不持私钥或上游指针。公开快照包含原注释、指纹和延迟/明文状态；SSH1 使用原可用 MD5 指纹，SSH2 使用 SHA256。

添加支持 PPK 和原 SSH1 文件；延迟加载、交互添加、删除、全部删除、单项/全部重加密均调用原 API。原版不接受 OpenSSH 私钥文件作为“添加文件”；OpenSSH `ssh-add` 的协议添加独立验证。批次口令缓存每次添加后清除。提示使用运行时代次与一次性请求编号，错误回复会生成新编号，删除使旧回复过期。

Linux 只接受显式 socket 位置。逐层 `openat(O_NOFOLLOW)` 拒绝符号链接和 `..`，父目录须同 UID 且无组/其它权限，既有路径拒绝；socket 为 0600。测试显式位于项目 `.tmp`，没有启动原硬编码 `/tmp/pageant` 代理。生产 GUI 的 XDG 私有目录入口随后接入。

核心驱动 `pageant-core-host` 和原客户端 vtable 分支驱动 `pageant-prompt-failure` 不安装。标准协议测试端只构造客户端请求，不实现代理签名或替代上游解析。SFTP 认证删除全部 IdentityFile/个人代理，只配置此新 socket；添加前失败，添加后成功。

## 真实发现及最小修复

- `0004-pageant-test-prefix.patch`：`PAGEANT_DIR_PREFIX` 增加编译覆盖保护；原默认 `/tmp/pageant` 不变。后续原 CLI 对照只在测试目标编译项目 `.tmp` 前缀。
- `0005-pageant-connection-lifecycle.patch`：accepted 连接生命周期跟踪；新增显式 `pageant_listener_shutdown_connections`，先撤监听再取消全部连接及在途请求。保留原 `free` 语义，未 shutdown 的路径会安全脱离跟踪链；断开后释放尚未回复的队列项。
- 两把加密 key 同时签名，第一把显示提示，第二把等待 GUI；此时删除全部 key，原 `requests_blocked_on_gui` 的操作仍持已释放私钥，恢复后在 `signop_coroutine` 的 `assert(so->priv->encrypted_key_file)` 崩溃。修复令 `fail_requests_for_key` 同时沿原失败/取消路径处理该链，并撤销排队回调。
- 提示中外部删除最后公钥后取消，原 moribund 私钥未释放；success 的 moribund 及其它失败早退也不唤醒下一把等待提示的 key。修复保持单提示锁，只有未创建新提示时唤醒待 GUI 操作。
- ASan 以一把明文 Ed25519 key 复现 908 字节/11 次分配泄漏：公钥 `base_pub` 和 ADD_IDENTITY 临时输入未释放。原 add 函数会复制输入，修复保留原清理块并覆盖 SSH1/SSH2/明文 PPK 扩展三条成功路径。修前原始证据：`.tmp/build/suite-pageant/asan-leak-before-fix.log`。

上述上游修改均经父代理明确批准，只改三个已分配 vendor 文件，不修改密码算法。公共来源记录由父代理维护。

## 执行记录

本机 Arch Linux x64，GCC 16，Qt 6.12.0；这不是发布锁 Qt 6.8.3 或 Ubuntu 原生证据。

```bash
cmake -S . -B .tmp/build/suite-pageant -G Ninja -DCMAKE_BUILD_TYPE=Debug \
  -DPUTTY_BUILD_PUTTYGEN=OFF -DPUTTY_BUILD_PAGEANT=ON -DBUILD_TESTING=ON
cmake --build .tmp/build/suite-pageant --target pageant-core-host pageant-prompt-failure -j 4
python3 -B apps/pageant/tests/agent_checks.py \
  --host .tmp/build/suite-pageant/apps/pageant/tests/pageant-core-host \
  --branches .tmp/build/suite-pageant/apps/pageant/tests/pageant-prompt-failure \
  --puttygen .tmp/build/suite-cli-tests/suite-test-keygen/puttygen
```

正常构建 15 项在加入 SSH1/明文扩展项前通过，3.742 秒；最终 sanitizer 构建包含 16 项，4.195 秒，退出 0。日志分别为 `core-tests.log`、`asan-tests.log`。Sanitizer 配置另置 `.tmp/build/suite-pageant/asan`，C/C++ 使用 `-fsanitize=address,undefined -fno-omit-frame-pointer`，链接相同 sanitizer；执行环境 `ASAN_OPTIONS=detect_leaks=1:abort_on_error=1:malloc_context_size=12 UBSAN_OPTIONS=halt_on_error=1`，无抑制文件。

16 项涵盖：socket 权限/空列表；OpenSSH 添加/列表/签名验签/删除；Ed25519/ECDSA/RSA PPK 双向身份与签名；延迟解密/错误重试/拒绝/重加密；交互添加取消/重试；外部删除后成功或取消且第二把恢复；客户端断开后错误口令且第二把恢复；删除全部与旧回复不复活；活动/半包/在途提示退出；损坏延迟私有数据且第二把恢复；同私钥多证书分别删除；真实 agent-only SFTP；格式错误/不能重加密的 key；原客户端第二次 `ask_passphrase` 返回 false 后第二把真实签名；明文 PPK 扩展与 SSH1 临时对象释放。

Qt 接入后的增量：新增 `PA_DISMISS`，撤销事件与操作结果一起有界保留到宿主队列可写，避免背压留下旧对话框。`PR_COMMAND_REPLY` 的编号已由运行时消费，非法载荷也沿取消路径结束内部提示，不保留无法回复的对象。新增第 17 项：错误 magic 的一次性回复被拒绝，第一把请求结束，第二把真实 OpenSSH 签名恢复。正常构建最终随 Pageant CTest 通过 17 项，整组 CTest 为 9/9、3.81 秒；最终核心 ASan/UBSan 17/17、5.223 秒，仍启用泄漏检测且无抑制，日志 `asan-final-core.log`。Qt、CLI 入口和安装实测见同目录 `gui-implementation.md`。

## 阶段边界

Qt 窗口和同程序原 CLI 分流已接入并有本机离屏证据；真实桌面托盘、固定原版完整双向对端、完整原版参数矩阵、代理转发第二跳、同包 PuTTY 会话入口及目标平台桌面仍未完成。Windows 需独立命名管道和旧窗口 IPC 适配、原 SID/映射长度检查、原互斥实例生命周期；旧窗口等待线程必须与唯一核心线程分离。公共运行时已有网络桥，当前没有提出新公共 API。Windows 原生环境由仓库 CI 和用户桌面验收接续，Linux 证据不替代它。
