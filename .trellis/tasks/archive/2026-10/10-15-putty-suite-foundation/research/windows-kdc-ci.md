# Windows KDC 自动验收接线

## 边界与入口

本次增加 `.github/workflows/putty-suite.yml` 的 `windows-mit-kdc` 作业、`gss_kdc/ci.py` 和 `tests/test_ci.py`。独立检查发现同类报告问题后，主会话明确扩展所有权至既有 `verify.py` 的独立 CLI 入口及 `tests/test_safety.py` 对应新增回归；真实协议算法及其余 KDC 文件、MIT ABI 实现和产品客户端保持不变。

`windows-mit-kdc` 依赖同一提交的 `windows-mit-abi` 成功；使用 Windows Server 2022、Python 3.11 和二十分钟作业上限。7z 使用 runner 已有的绝对路径，只提取固定 MSI/CAB，不运行安装器。新入口仍重新验证十个文件的内容锁及 PE 依赖闭包，然后只调用这些文件中的绝对路径 `kinit.exe`、`kvno.exe`、`klist.exe`。

```text
python -B tools/suite/windows/gss_mit/prepare_kfw.py --parent .tmp/windows-kdc-mit --sevenzip <已有7z.exe绝对路径>
python -B tools/suite/windows/gss_kdc/ci.py --tools .tmp/windows-kdc-tools --mit-directory .tmp/windows-kdc-mit/portable/bin --parent .tmp/windows-kdc-runs --report .tmp/windows-ci/reports/gss-mit-kdc.json
```

入口在原生执行前 fsync 并原子发布 `passed=false` 占位，直接调用已审的 `prepare()` 与 `verify()`，不运行其独立 CLI、不先发布其成功报告。任一例外只输出固定阶段，内部路径、错误文本、票据和构建输出均不回显。非 Windows 明确返回 77、`passed=false`、`skipped=true`，不能计为原生成功。

独立检查后，`run()` 只返回候选结果，CLI 先刷新候选标准输出，再原子提交最终报告。初始失败占位落盘后启动本进程十八分钟硬期限，覆盖准备、原生调用、清理、输出及最终提交；超时退出 124，不能把退出本身当作清理成功。标准输出失败或提交前挂起仍保留失败占位；验收须同时要求最终文件成功及进程退出 0，文件提交和进程退出不构成原子事务。

独立 `verify.py` CLI 同样先刷新候选再原子提交，并从失败占位落盘后启动一百八十秒硬期限；期限覆盖 `verify()`、其异常清理、标准输出及最终文件。CI 直接调用 `verify()` 时由 CI 入口的十八分钟期限负责，不创建互相导入或第二个 CLI。

## 实际个人目录观察

通过当前进程令牌的 `GetUserProfileDirectoryW` 定位真实用户 profile；不以 HOME、USERPROFILE、APPDATA 等环境字典作为实际目录证据。准备 SDK/NuGet 之前与验证结束之后分别只读观察该目录下 `.dotnet`、`.nuget`、`.aspnet`；prepare、verify 和运行根清理失败也执行后置观察。

快照仅保留在当前进程内存，包含相对项名、真实文件身份和元数据、内容 SHA256。Windows 复用公共 `PrivateData._open(..., ancestor=True)` 的非 reparse、实际句柄路径、类型及文件单硬链接检查；访问仅为读取。祖先和被观察目录的句柄不共享 DELETE，读取同一文件句柄并再次核验元数据。权限观察只读取 owner/group/DACL 的摘要，不执行 ACL 收紧。读取可能引起的 last-access 时间不参与比较；创建/写入时间、属性、卷、文件身份、大小、链接数和权限均参与比较。

单次观察最多 32768 项、2 GiB 内容、单文件 256 MiB、深度 64、总期限 60 秒；路径经过链接或 reparse、特殊文件、硬链接、读取期间变化、无法读取或超限均失败。上限只用于拒绝，不截断快照后声称未变。报告仅含已观察布尔、文件计数和总字节数，绝不保存或上传相对项名、完整路径、文件内容及其摘要。

首次原型曾新增五个个人 store 文件并刷新一个既有 CRL：五个新增文件已按清单定点删除，既有 CRL 旧字节无备份、无法恢复。本次前后无变化即使成立，也只证明本次三个目录观察范围内无变化，不能抵销首次事故；历史依据见 `windows-kdc-portable.md`。

## 清理与报告门

本轮新建的运行父目录使用 `PrivateTree`；既有父目录直接拒绝。`verify()` 负责定点移除其各自拥有的随机运行子树；wrapper 只在父目录确实为空时调用 `PrivateTree.remove()`，不登记或递归接纳未知文件。发现失败残留或未知资料会保留并使最终报告失败。下载、SDK、离线 feed、构建根及所有运行资料均位于项目 `.tmp/`，不修改父进程环境。

最终成功要求真实 AS/TGS 及服务 keytab 解密、各协议负例、关闭资源归零、私有运行资料删除、空父目录删除，以及前后个人目录观察一致。报告按固定白名单重新构造，不直接转发内层任意字段；GSS SSH、正式 Plink 和 SSPI 始终分别标为未验证。Actions 仅上传 `gss-mit-kdc.json` 一个文件，不上传准备状态、工具树、私钥、cache、trace 或个人目录快照。

## 本机检查与平台缺口

局部回归覆盖同长度内容篡改、缺失目录创建、修改时间变化、链接与体积/项数/期限拒绝、旧成功占位失效、prepare/verify/清理失败后的后置观察、未知资料保留、个人目录变化覆盖协议成功、非 Windows 明确跳过和报告白名单。Linux 上这些回归只验证逻辑及本机只读扫描；Windows 句柄、实际 profile API、ACL 摘要和原生 KfW 票据交换必须由新增作业实际执行，尚未宣称通过。

独立检查补充候选刷新顺序、输出/报告失败、watchdog 启动失败、前置观察失败后的后置观察，以及四个真实子进程挂起探针。每个探针确认实际进入 prepare、清理、stdout 刷新或最终报告提交，再由两秒测试期限以 124 退出，旧成功均已被失败占位替换；生产期限仍为十八分钟。

本机 Python 3.14：新增入口回归 20/20；独立 CLI 的三项新增回归另覆盖候选刷新、失败/启动异常，以及执行/清理/输出/报告四个真实子进程挂起点。合并既有安全回归共 46 项，45 通过、1 项明确跳过 Windows 原生挂起进程/Job 测试，耗时 17.655 秒。十一文件的 Python 3.11 AST/编译、工作流 YAML、`needs`/二十分钟上限/单文件上传和 `git diff --check` 均通过。项目未配置 Python lint/type-check，本机没有对应工具，不把语法检查称作静态类型检查。根另行要求主 MSVC 作业精确上传 `pageant-windows-ipc-tests.exe.map` 一行，用于已有断言的 RVA 符号化；本次未扩展上传其余构建树。
