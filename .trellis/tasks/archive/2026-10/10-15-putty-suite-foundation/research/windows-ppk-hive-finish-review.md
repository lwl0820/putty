# Windows PPK hive 收尾独立审查

日期：2026-10-15。已按 foundation 的 check.jsonl、PRD、design、implement 及 suite-terminal 规范审查；范围仅为正式终端销毁、受控 Windows 包装层及 PPK 运行器。没有修改 Pageant、MIT／KDC／GSS、工作流或公共 hive／seed guard，没有提交。

## 已确认的实现路径

- 正式 `pt_terminal.c::create` 仅在渲染器创建成功后调用 `random_ref`；可返回的创建失败发生于取得随机引用之前。Backend、Terminal、日志、绘制器、确认状态及 Conf 的释放共用 `destroy`，没有另一个只做 unref 的正常／拒绝／取消收尾路径。
- 公共运行时先核对 owner、禁止分发栈中析构并撤销请求，然后在唯一核心线程调用 module.destroy。新增 `random_save_seed` 位于 Backend／Terminal 等析构之后、最后 `random_unref` 之前；没有从 GUI 线程补写，也没有测试专用生产逻辑。
- 固定上游 `sshrand.c::random_unref` 先减最后引用到零，`random_clear` 内的保存此时受 `random_active` 条件阻止。显式保存与原版 Windows／Unix 宿主退出行为一致，不能把旧代码中 random_clear 的保存调用当成已写入。
- Windows `guarded_destroy` 在真实模块销毁后仍检查 `random_active == 0`，随后读取实际 guard 统计、累计，再 unbind 并释放 hive owner。因此新增保存期间仍持有私有 hive 及种子 guard，真实写入的打开／字节计数来自 `CreateFile`／`ReadFile`／`WriteFile` 包装层，而非宿主预填值。
- 十次写打开与五次读打开强门保持。成功须同时有主机键、五个创建／销毁核心、实际读写字节、guard 计数、hive 关闭及私有目录清理。核验错误不会阻止随后关闭；关闭成功才允许删除，首个错误状态不会被后续成功覆盖。
- 失败诊断只输出固定中文字段及有界十进制计数／错误码；不包含路径、SID、注册表值、种子材料、口令或任意上游日志。原 16 KiB／128 行／12 条诊断限额保持。

## 审查自修

### 最终摘要遗漏新增的实际证据

`validate_hive` 已强制要求 `seed_opens_read`、`seed_opens_write`、`seed_bytes_read`、`seed_bytes_written`，但 `run` 后续的 hive 白名单仍将这四项丢弃。因此即使原生验证成功，上传摘要也无法展示这组真实证据。

现提取 `summarize_hive` 明确白名单，保留以上四项及原字段。回归直接验证全部真实计数原样保留，同时丢弃模拟私有路径和种子附加字段，不通过拷贝整个宿主 JSON 扩大导出范围。

### 计数类型与极大整数拒绝

hive 的整数计数现在显式拒绝浮点数和布尔值；原生 C 摘要本来就输出整数，原门槛不变。公共有界数字辅助判断先比较范围，再调用 `math.isfinite`，避免输入极大整数时先转换浮点导致 OverflowError 逃出预期验证错误。回归覆盖 `10.0` 打开次数、布尔字节计数及千位整数，均以 ValidationError 拒绝。

## 独立验证

| 检查 | 结果 |
| --- | --- |
| `python -B -m unittest discover -s apps/putty/tests/windows -p test_ssh_runner.py -v` | 12／12，通过、零跳过；包含新增摘要保留／私有字段拒绝及数值边界 |
| 两个 Python 文件按 Python 3.11 AST 解析 | 通过 |
| 所属五个文件 `git diff --check` | 通过 |
| 原样 `suite_terminal_finish`＋窄 WinAPI shim，严格 C11 `-Wall -Wextra -Werror` | 11／11：成功、写打开不足、关闭失败、清理失败、注册表核验失败、原测试失败、错误线程、owner 获取失败、零读字节及双错误状态；核对真实函数的调用顺序、首错保留、仅关闭成功后清理、成功 JSON 和诊断白名单。此项不执行 Windows 注册表或文件系统 |
| 实际生产终端库＋正式 sshrand 差分探针，独立复跑 | 旧变体实际写一次并触发强断言，退出 250；当前变体实际写两次并退出 0。真实渲染器、隔离存储及正式写入函数仍执行，非替代 PRNG；`native_windows_verified=false` |

收尾函数探针及固定报告位于 `.tmp/suite-validation/ppk-hive-finish-review/`，生产差分探针与报告位于 `.tmp/suite-validation/ppk-hive-finish/`；临时产物没有进入源码或提交。

实现者已记录匹配 Release 的真实 SSH／GSS 用例 2／2，以及 C／C++ ASan／UBSan、默认 LSan 的相同两项通过。本轮只改 Python 证据过滤和验证，没有重复这组无新增 C 行为的长认证矩阵；独立复跑生产种子差分及定点错误分支。项目没有为这两个脚本配置独立类型检查器，AST／实际执行通过不冒称静态类型检查通过。

## 未修问题与证据边界

本次代码范围未发现其它需要保留的逻辑问题。Windows run 38678572546 已有“终端阶段完成、hive 收尾错误 13”的历史失败，不能标记整项通过。本机没有 Windows SDK／MSVC，以下仍须下一次仓库 Windows CI 实际确认：

- 新诊断与 JSON 格式由目标 MSVC 编译／执行；
- 五个真实 Windows 核心合计读打开 5、写打开 10，及实际种子读写字节；
- 私有 HKCU 的主机键、连续映射、关闭、事务日志及目录删除均成功；
- 运行器最终摘要确实保留新增字段且 `private_data_cleanup=true`。

所有权校验或关闭失败时保留仍不能安全删除的 hive，而不是冒险删除；失败不投影成清理成功。原生最终结果和 Windows 10／11 桌面人工验收继续分别记录。本范围可以交主会话定点提交并进入 CI 检查点，不能据辅助 shim 或 Linux 差分先勾选原生 PPK 验收。
