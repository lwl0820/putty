# PSCP 文件复制工具

本组件直接构建固定 PuTTY 0.85 的原版 PSCP，保留参数、SCP/SFTP 协议和原版输出行为。

```bash
cmake -S . -B .tmp/build/pscp -G Ninja -DPUTTY_BUILD_PUTTYGEN=OFF -DPUTTY_BUILD_PSCP=ON
cmake --build .tmp/build/pscp --parallel 4
.tmp/build/pscp/suite-cli/pscp --help
```

仅构建 CLI 不依赖 Qt。Ninja Multi-Config 的程序位于 `suite-cli/<配置>/pscp`；Windows 文件名带 `.exe`。安装只登记所选择的程序，测试工具不进入安装清单。

上传使用 `pscp 本地文件 用户@主机:远端路径`，下载时交换两侧参数。默认尝试 SFTP，`-sftp` 或 `-scp` 固定协议；`-r` 递归，`-p` 保留属性，`-q` 关闭进度，`-ls` 列出远端目录。SCP 路径还会经过远端 shell，包含空格时须为远端保留引用；SFTP 的引用语义不同。完整说明见 `vendor/putty/doc/pscp.but`。

本机自动检查：

```bash
ctest --test-dir .tmp/build/pscp --output-on-failure -L pscp
```

Linux 默认检查使用隔离 SFTP 对端，比较空文件、超过 1 MiB 的二进制、中文空格路径与递归目录的实际内容。`PUTTY_SUITE_CONTAINER_TESTS=ON` 增加专用容器账户中的真实 SCP、属性和通配符行为检查，需要本机已有 Docker 镜像和相应权限。测试不读取个人代理和会话。各目标系统和 Pageant 互操作仍须独立验收。
