# Linux 隔离与 SSH/SFTP 夹具实施记录

日期：2026-10-15。所有权：`tools/suite/`，未修改根 CMake、运行时、产品或任务状态。

## 已实现

| 文件 | 责任 |
| --- | --- |
| `tools/suite/isolation.py` | `.tmp/` 私有运行根、五个 PuTTY 存储覆盖、XDG/TMP 覆盖、代理环境清理、真实子进程环境探针、Pageant/Windows 拒绝门、权限/链接校验及清理 |
| `tools/suite/ssh_fixture.py` | 普通用户回环 OpenSSH、临时公钥认证、严格主机密钥检查、进程内 SFTP 和 pidfd 子树关闭 |
| `tools/suite/tests/test_isolation.py` | 子进程环境/二进制标准流/退出/超时、私有 umask、越界/链接/权限、socket、关闭与拒绝门 |
| `tools/suite/tests/test_ssh_fixture.py` | 真实 SSH 横幅/配置、认证和主机密钥拒绝、1 MiB 二进制与中文空格路径往返、空文件/改名/权限/删除、批失败、关闭与活动连接 |
| `tools/suite/README.md` | 可复用 API、运行命令、边界及未执行项 |

根 CMake 的 `suite-fixture` 注册由构建代理负责；命令为本目录 unittest discovery，标签 `runtime;suite;isolation`。本记录不代表已运行主会话最终集成 CTest。

## 实际验证

命令：`python3 -B -m unittest discover -s tools/suite/tests -v`。

最终回归结果：**31 项通过，0 失败，0 跳过，2.318 秒，退出 0**。本地 socket 被执行沙箱限制，实际 SSH/Unix socket 测试通过 `require_escalated` 运行，范围为项目 `.tmp/` 和本次回环服务。此前首轮为 30 项通过；新增第 31 项检查缺少系统账户身份时的错误边界。

另执行：

- `python3 -B tools/suite/isolation.py probe`：真实子进程环境探针通过；报告明确 `upstream_storage_verified: false`。
- `python3 -B tools/suite/ssh_fixture.py`：本机普通用户服务启动、真实认证与 SFTP 请求通过，报告明确 `shell_commands_verified: false`。
- `git diff --check -- tools/suite`：无空白错误（新增文件内容另经 Python 实际运行加载）。

本机使用 UID 1000 普通用户、Linux x86_64、Python 3.14 与 OpenSSH 10.6p1。它不是 Ubuntu 22.04/24.04 或 Windows 平台验收。

## 实际关闭缺陷与修复

真实活动会话对照发现：监听者与已认证子进程拥有不同进程组。仅结束监听者进程组后，已有 SFTP 连接仍能创建 `survived-close` 文件。修复为停止并逐层核验本夹具子树、以 pidfd 结束并等待；同一关闭后写入被阻止。

SFTP 客户端在没有待决请求时收到关闭，可能正常返回 0，因此用真实关闭后文件请求和未生成文件验证断开，不能仅断言退出码非零。测试中的实际传输/认证失败仍检查非零及相应结果。

## 构建容器身份缺失回归

主会话的 Ubuntu 22.04 构建容器使用 `--user 1000:1000`，但没有对应 passwd 项且未安装 `sshd`。原构造器在核对依赖前查询账户，导致缺依赖失败测试抛出裸 `KeyError`。现先检查 OpenSSH 依赖；依赖齐全后才查询真实身份，缺账户记录转换为现有公开 `FixtureUnavailable` 并明确原因，不创建或伪造宿主账户。

回归用例在缺依赖和缺 passwd 同时存在时断言账户查询从未调用；另检查依赖齐全但无账户记录时的解释性拒绝，二者均核实没有创建服务文件。独立运行 2 项通过，完整回归 31 项通过。主会话反馈 Ubuntu 22.04/24.04 构建容器未安装 `sshd`；真实 SSH/SFTP 组按依赖检查明确跳过，不是目标平台互操作成功。

## 验收映射与缺口

覆盖本子任务 R4 / AC4 的 Linux 环境和受控对端部分；支持 AC3 的真实 SSH/SFTP 对端。当前没有证明所有原版/自有可执行文件内部的实际存储路径或个人文件访问情况；没有把环境探针当成此项通过。

Windows HKCU/hive、随机种子和 IPC 的逐进程探针尚未实施，本工具直接拒绝 Windows。原 Pageant 的系统临时前缀未安全适配，禁止运行其默认路径。完整远端命令、SCP、转发由 CLI 专项新增的独立容器夹具验证；默认对端仍强制进程内 SFTP，避免个人 shell/RC。两个 Ubuntu 目标系统的真实 SSH 互操作及原生桌面/Windows 验证仍未执行。

所有密钥和服务按 context 结束；报告不包含私钥、口令或个人代理内容。未安装系统依赖、修改系统服务/账户、提交代码或更新任务状态。
