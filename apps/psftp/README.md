# PSFTP 交互文件传输工具

本组件直接构建固定 PuTTY 0.85 的原版 PSFTP，保留交互命令、批处理、参数和原版标准流行为。

```bash
cmake -S . -B .tmp/build/psftp -G Ninja -DPUTTY_BUILD_PUTTYGEN=OFF -DPUTTY_BUILD_PSFTP=ON
cmake --build .tmp/build/psftp --parallel 4
.tmp/build/psftp/suite-cli/psftp --help
```

仅构建 CLI 不依赖 Qt。Ninja Multi-Config 的程序位于 `suite-cli/<配置>/psftp`；Windows 文件名带 `.exe`。安装只登记所选择的程序，测试工具不进入安装清单。

`psftp 用户@主机` 打开交互会话，`help` 查看命令；`put/get` 上传下载，`mput/mget` 使用通配符，`reput/reget` 续传，`-r` 递归目录。包含空格的交互路径使用双引号。`-b 文件` 执行批处理，默认命令失败返回 2；`-be` 继续后续命令，最终可能返回 0，调用脚本应按原版语义处理。`-bc` 回显批处理命令，`-batch` 禁止连接阶段人工确认。完整说明见 `vendor/putty/doc/psftp.but`。

本机自动检查：

```bash
ctest --test-dir .tmp/build/psftp --output-on-failure -L psftp
```

Linux 检查在独立 `.tmp/` 中运行真实 SFTP 对端，验证全部 28 个命令的帮助入口，以及传输、续传、目录、改名、权限、删除、会话关闭重开和失败行为。文件、会话和主机密钥缓存均受控，不使用个人代理。Windows 和两个目标 Ubuntu 的结果须独立记录。
