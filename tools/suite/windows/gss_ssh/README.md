# 隔离的 MIT GSS 与 SSH 认证夹具

本目录把真实 MIT token/MIC、Paramiko 4.0.0 的正式 SSH 认证状态机和固定回环命令组合起来。它是自动验收依赖，不是产品 GSS 实现，也不会执行远端系统命令、读取个人代理或回退密码认证。

## Windows 准备与运行

使用 Python 3.11 x64，在仓库根执行。先按相邻 `gss_mit`、`gss_kdc` 的 README 准备便携 MIT 和正式 KDC，并先通过无凭据原生 ABI 门。以下目录全部位于项目 `.tmp/`；当前 KfW 窄字符夹具只接受 ASCII 依赖和运行路径。

```text
python -B tools/suite/windows/gss_ssh/prepare.py --parent .tmp/windows-gss-ssh
python -B -c "from tools.suite.windows.gss_kdc.security import PrivateTree; PrivateTree('.tmp/windows-gss-ssh-runs').close()"
.tmp/windows-gss-ssh/venv/Scripts/python.exe -B -m unittest discover -s tools/suite/windows/gss_ssh/tests -v
.tmp/windows-gss-ssh/venv/Scripts/python.exe -B -m tools.suite.windows.gss_ssh.run --prepared-kdc .tmp/windows-kdc-tools --parent .tmp/windows-gss-ssh-runs --mit-directory .tmp/windows-gss-mit/portable/bin --report .tmp/windows-ci/reports/gss-ssh.json
```

`prepare.py` 只创建新的独立 venv，以 `pip --require-hashes --only-binary=:all:` 下载并安装七个固定 wheel；逐个核对 `windows-wheel-lock.json` 的文件名、长度和 SHA256，拒绝额外文件。离线准备可指定 `--wheel-directory`，同样完整核验。Python、pip 的 HOME、AppData、TMP、缓存和子进程环境均封闭在准备根内；依赖目录含公开文件，保留以复查，不能上传整个准备或运行树。准备失败返回非零，不复用部分 venv。

Windows 当前只允许 MIT 加 Paramiko 客户端，报告固定 `formal_plink_verified=false`、`sspi_verified=false`。Windows 原版 CLI 还没有独立 hive 覆盖，传入 `--plink` 会失败；MIT 成功不能补填 Microsoft SSPI 的正向认证或 Windows 桌面证据。

## 固定源码与小补丁

原版 Paramiko 4.0.0 的 `GssapiWithMicAuthHandler._handler_table` 返回未绑定实例的方法；正式 Transport 按绑定方法分发。`paramiko-4.0.0-gss.patch` 只在独立 venv 中修正原函数的实例绑定，并把 GSS 提供方导入指向本目录。token/MIC 包处理、允许的消息、认证转移和签名验证仍由固定 Paramiko 源码执行。

`source-lock.json` 固定官方 `auth_handler.py`、`transport.py`、`ssh_gss.py` 的原始 SHA256、补丁 SHA256 和补丁后 SHA256。准备阶段显式应用两个唯一替换；每个认证子进程只读复核。没有运行时猴补，不改变现有 Paramiko 5 公钥/PPK 对端。提供方仅实现固定 Kerberos OID 的用户认证，不实现 GSS 密钥交换。

## 认证、进程和资源边界

正式 KDC 只含 `SUITE.TEST`、`suite@SUITE.TEST` 和 `host/localhost@SUITE.TEST`。每轮重新 AS 获取 TGT，真实 TGS 经 1.6 秒代理后才返回，成功轮次必须观察到真实 TGS。官方 KDC 的 Forwardable 能力位和实际禁止 forwarding 的边界见相邻 KDC README；本 GSS 只请求 MUTUAL/INTEG，检查无 DELEG 标记及 delegated credential，不声称执行了带有效身份的委托负例。

token/MIC、SSH 成功、缺失 FILE cache、错误 SSH 用户和篡改 SSH MIC 分别使用新进程。在首次 MIT DLL/共享库加载前，通过 CreateProcess/exec 传入封闭环境，再按相同固定规则精确核对实际环境和完整路径，拒绝额外变量；Windows 按环境键不区分大小写的语义比较。从不在加载后改环境或宣称 DLL 已卸载。Windows 复用已经验过的六 DLL 内容、依赖闭包、受限加载和实际句柄路径检查，真实 getter 再核对本次 realm/cache。凭据显式绑定本次 FILE cache/keytab，缺失客户端 keytab 阻止隐式 fallback。

每个内部子进程有 45 秒硬期限，外部进程管理器有 50 秒总期限和进程树回收，整个命令有 180 秒硬期限，覆盖原生加载、调用、清理和报告提交。执行前先 fsync 并原子发布 `passed=false, stage=pending`；超时或崩溃不沿用旧成功。标准输出仅为候选，刷新后再原子提交最终文件；验收同时要求该文件和进程退出零。输出限 16KiB；协议拒绝递归重复字段、非有限数值、类型混淆、越出 u32 的原生值和场景不对应的阶段或计数。报告只含固定阶段、版本、哈希、数值状态、计数和布尔，不附私有路径、票据、keytab、token/MIC 或原始日志。

SSH 只监听 `127.0.0.1` 随机端口，内存生成 RSA2048 主机密钥并在客户端精确核验。最多两个活动连接、累计 16 次 accept，窗口 64KiB、包 32KiB、输入 4096 字节、输出 512KiB，banner 1024 字节、原始包 256KiB、连接总期限 25 秒。只接受一个 session 和固定命令 `suite-gss-probe-v1`，精确输出 `GSS_SSH_OK\n`；关闭共用三秒预算，核对全部 worker/Transport 线程为零。正常发送 EOF、exit-status 和通道 CLOSE 后才释放传输。后续十秒 Unicode 负载的常量及编码函数与已有终端夹具相同，目前此命令不计入实际长负载证据。

replay cache 始终启用。固定 KfW 4.1 的 `gss_acquire_cred_from` 接受显式 `rcache=dfl:suite-gss-replay`，沿 `KRB5RCACHEDIR` 只生成该确切文件；Linux 1.18+ 的 dfl 根据官方实现使用 `krb5_<uid>.rcache2`。运行后只登记对应完整路径，实际验证 owner、普通单链接、权限和非 reparse；不扫描并吸收任意新文件。未知产物会保留并使清理失败。Windows 的实际命名与删除仍须 CI 验证，源码规则不能替代目标内核运行。

## Linux 补充对照与当前证据

Linux 必须显式 `--allow-linux`，使用独立且版本相同的 Paramiko 4 venv，先执行 `python -B -m tools.suite.windows.gss_ssh.dependencies --apply`。真实 MIT 共享库、kinit 和可选的正式 Plink 路径必须明确指定；Plink 只使用已验证的 `PUTTYDIR`/`PUTTYRANDOMSEED` 私有隔离、`-batch -noagent -noshare` 和显式主机公钥。

```text
<独立v4环境>/bin/python -B -m tools.suite.windows.gss_ssh.run --prepared-kdc .tmp/windows-kdc-formal/tools --parent .tmp/windows-gss-formal/runs --linux-library /usr/lib/libgssapi_krb5.so.2 --kinit /usr/bin/kinit --plink .tmp/build/suite-integrated-all/suite-cli/plink --allow-linux --report .tmp/windows-gss-formal/real-report.json
```

当前本机 Arch x64、Python 3.14.8 的真实六场景已经通过：AS12/TGS5、真实相互认证/MIC、SSH 固定命令、三个独立拒绝、正式 Linux Plink 退出零，所有原生对象、线程、KDC/代理 socket 为零且本次私有根已删除。独立检查后的 22 项回归覆盖内容篡改、额外 wheel、完整实际私有环境、严格报告协议、隔离脚本入口、固定 replay 登记、16 连接容量、三秒关闭，以及真实挂起的原生加载、清理、标准输出和报告提交的硬退出。Windows、目标 Ubuntu 和十秒终端 GSS GUI 心跳另留实际结果；本次 Linux 对照不替代它们。

旧 .NET 研究原型曾刷新一个原有个人 CRL，旧内容无备份；新增的五个文件已按核验清单精确删除。正式 KDC 后续隔离和本轮成功不抹去首次事故，见任务研究记录。
