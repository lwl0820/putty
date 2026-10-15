# Windows Pageant 公共 CI 接线复核

日期：2026-10-15。Active task 为 `10-15-putty-suite-foundation`。本轮只审核主会话指定的 `run_ctest.py`、公共拒绝门测试、Windows README、Linux 必需集合、Windows 工作流及 Pageant 两处标签接线；未重复 Pageant 产品审查，没有修改并行的 hive、存储或终端源码，没有提交和改变完成状态。

## 结论与已核对路径

没有发现本轮公共接线需要自修的确定性问题，可作为后续真实 Windows CI 的提交点。

- `windows-native` 仍只承载两个运行时原生宿主。Pageant IPC 使用独立 `windows-pageant`，runner contract 仅在 Windows 分支追加同标签；Linux 仍可运行其 Python 契约。没有把 Python 契约算成两个事件原生宿主之一。
- `--require-pageant` 先核对标签精确注册两个不同名称，再要求实际 JUnit 两项都存在且唯一，拒绝 `failure/error/skipped` 及未执行状态。工作流确实传入此参数。CTest 的 IPC 子命令仍是受限 runner；只有其 MAP、随机命名空间、无存储边界和 15 项报告全部满足，才可返回成功。公共汇总没有仅凭编译或 runner 自测就宣布 IPC 通过。
- 开启 `PUTTY_BUILD_PAGEANT=ON` 后全构建包含产品及专用宿主。测试通过加入 Qt `bin` 的 PATH 装载锁定运行库；没有执行或部署生产 GUI。两个 GUI 的完整部署继续未验。
- 原 Pageant 根目录安装规则会使全量安装多出 EXE；工作流改为对 Plink、PSCP、PSFTP 分别执行 `--component`，与 `SuiteCLI.cmake` 中逐产品登记的组件名一致。安装摘要仍要求精确三项、正确源产物和有效 Release 断言，没有放宽清单或混入 Pageant。
- Linux REQUIRED 包含新增 runner contract，现有预选完整集合与 JUnit 精确一致／拒绝跳过的流程保留。旧的 Ubuntu 29 项成功不会自动成为新增集合的成功证据。
- 上传仍限定报告、具名宿主和三个合法 CLI，新增 Pageant 宿主不扩大到运行根／密钥／hive；汇总保留原名跨版本、跨权限、OpenSSH、真实 SSH／PPK、SSPI 及桌面未验边界。

## 独立验证

本机 Linux，以下都是拒绝门／静态检查，不是 Windows 原生执行证据：

| 检查 | 结果 | 日志 |
| --- | --- | --- |
| 公共 Windows Python 契约 | 18/18，0 skip，0.193 秒 | `.tmp/pageant-ci-review-windows-contract.log` |
| Linux CI 契约 | 4/4，0 skip，0.004 秒 | `.tmp/pageant-ci-review-linux-contract.log` |
| Pageant runner 契约 | 3/3，0 skip，0.004 秒 | `.tmp/pageant-ci-review-ipc-contract.log` |
| 本轮三份 Python AST | 通过 | 实际命令退出 0 |
| 本轮六个文件差异空白检查 | 通过 | `git diff --check` 退出 0 |

没有 C/C++ 实现变更或独立 Python 类型检查器配置，本轮不重复产品／CLI／PuTTYgen 运行矩阵。Windows MSVC、WinAPI、PowerShell 组件安装及新版 CTest 标签实际生成仍待仓库 CI 执行，不能从这些 Linux 契约结果推断已经成功。没有遗留的已知公共接线缺陷；未执行平台和产品项按上述边界交接主会话。

## 补充：终端最小修复只读复核

主会话随后指定的 `pt_platform.[ch]`、`pt_terminal.c`、`windows_platform_test.c` 已只读核对，未发现确定性缺陷。首轮 Windows 日志确实在宿主链接阶段缺少 `modalfatalbox`；测试宿主提供终止式实现，未引入产品 GUI 或存储。平台包装在 Windows 使用现有 `filename_from_utf8`，与上层 UTF-8 私钥路径契约一致；上游 `filename_from_str` 实际按 `DEFAULT_CODEPAGE` 转换，不能承担该契约。测试用中文宽字符路径比对实际转换，并为内嵌源码探针重命名新增符号，避免重复定义。此补充没有修改终端文件或重复主会话已运行的 Linux 测试，Windows 修复结果仍待 CI。
