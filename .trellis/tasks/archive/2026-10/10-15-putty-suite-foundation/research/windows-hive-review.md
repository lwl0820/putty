# Windows 私有 hive 独立检查

日期：2026-10-15。活动任务为 `10-15-putty-suite-foundation`。已读取父、子规划、`check.jsonl` 对应规范、hive README 与 `windows-hive-implementation.md`。本轮写权限仅为 `tools/suite/windows/hive/` 和本记录；共享 CMake、工作流及报告门均由主代理修改。未提交、未推送、未标记整任务完成。

## 已修问题

`suite_hive_cleanup` 原来用不预留进程槽的上下文执行文件枚举与删除。若在本进程仍有活动 HKCU 映射时误调用，可能先删除未被占用的 seed/日志，之后才在仍加载的 hive 上失败，使活动上下文留下部分数据丢失。

已将 cleanup 改为预留同一个进程原子槽：本进程有活动映射时，清理在打开运行根或删除任何文件前返回 `ERROR_BUSY`；清理成功或失败释放其自己的槽。增加真实原生断言：活动映射期间 cleanup 必须拒绝，`ready` 仍真，种子文件仍存在，随后继续通过 HKCU 写标记并从 app hive 原始句柄反查。此断言放在原有 `single-mapping-and-path-guard` 项内，报告仍为 10 个具名检查。头文件和 README 已同步。

这项原生回归尚未在本机执行；不能把下列 Python 结果当作此修复的 Win32 执行证明。

## 实际代码路径复核

- 创建时传给 `RegLoadAppKeyW` 的 `registry.hive` 必须缺失，由系统生成空 hive；没有复制个人/default hive，也没有自行拼接 hive 格式。微软文档明确支持缺失文件创建空 hive、每个参与进程分别加载、`REG_PROCESS_APPKEY` 独占加载及内部键 SD 的限制。[RegLoadAppKeyW](https://learn.microsoft.com/en-us/windows/win32/api/winreg/nf-winreg-regloadappkeyw)
- 没有启用或请求恢复/备份特权。实际进程 token 提取当前用户 SID，拒绝 SYSTEM 本身；目录、种子和收紧后的 hive/日志逐一通过文件句柄回读 owner 与受保护 DACL。DACL 必须精确两条允许 ACE，仅当前用户和 SYSTEM。没有将文件 DACL 当作内部注册表键的 SD。
- 本地绝对路径必须落在项目 `.tmp`，拒绝父跳转、别名、reparse、非磁盘对象和文件硬链接。目录链持有不共享删除的句柄并核对最终路径；新运行根创建之前已固定并核对私有父目录。普通共享读写允许系统加载与文件权限检查共存，不允许共享删除来替换已固定目录。
- 每次恢复都先核验文件，再加载、再次检查当前文件、覆盖 HKCU、写随机命名空间进程标记并从原始 hive HKEY 反查；未就绪时不返回借用 HKEY。环境变量或 `GITHUB_ACTIONS` 没有许可作用。
- 映射槽和所有者线程检查防止另一上下文覆盖同进程 HKCU。pause 按 flush、恢复 HKCU、关闭 HKEY、核查/收紧文件、释放槽顺序执行。flush/恢复/关闭失败会返回错误并保留相应句柄/槽，调用方必须停止后续操作；初始化失败不返回可用上下文。
- 父映射期间未初始化的子进程只查询随机探针键且不写真实 HKCU；父 pause 后另一个子进程自己调用 open，读取父标记、回写子标记；父 resume 后核对双方值。没有将父覆盖当作可继承权限。[RegOverridePredefKey](https://learn.microsoft.com/en-us/windows/win32/api/winreg/nf-winreg-regoverridepredefkey)
- 种子仅为已有私有文件的固定二进制标记：同句柄验证权限、写入、flush、回读。RandSeedFile 路径只写入私有 hive，再从原始 HKEY 反查。取消 seed 的 DACL 保护后，实际 resume 必须返回权限拒绝；修复回原 ACL 才能继续。这里没有执行上游 seed 或 PRNG。
- 运行器只接受指定独立宿主名、完整唯一 10 项报告、无新增根残留及上游存储/seed/PRNG 全部 `not-run`。唯一 hive 报告是 `.tmp/windows-hive-reports/private-hive.json`，不上传 hive、种子或事务日志树。

此层不是防御同 SID 恶意程序的通用沙箱。系统生成日志的实际共享模式与 ACL、加载/恢复持久化、子进程边界等必须由真实 Windows 测试继续验证；静态审查不替代这些证据。

## 主代理文件只读协作

共享 CMake 的 hive 静态库和控制台宿主只连接 `advapi32`、`bcrypt`，继承 C99，固定 `_WIN32_WINNT=0x0602`、MSVC `/utf-8 /UNDEBUG`、项目 `.tmp` 路径宏。`$<TARGET_FILE:...>` 指向实际当前配置程序；独立 `windows-hive` 标签、90 秒超时，没有 skip 成功路径。没有要求修改其接线。

发现 `run_ctest.py` 对原生/hive 结果最初只拒绝 `<skipped>/<failure>/<error>`，未拒绝单独的 `status="notrun"/"disabled"/"skipped"`。已通知主代理，由主代理修复并扩充既有回归，本轮重新运行 14 项通过。`--require-hive` 仍独立要求恰好一个已注册、实际成功的 hive 结果，保留两个无存储原生宿主的原数量门。工作流只上传唯一 hive JSON 和明确 exe，未扩大私有文件树上传。

主代理补的 `WIN32` 单行 `target_sources(putty-runtime-tests ... windows/no-jump-list.c)` 只为旧测试宿主提供上游无 jumplist 的三个符号，不影响产品目标或无存储宿主的 map/致命拒绝边界。`run_runtime_test.py` 在非 Linux 仍直接返回 77，未开放旧 Windows storage 执行。本轮没有据此宣称其 MSVC 链接已通过。

附带只读复核 `run_linux_ci.py/ci_tests`：注册集合、重复/缺项、实际结果与注册精确比对、失败/跳过/未执行状态、非法耗时均有拒绝门；输出仅摘要，不带原始 system-out。发现 Ubuntu 工作流最初缺 `PUTTY_SUITE_GSS_TESTS=1`，会使独立 GSS unittest 组跳过，已通知主代理并由其补齐。CTest 无跳过不自动等价于其内部 unittest 无跳过；当前 `suite-fixture` 在缺 OpenSSH 时有内部 SkipTest，CI 依赖安装步骤必须成功提供该依赖。

## 本机实际验证

| 命令或检查 | 结果 |
| --- | --- |
| `python3 -B -m unittest discover -s tools/suite/windows/hive/tests -v` | 5 项通过，0 失败、0 跳过，0.073 秒 |
| `python3 -B -m unittest discover -s tools/suite/windows/tests -v`（主代理状态拒绝修复后重跑） | 14 项通过，0 失败、0 跳过，0.174 秒 |
| `python3 -B -m unittest discover -s tools/suite/ci_tests -v` | 4 项通过，0 失败、0 跳过，0.003 秒 |
| hive 全 6 文件 UTF-8、尾换行、行尾空白 | 通过 |
| hive 两个 Python 文件 `ast.parse` | 通过 |

项目未配置独立 Python 类型检查器，本机没有 ruff、mypy 或 pyright；没有把 AST 检查称为类型检查。没有 Windows SDK、MSVC、MinGW 或 Wine，未执行 Win32 编译、链接、ACL、注册表或 native 10 项检查。存在的 clang-cl 没有 Windows SDK，不能据此补填原生通过。

代码审查范围内没有另一个已确认且未修复的本地缺陷。此交付可进入真实 Windows CI；只有该批原生证据通过后才设计后续实际上游 session/hostkey/CA/seed 探针。旧 storage、PRNG、原版 CLI 自动执行和 Windows 桌面验收仍未开放。
