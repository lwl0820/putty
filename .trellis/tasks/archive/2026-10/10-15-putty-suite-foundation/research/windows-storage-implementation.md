# Windows 上游存储隔离实施

日期：2026-10-15。独占新增 `tools/suite/windows/storage/` 和本报告；没有改既有 hive、工作流、CMake、runtime、apps 或 vendor。前置 hive 由提交 `69e7607` 的[第三轮 Windows CI](https://github.com/lwl0820/putty/actions/runs/38664741693)证明 10 项通过；本批新存储宿主尚未原生编译或执行，不能继承该通过结论。未提交、未改任务状态。

## 行为差距及责任边界

现有 hive 已证明逐进程 HKCU 映射和精确私有文件权限，但 Windows 上游 `access_random_seed` 在缺配置或失败时会继续找 APPDATA、用户和 Windows 目录。只设置 RandSeedFile 无法保证失败情况下隔离。因此本批把适配放在真实 Windows storage 源的文件/API 边界，不修改生产回退语义，也不开放旧 runtime/原版 CLI。

| 新文件 | 责任 |
| --- | --- |
| `storage_variant.c` | include 真实 Windows storage 源，仅该翻译单元替换 HKCU/文件/回退 API 边界 |
| `seed_guard.c/h` | 绑定已验证 hive，精确 seed 候选、实际句柄权限/路径检查、权限通过后同句柄截断、失败进程退出、读写计数 |
| `storage_test.c` | 19 项原生计划、六种真实子进程拒绝、hive 反查、真实 PRNG 与 timer 清理 |
| `frontend.c` | 前端 timer/致命错误策略和不创建网络的协议元数据表 |
| `run_storage_test.py` | 原生与 EXE 白名单、源摘要、MAP 审计、完整摘要和残留拒绝 |
| `tests/test_storage_runner.py`、`README.md` | 本机拒绝门回归、构建/运行/证据与缺口 |

CMake 由主代理另接 `suite-windows-storage-tests`，本目录四个 `.c` 加上游 no-jump-list，link suite-windows-hive/suite-putty，无 Qt/putty-runtime，C99、MSVC /utf-8 /UNDEBUG、项目 .tmp 宏与 /MAP。CTest `runtime-windows-storage` 使用新 runner，parent 为 `.tmp/windows-storage-<build-id>`，runtime;windows-storage，120 秒无 skip。

## 关闭失败回退的证据设计

上游函数体仍由固定源编译，真实 `read_random_seed/write_random_seed` 调用受控 Win32 边界。HKCU getter 在任何上游注册表访问前要求本线程 hive ready，并返回被 RegOverride 映射的真实伪句柄；原始 hive 句柄只做独立反查。

guard 在真实 CreateFile 前精确比较候选路径；只接受本次已有 seed。上游写分支的 CREATE_ALWAYS 收窄为 OPEN_EXISTING/OPEN_REPARSE_POINT，打开后通过同一句柄核验 owner、当前 SID+SYSTEM 两条完全访问 ACE、受保护无继承 DACL、实际最终路径、非 reparse/目录/硬链接，再在该句柄截断。成功写入要求完整长度、flush、关闭。缺配置进入 fallback 之前的 load_system32_dll 钩子、任何非法候选/打开失败/I/O 失败都直接专用 ExitProcess，不能返回继续尝试。

六个独立子进程各自显式初始化（未绑定用例故意不初始化），用 85/82/82/80/81/81 精确拒绝码检验未绑定、缺失、损坏、非法候选和实际独占锁引发的读写打开失败。缺/损坏配置在任何个人目录候选查询或打开前终止；错误 seed 在第一次真实打开前终止。父进程在每个退出后重新映射并恢复私有配置、逐字节检查 seed 未变。拒绝消息只含代码，不输出 SID、路径或敏感字节。

真实 settings 使用完整 Conf 保存/加载、字体/文件名和枚举；host cache 覆盖三种返回值，host CA 覆盖 save/load/enum 与选项。原始 hive HKEY 独立读取会话、host key、CA 公共材料与 RandSeedFile。一个正常子进程须自己 open/绑定，再读取所有真实存储并回写标记，父进程独立反查。

PRNG 在 hive 持续映射和 guard 活动期间初始化：真实 windows/noise、sshrand、crypto/prng、timing 从 suite-putty 链接。检查真实系统随机源成功、seed 读写增加、随机输出差异、save/clear 及另一轮 ref/unref 后 timer 队列无剩余；所有随机材料清零，不日志。仅验证执行与生命周期，不宣称随机质量。Windows 公共目录噪声读取和临时加密上下文保留原实现，不读取个人 seed。

runner 要求 19 项集合恰好完整，MAP 有 variant/guard/settings/noise/sshrand 及实际 PRNG、timer 符号，无生产 storage.c.obj。重新审查主代理的唯一 `close_regkey(rkey)` 差异后，锁定当前完整 Windows storage 源 SHA256 `9c34b56e5f1705af09125bf1d851be2f51c9d9707b4f05b2abda6cc172e4fbb9`；源变化先重新审查全部候选和钩子，不自动刷新摘要。source/MAP 不是原生执行替代，仍需 CI。

## 发现的上游 host CA 删除泄漏

源码位置：`vendor/putty/windows/storage.c:512-524`。`host_ca_delete` 的 `open_regkey_rw(HKEY_CURRENT_USER, host_ca_key)` 成功后，对子键 del_regkey，再释放 name buffer，直接 `return NULL`，未对 rkey 调用 `close_regkey`。这是源码确认的遗漏，尚未在本机 Windows 复现。

最小待原生复现：在已初始化的私有 hive 内先保存一条 CA，记录进程 handle 数，循环调用 `host_ca_delete`（已删除子项时 host_ca_key 仍存在，因此每轮仍成功打开父键），再比较 handle 数/尝试父卸载后由另一进程独占加载。没有源修复时，每轮缺少对应关闭，也会破坏夹具“pause 前释放全部子键”契约。必需最小修复位置为函数成功分支 `strbuf_free(sb)` 后、`return NULL` 前 `close_regkey(rkey)`，另应添加成功删除与重复删除的真实原生回归。

主代理已另做 vendor 最小关闭修复及 `vendor/patches/0009-host-ca-delete-handle.patch` 和来源。已读取源码差异确认仅增 `close_regkey(rkey)`，没有改 seed/fallback。宿主恢复真实删除：先独立确认 CA 父键不存在并调用缺失分支；正常保存/加载后实际删除、hive 句柄反查子键消失，再对存在父键重复删除 128 次；各阶段比较实际进程 handle 数不增长。此回归若原生成功，才在完整 19 项成功报告中计 `host-ca-delete`；当前仍未运行。会话 del_settings 也实际纳入计划。

## 本机检查与待执行项

`python3 -B -m unittest discover -s tools/suite/windows/storage/tests -v`：6 项通过，0 失败、0 跳过，0.070 秒。覆盖完整报告及未验状态、MAP 缺少真实 PRNG/variant 或混入生产 storage 时拒绝、源变化必须重审（仅归一 CRLF 为 LF 的同源 checkout 接受）、伪 CI 标记不能绕过平台、原版 CLI 拒绝且不建私有父目录、cp1252 管道输出 UTF-8。19 项 C JSON 与 runner 集合一致；2 个新 Python AST、9 个交付文件 UTF-8/尾换行/行尾空白检查通过。

未执行：Windows 编译/链接、19 个原生检查（含 CA 删除补丁的 handle 回归）、精确文件 ACL、被独占锁拒绝的实际错误、异常进程退出后的 hive/日志、跨进程和 PRNG/timer 清理。独立检查后由主代理接 CI；没有把静态结果或前置 hive CI 填成本批通过。

唯一摘要 `.tmp/windows-storage-reports/private-storage.json`（可显式另选公开报告目录），强制 `production_fallback/upstream_cli: not-run`、`existing_tests_enabled: false`。私有 root、hive、seed、日志不上传。Windows 10/11 桌面、旧 runtime、原版 CLI、生产 seed 回退继续保留缺口；本批 CA 删除和其它原生证据均等待实际执行。
