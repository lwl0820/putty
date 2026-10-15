# Windows 终端纵切的受控 SSH 对端

## 范围与依据

新增 `tools/suite/windows/ssh_peer.py` 和 `ssh_peer_tests/`，供 Windows CI 后续的受保护终端宿主连接。本次没有修改产品、上游 SSH 核心、注册表／种子保护变体或工作流。产品仍使用固定 PuTTY 0.85；Paramiko 只存在于测试 Python 环境，不进入程序依赖或发布包。

测试依赖固定 `paramiko==5.0.0`，入口会拒绝其它版本。依据为 [Paramiko 5.0.0 发布页](https://pypi.org/project/paramiko/5.0.0/)、[官方 ServerInterface](https://docs.paramiko.org/en/stable/api/server.html) 和 [Transport](https://docs.paramiko.org/en/stable/api/transport.html)，并经 Context7 `/paramiko/paramiko` 查询核对。服务回调运行在传输线程，因此回调只核对允许项、设置事件；持续输出和读取在独立工作线程进行。公钥回调只决定允许的身份及 key blob，真实客户端签名由 Paramiko 验证；自测另以“正确公钥声明＋另一私钥签名”确认不能绕过验签。

## 调用接口

```python
from tools.suite.windows.ssh_peer import (
    RestrictedSSHPeer, WORKLOAD_COMMAND, ORDERED_INPUT_COMMAND,
    EXPECTED_WORKLOAD_INPUT, EXPECTED_ORDERED_INPUT,
)

with RestrictedSSHPeer(public_blob) as peer:
    port = peer.port
    fingerprint = peer.host_key_fingerprint
    # 将端口、公钥对应的测试 PPK 和固定标记传给已隔离的终端宿主。
    # 子进程退出后先检查真实终端报告，再退出对端上下文。
statistics = peer.statistics()
assert statistics["live_threads"] == 0
```

构造函数只接受一个公开 SSH 二进制 key blob，验证规范编码并限制为 16–8192 字节。它不接收私钥、密码、用户名、监听地址或命令文本。用户名固定 `suite`；RSA 2048 位主机私钥在内存生成并持有，不写入文件。`host_key_fingerprint` 为可传给原版 Plink `-hostkey` 的 SHA256 指纹。

只绑定 `127.0.0.1:0`，由系统分配端口；不创建 OS 账号，不启动服务或子进程，不执行 shell／Python 命令，不写文件，不加载个人 known_hosts、代理或配置。Windows 上使用可用的独占地址选项。以下两个标记是固定业务分支，不作为命令执行：

| 标记常量 | 线上命令值 | 行为 |
| --- | --- | --- |
| `WORKLOAD_COMMAND` | `suite-terminal-workload-v1` | 持续 10.2 秒，每次发出 ANSI 定位／真彩色、中文、组合字符、希腊／西里尔字母和非 BMP 字符；同步读取有界输入。最终发出 `FINAL 中文 é 😀` 与 `INPUT_OK`／`INPUT_BAD`，返回真实 SSH exit-status 0／9。 |
| `ORDERED_INPUT_COMMAND` | `suite-terminal-ordered-input-v1` | 比较 642 字节的 `BEGIN`、64 组上方向键＋`x`、127 个左方向键；发出 `ORDER_OK`／`ORDER_BAD`，返回 0／9。达到完整长度后观察 100ms 内的尾随字节，或在对端 EOF 时立即校验；结束交易后不再接受新输入。 |

负载期望输入由 `EXPECTED_WORKLOAD_INPUT` 唯一定义，内容为 UTF-8 `terminal-input-中文\n`，共 22 字节。有序输入由 `EXPECTED_ORDERED_INPUT` 唯一定义，禁止调用方复制出不同序列。

密码、none、错误用户／公钥、交互认证及 GSS 均不开放。仅允许每连接一个 session channel；拒绝任意其它 exec、shell、PTY、环境变量、SFTP／其它 subsystem、X11／代理／本地／远程转发及 global request。这是 PPK 纵切的公钥对端，不能用它声称原生 SSPI／KDC 验证通过。

## 资源与关闭契约

| 资源 | 固定边界 |
| --- | --- |
| 活动连接 | 最多 2；总接受 16 次后关闭监听，不保留无限历史 |
| 线程 | 1 监听线程、每连接至多 1 工作线程＋1 Paramiko 传输线程 |
| SSH 接收窗口／单包 | 64KiB／32KiB；packetizer 在读取大于 256KiB 的单次请求前拒绝，明文 banner 单行最多 1024 字节 |
| 应用输入 | 最多 4097 字节，最后一字节仅用于识别超过 4096 字节的失败 |
| 应用输出 | 每连接最多 512KiB；发送超时 200ms，不缓存完整输出历史 |
| 单连接 | 总期限 25 秒；命令等待和输入读取均检查关闭事件 |
| 收尾 | 先 shutdown 底层 socket，再关闭 transport／channel；线程共享 3 秒 join 期限，未回收抛出 `PeerShutdownError`；`close()` 可重复 |

`statistics()` 返回版本、回环端口、认证／拒绝／容量／完成计数、活动高水位、各固定命令的字节数／采样数／耗时／退出状态及存活线程数。不保存输入正文、密码、公私钥或原始认证消息。`offered_public_keys` 包含 SSH 公钥探测，不能当成已完成认证次数；`authenticated_connections` 在真实完成 SSH 认证后计数。测试应同时核对真实客户端退出码、完成命令数、错误计数和最终线程数。

## 安装及运行

所有下载、pip 临时目录、缓存、venv、夹具和报告均位于项目 `.tmp/`。安装前创建并收紧本次目录，不安装全局 Python 包：

```bash
mkdir -p .tmp/windows-ssh-peer/temp .tmp/windows-ssh-peer/pip-cache
chmod 700 .tmp/windows-ssh-peer
python3 -m venv .tmp/windows-ssh-peer/venv
TMPDIR="$PWD/.tmp/windows-ssh-peer/temp" TMP="$PWD/.tmp/windows-ssh-peer/temp" TEMP="$PWD/.tmp/windows-ssh-peer/temp" PIP_CACHE_DIR="$PWD/.tmp/windows-ssh-peer/pip-cache" .tmp/windows-ssh-peer/venv/bin/python -m pip install -r tools/suite/windows/ssh_peer_tests/requirements.txt
.tmp/windows-ssh-peer/venv/bin/python -B -m unittest discover -s tools/suite/windows/ssh_peer_tests -v
```

Windows CI 使用等价的 `.tmp` Python 环境和 `TMP`／`TEMP`／`PIP_CACHE_DIR`。依赖缺失或版本不符直接失败，测试没有将缺依赖转成 skip。

原版 Plink 对照使用独立入口，明确限定 Linux `IsolationEnvironment`，逐进程覆盖原版存储、随机种子、临时及代理环境。ssh-keygen 和上游 PuTTYgen 都通过该隔离对象运行，测试客户端密钥只存在本次私有根并于退出清理。该入口不能拿来直接运行尚无隔离的 Windows CLI。

```bash
.tmp/windows-ssh-peer/venv/bin/python -B tools/suite/windows/ssh_peer_tests/run_plink_interop.py --plink .tmp/build/suite-integrated-all/suite-cli/plink --puttygen .tmp/build/suite-integrated-all/suite-test-keygen/puttygen --parent .tmp/windows-ssh-peer/plink-runs --report .tmp/windows-ssh-peer/reports/plink-bounded-final.json
```

报告路径只允许项目 `.tmp/`，逐层验证父目录、独占创建 0600 文件；已有报告会失败，复跑需使用新文件名，以免覆盖旧证据。

## 实际验证结果

2026-10-15，本机 Linux／Python 3.14／Paramiko 5.0.0：

- 最终六项真实 SSH 夹具测试全部通过，11.276 秒，0 skip：认证拒绝、exec／PTY／shell／subsystem／转发拒绝、持续 Unicode 输出、正确／错误／尾随／超额输入、在途关闭和重复关闭、容量拒绝及未完成／超长 banner 握手关闭。最终各对端存活线程均为 0。
- 补充的真实签名拒绝测试单独通过；声明允许公钥但用另一私钥签名仍认证失败，不会获得 session。
- 固定上游原版 Plink 完成两次真实 Ed25519 公钥认证：有序输入 642 字节，输出精确 `ORDER_OK`，退出 0；持续负载输入 22 字节，10.2934 秒，输出 152844 字节／1910 次更新，最终 `INPUT_OK`，退出 0，stderr 为空。
- 对端记录两条认证连接、两条完成命令，connection_errors=0，active_connections=0，live_threads=0。报告 `.tmp/windows-ssh-peer/reports/plink-bounded-final.json`；自测日志 `.tmp/windows-ssh-peer/self-test-bounded-final.log`，补充签名日志 `auth-signature-final.log`。三份 Python 文件 AST 检查通过。
- 实际依赖环境另含 cryptography 50.0.2、bcrypt 5.0.0、PyNaCl 1.6.2、cffi 2.1.1、invoke 3.0.3、pycparser 3.11；这里只将 Paramiko 固定为夹具直接版本要求，未将本机传递依赖列表冒充跨平台锁文件。

Paramiko 客户端自测只证明夹具本身的行为。Plink 对照是 Linux 上的真实 PuTTY 互操作，不能外推 Windows MSVC 终端、原生字体／DPI、SSPI、注册表或桌面验收。

## 后续 Windows 接线

主会话先在受保护的测试宿主中完成逐进程 hive／seed 探针及生成测试 PPK，再把公开 key blob 传给对端，将动态端口和 `WORKLOAD_COMMAND.decode("ascii")` 传给终端宿主。Windows 有序输入分支应使用 `ORDERED_INPUT_COMMAND`，不能继续发出 Linux 专用 `/usr/bin/python3` 程序。固定命令不接受脚本扩展。

宿主运行期间保持上下文存活；每个子进程保持其独立注册表和 seed 保护。子进程失败或超时也必须退出上下文，核对对端回收结果；不能只凭 `statistics()` 当成终端 GUI 响应／字体／PPK 计算验收。需要单列真实 MSVC／Qt 6.8.3 运行证据，原生 SSPI 仍需其它真正的 GSS／KDC 方案。本次没有改动公共 CTest 运行器或 Windows 工作流，由主会话完成接线和独立审查。

## 审查后最终原版客户端对照

主会话在全部独立修复之后执行同一隔离Plink入口，报告 `.tmp/windows-ssh-peer/reports/plink-reviewed-final.json`。两次真实公钥认证及两固定命令均exit0：642字节精确输入；22字节Unicode输入、10.2804秒持续负载、153804字节／1922次更新。connection_errors=0、completed_commands=2、active_connections=0、live_threads=0。该结果属于最终夹具版本的Linux原版PuTTY互操作，仍不是Windows终端或SSPI证据。
