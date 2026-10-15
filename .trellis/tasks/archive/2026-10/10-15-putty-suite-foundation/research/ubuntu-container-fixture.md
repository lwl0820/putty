# 容器 SSH 夹具的宿主发行版与库布局适配

日期：2026-10-15。此记录只覆盖本轮夹具修复，本机 Arch 运行结果不等于 Ubuntu 22.04/24.04 实际宿主通过。

## 缺口和改动边界

原 `ContainerSSHServer` 默认固定 `archlinux:base`、入口固定 `/usr/bin/sshd`，仅只读绑定宿主 `/usr`。Ubuntu 的 sshd 位于 `/usr/sbin`，加载器及库可经 `/lib`、`/lib64` 解析；固定 Arch 镜像和目录假设不适用于目标 CI。行为实际位于 `tools/suite/container_ssh_fixture.py` 的构造、系统运行目录、Docker create 参数和 SSH 会话环境。

本轮仅改该模块、`tools/suite/container_tests/`、公共 README 的容器对端节及本记录。没有改根 CMake、公共隔离/普通 SSH 夹具、GSS 模块或 Linux workflow；没有回退其它代理修改。

## 已实现的契约

- `image=None` 按宿主 `platform.freedesktop_os_release()` 选已有匹配镜像：Arch → `archlinux:base`；Ubuntu 22.04/24.04 → 同版本 `ubuntu:`。未知系统没有隐式默认，须显式 `image=`/`--image`；空显式镜像拒绝。镜像 inspect 后固定 ID，create 仍为 `--pull=never`，测试不拉取或构建镜像。
- 默认 Docker、OpenSSH 客户端、sshd、Python/bash/nologin 从 `/usr/bin:/usr/sbin:/bin:/sbin` 发现。容器入口使用实际发现的 sshd，不再固定 bin；relay/echo 使用系统 Python，专用 passwd 使用实际 bash/nologin。
- 除本次 `.tmp` 根以外，只读绑定宿主 `/usr`、存在的 `/bin`、`/sbin`、`/lib`、`/lib64` 与普通文件 `/etc/ld.so.cache`。源链接只允许落在对应目录自身或 `/usr` 内，保持程序/加载器/库布局，拒绝其它宿主目录。没有绑定个人 home、整个项目或 Docker socket到受测 SSH 服务容器。
- Docker 子进程和远端会话使用最小 PATH；TMPDIR 仍指向本次项目 `.tmp`。真实远端 PATH 断言曾捕获两条 `SetEnv` 的首次取值语义：只有 TMPDIR 生效，PATH 仍为镜像默认。现已将 TMPDIR 与 PATH 合并为同一条 `SetEnv`，实测两者均生效。
- `--read-only --network none --cap-drop ALL --security-opt no-new-privileges`、普通 UID/GID、资源限额、回环 relay、严格主机密钥及按本次 ID/标签清理未变。`_prepare`/`_docker` 可覆写 hooks 保留，GSS 子类仍可在 super 后改认证配置并在 entrypoint 前注入 KRB5 环境。
- `isolation_report()` 新增实际选择镜像、sshd、宿主 ID/VERSION_ID 和系统挂载计划；原字段保持。既有调用方不需要新增参数。

## 真实与静态验证

执行：

```bash
PUTTY_SUITE_CONTAINER_TESTS=1 \
python3 -B -m unittest discover -s tools/suite/container_tests -v
```

最终 **9/9 通过、0 跳过、7.868 秒**，退出 0。日志：`.tmp/build/container-host-compatibility.log`。临时配置、密钥、专用账户、运行时、载荷及独立 Docker 配置仍全部由 `IsolationEnvironment(DEFAULT_CONTAINER_PARENT)` 放在项目 `.tmp`。

其中 6 个是真实 Docker/OpenSSH 用例：

| 用例 | 实测内容 |
| --- | --- |
| actual_container_restrictions_and_scoped_mounts | Docker inspect 的断网/只读/capdrop/UID/日志驱动/全部系统挂载；每个系统挂载不可写、其它源只在本次根；容器与宿主 sshd/Python/动态加载器 SHA256 一致；根目录写入失败 |
| dedicated_account_and_binary_stream_exit_status | `suite` 身份、专用 HOME/cwd、项目 TMPDIR、最小 PATH；二进制 stdout/stderr 原样保留、退出 7 |
| real_legacy_scp_binary_round_trip | OpenSSH `scp -O` 的超过 1 MiB 二进制双向摘要一致 |
| real_sftp_subsystem | 真实 internal-sftp 上传含 NUL/非 UTF-8 数据 |
| loopback_tcp_forward_transfers_binary | 实际 SSH -L 向容器回环服务传输 256 KiB |
| close_kills_active_command_container_and_relays | 活动 exec 时重复 close，远端失败、容器删除、relay 回收、宿主监听撤销 |

新增 3 个无 Docker 的接线回归：模拟两 Ubuntu 的发行版元数据和 sbin 程序发现，检查真实 `_create` 参数传匹配镜像、实际 sshd 和固定镜像 ID；未知系统在系统挂载前要求显式选择；未知系统显式镜像始终 inspect/固定 ID 而不 pull。这些用例记录创建调用，不启动服务，不能冒充 Ubuntu ABI/协议验证。

另执行 Python AST、行尾/尾随空白检查通过；没有生成源码树内 pycache。没有为此修复重编 Qt 或执行无关密钥算法矩阵。

## Ubuntu 实际环境与 CI 接续

已读取 `.github/workflows/putty-suite-linux.yml`：父代理准备 Ubuntu 22.04/24.04 实际 runner，安装 openssh-server/MIT，并在夹具测试前显式 pull 对应 `ubuntu:` 镜像，后续全组件 CTest。当前代码与该顺序相容；本轮没有修改、触发或宣称远端 workflow 通过。

本机是 Arch。虽然已有 `puttygen-build-ready:ubuntu2204/2404`，仅把外层 Docker socket 接进内层 Ubuntu 并不足以验证宿主挂载：daemon 解析 `/usr`、`/lib` 的命名空间仍可能是外层 Arch，内层识别为 Ubuntu 而对端实际执行 Arch 文件。真实用例增加程序/加载器字节摘要正是为了拒绝这种错配，不为局部验证加入特殊文件系统逃逸参数或 privileged 容器。

因此本轮没有将这两个嵌套镜像的运行计入 Ubuntu 真验。Ubuntu 两版实际服务/加载器/协议结果由匹配宿主的仓库 CI 接续。未知系统和非标准系统目录布局只提供明确失败/显式镜像入口，不承诺新平台支持。GSS 的完整真实认证回归仍归其 reviewer；其 README 中 Arch 镜像表述已提醒父代理由所属人同步。

没有提交、push 或标记基础任务整体完成。
