# Windows GSS 与 SSH CI 入口独立复核

## 范围与结论

独立检查新增 `tools/suite/windows/gss_ci.py`、`tools/suite/windows/tests/test_gss_ci.py`、工作流的 `windows-mit-gss-ssh` 作业及 `windows-gss-ci.md`。已沿用本任务 `check.jsonl`、PRD／design／implement 与父计划上下文，补读 `suite-windows-fixtures.md`，并对照已审的 KDC 观察／进程／私有树边界和 GSS 五场景协议。

本轮没有发现需要自修的代码问题，生产文件与现有测试未改；局部入口可以进入同一提交的 Windows 原生 CI。此结论不表示 Windows 认证已经成功。

## 已核对的执行与隔离边界

- 新作业通过 `needs: windows-mit-kdc` 等待同一工作流提交的 KDC 成功，使用 Windows Server 2022、Python 3.11、20 分钟作业期限；独立获取并纯提取固定 MIT 文件，不安装 MSI、不下载其它作业的依赖或私有资料。
- 包装器在 SDK／venv 准备前读取真实身份的用户目录并建立只读内存快照，在全部运行与清理之后再观察；准备、调用或清理失败也进入后观察。前后任一次观察失败或内容／身份／元数据变化均禁止成功。
- KDC、Python、MIT、运行父与调用根五个位置及公开报告相互检查祖先关系，运行父与调用根独占新建。调用根仅登记封闭环境确切目录及一个内层报告；运行父只允许为空后删除，未知内容保留且失败。
- 内层使用新的 venv `python.exe -I -B <绝对 run.py>`，创建时传入封闭环境与私有 cwd；经既有 `BoundedProcess` 的挂起创建／Job 收尾路径，外层执行限额为 190 秒、每条输出流 64 KiB；内层 180 秒总期限和场景自身期限保持原有契约。
- 只允许 `token-mic`、`ssh`、`missing-cache`、`wrong-user`、`tampered-mic` 五场景，逐场景细节复用 `validate_child`，全部字段白名单、布尔和计数类型均受核验。SSPI 与正式 Windows Plink 必须为未验证，不接受 Linux 第六场景。
- 内层最终文件先通过同一对象句柄收紧并核验夹具 ACL，再有界读取；最终文件和 stdout 候选均拒绝嵌套重复字段、非有限数、超长整数、额外内容及错误类型。规范 JSON 比较保留 bool／int 差异，成功仍须进程退出零且资源回收完成。
- 失败阶段为固定白名单，仅发布经校验的原生 u32 状态，不转发私有路径或认证原文。公开结果按白名单重建，资源和清理计数不能靠顶层成功布尔代替。
- 执行前使旧成功变为持久化失败占位，18 分钟硬 watchdog 覆盖准备、执行、清理、stdout 刷新及最终报告提交。候选刷新后才原子提交；文件与进程退出仍需外部同时核对。
- YAML 实际解析确认唯一 `always()` 上传仅匹配 `.tmp/windows-ci/reports/gss-mit-ssh.json`，未上传 SDK、venv、调用／运行根、内层报告或整个 `.tmp/`。

## KDC 与代理计数对照

已读取现有真实 Linux 五场景 `.tmp/windows-gss-formal/five-cases.json`，其 KDC 为 AS=10、TGS=4、accepted=completed=14，最终代理为 AS=0、TGS=1、累计接受连接 4、延迟约 1.60009 秒，关闭后资源计数为零。该内容与当前成功测试夹具和外层阈值一致。

源码 `reset_client` 先直接向 KDC 获取 TGT，再清零代理的请求、响应和延迟统计；最后代理 AS 为零符合实际路径，不能把它误判为未获取凭据。外层仍要求 KDC 本身有 AS／TGS 计数、收发计数一致、真实 TGS 延迟及零连接／worker。读取 Linux 证据只用于核对阈值，没有改其平台字段、发布为 Windows 结果或以此替代原生验收。

## 验证

环境为本机 Arch Linux／Python，逻辑测试不执行 Windows MIT／SDK／Job API。

| 检查 | 实际结果 |
| --- | --- |
| 新 GSS CI 专用测试 | 18／18 通过，8.897 秒，无跳过 |
| `tools/suite/windows/tests` 公共组 | 60／60 通过，8.934 秒，无跳过 |
| 四个真实挂起子进程 | 均先确认进入准备／清理／stdout／最终提交挂起点，再由两秒测试 watchdog 以 124 退出，失败占位保留 |
| 独立 `-I -B .../gss_ci.py --help` | 退出零 |
| 两个新增 Python 文件 3.11 AST 与 `compile()` | 通过 |
| YAML 解析、KDC 依赖、平台、版本、期限与唯一上传 | 通过 |
| 新文件 UTF-8／末尾换行／尾空白及工作流 `git diff --check` | 通过 |

公共组比实施记录多一项，原因是本轮同时已有此前独立检查完成的运行时必需回归合同；本报告保留本次实际执行计数。仓库没有为该范围配置独立 Python linter 或静态类型检查器，没有将未运行工具记为通过；本轮完成语法／编译、文本和实际合同检查。

## 未修复问题与待验边界

没有遗留的已发现代码问题。Windows SDK／七 wheel 准备、KfW 五场景认证、实际用户目录前后快照、原生 Job 行为与确切清理仍须由新作业实测。Microsoft SSPI 正向认证、正式 Windows Plink、长终端 GSS 负载及 Windows 10／11 人工桌面验收均保持独立缺口；本轮未扩大范围或修改它们。
