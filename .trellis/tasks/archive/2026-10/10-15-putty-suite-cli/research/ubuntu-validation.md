# 两目标 Ubuntu 的 CLI 原生执行记录

日期：2026-10-15。由主会话补充，区别于 `implementation.md` 的 Arch 本机 68 项测试。本次受测二进制实际在各自 Ubuntu 镜像内运行，不是在宿主直接执行 Ubuntu 文件。

## 环境与方法

受测产物来自两版目标系统的 Release 构建与精确安装，分别位于 `.tmp/stage/suite-ubuntu22-cli/bin`、`suite-ubuntu24-cli/bin`。使用已有 `puttygen-build-ready:ubuntu2204`、`ubuntu2404` 镜像、非 root uid/gid、只读根、禁提权、删除全部 capabilities。每次仅绑定只读 CLI 目录和本次私有 `.tmp/` 根，临时路径也绑定本次目录。保留继承的 HOME 值但不挂载个人目录；全部 PuTTY、XDG 与临时目录覆盖和代理清理来自公共隔离器。

SSH 对端为已经独立验证的专用账户 Arch 容器，网络为 none，由宿主回环 relay 连接。受测 Ubuntu 容器使用 host 网络，仅连接该明确的 `127.0.0.1` 端口；没有修改系统 sshd、账户或个人 shell。测试密钥、PPK、配置、传输与日志均在隔离根，关闭后删除容器及私有根。测试用原版 puttygen 只转换临时密钥，不进入安装清单。

脚本 `.tmp/suite-validation/validate_cli_native.py`，结果 `.tmp/suite-validation/ubuntu-cli-native.log`。首次测试启动 Docker 时遗漏 `--interactive`，导致输入未送入容器，收到空 stdout；这是测试启动参数错误。补上该参数后重新运行完整场景得到以下结果，未修改产品代码或放宽字节断言。

## 实际结果

| 场景 | Ubuntu 22.04 x64 | Ubuntu 24.04 x64 |
| --- | --- | --- |
| Plink 传入并回传 1,048,584 字节，stdout 精确匹配，独立 NUL/非 ASCII stderr，远端退出 17 | 通过 | 通过 |
| 错误主机密钥明确失败，退出 1 | 通过 | 通过 |
| PSCP 强制 SFTP 中文空格路径双向传输，文件逐字节比较 | 通过 | 通过 |
| PSCP 强制 SCP 中文空格路径双向传输，文件逐字节比较 | 通过 | 通过 |
| PSFTP 批量 put/get 中文空格路径，文件逐字节比较 | 通过 | 通过 |
| PSFTP 非法命令批次退出 2 | 通过 | 通过 |

每个系统实际执行 **8 次 CLI 进程，共 16 次，脚本退出 0**。三种传输与 Plink 使用的测试材料 SHA256 均为 `b155ec5f8f3e10209f39e77ec81bb87fdae2afa8722f4401daa21221b505a2b0`。两个系统此前的禁 Qt 构建、三文件安装清单、版本和全部关闭配置也退出 0，日志与基础 `platform-validation.md` 对应。

本轮只验证上述场景，不把它扩写为本机全部 68 项已在 Ubuntu 运行；没有验证 Ubuntu 服务端、桌面、Windows、完整参数组合或 Pageant 互操作。C05 等缺口保持在参数状态清单和父任务中。
