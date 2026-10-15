# Windows MIT 私有配置探针独立审查

## 范围与结论

按本任务 `check.jsonl`、PRD、设计、实施记录及 `suite-windows-fixtures.md` 读取上下文，审查 `gss_mit/mit_gss.py`、`probe.py`、`native_probe.py`、`tests/test_boundaries.py`、`README.md`。未改 KDC、GSS SSH、公共 PrivateData、工作流或产品代码，未提交。

实施者的默认缓存名修复与固定官方源码相符。独立检查本地固定源码 `ccdefname.c` 的配置选择及 `expand_path.c` 的 Windows 分隔符转换；预期值只执行 `/` 到 `\` 的确定转换，再比较整个 FILE 名称，继续拒绝其它目录、类型和仅 basename 相同的值。README 的源码链接进一步固定到提交 `aa75c3374ce654f3dcfd6ec50fc1f1dfeee673b9`，不依赖可变标签。

本次修复两处协议缺口，补充回归后本机 22 项零跳过通过。仍需 Windows CI 对首次 DLL 加载、CRT 环境副本及真实配置 getter 重新执行，不能把本报告当作原生成功。

## 已修问题

1. 父进程的 `json.loads` 原先接受重复字段，后值可以覆盖矛盾的 `passed` 或未知 `stage`。现用唯一键解码拒绝重复字段，将格式/编码/多份 JSON 拒绝归入固定 `native-child-protocol`。同时仅允许上下文初始化、realm 读取两个真实数值返回阶段携带且必须携带 `native_status`，继续严格要求非布尔整数及有符号 32 位范围。其余阶段不接受伪造返回码。
2. 最终成功报告原先先于标准输出刷新提交。独立真实子进程复现：stdout 挂起后 watchdog 退出 124，但本次报告已是成功。根代理批准将候选标准输出先刷新，最终文件最后原子提交。现在 stdout 刷新异常返回 1，刷新挂起则 124 且仍是已落盘失败占位；最终文件提交失败返回 1，并保留占位。候选 stdout 可能已经包含成功，不能据此验收。最终文件与进程退出不能组成原子事务，正式判定必须同时核对报告及退出 0。

回归增加重复 `passed`/`stage`、多份 JSON、非法编码、缺失/错误阶段的原生返回码和越界状态码；增加大小写不同的系统环境键可用、个人 `krb5ccname` 在加载前被拒绝；原有父 watchdog 测试扩展到真实子进程内 `sys.stdout.flush()` 注入挂起，另覆盖最终报告提交异常。报告及 stdout 不回显注入的私有错误字符串。

## 已核调用边界

- 父进程不实例化 `MitGss`，不修改 `os.environ`。`Popen` 首次创建原生子进程即传入封闭环境和私有 cwd，使用绝对 Python/脚本路径及 `-I -S -B`。子进程加载前比较真实环境，比较键统一大写，Windows 环境键大小写差异不会造成误拒；个人 Kerberos、GSS、Python、PATH、代理配置不在 allowlist。
- 系统 allowlist 仅由公共环境帮助函数保留既定系统位置/处理器字段，其余用户目录和配置由本次私有根覆盖。不把环境字典本身当作库实际采用的证据；realm 与默认 FILE 名称仍由真实 getter 验证。
- 六个目标模块首次加载前全部查询，任一预加载均拒绝。每个加载请求为绝对路径、`LOAD_LIBRARY_SEARCH_DLL_LOAD_DIR | LOAD_LIBRARY_SEARCH_SYSTEM32`，每个真实句柄再核实际模块路径。`kernel32` 为 Win32 已知系统模块入口。MIT 模块仅在短命子进程持有，不靠父进程恢复环境或 Python 对象回收假称 DLL 卸载。
- 配置 getter 仅初始化上下文、读取 realm 及默认缓存名称。已绑定接口没有申请凭据、cache resolve/open 或遍历个人缓存。realm 在 finally 释放，上下文同样释放；机制集合枚举后在 finally 真正释放。
- 持久化失败占位之后才进入可能挂起的准备、子进程原生调用及收尾。父 watchdog 固定 60 秒且直到候选 stdout 和最终报告结束才取消；子进程原生调用另有至多 45 秒 watchdog，父进程根据绝对剩余期限扣除七秒后计算预算，另有有界等待及输出额度。普通调用、异常清理、最终报告及 stdout 均处于父硬期限中，没有给公开 CLI 增加缩短期限参数。
- `run_bounded` 的 stdout 上限为 16384 字节、stderr 为公共帮助函数的独立 65536 字节限额；两个管道分别有界，不向公开报告传递原始子进程日志。超限、退出非零、超时或协议异常均为固定失败阶段。
- 父进程无论原生成功或普通失败均先关闭 PrivateData 固定句柄，再精确删除本次配置、两个空目录和随机运行根。未知内容使清理失败并保留，不递归删除便携依赖或未知文件；只有清理真正成功才加入 `private_run_removed: true`。硬超时可能保留运行根，不声明清理成功。

## 验证

- 初始 19 项独立复跑：19/19，零跳过，1.843 秒。
- 自修最终回归：`python3 -B -m unittest tools.suite.windows.gss_mit.tests.test_boundaries -v`，22/22，零跳过，2.201 秒。
- 8 个 Python 文件：`ast.parse(feature_version=(3, 11))` 及内存 `compile` 全过，不写缓存。
- `git diff --check` 通过。此范围没有配置独立 Python lint/type-check 工具；语法、编译及运行时契约测试已执行，不能把它们称为静态类型推导通过。
- 修复前 stdout 挂起的独立复现留在 `.tmp/suite-validation/windows-mit-config-review/stdout-hang.json`；正式回归会自行清理各次随机测试根。上述均是 Linux 上的 Python 流程和 ctypes 指针比较证据。

## 未修事项与证据边界

- 没有遗留需本范围进一步修改的确定代码问题。候选 stdout 与最终文件的权威次序已在本目录 README 同步；共享规范已要求真实文件报告及失败占位，本次没有越权修改共享规范。
- 首次 Windows 运行 `38678912619` / job `116238729331` 的 `configuration-defaults` 失败事实保留。官方斜杠转换证明旧比较存在缺陷，但不足以证明它是该次失败的唯一原因；仍需重跑原生探针观察固定细分阶段和数值。
- 本轮没有 Windows SDK、MSVC 或 Windows 运行环境，未验证 CreateProcess 实际初始环境、MSVCR100 初始化、真实六 DLL 句柄、MIT 配置 getter、Windows ACL/清理和 Windows watchdog 行为。本机模拟及真实 Python 子进程不能替代这些证据。
- 文件原子提交和进程退出之间仍有不可消除的故障窗口；必须同时使用最终报告和退出码，不能承诺任意机器故障都保留 pending。
- `credentials_acquired`、`kerberos_authentication_verified`、`sspi_verified` 固定为 false；未声明取得凭据、Kerberos/SSH 认证、SSPI 正向认证或 Windows 10/11 人工桌面验收通过。

## 交接

上述五个 `gss_mit` 文件和本审查记录已释放，可由根代理提交并立即进入 Windows CI；不改动前面已释放的 Pageant/PPK 文件。根代理另报 Pageant 原生 IPC 在运行 `38679891218` / job `116241793352` 出现 `tree234.c` 断言失败，此轮未碰该范围，需另行定位，不将其推定为本轮 MIT 修复的问题。
