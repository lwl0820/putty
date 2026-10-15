# Windows 私有 hive 第一阶段实施

日期：2026-10-15。范围仅为新的 `tools/suite/windows/hive/`；没有改既有工作流/无存储宿主、runtime、apps、vendor 或公共 CMake。主代理所有的 CMake 另接静态库与宿主；本阶段与第一批无存储 Windows CI 独立。未提交、未改任务状态。

## 文件和公共约定

| 文件 | 责任 |
| --- | --- |
| `private_hive.c/h` | 系统空 app hive、精确私有 ACL、路径与实际句柄检查、进程全局单映射、HKCU 标记反查、pause/resume 与安全清理 |
| `hive_test.c` | Win32 自测宿主及两个显式子进程模式，10 个具名检查，无上游或 Qt 依赖 |
| `run_hive_test.py` | 只执行指定原生宿主，严格完整报告、残留检查、单文件报告，不开放旧测试 |
| `tests/test_hive_runner.py`、`README.md` | 入口与证据拒绝回归、API/命令/缺口 |

C API：`suite_hive_create/open`、`pause/resume/close`、`cleanup`，以及 `ready/root/seed/key` 借用查询。当前线程及进程全局原子槽共同控制映射；不能并发覆盖 HKCU。每次加载和映射都先核查权限/链接，再做经 HKCU 写入、从原始 hive 句柄读取的真实随机命名空间进程标记反查。未映射时 getter 不返回 HKEY。

创建使用 `RegLoadAppKeyW(KEY_ALL_ACCESS, REG_PROCESS_APPKEY)` 的缺失文件能力，不读任何个人或 default hive。目录/文件实际 owner 为当前用户，受保护 DACL 精确两条允许 ACE：当前 SID、SYSTEM，完全访问；不借 CI 标记放行。目录链持有不共享删除的句柄，并核对真实最终路径；创建运行根前先固定并回读私有父目录权限，reparse、硬链接、越界、上级跳转和别名均拒绝。

系统生成的 hive 和事务日志首次关闭后收紧并回读 ACL；加载后再次检查所有当前文件，未满足同一要求时不能覆盖 HKCU。pause 先 flush/恢复映射/关闭键，再检查/收紧日志，释放活动槽。调用方打开的子键必须先关闭。关闭保留文件供显式跨进程重载；cleanup 仅删除核验过的随机私有运行根。失败遗留的私有文件留 `.tmp/`，不上传。

## 原生自测计划及安全边界

1. 系统创建空 hive 与实际 owner/受保护 DACL 核验。
2. HKCU 写入唯一标记，从 app hive 原始句柄反查。
3. 父映射期间新子进程只读随机探针键，不可见父标记；不向真实 HKCU 写入、不读取个人会话。
4. 父 pause 关闭全部键后，另一子进程必须自己调用初始化，读取父标记、回写；父恢复后验证两者均持久化。
5. 已有私有种子文件用同一句柄 ACL/写/flush/回读；仅把其路径写进私有 RandSeedFile 并反查，没有执行上游 seed/PRNG。
6. 暂时取消固定种子标记文件的 DACL 保护（不授额外 SID），实际恢复初始化须失败；修复回原 ACL 后才继续。
7. 第二个活动映射、越界和非法参数拒绝；最后删除全部已验证运行文件/根。报告以 10 个具名检查计数，不是已执行结论。

注册为独立 `runtime-windows-hive`（标签 `runtime;windows-hive`，TIMEOUT 90，无 skip），不并入原先两个 `windows-native` 数量门。静态库/宿主只需 Win32 `advapi32`、`bcrypt`，C99、`_WIN32_WINNT=0x0602`、MSVC `/utf-8 /UNDEBUG` 和项目 `.tmp` 绝对路径宏；无需 suite-putty 或 Qt。

唯一收集文件 `.tmp/windows-hive-reports/private-hive.json`；明确 `upstream_storage/upstream_seed/prng: not-run`，`existing_tests_enabled: false`。不能上传整个 hive/seed/事务日志树。

## 实际本机结果

`python3 -B -m unittest discover -s tools/suite/windows/hive/tests -v`：**5 项通过，0 失败，0 跳过，0.069 秒**。覆盖完整检查集合与未验分组、伪 CI 标记不能越过原生门、原版 CLI 拒绝、子环境不改个人 profile/父环境、cp1252 重定向下 UTF-8 帮助。另用 Python `ast.parse` 解析 2 个新 Python 文件，全部交付文件 UTF-8、尾换行和行尾空白检查通过；没有据此宣称 C 代码已编译。

这些测试不调用 Windows API，不是私有 hive/ACL 已通过。本机无 Windows/MSVC/MinGW/Wine，原生编译、链接、事务日志权限、同进程/跨进程 registry 行为均尚未运行，等待独立检查及第二批 Windows CI。第一批无存储 CI 的结果不能替代此批。

官方依据：[RegLoadAppKeyW](https://learn.microsoft.com/en-us/windows/win32/api/winreg/nf-winreg-regloadappkeyw) 明确缺失文件创建空 hive、参与进程各自调用、`REG_PROCESS_APPKEY` 的独占加载和键 SD 限制；[RegOverridePredefKey](https://learn.microsoft.com/en-us/windows/win32/api/winreg/nf-winreg-regoverridepredefkey) 的映射只影响调用进程；[GetSecurityInfo](https://learn.microsoft.com/en-us/windows/win32/api/aclapi/nf-aclapi-getsecurityinfo) 支持通过已打开句柄回读实际 owner/DACL。本实现核查的是文件/目录 DACL，没有修改 app hive 内部键 SD。

下一阶段仅在本批原生证据通过后设计实际 upstream session/hostkey/CA/seed 小宿主；Windows 原版 seed 配置失败仍会回退 APPDATA、用户目录、Windows，因此尚不能启用 PRNG、旧生命周期测试或任何原版 CLI 参数入口。基础整任务继续进行中。
