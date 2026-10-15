# Plink 命令行连接工具

本组件直接构建固定 PuTTY 0.85 的原版 Plink，不增加参数包装层。命令名、参数、标准流和远端退出状态沿用原版；帮助和脚本输出保留英文。

```bash
cmake -S . -B .tmp/build/plink -G Ninja -DPUTTY_BUILD_PUTTYGEN=OFF -DPUTTY_BUILD_PLINK=ON
cmake --build .tmp/build/plink --parallel 4
.tmp/build/plink/suite-cli/plink --help
```

仅构建 CLI 不依赖 Qt。Ninja Multi-Config 的程序位于 `suite-cli/<配置>/plink`；Windows 文件名带 `.exe`。安装只登记所选择的程序，测试用原版 PuTTYgen 不进入安装清单。

常见用法为 `plink -ssh 用户@主机 命令`，或 `plink -load 会话名 命令`。`-P` 指定端口，`-i` 指定 PPK 私钥，`-hostkey` 固定预期主机密钥；`-batch` 在需要人工确认时失败，适用于已经准备好认证和主机密钥策略的脚本。完整选项以 `--help` 和 `vendor/putty/doc/plink.but`、`using.but` 为准。

本机自动检查：

```bash
ctest --test-dir .tmp/build/plink --output-on-failure -L plink
```

Linux 检查在独立 `.tmp/` 中启动回环 SFTP 服务，不使用个人会话或代理。另启用 `PUTTY_SUITE_CONTAINER_TESTS=ON` 可验证远程命令、二进制标准流、退出码、PTY 和转发；该专项需要本机已有 Docker 镜像和相应权限，不下载镜像。原生 Windows 的运行检查需要逐进程注册表隔离，当前没有把 Linux 结果当成 Windows 验收。
