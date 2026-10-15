# Windows 原版 CLI 逐进程隔离纵切

## 本轮范围

本轮由主会话分派，沿已批准 foundation 任务实施。只拥有新增 `tools/suite/windows/cli/`、`cmake/SuiteWindowsCLI.cmake`，以及 `SuiteCLI.cmake` 中三个产品共用的一处可选接入。不改根 CMake、workflow、vendor、产品入口、既有 hive/seed/PrivateData、GSS/KDC 实现，不提交其它代理工作。生产 `suite-cli-build`、导入路径与安装规则没有改变。

目标是解除 Windows 原版 Plink/PSCP/PSFTP 尚无逐进程存储与种子拒回退保护的阻塞。当前只安全执行版本和非法参数；真实 SSH/SFTP、MIT GSS、SSPI、桌面和完整套件仍需后续验收，不把本纵切当作产品完成。

## 实现与源码证据

- 固定上游 `CMakeLists.txt` 创建 `settings`，`windows/CMakeLists.txt` 只向其添加一个绝对路径 `windows/storage.c`。独立外部子构建保持 `CMAKE_SOURCE_DIR=vendor/putty`，通过 `CMAKE_PROJECT_putty_INCLUDE` 登记根目录 `DEFER`。执行时严格要求原存储源唯一，换成现有 `storage_variant.c`，添加原 `seed_guard.c` 和 `private_hive.c`；没有复制存储算法。
- Plink 的 `windows/plink.c` 独立定义 `main`。PSCP/PSFTP 只有各自的 `psftp_main`，共用 `sftpclient` 中的 `windows/sftp.c` 定义 `main`。仅这两个原翻译单元被加 `main=suite_original_main`，wrapper 保留自己的 `main`；原 `argv` 与 `GetCommandLineW` 不改写。
- 原版三个程序版本路径直接 `exit(0)`；PSCP/PSFTP 非法参数直接 `exit(1)`，Plink 非法参数返回 1。wrapper 在调用前注册 `atexit`，覆盖直接退出与正常返回，不依赖替换每一处 exit。
- wrapper 先验证专用报告 HANDLE 的可读写、磁盘类型、最终精确路径、无 reparse/硬链接和失败占位，再创建私有 hive。写入 RandSeedFile 和进程标记后，从 HKCU 与 app hive 句柄分别核验；同一主线程绑定已有 seed guard 后才进入任何原版配置初始化。
- 收尾再次读回配置，要求真实 guarded 存储访问计数，解绑、close；创建后登记的 hive 文件完整集合至多 16 个，收尾发现新增/缺失/目录/reparse 即保留并失败。仅集合相同才调用原有 hive cleanup。新运行器只清已知候选文件和已空的独占父目录，不递归删除未知产物。
- 报告 HANDLE 由父 `PrivateData` 的同一文件句柄核验 owner、受保护两 SID DACL、类型、真实路径和单链接，通过 `close_fds=True`、唯一 `handle_list` 显式继承。父继承标记在 CreateProcess 返回后复位，子退出后才读取候选，原 stdout/stderr 保留为有界原始字节。
- 主会话明确认可内部复用 `gss_kdc.process._Job`，不修改其接口、不复制 Job/Toolhelp 算法。新 CLI 进程仍为挂起启动→加入 Job→唯一初始线程核对→恢复；构造、超时、输出超限或关闭失败均回收主进程及两个管道。该内部依赖未来改动需 CLI/KDC 集成回归。
- MAP 逐项要求真实 SSH、PRNG、噪声、hive、全部 guard 入口，拒绝生产 storage 对象。CMake 与 Python 共用 `link_symbols.txt`，MSVC `/INCLUDE` 精确保留清单函数，防止未调用的 guard 和 `random_setup_custom` 被链接优化删除；目标明确 x64，避免 x86 装饰名混用。链接闭包不能说明版本路径实际执行过 PRNG。报告明确角色为 guarded-test-variant，`installed_binary_verified/network_verified/gss_verified/sspi_verified` 均 false。

## 根会话 CI 接入契约

配置显式加入 `-DPUTTY_SUITE_WINDOWS_CLI_TESTS=ON`，默认 OFF。仅原生 Windows MSVC、BUILD_TESTING 和被选 CLI 组件生效。新外部目标 `suite-windows-cli-isolation-build`；生成目录同名，测试程序 `suite-isolated-{plink,pscp,psftp}.exe` 及各自 `.exe.map`，多配置保持配置子目录，不安装。

三个 CTest 名固定 `cli-plink-windows-isolation`、`cli-pscp-windows-isolation`、`cli-psftp-windows-isolation`，标签均含 `windows-cli`，每项 TIMEOUT=70 秒。CLI 运行器父硬期限 55 秒，单个程序 15 秒、统一回收宽限 3 秒。每项运行两个案例 `version`、`unknown-option`，验收同时要求固定原版退出码、原标准流、完整严格候选、精确清理。

最终报告为 `.tmp/windows-cli-reports/{plink,pscp,psftp}.json`，`schema=1`、`kind=windows-cli-isolation`、`component` 对应程序、`passed=true` 只在全部阶段通过后发布。首次执行工作前覆盖为失败占位；硬超时/崩溃不得沿用旧成功。候选通过不等于最终通过，最终文件通过还须配合运行器退出 0。stdout 摘要先 flush，最终报告随后原子替换；不承诺文件发布与进程退出原子化。

工作流由其它代理/根会话负责，应要求恰好三个 `windows-cli` 标签用例及 JUnit 全过，只上传上述三个最终公开报告，不能上传候选、hive/seed/私有根。原普通生产 exe 仍不自动运行。后续 GSS 接入必须扩充固定场景白名单、证明代理/子程序隔离和实际 PRNG 读写，不能因本轮版本通过直接运行任意命令。

## 本机验证

环境：Linux/Arch、GCC 16.2.1、系统 Python 3.14，CMake/Ninja。没有 Windows SDK/MSVC/Wine/MinGW；以下结果不是 Windows 原生证据。

1. `python3 -B -m unittest discover -s tools/suite/windows/cli/tests -v`：15 项通过，最后记录轮 0.911 秒、无跳过。覆盖严格 JSON 字段/重复/整数/长度、MAP 正式存储拒绝与必需函数逐个缺失、普通 binary 拒绝、环境去个人值、旧成功替换、实际子进程 stdout/stderr/退出码保持、超时/超限回收、未知文件保留与精确空根清理、父进程硬期限、显式 HANDLE 继承复位和 Job 构造失败回收。
2. 其中三项使用真实 CMake/Ninja 生成模拟上游图：实际 `DEFER` 执行后没有 storage.c、恰一个 storage_variant、三个 wrapper、恰两个原入口带 main 重命名；重复 storage 与缺失 sftp 入口分别配置失败。没有模拟 Win32 API 成功或生成原生通过报告。
3. Linux 正式 CLI-only 新目录 `.tmp/build/windows-cli-guard-linux`，Release、BUILD_TESTING=OFF、关闭 PuTTYgen、开启全部三 CLI、显式禁 Qt 查找：配置退出 0；独立上游 285 步编译及三个原程序链接退出 0。未运行这些普通程序，也未配置 Windows 夹具目标。
4. 全部组件关闭的新目录 `.tmp/build/windows-cli-guard-off` 配置及构建均退出 0；没有 Qt 查找或 Windows 夹具目标。4 个 Python 文件的 3.11 AST 语法兼容检查与责任范围文本的空白检查通过，`git diff --check` 通过。

## 独立检查修正

独立检查修正了进程收尾 `wait()` 抛出系统错误时会跳过后续管道／读取线程关闭的分支，新增两项对应回归；CMake 图测试统一使用 `as_posix()` 写入 CMake 文本与参数，避免 Windows 路径被解释为反斜杠转义。主 Windows 作业的公共脚本步骤已追加本目录轻量回归及明确退出码门。新旧测试合计 17／17 在本机通过，根 CTest 门禁合同 22／22 通过；正式 Linux CLI-only 与全关闭增量构建继续成功。`suite-build.md` 第 3 节同步测试变体不安装、普通 CLI 不自动执行及原生门边界。详细独立记录见 `windows-cli-isolation-review.md`，Windows 原生行为仍待 CI。

## 原生风险与后续门

必须让 Windows CI 实际确认 MSVC 编译、main 重命名链接、MAP 符号、两 SID ACL、继承文件 HANDLE、atexit、RegOverridePredefKey 和实际 hive 事务文件集合。初次集合之外的日志出现时应固定失败并调查，不能放宽成递归删除。还需检查多配置真实产物和未安装测试程序；Linux 图检查不能代替上述行为。

共享 hive 清理接口内部仍枚举其私有平面根，本调用先以本次登记的精确集合把关。当前只有无网络、没有自行生成资料的固定入口；未来接入网络和异步子程序前应复核迟到产物和清理竞态，必要时由公共接口所有者增加精确清单删除契约。没有宣称对同 SID 恶意并发程序提供通用沙箱。

本轮没有 Windows 原生通过结果；任务保持进行中。
