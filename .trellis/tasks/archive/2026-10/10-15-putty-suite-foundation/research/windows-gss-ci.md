# Windows MIT GSS 与 SSH CI 接线

## 范围

新增 `tools/suite/windows/gss_ci.py`、其独立回归及 `putty-suite.yml` 的 `windows-mit-gss-ssh` 作业。产品、MIT 加载器、KDC 与 GSS/SSH 夹具由各自所有者维护，本次不改它们。作业需要同一提交的 `windows-mit-kdc` 成功后才执行，Windows Server 2022、Python 3.11 x64、20 分钟作业上限。

作业内使用现有绝对路径 7z 纯提取固定 MIT 文件，再由包装器直接调用已审 KDC 与七个哈希锁定 wheel 的准备函数。每个作业重新准备自己的 SDK、包和独立 Paramiko 4 venv，不通过跨作业 artifact 搬运依赖或秘密资料。

## 目录与个人环境观察

公开准备目录分别为 `.tmp/windows-gss-ci-tools`、`.tmp/windows-gss-ci-python`、`.tmp/windows-gss-ci-mit/portable/bin`。本次独占新建 `.tmp/windows-gss-ci-runs` 和 `.tmp/windows-gss-ci-invocation`；后者生成 `closed_environment` 的用户、配置、临时等确切目录，作为外层子进程 cwd，只登记一个 `inner-report.json` 文件。准备根、运行父、调用根和最终报告拒绝相等及祖先包含关系。固定 KfW 窄字符边界保留 ASCII 路径要求。

复用 `gss_kdc.ci.actual_profile/snapshot_profile` 只读观察真实身份的 `.dotnet/.nuget/.aspnet`。第一次观察在 SDK/venv 准备前，第二次在所有运行及清理后；准备、认证或清理失败也必须尝试第二次。快照只留内存，公开报告仅有文件数、字节数和变化布尔。前次观察失败、后次观察失败、元数据或内容变化都禁止成功。

运行父只在为空时删除，调用根仅按 `PrivateTree` 明确登记的目录与单个报告文件清理。未知内容保留并使验收失败；公开 SDK/venv 资料保留复查，不递归删除，不上传。

## 原生执行与报告门

包装器启动新 venv 的 `python.exe -I -B <绝对 run.py>`，在创建进程时一次性传入封闭环境与私有 cwd。外层 `BoundedProcess` 共用 190 秒期限、每条标准流 64 KiB 上限以及 Windows Job 回收，内层继续保留整体 180 秒、五个独立子进程各 45/50 秒的原有边界。未传 Windows Plink，也没有第六个 Linux 场景。

只接受 `token-mic`、`ssh`、`missing-cache`、`wrong-user`、`tampered-mic` 五个场景。场景细节复用 `run.validate_child` 的唯一判定规则；外层严格核对原生 Windows、源码锁摘要、凭据/认证/SSH 成功、全部原生对象与线程释放，以及 `formal_plink_verified=false` 和 `sspi_verified=false`。KDC 需真实 AS/TGS、零在途连接和 worker、确切清理；代理需真实 1.6 秒 TGS 延迟及零 socket/worker。`reset_client` 直接获取 TGT 后清零代理计数，最终代理请求是 `as=0`、至少一个 `tgs`，不能误要求代理出现 AS。计数格式与现有真实 Linux 五场景报告及固定源码核对，未将该对照写成 Windows 通过。

内层报告是本次确切登记的夹具文件；外部 Python 原子提交后，先通过同一对象句柄收紧并核验其两 SID 受保护 ACL，再读取。此行为只适用于验收报告，不改变产品导出的私钥权限。内层最终文件和 stdout 候选分别有界严格解析，拒绝嵌套重复字段、非有限或超长数、额外字段、类型混淆、场景不匹配，以及阶段不对应或越出 u32 的原生返回码；规范 JSON 比较保留 bool/int 差异。必须同时满足两份结果一致、子进程退出零和全部资源回收。失败只发布固定阶段和经核验的数值状态，不转发内部路径或原始认证日志。

执行前 fsync 原子写入 `passed=false,stage=pending`，再启动覆盖准备、认证、清理、stdout 和最终文件提交的 18 分钟硬 watchdog。stdout 只是一份候选，完成刷新之后才原子提交最终文件。stdout/提交挂起会硬退出 124，保留失败占位。文件提交和进程退出不构成原子事务，外部验收仍需同时核对文件和退出码。

Actions 的 `always()` 上传只匹配 `.tmp/windows-ci/reports/gss-mit-ssh.json` 一个最终文件，不上传 SDK、venv、inner report、cache、keytab、trace、用户快照或 `.tmp/` 目录树。

## 验证

本机 Arch Linux/Python 的 18 项逻辑回归两轮通过（8.660／8.712 秒），覆盖准备失败仍做后观察、个人变化强制失败、未知内容保留、独占目录/祖先关系、非 Windows 拒绝、严格成功/失败协议、退出码与最终文件/候选一致性、封闭子进程及上传白名单。四个真实子进程分别在准备、清理、stdout 刷新与最终文件提交处无限挂起，均由两秒测试 watchdog 退出 124，旧成功被失败占位替换。公共 `tools/suite/windows/tests` 全组 59 项通过（8.986 秒），零跳过；Linux 的逻辑模拟不替代 Windows API 验收。

独立入口 `python3 -I -B tools/suite/windows/gss_ci.py --help` 退出零；两个新增 Python 文件按 Python 3.11 AST 解析通过，新文件与工作流空白检查通过。最终 ACL 核验参数、候选一致性和非有限数解析的 3 项定向回归通过（0.063 秒），独立检查由主会话接续。Windows SDK、KfW 的实际五场景、真实用户目录观察及 Job 行为必须由新作业执行，当前不声明原生通过；Microsoft SSPI、正式 Windows Plink、长终端 GSS 负载和桌面人工验收仍是独立缺口。
