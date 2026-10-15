# Windows KDC CI 独立检查

日期：2026-10-15。按检查上下文、PRD、设计、实施计划及 Windows 夹具规范核查新增 `windows-mit-kdc` 作业、Pageant MAP 精确上传行、`gss_kdc/ci.py` 和对应回归。没有提交代码。其它代理的 Pageant、GSS SSH 与公共规范修改保持原样。

## 已修复

1. `ci.py` 原先在标准输出刷新前提交最终成功文件；输出失败或挂起时可能留下成功报告。现在 `run()` 只返回候选，入口先刷新候选，再调用既有 fsync/同目录原子提交。输出和最终提交失败回归均证明旧成功已被失败占位替换；成功回归核对刷新先于提交。
2. `ci.py` 原先只有内层进程期限及 Actions 二十分钟上限，没有覆盖整个入口的独立硬期限。初始失败占位落盘后，现启动十八分钟 daemon watchdog；准备、原生调用、清理、输出及提交均在其范围内，超时直接退出 124。启动失败不会进入准备。四个真实子进程分别确认已进入 prepare、清理、stdout 刷新、最终报告提交挂起点，再按两秒测试期限退出，均保留失败占位。
3. 协议和准备摘要的 schema 原先接受 `True == 1`，嵌套结构异常则可能产生非契约的 AttributeError。现严格核对 schema/count 为整数、关闭摘要为字典；布尔 schema、错误结构及非有限计时回归拒绝。另补前置个人目录观察失败时仍尝试后置观察、且不进入准备的回归。
4. 只读检查还发现既有 `verify.py` 独立 CLI 有同类“先成功文件、后 stdout”及缺失整体期限的问题。经主会话明确扩展所有权，仅修改其 CLI 和 `test_safety.py` 新增回归：候选先刷新、最终文件后原子提交，初始失败占位后启动一百八十秒 watchdog；覆盖执行、finally 清理、输出、报告挂起及启动/输出/提交失败。`verify()` 的真实 AS/TGS、keytab 解密及协议负例算法没有改动。此项是相对先前十四文件独立审查的新增差异。

## 核对结果

- 作业依赖同一提交的 `windows-mit-abi`，使用 Windows Server 2022、Python 3.11、项目 `.tmp/` 的 TMP/TEMP，作业上限二十分钟。固定 MIT 仍用既有绝对路径 7z 纯提取；入口再次核验内容锁和 PE 闭包，只直接调用准备及协议函数，不运行未经 hive 隔离的 Windows PuTTY CLI。
- 通过当前进程 token 的 `GetUserProfileDirectoryW` 定位实际 profile；只读 `.dotnet/.nuget/.aspnet` 的前后快照仅在内存中保留。Windows 文件访问只申请读取，沿 `PrivateData._open(..., ancestor=True)` 固定无 DELETE 共享的祖先/对象，检查真实句柄路径、类型、reparse 和单链接。`ReadFile`、profile API、句柄及安全描述符 API 的参数/返回类型与宽字符调用一致；未发现写入或 ACL 修改路径。
- Windows `BY_HANDLE_FILE_INFORMATION` 的十三个 DWORD 按真实布局读取，排除 last-access 的两个 DWORD，保留创建/修改时间、属性、卷、大小、链接数和文件身份；owner/group/DACL 只计算内存摘要。每个文件用同一句柄读取并前后核对元数据；数量、大小、深度和扫描期限只用于拒绝，未截断后声称无变化。
- prepare/verify/父目录清理失败均尝试后置观察。父目录只允许本轮独占新建，未知资料保留并失败，仅确切空父目录可删除。最终成功同时要求协议证据、资源归零、资料/父目录清理和个人目录无变化。
- 最终报告重新构造固定布尔/有界数字白名单，不转发个人目录项名、内容、摘要、票据或私有路径。GSS SSH、正式 Plink、SSPI 分别保持未验证。新增作业只上传 `gss-mit-kdc.json` 一个文件；既有主作业的 Pageant MAP 路径与 CMake `/MAP:$<TARGET_FILE:...>.map` 精确对应，没有扩大构建树上传。
- 根新增的 Windows 夹具规范与固定依赖、直接 MSBuild、Job、确切产物登记、Forwardable 能力位及拒绝策略的实现一致；既有“候选先刷新、文件后提交”和硬期限契约已由本轮恢复。

## 验证

命令：`python -B -m unittest discover -s tools/suite/windows/gss_kdc/tests -v`。

本机 Linux／Python 3.14：46 项，45 通过，Windows 原生挂起进程/Job 专项一项按平台跳过，17.655 秒。新增 CI 入口二十项全部通过，既有安全组二十六项中二十五项通过、一项跳过。八个真实子进程硬期限探针均实际进入指定挂起点后退出 124；这些探针使用模拟验证函数，只证明 CLI 的期限和提交边界，不冒充真实 KDC 认证。

十一 Python 文件通过 Python 3.11 AST/内存编译，工作流 YAML、依赖、运行平台、TMP/TEMP、版本、超时及单报告上传白名单检查通过，`git diff --check` 和新增文本空白检查通过。项目没有配置 Python lint/type-check 命令，本机没有 ruff/mypy/pyflakes；记录为未配置，不将语法检查当作静态类型检查。

## 未修复及平台边界

本次范围内没有保留的代码缺陷。Windows profile API、ACL/固定句柄、Job、固定 KfW 的真实 AS/TGS、Windows SDK ZIP 和离线 MSBuild 仍须由新增 Windows CI 实际执行；本机逻辑测试和源码 API 检查不替代原生结果。Windows 10/11 桌面人工验收、完整 GSS SSH/Plink 和 Microsoft SSPI 正向认证不在本检查的已验证范围。

硬超时或未知资料失败时不声称已清理，不递归删除残留。最终文件提交与进程退出不构成原子事务，验收仍需同时核对成功文件和退出 0。首次原型刷新既有 CRL 且旧字节无法恢复的历史记录继续保留，本次前后观察不能抵销该记录。

## Windows 首次作业后的元数据与换行复核

本轮仅复核并自修 `gss_kdc/fixture.py`、`tests/test_safety.py`，没有修改生产 `BoundedProcess` 的行语义、其它代理工作或提交代码。

实际 Windows 证据：SHA `5428f80` 的首次 KDC 作业在前置回归中唯一失败为 `b'ready\r' != b'ready'`，日志 `.tmp/suite-validation/windows-kdc-first-ci.log` 记录旧四十六项、四项平台跳过；因此没有进入真实 AS/TGS。Windows 文本 stdout 使用 CRLF，而有界读取器只分割 LF，根已将测试预期按 `os.name` 精确区分，未加入通用 strip 或修改生产协议字节。

根新增的 `decode_metadata` 统一用于准备状态、ready、关闭摘要，拒绝任意嵌套的重复字段、NaN/Infinity/-Infinity、非对象根；三个消费者的 schema 均严格要求整数 1。独立检查确认 ready 的歧义数据会在代理创建前失败，关闭摘要的重复活跃 worker 字段不会被末项零覆盖。异常路径仍 abort 子进程、关闭私有树的保护句柄，保留失败资料，且 `report()` 不会投影为成功。

本次额外自修：`parse_constant` 不会拦截合法数字文本 `1e309` 转成的无穷值，现由有限浮点解析器统一拒绝正负溢出，保留正常有限 JSON 小数，并继续由消费者严格拒绝浮点 schema/counter。解析器的 `RecursionError` 也统一转为 `ValidationError`；各 Python 版本深度限制不同，该异常路径用定点注入覆盖，没有强加新协议深度约束。

补充回归证明准备状态中的布尔/浮点 schema、重复 schema 和嵌套文件名重复，在访问 SDK 归档之前即被拒绝；合法 ready/关闭摘要带 CR 仍能启动代理并完成原有确切清理。伪造测试只证明解析、生命周期与清理门，不作为真实原生认证证据。

最终本机命令仍为 `python -B -m unittest discover -s tools/suite/windows/gss_kdc/tests -v`：五十一项，五十通过，Windows Job 专项一项按平台跳过，17.895 秒。两文件的 Python 3.11 AST/内存编译、空白及定点 `git diff --check` 通过；Lint/TypeCheck 仍为项目未配置。本轮范围内没有保留的代码问题。Windows 原生修复后回归和真实票据交换均待下一次仓库 CI，不能据本机通过报告目标 Windows AS/TGS 已验收。
