# 共享基础平台验证记录

日期：2026-10-15。以下为主会话执行的结果，保留独立审查和实施代理报告的来源边界。基础任务仍为进行中，不能据此认定六组件已交付。

## 本机集成

环境：Arch Linux x64、GCC/G++ 16.2.1、CMake 4.4.4、Qt 6.12.0。构建目录 `.tmp/build/suite-integrated`，Debug，同时启用既有 PuTTYgen GUI、共享运行时和测试。

最终增量构建退出 0。`ctest --test-dir .tmp/build/suite-integrated --output-on-failure --no-tests=error` 为 **8/8 通过，20.74 秒**：公共隔离夹具、PuTTYgen 核心往返/原版互操作/GUI 交互、运行时生命周期、真实 Windows 等待算法的 Linux 宿主、完整客户端链接/配置/日志、独立代理核心链接/初始化。

公共夹具内部 **31/31、0 跳过**，覆盖真实 SSH/SFTP；运行时包括复制前限额、不可读 payload 拒绝及旧等待快照回归。相关日志为 `.tmp/suite-validation/integrated-r1r2-build.log`、`integrated-r1r2-ctest.log`。默认沙箱首次禁止回环和 Unix socket，两个相关 CTest 因权限失败；允许本次受控回环测试后得到上述结果，未把初次权限失败归为产品缺陷。

本轮 GUI 为离屏自动交互，不代替 X11/Wayland 真桌面。原有 PuTTYgen 测试的隔离机制保持原样，不能把新公共夹具的全部保证追溯赋予旧测试。

## 目标 Ubuntu 与固定 Qt

使用已有 `puttygen-build-ready:ubuntu2204`、`ubuntu2404` 镜像。容器禁网、根文件系统及源码只读，只允许项目 `.tmp/` 可写，以 uid/gid 1000 运行；不读取个人配置或代理。Qt 前缀分别为 `.tmp/qt-ubuntu22.04`、`.tmp/qt-ubuntu24.04`，均为工程锁定 **Qt 6.8.3**。使用项目已缓存 CMake 3.30.9；Ubuntu 22.04 的系统 CMake 3.22 不满足项目最低版本。

| 系统 | 工具链 | 构建与运行结果 |
| --- | --- | --- |
| Ubuntu 22.04 x64 | GCC/G++ 11.4 | Debug 运行时及真实链接目标构建退出 0，runtime 标签 5/5 通过，1.65 秒 |
| Ubuntu 24.04 x64 | GCC/G++ 13 | Debug 运行时及真实链接目标构建退出 0，夹具身份修复后的 runtime 标签最终轮 5/5 通过，1.70 秒 |
| 两个 Ubuntu | 对应系统工具链 | 三 CLI 的 Release 仅构建、精确安装和 `-V` 均退出 0；安装恰好三个程序；禁 Qt 的全部关闭配置及构建退出 0 |

两个 Ubuntu 构建镜像缺少 OpenSSH 服务，公共夹具的真实 SSH 组明确跳过，其余隔离用例实际运行；**CTest 5/5 不表示该组互操作已在 Ubuntu 完成**。运行时自身的真实 TCP 生命周期用例没有跳过。Ubuntu 22.04 的无 passwd 项身份暴露夹具初始化顺序错误，已修为先核依赖、缺真实身份返回可解释异常，再复跑成功；没有创建或伪造系统账户。

命令脚本：`.tmp/suite-validation/validate_runtime.sh`、`validate_cli.sh`。日志：`ubuntu22-runtime-r1r2-final.log`、`ubuntu24-runtime-r1r2-final.log`、`ubuntu22-cli-final.log`、`ubuntu24-cli-final.log`。CLI 的连接/传输功能由独立 CLI 任务记录，不由版本输出推导。

## 多配置与独立审查

独立审查发现多配置共用输出路径导致按 Release 安装误收 Debug，主会话修复后实测：仅构建 Debug 时 Release 安装非零失败；分别构建并安装后，两个安装摘要各自匹配对应配置，且配置之间摘要不同。详见 `build-review.md` B4 补记与 `.tmp/suite-validation/multi-*.log`。

R1/R2 的普通与 ASan/UBSan 结果由运行时实施代理记录：两组均 2/2 通过，无 sanitizer 诊断；真实算法测试编译上游 `handle-wait.c` 与提取的真实头。`0002/0003` 反向补丁校验及空白检查通过。独立最终复核另记于 `runtime-review.md`。

独立复核继续发现 R3：Unix 同批读回调发起关闭后仍进入写回调。修复前真实 `socketpair` 探针记录 `read=1/write_after_close=1`；逐项重查关闭及保护分发深度后，同一探针为 `read=1/write_after_close=0`。独立 Release 与 ASan/UBSan 定向生命周期均通过。主会话再验本机集成生命周期 **1/1，1.39 秒**，Ubuntu 22.04/24.04 的运行时标签各 **5/5，1.53/1.66 秒**；各日志为 `integrated-r3-*`、`ubuntu22-runtime-r3.log`、`ubuntu24-runtime-r3.log`。没有无故重跑不受此修复影响的 PuTTYgen 组。

## 未执行项

Windows 原生编译、HKCU 隔离、真实 HWND/socket/HANDLE 与自动/手动复位事件仍未执行；Linux 算法宿主不能替代。两个 Ubuntu 的真实 X11/Wayland 桌面、实际 GSS/KDF 与终端线程绘制纵切属于后续任务。完整套件发布包及六组件联合验收尚未完成。

## 首次仓库 Windows CI

用户选择“使用仓库的 Windows CI，桌面操作由我验收”。主会话完成 Windows CI 和 CLI 的独立审查后，将明确选定的 58 个共享基础／CLI／CI 文件提交为 `24e054b47b54b48365f03993bb6c9a02868d1ef4`，推送到 `codex/putty-suite-completion`。既有 PuTTYgen 工作流也按原有 push 触发，未修改它。

新 [Windows 原生检查运行](https://github.com/lwl0820/putty/actions/runs/38662908235) 已完成，首轮结果为失败：禁 Qt 全关闭、PSFTP 独立多配置安装边界和固定 Qt 6.8.3 准备均通过，实际共享运行时编译在 `runtime.cpp:196` 报 MSVC C2061，使用了没有声明的全局 `HANDLE`。两个原生宿主因此未执行，不能填写通过。修复改用公开的 `Qt::HANDLE`，与固定 Qt 6.8.3 构造参数的实际类型一致。独立审查发现类内 `QWinEventNotifier::HANDLE` 别名私有，已在提交前排除该写法；等待下一轮 CI 验证。普通生命周期的 Windows 存储组仍跳过；私有应用 hive 探针另行开发，未开放原版子进程或真实存储测试。

同次推送触发的[既有 PuTTYgen 工作流](https://github.com/lwl0820/putty/actions/runs/38662908025) 已全部成功，四个实际任务分别为 Windows MSVC 2022 x64、Ubuntu 22.04 x64、Ubuntu 24.04 x64、Arch Linux x86_64。其原有自动测试、打包、安装卸载及冒烟范围通过，不能扩张为新增终端或 Pageant 的验收。

句柄类型修复提交 `5e1e301f28eb2da31ba56f29f69618248f8259db` 后，[第二轮 Windows CI](https://github.com/lwl0820/putty/actions/runs/38663784108) 已完成，结果仍为失败。`runtime.cpp` 已编译成功，原 `putty-runtime-tests.exe` 链接暴露 `storage.c` 引用的 `remove_session_from_jumplist`／`clear_jumplist` 未提供。已为此单一测试宿主增加正式上游 `windows/no-jump-list.c`，未改变产品跳转列表，也未开放该宿主的 Windows 存储执行；旧运行器仍返回 77。两个无存储原生宿主因构建阶段失败尚未执行。下一批同时纳入独立审查完成的私有 hive 自测及强制实际结果门，继续由原生 CI 判定。

提交 `69e7607bd105b15b24901b6aac559ad1eefba295` 的[第三轮 Windows CI](https://github.com/lwl0820/putty/actions/runs/38664741693) 已实际完成并成功，MSVC 19.44／Windows Server 2022 x64／Qt6.8.3。全量构建、Release断言、三个CLI精确安装及Debug／Release安装边界通过。CTest共注册5项，4项实际通过、1项明确跳过：真实HANDLE宿主0.06秒、真实Qt HWND／TCP／OOB宿主0.44秒、私有app hive宿主0.08秒；`runtime-lifecycle` 因尚未接入原版存储隔离返回77。`suite-fixture` 在Windows仅提供平台拒绝边界，不能算Linux真实SSH组已运行。

私有hive宿主的运行器强制核对10个具名检查、唯一JSON和清理结果，包括当前SID＋SYSTEM DACL、真实HKCU句柄标记、重载、子进程不继承／显式初始化、私有seed文件权限拒绝、单映射／活动清理拒绝和最终清理。**这仍是隔离能力探针**，其 `upstream_storage`／`upstream_seed`／`prng` 均为 `not-run`，没有凭此打开原CLI进程或记录存储兼容成功。后续才能基于已验证探针实现对应宿主。完整日志在 `.tmp/suite-validation/windows-third-ci.log`，状态在 `windows-third-ci-status.json`；上传仅含指定报告与程序，不含hive／seed／运行根。

提交前 CLI 独立审查补强分片 TCP 读取和异常退出清理，完整回归为 **8/8 组、70 项、0 跳过，46.01 秒**；公共夹具定向再验为 **31/31、0 跳过，2.288 秒**。补丁文件中的单空格上下文属于标准 diff 格式；源码／文档的暂存空白检查退出 0，0002/0003 在 `vendor/putty` 相对根的反向校验退出 0。

终端子任务新增 `putty-qt` 与 `putty-terminal-tests` 后，主会话在两版既有 Ubuntu／固定 Qt 6.8.3 环境中实际编译链接两目标，均退出 0，日志为 `.tmp/suite-validation/ubuntu22-terminal-build.log`、`ubuntu24-terminal-build.log`。这一轮没有在 Ubuntu 运行终端认证或字体测试，不能据此补勾产品纵切门。

## Linux 组件检查点与首次目标宿主 CI

独立终端审查补齐两个 Ubuntu／Qt6.8.3 的真实加密 PPK 与 GSS 延迟／关闭运行证据，详见终端子任务 `research/terminal-review.md`；Pageant 普通及 ASan/UBSan/LSan 均10/10，详见代理子任务 `research/review.md`。Linux 夹具／工作流独立审查见本任务 `research/linux-ci-review.md`。

主会话首次同一配置启用六组件，`.tmp/build/suite-integrated-all` Release 构建成功。首轮32项中31项通过，唯一 PuTTYgen GUI 失败发生于其它会话并行提交布局修复期间；对已提交 `bfd1f26` 的新源码增量重建后，该用例1/1通过，6.40秒。没有改写该会话的产品修复。原始集成与补测日志保留在 `.tmp/suite-validation/integrated-all-ctest.log` 和 `integrated-all-pg-refresh-ctest.log`，不能将分次结果写成一次32/32。

稳定 Linux 实现、必要上游0004–0008补丁及新 Ubuntu 工作流一起提交为 `61f7584`。其 [Windows 原生回归](https://github.com/lwl0820/putty/actions/runs/38666390874)及[既有 PuTTYgen 四平台回归](https://github.com/lwl0820/putty/actions/runs/38666391115)均实际成功；新 GUI Windows 功能尚未接入这一轮。

[首轮 Ubuntu 套件 CI](https://github.com/lwl0820/putty/actions/runs/38666391207)在真实 Ubuntu22.04／GCC11 和 Ubuntu24.04／GCC13、Qt6.8.3 上完成，各 **28/29 CTest 通过**。实际容器与 KDC 前置检查成功，终端6项、Pageant10项和CLI8项均通过；唯一失败是 `suite-fixture` 内 `test_unsafe_parent_permissions_are_rejected_without_repair`。运行器主动设置 `umask(077)`，负例的 `mkdir(mode=0755)` 因此得到0700，没有触发预期拒绝；不是产品放行宽权限。

修复仅在负例创建后显式 `chmod(0755)`，并在调用隔离器前断言实际权限；隔离器源码未改。本机在 `umask077` 下全夹具 **31/31、0skip，2.220秒**，日志 `linux-isolation-umask077.log`。该单文件修复提交 `3d061a0` 并推送重跑，目标宿主新结果待完成。两版首次失败日志为 `linux-ubuntu22-first-ci.log`、`linux-ubuntu24-first-ci.log`，保留失败证据，不放宽结果门或减少用例。

`3d061a0` 的[第二轮 Ubuntu CI](https://github.com/lwl0820/putty/actions/runs/38666924374)已实际全部成功：Ubuntu22.04／24.04 各 **29/29 CTest，零失败、零跳过**。Ubuntu22.04 日志明确记录90.51秒、容器前置9项与GSS前置4项均成功；严格运行器确认完整注册和实际结果一致。证据 `linux-umask-ci-status.json`、`linux-ubuntu22-second-ci.log`。这补齐目标 Ubuntu 宿主的真实容器、Pageant和终端纵切自动验证；X11/Wayland真桌面及后续新增代码仍须分别验证。

独立存储审查另修复 `host_ca_load` 的PublicKey／Validity临时字符串及旧MatchHosts缓冲释放，补丁0010与0009一起精确正反向核验。`02841d5` 已提交独立受控Windows存储宿主与19项结果门；新MSVC原生结果待完成，原CLI与既有runtime存储仍未开放。详见 `windows-storage-review.md`。

## Windows 存储首次执行与终端接线

`02841d5` 的 [Windows CI](https://github.com/lwl0820/putty/actions/runs/38667665115) 已真实编译并安装共享核心、三个 CLI 和私有存储宿主；执行阶段 `runtime-windows-storage` 失败，退出3221226505，原版 Conf 的 `CONF_TYPE_STR` 断言触发。事件、HANDLE、hive 仍通过，旧 runtime-lifecycle 明确跳过。本次失败保留于 `.tmp/suite-validation/windows-storage-first-ci.log`，不得写成19项存储检查成功。已派独立检查修复宿主字段类型调用，不改变产品断言或隔离策略。

终端独立审查已完成，修正 Windows OEM 字符表模式偏差；本机局部5/5及OEM确定性探针成功，原生仍待CI。根 runner 新增 `--require-terminal`，预注册及实际JUnit必须恰四项且全通过；Linux严格必需集合增加 `terminal-cpu-render`。公共脚本17/17、Linux拒绝门4/4通过。本批尚未推送，仍不执行含生产存储的旧终端测试或主程序。

`2f1a970` 已推送终端原生接线与存储宿主类型修复，Windows run38669409654／Ubuntu run38669409661开始执行。此前 `02841d5` 的 Ubuntu24.04 全部通过，22.04 的 runtime-lifecycle 在 inline OOB 用例等待 `urgent` 状态超时，数据／事件时序正在独立检查；日志 `.tmp/suite-validation/linux-ubuntu22-storage-ci.log`。不能将上一SHA的全部成功沿用至本SHA。

Pageant独立审查已修复一次性客户端无限查询和失败被当空库的问题。根新增Pageant预注册/实际JUnit双门，将其标签与原runtime两项明确区分，并改完整配置只按三CLI组件安装，保持精确清单。公共Windows18/18、Linux契约4/4、Pageant runner3/3通过；Pageant原生实现尚待提交及MSVC运行。

## 终端首次原生构建结果

`2f1a970` 的 Ubuntu run38669409661 和 PuTTYgen四平台 run38669409625 均实际成功；Windows run38669409654在链接 `putty-terminal-platform-tests.exe` 时失败，唯一已报告缺失为 `modalfatalbox`（LNK2019）。因此后续CTest未运行，本SHA不能给存储修复或四项原生终端记通过。日志 `.tmp/suite-validation/windows-terminal-first-ci.log`。根为独立平台测试补与现原生HANDLE宿主相同的致命错误入口，不引入GUI/存储。

另修正真实Qt UTF-8密钥路径在Windows被`filename_from_str`按ACP解释的产品缺口，经内部`pt_filename_from_utf8`调用正式平台构造，平台回归验证中文空格路径的宽字符串。Linux三个受影响目标增量构建成功，terminal本地5/5、0skip、0.44秒；Windows仍须下一次CI验证。

`992fb00` 已推送 Pageant Windows 产品／双IPC测试、终端中文路径与链接修复、受控SSH对端和OOB测试修正。Pageant独立审查及公共接线检查无剩余已确认局部问题；这次仅针对审查稳定文件暂存，仍在实施的hive线程移交与Windows真实PPK宿主没有带入提交。新CI尚待结果，不能继承此前Windows事件/hive成功作为新15项IPC或19项存储成功。

最终SSH对端真实自测11/11、0skip，审查后原版Plink两固定命令再次真实通过，详情见 `research/windows-ssh-peer-review.md`。PPK helper先决核查发现：上游Argon2轮数和毫秒字段是同一union，原PuTTYgen映射没有错误；同配置自动1500ms真实生成377轮、解密1825.811ms，保留产品原代码，详见终端任务 `research/puttygen-kdf-review.md`。

## Pageant 与受控存储首次原生通过

`992fb00` 的 [Windows CI](https://github.com/lwl0820/putty/actions/runs/38670751882) 已完成全部产品与测试编译、三个 CLI 的精确安装。CTest 注册 12 项，9 项实际通过、2 项崩溃、1 项明确跳过；其中受控存储宿主19项为首次真实通过（0.18秒），Pageant私有双IPC宿主15项为首次真实通过（17.28秒）。原生事件、HANDLE、旧10项hive及两项MIC窄夹具也通过。`runtime-lifecycle` 仍返回77，原版CLI／生产seed回退／原名跨版本与权限／桌面未验。

两项崩溃为 `terminal-cpu-render`（0.05秒）与 `terminal-windows-platform`（0.01秒），均无诊断输出，整轮结果为失败。独立检查已确认平台适配调用了上游未在头文件公开声明的 `filename_from_utf8`，MSVC隐式int使64位返回指针截断；正在改用公开的UTF-8解码及宽路径构造接口，等待下一原生运行。CPU崩溃尚未定位，不凭推测改动生产绘制。

同一提交的 [Ubuntu CI](https://github.com/lwl0820/putty/actions/runs/38670751793) 两目标均全部成功，22.04日志明确为31/31 CTest、零跳过；[既有 PuTTYgen 四平台 CI](https://github.com/lwl0820/putty/actions/runs/38670751773) 亦成功。Windows日志 `.tmp/suite-validation/windows-pageant-first-ci.log`，Ubuntu22.04日志 `ubuntu22-pageant-first-ci.log`。新增hive连续所有权移交12项和Windows真实PPK终端宿主仍在实施，均不在本SHA已验范围。

## Windows 真实 PPK 与系统 SSPI 负例检查点

独立检查后，将34个明确文件提交为 `879f5ce` 并推送现有分支：中文路径改用公开宽路径接口、MSVC隐式函数声明编译拒绝、离屏字体原环境诊断与正式系统字体夹具；hive连续操作权移交、正式终端受控模块、同配置独立PG校准工具、真实SSH/PPK宿主；SSPI初始化失败时间输出补丁0011、20项原文窄回归及真实系统负例宿主/运行器。新增根门严格区分四项基础终端、真实PPK组和原生SSPI负例，不把SSPI负例转换为正向成功。

本机增量编译成功；根定点CPU/MIC/新SSPI-init共4/4、零skip；PPK实施与独立检查最终11/11、hive5/5、公共含SSPI脚本34/34、Linux结果门4/4。补丁0011精确正反应用与原文确定失败/修复20项及ASan/UBSan/默认LSan证据在 `.tmp/sspi-init/result.json`。补丁文件的单空格上下文是标准diff内容，其余暂存源码/文档空白检查通过。

这一检查点尚待新的Windows SDK编译及真实执行。没有把本机窄夹具、hive旧10项或上一轮Pageant/存储成功沿用为新12项/五核心PPK/系统SSPI成功。Windows GSS便携DLL、受控KDC与真实长调用继续独立研究；技术门未齐前保持完整配置UI未启动。

## Windows PPK 检查点的前置与编译结果

`879f5ce` 的 [Windows CI](https://github.com/lwl0820/putty/actions/runs/38673305267) 在安装受控 SSH 对端依赖前失败：runner 自带 pip24.0 按 cp1252 解码含中文注释的 UTF-8 清单，报 UnicodeDecodeError。后续原生构建／执行均跳过。修复仅在 requirements.txt 首行声明 UTF-8；使用真实 pip24.0 的解码函数及 cp1252 本机确定复现旧失败、验证新清单成功，没有更改全局 Python／MSVC 输出解码。该两文件修复提交 `70e2871`。

`70e2871` 的 [Windows CI](https://github.com/lwl0820/putty/actions/runs/38673982402) 已通过真实 SSH 对端11项自测及公共脚本，并首次成功编译／链接独立 PPK 校准工具和真实 SSPI 负例程序。全量构建随后在 guarded_module.c 失败：hive_bootstrap.h 间接引入 windows.h，之后 putty.h 引入 winsock2.h，触发 SDK 类型重定义。hive_bootstrap.c 有相同确定包含链，独立检查正在一起修复。该轮 CTest 未执行，不计原生 PPK、SSPI 或新12项 hive 成功。日志 `.tmp/suite-validation/windows-ppk-second-ci.log`。

同一提交的 [双 Ubuntu 套件 CI](https://github.com/lwl0820/putty/actions/runs/38673982301) 及 [既有 PuTTYgen 四平台 CI](https://github.com/lwl0820/putty/actions/runs/38673982295) 实际全部成功。对应状态保存为 `.tmp/suite-validation/ubuntu-ppk-second-ci-status.json` 与 `puttygen-ppk-second-ci-status.json`；没有从工作流成功推断目标桌面人工验收。

`093f038` 的 [下一轮 Windows CI](https://github.com/lwl0820/putty/actions/runs/38674892493) 确认两个宿主包含顺序修复已通过 MSVC。全量构建继续在 `storage_variant.c` 的明确 ANSI 接口保护处失败：Qt 链接目标传播 `UNICODE`，将受控上游存储翻译单元纳入其编译环境不符合契约。正在将该两个存储／seed 对象放入非 Qt 对象库，再直接注入最终测试程序；不移除保护、不在整个 Qt 目标取消 Unicode，也不以全局源属性影响其它宿主。此轮仍未执行 CTest，日志 `.tmp/suite-validation/windows-ppk-third-ci.log`。

## Windows 首次系统 SSPI 负例及新 hive 通过

`3595796` 的 [Windows CI](https://github.com/lwl0820/putty/actions/runs/38675639308) 已完成全部构建和16项CTest：14项通过、1项失败、旧runtime-lifecycle明确跳过。首次通过包括连续所有权移交的新12项hive、系统SSPI负例（0.11秒）；受控storage19、Pageant15及runner、真实事件／HANDLE、三项MIC/init窄夹具继续通过。

CPU原环境诊断实际枚举0个字体，新增明确拒绝返回1；设置 `QT_QPA_FONTDIR` 为系统Fonts后的正式CPU CTest成功（2.07秒）。公开宽路径修复后的平台测试也原生成功。原最初CPU SegFault没有保留调用栈，本轮证明字体前置与正式环境成功，不凭此虚构原始异常栈。

唯一失败为真实PPK终端runner在执行helper前的MAP核查：它要求 `random_setup_special` 符号，但正式 `putty.h:2245` 中该名称是宏，实际展开调用 `random_setup_custom`；不是缺失真实初始化函数。修正要求实际可链接函数名，并新增回归拒绝仅有宏名的假MAP。现本机12/12零跳过；没有改helper、产品密钥核心或降低KDF阈值。新的真实认证／五核心／ACL执行仍待下一原生轮。日志 `.tmp/suite-validation/windows-ppk-fourth-ci.log`。

## Windows PPK 正式私钥权限契约核对

`665fa8a` 的 [Windows CI](https://github.com/lwl0820/putty/actions/runs/38676436402) 完成全部编译、安装和16项CTest，14项实际通过、1项失败、旧runtime明确跳过。MAP门已通过，独立工具实际生成加密PPK后，运行器因要求两条SID许可而拒绝。正式 `pg_platform.c:verify_private_security` 明确要求私钥仅当前用户一条许可，故为测试契约偏差，未证明真实SSH认证或五核心关闭成功。日志 `.tmp/suite-validation/windows-ppk-fifth-ci.log`。

修复为公共PrivateData新增显式单用户检查模式，只用于未经改写的产品PPK；通用夹具仍严格两SID。明确禁止同时harden，新增内存描述符回归检查多余身份、继承、owner、保护位及权限拒绝。本机公共39项与PPK运行器12项全过；这些是检查逻辑证据，实际Windows密钥ACL、认证与绘制仍待后续CI。

## Windows PPK 首次实际完成终端阶段

`8df3261` 的 [Windows CI](https://github.com/lwl0820/putty/actions/runs/38678572546) 已通过直接读取正式导出PPK的单用户ACL。真实终端宿主持续25.05秒，阶段记录覆盖主机密钥拒绝、密钥负载与呈现、计算中关闭、提示取消、有序输入与背压，并抵达终端摘要完成；最后私有hive核验与清理报代码13，整项失败，不能据阶段记录写成纵切通过。其它14项CTest实际成功，旧runtime仍明确跳过。失败日志 `.tmp/suite-validation/windows-ppk-sixth-ci.log`。现独立实施者定位收尾具体契约，保持原强门。

同一提交的双Ubuntu与既有PuTTYgen四平台CI均成功。`af4919b` 已推送正式MIT无凭据夹具和独立WindowsABI作业，13项边界回归及独立审查通过；六DLL真实加载、ACL、机制及配置读回仍待新原生CI，不算正向Kerberos/SSPI认证。

## MIT 首次原生 ABI 配置检查结果

`af4919b` 的 [Windows CI](https://github.com/lwl0820/putty/actions/runs/38678912619) 已完成。独立 MIT 作业的公共 ACL 7 项及 MIT 边界 13 项原生回归通过；固定 MSI 的纯提取、10 个文件／6 个 DLL／2,960,208 字节及 PE 依赖闭包核对通过。实际探针在 `configuration-defaults` 阶段失败，耗时 0.31 秒，不能计完整 ABI 验证或配置隔离成功，更不能计 Kerberos／SSPI 正向认证。日志 `.tmp/suite-validation/windows-mit-first-ci.log`。正在细化实际配置 getter 的失败位置并核对跨 CRT 环境读取。

同轮主 MSVC 作业完成全部产品编译后仍在原生执行步骤失败，尚未将其详细日志沿用或计为新的功能成功。PPK 收尾修复与 Pageant AF_UNIX 增量均未包含在该 SHA。

补充读取主作业日志后确认：`af4919b` 的真实 PPK 仍在全部终端阶段后报 hive 收尾 13；14 项 CTest 通过，旧 runtime 返回 77。双 Ubuntu（38678912641）及既有 PuTTYgen 四平台（38678912648）本 SHA 实际成功。

## Pageant AF_UNIX 与 PPK 种子收尾检查点

`f7d913f` 提交可选 AF_UNIX 核心、三传输共享库检查和跨进程端点替换保留；独立审查及本机 Pageant 11／11、运行器 4／4 通过。[首次 Windows CI](https://github.com/lwl0820/putty/actions/runs/38679891218) 已完成 MSVC 编译，原生 Pageant 宿主 17.30 秒后触发 `tree234.c:536` 的非空查询断言，退出 3221226505，因此新增 23 项未通过。日志 `.tmp/suite-validation/windows-pageant-unix-first-ci.log`；尚无调用栈，不由断言位置推断根因。该轮 PPK 仍为旧 hive 收尾 13；双 Ubuntu（38679891206）及 PuTTYgen 四平台（38679891205）实际成功。

`08b5cc3` 已推送生产终端退出保存种子修复：在唯一核心线程释放随机状态前显式保存，保留五核心十次写打开门，并将实际读／写打开与字节计数纳入最终白名单摘要。独立检查自修摘要漏字段及数值边界；运行器 12／12，实际生产库加正式 sshrand 差分探针旧实现一写失败、新实现两写成功。匹配 Release 及 C／C++ ASan／UBSan、默认 LSan 的真实 SSH／GSS 均 2／2 通过。以上为本机证据，新 Windows 五核心及 hive 清理仍待本 SHA CI；不能用其推导 Pageant 断言已经修复。

## Windows PPK 整项首次通过与 MIT 配置修复

`08b5cc3` 的 [Windows CI](https://github.com/lwl0820/putty/actions/runs/38680275822) 中，真实 PPK 终端整项首次通过，耗时 21.89 秒。公开 `terminal-ssh.json` 已单独提取至 `.tmp/suite-validation/ppk-seed-fix-native-report.json`：五核心创建／销毁均为 5，连续映射、私有 hive 和清理均成功；种子读／写为 10／10，读打开 5、写打开 10，读 519 字节、写 1280 字节，注册表访问 121 次。正式 MSVC Release、Qt6.8.3 离屏宿主的实际认证工作 1765 毫秒，12.114 秒负载采样 240 次、最大心跳 97 毫秒；计算中关闭调用 0 毫秒、安全回收 1773 毫秒、关闭中仍有 28 次心跳。642 字节有序输入由远端精确核验，出现背压且非 BMP 屏幕内容通过；对端所有线程与连接归零，运行资料删除成功。

本轮 Pageant 仍于 17.59 秒触发同一 `tree234.c:536` 断言，主作业整体失败；14 项 CTest 实际成功，旧 runtime 返回 77。这些结果不证明 Pageant 新 23 项、原版 Windows CLI、生产 seed 回退、GSS／SSPI 正向或桌面通过。同 SHA 双 Ubuntu（38680275601）和 PuTTYgen 四平台（38680275522）均实际成功。

`4c9823f` 已推送 MIT 初始子进程环境及私有配置读取修复：按固定官方源码的唯一斜杠转换核对完整缓存名，将配置失败拆成五个固定阶段；父进程不加载 DLL 或修改环境，独立子进程加载前核对封闭环境，硬期限覆盖原生调用、清理和报告。独立检查后 22 项边界回归全过。新 [Windows CI](https://github.com/lwl0820/putty/actions/runs/38681296773) 尚在执行，六 DLL 加载和实际配置 getter 不预先记通过；同 SHA 双 Ubuntu（38681296470）及 PuTTYgen 四平台（38681296569）已成功。

随后公开 Actions API 已确认该 SHA 的 MIT ABI 作业 `116246227779` 实际成功，边界回归、纯提取、原生加载并核对私有配置及摘要上传各步骤均成功。这是该无凭据纵切首次原生通过；尚未取回其详细日志／摘要，不追加未观察的数值，也不据此推导凭据申请、GSS／SSPI 或真实 KDC 已成功。主 MSVC 作业 `116246227991` 构建成功、原生执行失败；新 Pageant 诊断增量还未进入该 SHA。

现已取回详细日志 `.tmp/suite-validation/windows-mit-config-fixed-ci.log` 及唯一公开摘要 `.tmp/suite-validation/mit-config-fixed-native-report.json`：真实加载六个 DLL、枚举五个机制，Windows loader、ABI 布局、私有配置、去除继承的 Kerberos 环境和 FILE 默认缓存选择全部为 true，私有运行根实际删除。`credentials_acquired`、`kerberos_authentication_verified`、`sspi_verified` 仍全部为 false。固定 MSI 摘要与锁保持一致。

`7b30234` 仅增加测试宿主固定阶段、MSVC 故障数值和当前 EXE 的有限 RVA 栈；独立检查修复了截断半行可能误报 RVA，运行器 7／7、Linux Pageant 11／11 通过。`f2f7e15` 单独增加精确 MAP 产物白名单并已推送，新 [Windows 诊断 CI](https://github.com/lwl0820/putty/actions/runs/38682442363) 正在构建。监听和清理算法未改变，尚无断言修复声明。

## Pageant 原生断言的实际调用栈

`f2f7e15` 的诊断运行现已结束。主作业 `116249773392` 在 Pageant 首个“已有文件拒绝”负例中于 16.93 秒失败，退出 134。固定阶段为启动 `1,2`，自行销毁 `30,31,32,33,38,39`；同次 EXE 的 RVA 与精确 MAP 对应为 `findrelpos234 → del234 → pr_core_destroy → pr_core_create → Worker::start`。没有 `winselgui_response` 栈帧。模块创建失败后返回空上下文，公共运行时却仍执行 `expire_timer_context(NULL)`，正式计时器树的空查询触发断言。详见 `windows-pageant-native-root-cause.md`；不能再将其未经证明归因于 Windows 网络消息。

最小修复正在独立审查：仅非空模块上下文才执行模块析构及其回调／计时器最终清扫，核心级收尾保持。新 `runtime-create-failure` 宿主通过真实上游计时器／回调／树，验证失败→成功→失败→成功及空上下文全局回调保留；精确旧源码差分触发原断言，新源码退出 0。本机 Release、ASan／UBSan 默认泄漏检查通过，Windows 修复及后续 AF_UNIX 23 项仍待下一次原生 CI。

## 隔离 KDC 首次 Windows 前置结果与 GSS 检查点

`5428f80` 提交正式受控 KDC、Windows AS／TGS 作业及隔离契约。[Windows CI](https://github.com/lwl0820/putty/actions/runs/38682835640) 的 MIT ABI 作业 `116250989998` 再次实际通过；KDC 作业 `116251091411` 在前置单测唯一失败，实际为 `b'ready\\r' != b'ready'`。生产有界读取器只移除 LF 的契约未变，测试须按 Windows CRLF 精确断言；后续 MIT 提取、SDK 准备和票据交换均没有执行。日志为 `.tmp/suite-validation/windows-kdc-first-ci.log`，不能记 Windows 真实 KDC 通过。同 SHA [双 Ubuntu CI](https://github.com/lwl0820/putty/actions/runs/38682835674) 与 [既有 PuTTYgen 四平台 CI](https://github.com/lwl0820/putty/actions/runs/38682835773) 均实际成功。

根修正该平台断言，并在准备状态、启动行、关闭摘要统一拒绝嵌套重复字段、NaN／Infinity、非对象及布尔／浮点伪装的 schema；错误启动不进入代理，错误关闭保留失败目录且不能发布成功。局部全组 49 项中 48 项通过、1 项 Windows Job 专属跳过（18.061 秒），仍待独立审查及目标平台执行。

`fd84930` 是已完成独立审查的 MIT GSS／SSH 夹具本地提交，尚未推送：固定七个 Windows wheel、官方 Paramiko 4.0.0 源码摘要和实例绑定小补丁，独立进程封闭环境、真实双向 token／MIC、五个固定场景及严格清理报告。独立回归 22／22；Linux 六场景实际重跑成功，AS 12／TGS 5、原版 Plink 固定命令成功、线程／连接／原生对象归零且私有运行根删除。该 Linux 结果不表示 Windows GSS、Windows 原版 Plink 或 Microsoft SSPI 正向通过；Windows 独立 CI 包装器仍在实施。

`97904dd` 已提交公共运行时最小修复、无存储新宿主及两平台必需结果门；独立复核 Release 2／2、真实回环生命周期 1／1、默认 LSan 的 ASan／UBSan 1／1，以及旧断言／新退出 0 的差分。`e7f9a5e` 提交 KDC 元数据与换行修复；独立检查另补有限浮点溢出及深递归拒绝、准备状态访问 SDK 前拒绝、合法 CR 元数据正常清理，最终 51 项中 50 通过、1 项 Windows Job 专属跳过（17.895 秒）。连同 `fd84930` 已推送，新的 [Windows CI](https://github.com/lwl0820/putty/actions/runs/38684586829)、[双 Ubuntu CI](https://github.com/lwl0820/putty/actions/runs/38684586813) 和 [PuTTYgen CI](https://github.com/lwl0820/putty/actions/runs/38684586876) 正在运行，尚不沿用旧成功。

`1e812f6` 已独立检查后提交并推送 GSS／SSH Windows 包装器和新作业。Windows 2022／Python3.11 在同 SHA KDC 成功后独立准备 SDK 和七 wheel venv，190 秒有界内层进程、五场景复核、真实个人目录只读前后观察、确切资料清理及 18 分钟总硬期限。只上传 `gss-mit-ssh.json`；候选刷新后才原子提交最终文件。独立专用 18／18、公共 60／60 通过，四个真实挂起点均退出 124 并保留失败占位；这些是逻辑和期限证据，Windows 原生五场景尚待新作业，正式 Windows Plink、SSPI 和长终端负载仍未验。

## 创建失败修复后 Windows 主作业首次通过

`e7f9a5e` 的 [Windows CI](https://github.com/lwl0820/putty/actions/runs/38684586829) 主作业 `116256421939` 实际成功。MSVC 新 `runtime-create-failure` 宿主原生通过（0.01 秒）；Pageant 私有 IPC 宿主原生通过（18.08 秒）。单独提取 `.tmp/suite-validation/pageant-unix-native-passed-report.json`，核对精确 23 项、命名管道／WM_COPYDATA／AF_UNIX 三传输、跨传输共享密钥／签名、已有普通文件保留、监听失败／部分帧／在途提示关闭、端点清理和替换保留全部成功。实际程序 SHA256 为 `9f39128f9680a6017e7c63803080ea01b9a494c8ff2624113ad2f056c94ecb83`。这验证公共空计时上下文修复后的后续负例，不表示原名跨版本、跨完整性、OpenSSH 或桌面通过。

该主作业 CTest 注册 17 项，16 项实际通过、旧 `runtime-lifecycle` 一项明确跳过，真实 PPK 纵切仍通过。MIT ABI 独立作业 `116256422316` 成功。但 KDC 作业 `116256517514` 在前置 51 项与 MIT 提取成功后于 `prepare-kdc` 失败，工作流整体为失败；AS／TGS 未进入。公开 `.tmp/suite-validation/windows-kdc-protocol-first-report.json` 证明本次实际个人目录前后观察成功且一致（268 文件、182231829 字节），运行父目录删除；尚未取得更细异常。双 Ubuntu 及既有 PuTTYgen 同 SHA 实际成功。

只读源码及真实 ctypes 参数差分确认另一确定缺陷：`prepare.fetch()` 把 `bytearray` 传入 Windows `WriteFile` 的 `c_void_p` 参数，Linux 写接口却接受，故原本机验证漏过。最小 `bytes(content)` 修复提交 `aec9f1c`；独立执行旧 HEAD fetch 的 SHA256／SHA512 分支均被真实 ctypes 拒绝，新实现完整字节／长度保持且哈希错误在写前拒绝。完整 53 项中 52 通过、1 项 Windows Job 专属跳过，独立新两项 2／2、Python3.11／空白检查通过。新修复已推送待 Windows 验证；没有把该确定类型问题写成此前 CI 已取得异常栈或唯一失败点。
