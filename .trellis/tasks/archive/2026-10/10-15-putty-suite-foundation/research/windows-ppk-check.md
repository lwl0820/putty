# Windows 受控 PPK 纵切独立检查

日期：2026-10-15。仅检查本检查点代码及 CI 接线；不表示 Windows 原生执行或桌面已通过。初次静态检查未修改 PPK 实施者拥有的源码；随后依据原生 CI 的编译错误修复两处头文件顺序，见末节。

## 检查结论

未发现可由当前代码证据确定的新增阻断缺陷，可进入仓库原生 CI。重点核对如下：

- `windows/main.cpp` 在任何 Qt 初始化前创建 hive；`hive_bootstrap.c` 写入受控种子路径后仅释放操作权，HKCU 映射始终保留。每个核心先 claim、绑定种子保护，再调用实际终端模块创建；实际销毁后检查 `random_active == 0`，收集真实读写计数，再解绑并释放操作权。
- 五个场景的 `CloseAtScopeExit` 在失败时也通过事件循环等待核心安全关闭。主线程操作权守卫在 QApplication 析构前执行，最终 main 在 QApplication 销毁后才关闭映射并清理 hive。公共所有者字段使用 Interlocked 操作；错误线程不能取得借用键、暂停或关闭映射；移交期间不能启动第二映射或清理活动根。
- 受控终端直接编译生产适配，仅重命名导出模块，由保护包装器接管入口。链接图拒绝原生产 `pt_terminal` 和生产 `storage` 对象，要求正式 settings／noise／sshrand／PRNG、种子保护和 hive 移交入口。普通 Windows `putty-terminal-tests` 仍不注册真实存储执行。
- 独立 helper 复用正式 pg-core／pg-putty，关闭 Qt GUI，不链接完整套件核心；父工程转交工具链、运行库、各配置标志及实际配置。helper 通过宽参数入口转 UTF-8，再走正式 pg-platform 公开路径契约；不再调用未声明的上游私有路径函数。校准使用真实 PPKv3／Argon2id／8192KiB／单线程／自动1500ms，runner 复核实际参数、公钥、编译器和客户端配置。
- `private_data.py` 显式声明 ctypes 的 Win32 参数及返回类型，避免 x64 句柄截断；核对文件信息的链接数索引、实际最终路径、owner、受保护 DACL 和恰好当前用户／SYSTEM 两条 ACE。目录句柄在数据访问期间固定路径；口令及 PPK 不进入摘要或上传白名单。
- runner 逐项核对五次连接／核心销毁、两次实际认证和两个固定命令、精确输入、真实十秒负载、KDF≥500ms、心跳≤250ms、关闭调用<25ms、输入背压、实际 hive／数据回收和对端零存活线程。失败诊断只允许固定阶段／断言，不复制任意原始 stderr。结果没有正向 SSPI 或桌面通过标记。
- 根 `run_ctest.py` 对 PPK 和 SSPI 负例分别核注册集合及本次 JUnit；拒绝缺失、重复、跳过及失败。旧终端标签改为锚定匹配，防止新 `windows-terminal-ssh` 被误计为原四项。工作流先在原环境执行无存储诊断，再向正式 CTest 提供 Windows 系统字体目录；未修改生产渲染算法。
- 新 SSPI 原生负例直接以显式无效句柄透传系统 API，没有调用默认凭据获取；只链接 `suite-config`，CMake 未引入完整核心、Qt、存储或 PRNG。它仍不能替代真实 Kerberos 票据、受控 KDC 或正向认证。

## 本机验证

Arch Linux，GCC16.2／Qt6.12；无 Windows SDK，不宣称 Windows 分支已编译。

- PPK runner 合同：11／11，0跳过。
- hive runner 合同：5／5，0跳过。
- Windows 公共门及 SSPI runner 合同：34／34，0跳过。
- 五个新增／变动 Python 文件按 Python3.11 AST 解析通过；Windows 工作流 YAML 解析通过。
- PPK／hive 已跟踪变动 `git diff --check` 通过。

真实 Windows 仍须验证 ctypes ACL、hive 移交、匹配配置 KDF、Unicode／字体与关闭、MAP 实际格式及 SSPI 系统返回码；它们属于原生环境缺口，本轮没有用 Linux 合同测试替代。CPU 首次 SegFault 的实际原因及路径修复后的原生结果见同目录 `windows-terminal-crash.md`，仍待下一 CI。

## CI 70e2871 后的头文件顺序修复

原生日志 `.tmp/suite-validation/windows-ppk-second-ci.log:2637` 起，MSVC19.44／Windows SDK10.0.26100.0 编译 `guarded_module.c` 时出现 C2011／C2375：`sockaddr`、`fd_set` 及套接字 API 重定义。本次原生证据暴露了初次静态检查遗漏的构建阻断项；前面的“可进入 CI”仅是当时结论，不能视为本次构建通过。

确定的包含链是 `hive_bootstrap.h → private_hive.h → windows.h → winsock.h`，之后 `seed_guard.h → putty.h → windows/platform.h → winsock2.h` 重复定义旧版套接字类型。同目录 `hive_bootstrap.c` 具有完全相同的包含链，即使该对象尚未在失败日志中调度，也会触发同类错误。

两源文件均改为首先显式包含 `putty.h`，随后保留自己的 hive 与种子保护头。上游 `vendor/putty/windows/platform.h` 本来就按 `winsock2.h → windows.h` 排列；调用方遵循此顺序即可。未修改公共 hive 接口、生产模块、CMake，未使用 `WIN32_LEAN_AND_MEAN` 隐藏冲突。

同批宿主检查：`guarded_terminal.c` 通过实际终端实现先取得 PuTTY 平台头；独立 `keygen.c` 的首个项目头 `pg_core.h` 只含标准类型，随后先 `putty.h` 再 Windows API；平台测试与 SSPI 负例均先进入 PuTTY 头。`windows/main.cpp` 及 Qt 终端宿主仅使用 hive／Qt／诊断接口，没有在 Windows 头之后引入上游 PuTTY 头，未发现相同冲突。不因此把 PuTTY 依赖扩入纯 Win32 的公共 hive 头。

修复后的本机验证：

- 两源差异及本文 `git diff --check` 通过；修改仅新增明确平台头及中文原因注释。
- PPK runner 合同11／11、hive runner 合同5／5，均为0跳过。
- 本机没有 Windows SDK／MSVC，无法对这两个 Windows 翻译单元执行真实编译或类型检查。上述合同只证明相关 runner 回归，不能替代下一轮 Windows CI 对头文件修复、其余目标和原生执行的确认。

## CI 093f038 后的 ANSI 存储编译隔离

`.tmp/suite-validation/windows-ppk-third-ci.log` 显示两处头文件顺序修复已实际通过 MSVC 编译。本轮唯一构建失败发生于 `storage_variant.c`：它原先作为 Qt 测试目标的直接源文件，继承 Qt 传递的 `UNICODE`／`_UNICODE`，触发既有 ANSI 接口保护的 `#error`。该保护正确揭示了编译配置混用，不能删除或在整个 Qt 目标上取消 Unicode。

`apps/putty/tests/CMakeLists.txt` 新增独立 `putty-terminal-windows-storage` 对象目标，仅编译原 `storage_variant.c` 与 `seed_guard.c`，仅依赖 `suite-config` 和 `suite-windows-hive`，显式要求 C99，并保留 MSVC `/we4013`。`suite-config`／hive 原有 `/UNDEBUG`、`/utf-8` 及平台头配置继续用于此目标。Qt 宿主通过 `$<TARGET_OBJECTS:putty-terminal-windows-storage>` 直接纳入两个对象，保留其 Qt 定义、链接库和初始化约束。没有源文件全局属性，没有 `#undef`，没有新增包装源。

对象文件仍名为 `storage_variant.c.obj`／`seed_guard.c.obj`，直接纳入可执行文件的对象列表，不依赖归档抽取；它们在正式 `suite-putty` 归档之前提供存储入口。现有 MAP 检查继续要求这两个文件和真实 settings／noise／sshrand／PRNG，同时拒绝生产 `storage.c.obj`，无需放宽任何规则。ANSI 拒绝保护和源摘要均未修改，两个目标也没有混合重编同一源的全局属性。

本机验证限定为目标图与定向合同：

- 在项目 `.tmp/suite-validation/terminal-storage-cmake/` 提取实际修改的 CMake 目标块，仅将相对源路径变为绝对路径，模拟 Qt 的传递定义及既有 suite 目标接口，由 CMake／Ninja 实际生成 `compile_commands.json` 和链接规则。该夹具没有运行 MSVC 或编译 Windows API。
- 逐项核对存储与 seed 各有唯一编译命令，归属独立对象目标，均没有 `UNICODE`／`_UNICODE`／Qt 定义或相应取消选项；保留配置头、Windows 版本、`/we4013`、`/UNDEBUG`、`/utf-8`。Qt 终端源仍有两项 Unicode 定义及 Qt 定义。
- 生成的链接规则确认两受控对象各出现一次，在 `suite-putty` 归档之前。检查摘要保存为 `graph-check.log`。
- PPK runner 合同11／11、存储合同9／9，均0跳过；定向 `git diff --check` 通过。未复跑无关 Linux 网络矩阵。

此修复的真实 Windows 编译、实际 MAP 内容和 PPK 原生执行仍待下一轮仓库 CI；CMake 图生成不能替代这些原生证据。

## CI 665／38676436402 后的产品私钥 ACL 独立检查

原生日志 `.tmp/suite-validation/windows-ppk-fifth-ci.log:3164` 在独立 helper 成功生成 PPK 后报“私有数据必须恰有两个 SID 许可”。逐项对照正式 `apps/puttygen/src/core/pg_platform.c:97`，`verify_private_security` 的既有契约恰为当前进程用户一条 `ACCESS_ALLOWED_ACE`，owner 相同、DACL 受保护、flags 为0且权限为 `FILE_ALL_ACCESS`；它不会给 SYSTEM 额外许可。问题是运行器把夹具数据目录的双 SID 规则误用于产品私钥，不能通过改写产品导出 ACL 来掩盖。

独立检查结论：根本轮修复可进入下一 CI，未发现新增阻断问题。`PrivateData.check_file(..., owner_only=True)` 仅由 PPK 导出完成后的明确调用启用；默认仍要求当前用户与 SYSTEM 恰好两条许可，目录／口令／其他产物及既有调用者未改。单用户模式要求恰好一条当前用户许可，拒绝 SYSTEM、其他 SID、重复／额外 ACE、错误 owner、缺失保护位、非 allow 类型、任何继承 flags 和不等于完整访问的 mask。目录不能使用此模式，`harden=True` 与此模式组合在打开文件前就被拒绝；只请求读取属性与权限的句柄，核验不调用权限改写。

路径拒绝链未放宽：仍经过项目路径校验，再以 `FILE_FLAG_OPEN_REPARSE_POINT` 打开、不共享删除；核对真实句柄类型、reparse、单硬链接和最终实际路径。新单用户模式通过同一 `_open`／`_check` 链，失败时分别释放已取得的安全描述符及文件句柄。

本检查者的自修限于类注释同步、内存夹具显式小端固定宽度打包，以及两条安全回归：单用户检查仍拒绝 reparse／目录／多硬链接／实际路径不匹配，且 ACL 拒绝时描述符与只读句柄各回收一次。内存夹具的 ACL header／ACE 类型、flags、mask 与 SID 起始偏移符合被测布局；SID 比较和系统 API 是受控模拟，不声称它们是可交给 Windows 的完整安全描述符。真实 `GetSecurityInfo`／`EqualSid` 读回仍须原生 CI。

定点验证：Windows 公共合同41／41（含私有 ACL 7项），PPK runner12／12，均0跳过；三份获授权 Python 文件按 Python3.11 语法解析及定向 `git diff --check` 通过。未运行 Windows 产品或旧 runtime，未改产品权限实现、SDK 声明或本轮范围外文件。此 Python 层未配置独立静态类型检查器；本机也无 Windows SDK，不能将合同模拟记为原生权限验收。
