# 正式 MIT GSS 与 SSH 夹具独立检查

日期：2026-10-15。检查范围为 `tools/suite/windows/gss_ssh/` 的十五个文件及对应实施研究；不修改 MIT 无凭据探针、KDC、公共 PrivateData、产品和 Actions。已读取 foundation 的 `check.jsonl`、PRD、设计、实施计划、父任务规划及适用规范。检查后释放本范围文件，不提交。

## 已修复问题

### 父子报告接受不严格 JSON 和不一致证据

- 文件：`tools/suite/windows/gss_ssh/run.py`、`tests/test_boundaries.py`。
- 原问题：普通 `json.loads` 接受重复字段和非有限数值；`schema == 1`、平台字段的相等比较接受布尔/整数混淆。原生标记和 MIC 状态没有 u32 上界，失败阶段未绑定场景，SSH 正向摘要也未要求发起端真实完成，申请失败数可为负值。
- 修复：16KiB 输入边界内递归拒绝重复字段和非有限数值，严格区分整数/布尔并核验 u32、失败状态形状及场景阶段；SSH 固定场景的发起/接受完成、错误用户名、MIC 拒绝、凭据申请失败和原版 Plink 首次无认证请求计数须一致。非法类型和深嵌套统一转成固定协议失败，不回显输入。
- 回归：重复顶层/嵌套字段、NaN、深嵌套、超限内容、布尔 schema/平台字段、超 u32、非字典 MIC 状态、错误阶段、null/零/超限原生失败码，以及 SSH 缺少发起端、意外 MIC 错误、负值/额外申请失败均被拒绝。

### 标准输出挂起时会先留下成功文件

- 文件：`tools/suite/windows/gss_ssh/run.py`、`tests/test_boundaries.py`。
- 原问题：最终报告先写出成功，再执行 `print(..., flush=True)`；标准输出挂起后，硬期限虽能退出，但磁盘已留下成功。
- 修复：先刷新候选 stdout，再原子提交最终文件；验收同时要求最终报告和进程退出零。公开文档同步此顺序；文件与进程退出仍不构成原子事务。
- 回归：独立真实子进程的 stdout `flush` 挂起后以 124 退出，先前成功已被执行前的 `passed=false, stage=pending` 替代；最终报告发布挂起的既有回归也保持通过。

### 首次 MIT 加载前没有完整核验实际环境

- 文件：`tools/suite/windows/gss_ssh/native.py`、`tests/test_boundaries.py`。
- 原问题：只检查部分身份/加载器变量；额外 SSL/Python 配置变量，以及私有根内另一份同名 `krb5.conf` 或其它 HOME/trace 路径仍可通过。
- 修复：按正式 KDC 的相同封闭环境规则重建预期，在首次加载前比较全部实际环境和固定完整路径，Windows 环境键不区分大小写。Linux 原版 Plink 的可选 PUTTYDIR/种子也限定本次完整路径。
- 回归：额外 SSL/Python 变量、私有根内其它 HOME、配置和 trace 位置均失败，原有真实环境及明确缺失 FILE cache 仍通过。

### CI 的隔离绝对脚本入口缺少导入边界

- 文件：`tools/suite/windows/gss_ssh/run.py`、`tests/test_boundaries.py`。
- 原问题：只支持 `-m` 模块执行；CI 从私有 cwd 以独立 venv 的 `-I -B <绝对 run.py>` 执行时，既不能发现仓库包，也不能解析相对 dependencies 导入。
- 修复：与内部 child 相同，仅从脚本自身位置加入确定的仓库根，依赖改为绝对导入；保留 `-m` 入口。
- 回归：以独立 venv、`-I -B`、绝对脚本路径、私有 cwd 和初始封闭环境执行 `--help`，核对退出零及固定参数。此项只证明导入，不代替真实认证。

## ABI、资源与范围核验

- 逐项对照已锁定 KfW CAB 的 `fil_gssapi_h`、`fil_gssapi_ext_h`：状态与时间为 32 位，名称/上下文/凭据为指针，store 的 count 为 OM_uint32、元素为两个窄字符指针。发起/接受、显式凭据申请、MIC 和各释放函数参数位置与类型一致；Windows 经既有六 DLL 受限加载，Linux 使用显式系统库。
- 凭据只从确切 FILE cache/keytab 申请；仅固定 client/service 和 Kerberos OID，MUTUAL/INTEG、无 DELEG、返回的委托凭据即使违规也有释放路径。失败报告不会提升为 SSPI 成功。
- 客户端及对端线程在共享三秒收尾期限内核验结束，仍在原生调用的线程导致整个隔离进程 125 退出，不先析构其上下文。普通 socket/transport/channel、凭据、名称和上下文都有对应收尾；内部 45 秒、外部 50 秒及总命令 180 秒期限保留。
- 七个 Windows wheel、三个认证源文件、显式小补丁及补丁后哈希仍固定；未改 SSH 认证状态机、Paramiko 5 或正式产品。replay 未关闭，仍只登记已知单一完整文件名；未知内容保留并失败。
- README 和实施研究同步新检查结果。适用 `suite-windows-fixtures.md` 已有完整环境、严格协议和候选 stdout 契约，无需在本轮重复修改由其它代理拥有的规范。

## 验证

```text
.tmp/windows-gss-portable/venv/bin/python -B -m unittest discover -s tools/suite/windows/gss_ssh/tests -v
```

22 项通过、零跳过，1.630 秒。首轮默认沙箱因禁止 socket 导致两项网络错误，未当作跳过或通过；随后按已有授权在可用本机回环环境重跑。

```text
.tmp/windows-gss-portable/venv/bin/python -B -m tools.suite.windows.gss_ssh.run --prepared-kdc .tmp/windows-kdc-formal/tools --parent .tmp/windows-gss-formal/runs --linux-library /usr/lib/libgssapi_krb5.so.2 --kinit /usr/bin/kinit --plink .tmp/build/suite-integrated-all/suite-cli/plink --allow-linux --report .tmp/windows-gss-formal/review-report.json
```

修复后真实六场景退出零，AS12/TGS5，实际 TGS 延迟 1.6001416219996827 秒，正式 Linux Plink 精确输出且退出零。全部原生对象、peer 线程、KDC/代理 socket 和 worker 为零，本次私有根删除成功。公开报告仅为上述独立 JSON；没有下载/重建固定依赖或上传私有树。

- 语法与文本检查：十个 Python 文件按 Python 3.11 AST 解析并编译，两个 JSON 解析，十五个文本文件行末空白检查通过；unified diff 的单空格上下文行按补丁语法保留。
- Lint：上述语法和文本门通过；仓库没有 Python lint 配置，环境没有 Ruff，未声称执行 Ruff。
- TypeCheck：未配置且环境没有 mypy，不适用；AST 检查不等同静态类型检查。

## 未修复和未执行项

- 只读发现相邻 `gss_kdc/fixture.py` 的 ready/summary 仍用普通 `json.loads`，其 schema 相等检查也接受布尔；已向主会话报告，建议由 KDC 所有者单独补严格协议门。本轮不越过文件所有权修改。
- Windows 七 wheel 实装、实际 KfW token/MIC、SSH、replay 命名/ACL/清理仍待仓库 Windows CI，Linux 与头文件对照不能代替目标内核运行。
- Windows 原版 Plink 尚无逐进程 hive 隔离，入口保持硬拒；Microsoft SSPI 正向、目标 Ubuntu、十秒终端 GUI 心跳/计算中关闭和人工桌面仍不属于本轮通过项。foundation 及父任务保持进行中。
