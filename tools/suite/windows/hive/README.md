# 私有 app hive 隔离探针

这是 Windows 隔离的独立第一阶段。它只调用 Win32 注册表、权限和普通文件 API，不链接上游或 Qt，不运行旧 `runtime-lifecycle`、原版 CLI 或客户端 PRNG。当前 Linux 环境没有执行过本探针；源码与 Python 回归均不能作为 Windows 隔离已通过的证据。

## 接口和构建

`private_hive.h` 提供 `suite_hive_create(project_tmp, parent, &context)`、`suite_hive_open(project_tmp, root, &context)`、`suite_hive_pause/resume/close(context)`、`suite_hive_cleanup(project_tmp, root)`，返回 Win32 错误码。只有成功初始化后 `suite_hive_ready` 才为真；初始化会先证明 HKCU 写入的进程标记能从 app hive 原始句柄读取。失败必须停止后续存储和 PRNG 初始化。

`suite_hive_root/seed` 返回借用路径；`suite_hive_key` 只在所有者线程且映射活动时返回借用 HKEY。不能关闭该 HKEY；调用方创建的全部子键须在 pause/close 或移交前关闭。一进程用原子槽限制为一个活动 HKCU 映射，第二个上下文在创建文件前返回 `ERROR_BUSY`。只有当前所有者可以修改上下文；线程限制不意味着 HKCU 覆盖只影响该线程。

`suite_hive_release_owner` 原子释放操作权，`suite_hive_claim_owner` 只从无所有者状态取得操作权。移交保持原 HKCU 映射、HKEY 和进程槽；不能通过 pause/resume 在 QApplication 存活期间短暂恢复个人 HKCU。调用方必须串行传递上下文生命周期，移交期间不能 close/cleanup。非所有者释放、重复取得以及暂停态移交均被拒绝。

`pause` 先 flush、恢复 HKCU、关闭本上下文 HKEY，再核查/收紧本私有根的 hive 及事务日志，最后释放进程映射槽。`close` 不删除文件，以便另一个进程显式加载；`cleanup` 也占用进程槽，存在活动映射时在删除任何文件之前返回 `ERROR_BUSY`，随后独立验证所有权、权限、路径、无链接后才删除平面运行文件与随机运行根。失败遗留的私有运行根不上传，也不通过放宽权限来清理。

共享 CMake 的接线由主代理负责：静态库 `suite-windows-hive` 使用 `private_hive.c`，控制台宿主 `suite-windows-hive-tests` 使用 `hive_test.c`，链接 `advapi32`、`bcrypt`，C99、`_WIN32_WINNT=0x0602`、MSVC `/utf-8 /UNDEBUG`；宿主另定义字符串 `SUITE_HIVE_PROJECT_TMP="<项目绝对路径>/.tmp"`。它没有 Qt 或 suite-putty 链接依赖。

```powershell
python -B -m unittest discover -s tools/suite/windows/hive/tests -v
python -B tools/suite/windows/hive/run_hive_test.py --binary .tmp/build/windows-release/libs/putty-runtime/tests/suite-windows-hive-tests.exe --parent .tmp/windows-hive
```

CTest 名为 `runtime-windows-hive`，标签 `runtime;windows-hive`，超时 90 秒，没有 skip 成功路径。父目录必须是项目 `.tmp/` 的直接子目录，由 C 层创建私有 DACL；调用方不要先用普通 mkdir 创建该父目录。

## 实际隔离契约

- 只接受绝对本地卷路径，项目临时根末级必须为 `.tmp`；拒绝越界、`..`、路径别名、reparse point、符号链接、非磁盘对象、文件硬链接和不匹配的实际 owner。逐层保留禁止删除共享的目录句柄，并比较句柄的实际最终路径。
- 用系统 BCrypt 生成随机 `run-<32位十六进制>` 目录；目录、hive、种子和日志的受保护 DACL 精确授予当前用户 SID 与 SYSTEM，owner 为当前用户。设置权限后再通过同一文件句柄核查实际权限。临时取消种子 DACL 的保护时，恢复加载必须拒绝；自测不向其它 SID 授权。
- `RegLoadAppKeyW(KEY_ALL_ACCESS, REG_PROCESS_APPKEY)` 在不存在的文件位置创建系统空 hive；不复制个人或 default hive，不手工拼 hive 格式。首次关闭后核查/收紧 hive 和事务日志，再加载；加载后再次检查新增日志，未满足权限要求不映射 HKCU。
- `RegOverridePredefKey(HKCU, hive)` 仅作用调用进程。每次初始化都在映射后向随机命名空间写进程标记，并从原始 hive 句柄反查。暂停时没有把映射当作子进程可以继承的权限。
- 自测在父映射活动时启动一个未初始化的子进程，只读查询随机 `Software\\SuiteHiveProbe\\run-*` 探针键，证明父标记不可见；不读个人会话，不向真实 HKCU 写入。父卸载并关闭所有键后，另一个子进程必须自己初始化同一个 hive，读取父标记并回写；父再加载验证持久化。
- `seed.bin` 只是已有私有文件的固定二进制标记，创建时通过同一句柄完成权限、写入、flush、回读。RandSeedFile 仅写进已映射的私有注册表，随后从 hive 原始句柄核验配置值。没有调用上游 `read_random_seed`、`write_random_seed` 或 PRNG。

所有文件和注册表事务日志都留在随机项目 `.tmp/` 根内。原生宿主把自己的 TEMP/TMP/TMPDIR 改到该根后才启动子进程；Python 父进程及个人 HOME/USERPROFILE/APPDATA 不变，个人代理环境仅在子进程字典清除。`GITHUB_ACTIONS` 不是任何许可条件。该探针不是防御当前相同 SID 恶意程序的通用文件沙箱。

## 证据与后续门

原生宿主现在要求 12 个具名检查：实际私有 ACL、系统空 hive 创建、HKCU/句柄双向标记、关闭重载持久化、子进程不继承、子进程显式初始化、已有私有种子文件、权限不满足时拒绝、单映射/路径拒绝、所有者移交、连续映射、结束清理。新增的移交检查使用真实工作线程，核验主线程→工作线程→主线程的操作权及其间相同标记仍可见；两项尚待新 Windows CI 执行，不能沿用此前 10 项通过。只有全部真正成功才输出 `passed: true`。Python 运行器要求完整检查集合、未遗留新运行根，并强制 `upstream_storage`、`upstream_seed`、`prng` 全为 `not-run`，`existing_tests_enabled: false`。

唯一可上传报告为 `.tmp/windows-hive-reports/private-hive.json`。不要上传 `windows-hive*/`、hive、seed 或日志树，也不要自动把此报告转换成旧 runtime/CLI 可运行许可。

2026-10-15 的[第三轮仓库 Windows CI](https://github.com/lwl0820/putty/actions/runs/38664741693) 已在 Windows Server2022 x64／MSVC19.44 实际编译并通过本宿主，运行器验证10项完整集合、清理和全部上游组仍为 `not-run`。该证据对应提交 `69e7607bd105b15b24901b6aac559ad1eefba295`。本机 Linux 的5项 Python回归另记为脚本证据，不能替代此原生结果。微软官方说明 [`RegLoadAppKeyW`](https://learn.microsoft.com/en-us/windows/win32/api/winreg/nf-winreg-regloadappkeyw) 可在缺失路径创建空 hive，并要求各参与进程自己调用；[`RegOverridePredefKey`](https://learn.microsoft.com/en-us/windows/win32/api/winreg/nf-winreg-regoverridepredefkey) 只影响调用进程。本实现没有修改 app hive 内部注册表键的安全描述符，文件 DACL 与内部注册表键 SD 是不同对象。

后续真实上游探针必须先有本阶段 Windows 通过证据，再逐进程初始化，并证明 session、host key、CA 和实际 seed 读写落点。固定上游 Windows seed 路径在配置失败时会回退 APPDATA/用户目录/Windows；仅设置 RandSeedFile 或环境变量不构成禁止回退的证明。未建立实际失败钩子和落点证据前，继续禁止上游 PRNG 和原版 CLI 自动运行。
