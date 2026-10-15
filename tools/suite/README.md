# 套件测试隔离与本地对端

本目录提供 Linux 开发夹具。所有可写产物位于仓库 `.tmp/`；不修改 `HOME`、父进程环境、系统 SSH 服务或账户。它不构成任意程序的文件系统沙箱，只能运行已经核查路径行为的受测程序。

## 使用

```bash
python3 -B tools/suite/isolation.py probe
python3 -B tools/suite/ssh_fixture.py
python3 -B -m unittest discover -s tools/suite/tests -v
```

最后两条需要当前普通用户能监听 `127.0.0.1`，并具备 `sshd`、`ssh-keygen`、`sftp`；SSH 服务夹具还需要 Python 的 Linux `pidfd_open`、`pidfd_send_signal`。缺少 OpenSSH 工具时互操作测试明确跳过；服务启动、权限或实际传输错误会使测试失败。构造器先检查工具依赖，再查询当前 UID 的真实系统账户；没有账户记录时抛出说明原因的 `FixtureUnavailable`，不会伪造宿主账户。受限制执行沙箱中的 socket 拒绝属于未运行环境，不是协议通过。

当前普通夹具在本机实测 31 项通过。Ubuntu 22.04/24.04 构建容器缺少 `sshd` 时，真实 SSH/SFTP 测试组明确跳过；这些容器的构建或 CTest 通过不能记为目标平台 SSH 互操作通过。无 passwd 身份且缺依赖的构建容器也会先报告缺依赖，不会泄漏 `KeyError`。

```python
from tools.suite.isolation import IsolationEnvironment
from tools.suite.ssh_fixture import SSHServer

with IsolationEnvironment() as environment:
    report = environment.probe()
    with SSHServer(environment) as server:
        result = server.run_sftp("pwd\nquit\n")
        assert result.returncode == 0
```

`IsolationEnvironment(parent=...)` 支持不同构建使用不同 `.tmp/` 子目录，每次创建独立 `run-*` 私有根。父路径中的 `.tmp/` 可为安全的 `0755`；其后每层必须归当前用户所有且为 `0700`，已有不安全目录会被拒绝，不自动修权限。路径不能越界、包含 `..` 或经过符号链接。

## 环境与文件契约

- `environment()` 返回新字典，覆盖 `PUTTYDIR`、`PUTTYSESSIONS`、`PUTTYSSHHOSTKEYS`、`PUTTYRANDOMSEED`、`PUTTYSSHHOSTCAS`，以及 XDG 配置/数据/状态/缓存/运行目录和 `TMPDIR`、`TMP`、`TEMP`。
- 只在子进程字典中清除个人 SSH/GPG 代理及提示器变量。`HOME` 保持原值。显式传入 `agent_socket=...` 时只接受本次根目录内、当前用户的私有 Unix socket。
- `write_private(relative_path, bytes)` 独占创建 `0600` 普通文件；已有文件、符号链接和上级路径失败，不截断原文件。存储文件的所有者、权限及硬链接数在运行前重新核查。
- `run(argv, timeout=30, input=None, check=False)` 直接创建子进程，不拼接 shell，工作目录是本次根目录，使用 `0077` umask；保留字节标准流和退出码，超时抛出 `TimeoutExpired`。
- `probe()` 在真实 Python 子进程中核对所有覆盖值，并在每个预期目录创建/删除标记。报告包含 `upstream_storage_verified: false`：这是启动环境探针，不是上游 C 存储函数、个人文件未访问或注册表隔离的证据。
- `socket_path(name)` 验证 Linux Unix socket 长度。`close()` 可重复调用，校验根目录身份后使用防符号链接的清理实现。调用方须先关闭受控服务和子进程，再关闭隔离环境。

直接使用 `environment()` 创建自己的子进程时，还需显式指定 `cwd=environment.root`、`umask=0o077`，并自行管理子进程生命周期。XDG 运行目录覆盖会隔离已有 Wayland 合成器 socket；真实桌面测试需要单独受控合成器，不能把已有个人运行目录接回此夹具。

## SSH/SFTP 对端

`SSHServer(environment)` 提供 `host`、`port`、`user`、`private_key`、`host_key_fingerprint`、`transfer_root` 与 `run_sftp(batch, timeout=20)`。服务仅绑定 IPv4 回环随机端口，只接受本次临时 Ed25519 公钥，使用独立主机密钥与严格的客户端 known_hosts。配置、私钥、PID、客户端文件和日志全部位于本次私有根。

默认强制 `internal-sftp`，关闭 PAM、密码、用户 RC、代理/TCP/Unix socket/X11 转发。它不进入当前账户的登录 shell；SFTP 初始目录为 `transfer/`。它没有 chroot，受信任的测试只能请求本次隔离根内的文件。完整远端命令、SCP 协议和转发使用下节的显式容器夹具验证，不能用此普通用户对端冒充已完成。

OpenSSH 已认证子进程可能脱离监听者进程组。关闭时先停止本夹具监听者及其逐层核实的子进程，再用 pidfd 结束这棵子树，并等待结束；不扫描或终止个人代理/系统 SSH 服务。测试覆盖活动会话关闭后继续请求的写入被阻止。

## 专用账户的容器 SSH 对端

`container_ssh_fixture.py` 是显式选择的本机 Linux x64 开发夹具，不加入普通 runtime 的自动测试发现。默认根据宿主 `/etc/os-release` 选择本机已存在的匹配镜像：Arch 为 `archlinux:base`，Ubuntu 22.04/24.04 分别为 `ubuntu:22.04`/`ubuntu:24.04`。未知系统必须显式传入 `image=` 或 `--image`。镜像先 inspect 并固定本次 ID，创建使用 `--pull=never`，夹具不拉取或构建镜像；CI 必须在验证前自行准备匹配镜像。

sshd、系统 Python/bash/nologin 和默认客户端工具只从 `/usr/bin:/usr/sbin:/bin:/sbin` 查找，支持 Ubuntu 的 `/usr/sbin/sshd`；Docker 子进程及远端会话使用同一最小 PATH。容器只读绑定宿主 `/usr`、实际存在的 `/bin`、`/sbin`、`/lib`、`/lib64` 及普通文件 `/etc/ld.so.cache`，保留真实程序、动态加载器与库的路径布局；标准目录链接仅接受其自身或 `/usr` 内目标，不绑定个人目录。协议服务使用宿主 OpenSSH 及库，不能在 Arch 选择一个 Ubuntu 镜像就算 Ubuntu 服务证据。

```bash
python3 -B tools/suite/container_ssh_fixture.py
PUTTY_SUITE_CONTAINER_TESTS=1 python3 -B -m unittest discover -s tools/suite/container_tests -v
```

需要普通用户 UID、本机 `/var/run/docker.sock` 访问权限、系统 OpenSSH 服务端/客户端、Python/bash/nologin 及已有匹配镜像。Docker 客户端指定本次 `.tmp/` 内独立 `--config`，不会读取个人 Docker 配置；只操作带随机名和本次标签的容器。测试载荷、专用账户文件、密钥、日志、Docker 客户端配置和运行目录位于 `.tmp/suite-container-fixture/`。Docker 自身的容器创建/删除元数据由守护进程管理，daemon 日志驱动为 `none`。

容器使用 `--read-only --network none --cap-drop ALL --security-opt no-new-privileges`，以当前普通用户 UID/GID 运行，并设置进程数、内存和 CPU 上限。没有 `privileged`、Docker socket、宿主个人目录或整个项目挂载。除上述只读系统程序/库以外，其余挂载源均位于本次私有运行根。自建 passwd/group/shadow/nsswitch 使 `suite` 账户的 home 和 shell 启动文件落在本次 `.tmp/`，不进入宿主用户 shell。

sshd 仅监听容器回环 `127.0.0.1:22222`，容器没有外部网络或 Docker 端口发布。宿主仅绑定 `127.0.0.1` 随机端口，通过 `docker exec -i` 的字节桥访问容器回环。SSH 允许真实命令、SCP/SFTP，以及只以容器回环为目标/监听地址的 TCP 转发；密码、PAM、用户 RC、代理/X11/Unix socket 转发关闭。

```python
from tools.suite.isolation import IsolationEnvironment
from tools.suite.container_ssh_fixture import ContainerSSHServer, DEFAULT_CONTAINER_PARENT

with IsolationEnvironment(DEFAULT_CONTAINER_PARENT) as environment:
    with ContainerSSHServer(environment) as server:
        result = server.run_exec("/usr/bin/printf 'controlled output'")
        assert result.returncode == 0
        echo_port = server.start_echo_server()
```

公开属性包括 `host`、`port`、`user`、`private_key`（OpenSSH 格式）、`host_key_fingerprint`、`client_config`、`remote_root`、`remote_home`、`container_id`、`image_id`。文件根在容器和宿主中使用相同绝对 `.tmp/` 路径。`run_exec(command, timeout=30, input=None)` 接受显式的远端 shell 命令字符串，保留二进制标准流与退出码；调用方只执行受控测试命令。`run_sftp(batch)` 使用独立严格 known_hosts；`ssh_arguments(command=None)` 为需要管理长连接的测试提供客户端参数。

`start_echo_server()` 返回容器 `127.0.0.1` 上的回显端口，用作 SSH `-L` 目标；它不是宿主监听端口。`isolation_report()` 读取实际 Docker inspect 的挂载与限制，并给出选择镜像、sshd 路径、宿主系统及只读系统挂载计划。`close()` 关闭宿主代理，再按本次容器 ID 删除容器，并回收本次 Docker relay 子进程；该操作同时结束已经脱离 sshd 进程组的活动远端命令。

本机 Arch 独立可选测试已实测 **6 个真实用例通过、0 跳过**：实际限制及全部系统挂载、容器与宿主 sshd/Python/加载器 SHA256 一致、专用账户 HOME/cwd/TMPDIR/PATH、二进制 stdout/stderr 与退出 7、OpenSSH `scp -O` 的 1 MiB 往返、SFTP、256 KiB 回环转发，以及活动命令关闭/容器删除/代理回收。另有 3 个不启动 Docker 的创建接线回归，验证 Ubuntu 两版镜像和 sbin 入口、未知系统显式要求及镜像 ID 固定；静态回归不代表 Ubuntu 实际运行通过。最终执行记录见基础任务 `research/ubuntu-container-fixture.md`。PuTTY 三 CLI 的验收由组件专项测试单独记录；`isolation.py`、`ssh_fixture.py` 和 GSS 子类公开 hooks 没有改变。

Docker daemon 必须与运行夹具的宿主共享系统文件命名空间。把外层 Arch 的 Docker socket 接入一个 Ubuntu 容器，daemon 的 `/usr` 仍可能是 Arch，不能作为 Ubuntu 宿主验证；真实用例以程序及加载器字节摘要拒绝这种错配。本轮 Ubuntu 实际宿主运行留待仓库的 Ubuntu 22.04/24.04 CI，不能从本机 Arch 结果推断通过。

## 尚未开放的入口

- 固定上游 Unix Pageant 的 `/tmp/pageant` 不受 `TMPDIR` 控制，`run()` 对 Pageant 名称直接拒绝。后续必须实现并验证显式测试目录前缀，再单独开放；不能通过更名或包装器绕过此门。
- Windows 直接拒绝使用此 Linux 夹具；环境变量不是 HKCU 隔离。每个 Windows 原版/自有子进程都需要专用身份或有效 hive 覆盖，以及随机种子、配置、端点的实际探针。独立的 [Windows CI 无存储宿主](windows/README.md) 不开放持久化入口。
- 环境探针、缺依赖时的跳过和本机 SFTP 通过不能替代上游存储与目标平台验证。运行时和三个 CLI 的专项记录另有真实存储及两个 Ubuntu 受测客户端的有限互操作结果，不能从这些结果推断全参数、全部平台或完整桌面已验收。
