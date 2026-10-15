# CLI 专用 SSH 容器对端实施记录

日期：2026-10-15。新增源码仅为 `tools/suite/container_ssh_fixture.py` 与 `tools/suite/container_tests/test_container_ssh_fixture.py`，说明补入 `tools/suite/README.md`。未改现有 `isolation.py` / `ssh_fixture.py` 公共接口、apps、CMake 或任务状态。

## 实际方案与证据

使用本机已存在的 `archlinux:base`（检查时镜像 ID 前缀 `3c64b5385884`），创建时固定完整镜像 ID，不下载镜像。只读绑定本机 `/usr` 获得匹配的 OpenSSH 10.6p1 / OpenSSL 3.6.5；其余宿主绑定源全部位于本次 `.tmp/suite-container-fixture/run-*`。专用 passwd/group/shadow/nsswitch 文件定义 `suite` 账户，UID/GID 是当前普通用户，home 与工作文件均为本次私有目录。

实际 Docker inspect 验证：只读根、`NetworkMode=none`、`CapDrop=[ALL]`、`Privileged=false`、`no-new-privileges`、非 root UID/GID、日志驱动 `none`。没有宿主个人目录、整个项目、Docker socket 或 host 网络挂载。CPU、内存和进程数有上限。

sshd 只监听容器 `127.0.0.1:22222`。宿主随机回环端口通过 `docker exec -i` 的受控字节流 relay 连接容器回环；没有 Docker 发布端口或外部容器网络。这是真实容器 OpenSSH 的 SSH/SCP/SFTP 和 TCP 转发协议，宿主 relay 只搬运字节。

Docker CLI 指定项目 `.tmp/` 内独立配置，清除继承的 Docker 环境选择；不读取个人 Docker 配置。密钥、账户数据、运行目录、辅助脚本和日志均位于本次私有根。Docker 守护进程正常创建/删除容器的内部元数据由 Docker 管理，daemon 日志关闭。

## 实际验证

最小原型先证明普通用户账户、真实 SSH 横幅和精确二进制命令：退出 7，stdout 为 `out` 加 NUL/0xff（5 字节），stderr 为 `err` 加 0xfe（4 字节）。随后落地模块，并运行：

```bash
python3 -B tools/suite/container_ssh_fixture.py
PUTTY_SUITE_CONTAINER_TESTS=1 python3 -B -m unittest discover -s tools/suite/container_tests -v
```

结果：模块真实命令 smoke 通过；**6 项测试通过，0 失败，0 跳过，10.378 秒，退出 0**。执行需要 `require_escalated`，操作范围为本次 Docker 容器、项目 `.tmp/` 与宿主回环监听。

| 用例 | 实际结果 |
| --- | --- |
| 实际容器限制及挂载、根目录写入拒绝 | 通过 |
| 专用账户 HOME/cwd/TMPDIR、二进制 stdout/stderr、退出 7 | 通过 |
| OpenSSH `scp -O` 强制旧 SCP 协议的 1 MiB 二进制往返摘要 | 通过 |
| 独立 SFTP 子系统上传 | 通过 |
| SSH `-L` 到容器回环回显，256 KiB 二进制一致 | 通过 |
| 活动远端命令关闭、容器消失、宿主 relay 结束、重复关闭及停止接入 | 通过 |

两份新增 Python 文件 AST 语法检查通过；收尾时 `.tmp/suite-container-fixture/` 中运行目录数为 0。没有将 Docker 测试加入普通 Linux SFTP 夹具的默认发现。该公共夹具随后单独修复无 passwd 身份的构建容器初始化顺序，最终本机回归 31 项通过；详见基础夹具实施记录。

## 调用契约

`with IsolationEnvironment(DEFAULT_CONTAINER_PARENT) as environment: with ContainerSSHServer(environment) as server:`。公开属性包括 `host`、`port`、`user`、OpenSSH 格式 `private_key`、`host_key_fingerprint`、`client_config`、`remote_root`、`remote_home`、`container_id`、`image_id`。

`run_exec(command, timeout=30, input=None)` 保留字节流与退出码；命令字符串由受控测试明确提供。`run_sftp(batch)` 使用严格的独立 known_hosts。`ssh_arguments(command=None)` 支持测试自行管理长连接。`start_echo_server()` 返回容器回环端口，供 Plink/OpenSSH 的 `-L` 目标使用。`isolation_report()` 提供实际 Docker inspect 的安全限制。`close()` 精确删除本次容器 ID 并回收本次 host relay，不查找或终止个人服务。

已将 API 与实际通过情况发送给 CLI 实施代理，供 Plink 二进制输出/退出码、PSCP `-scp` 与转发专项使用；本报告只证明测试对端能力，不替代三个 PuTTY CLI 的组件验收。

## 验收边界

验证环境为 Arch Linux 宿主 `/usr`、Linux x86_64、Python 3.14.8、OpenSSH 10.6p1 的受控容器。**不是 Ubuntu 22.04/24.04 或 Windows 的原生运行证据**。主会话的两个 Ubuntu 构建容器缺少 `sshd`，公共夹具的真实 SSH/SFTP 组明确跳过，不以构建/CTest 通过替代互操作证据。Windows 的逐进程 HKCU、随机种子与 IPC 隔离仍缺；原 Pageant 测试目录前缀、代理认证/转发另行实施。基础和 CLI 整任务均保持原状态，不在此标记完成。
