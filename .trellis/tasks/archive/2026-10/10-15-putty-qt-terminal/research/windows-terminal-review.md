# Windows 终端适配独立检查

日期：2026-10-15。范围为 `apps/putty/` 本轮 Windows 平台默认值、Unicode 输出表、入口／构建接入、CPU 渲染与两项 MIC 测试。没有改公共运行时、根构建、工作流、上游或其它组件，没有提交。Windows 原生结果仍须仓库 CI 取得；没有据本报告勾选双平台技术纵切门或完整配置界面。

## 已修复发现

### F1：Unicode 绘图模式同时改掉了 Windows OEM 字符集

- 文件：`apps/putty/src/core/pt_platform.c`。
- 问题：原实现把配置副本无条件改为 `VT_UNICODE`。上游 `windows/unicode.c` 在 `VT_XWINDOWS`／`VT_OEMANSI` 下使用当前系统 OEM 表作为 SCO ACS，而 `VT_UNICODE` 使用 CP437；非 CP437 系统的相同字节因此解释不同。`VT_OEMONLY` 原有的 CP437 字形及输入代码页非法／自动值回退 ACP 也被绕开。仅保留原 Conf 不足以证明输出表保真。
- 修复：XWINDOWS／OEMANSI 在 Unicode 初始化后选回同次上游生成的 `unitab_oemcp`。OEMONLY 保留正式上游模式，随后与其它模式共同把 GDI 字形标签还原为 Unicode。仍只在 Conf 副本上初始化；没有复制解码、系统代码页或逆映射算法。
- 回归：`terminal-windows-platform` 增加重命名生产适配函数的窄初始化探针，使用必定不同的 OEM／CP437 表，覆盖四种模式、两种标签还原、模式／输入配置不变、OEMONLY 分支保留。真实 Windows 部分另检查 SCO 表与真实 OEM 表一致，及 OEMONLY 非法输入代码页仍回退 `GetACP()`。
- 本地复现：仅用 Unix 的真实 Conf 分配／复制与窄初始化输出探针编译相同生产适配函数；修改前退出1，失败于 SCO 表选择，修改后退出0。该探针不含 Windows SDK 或系统调用，不能代替原生 Windows 结果。

## 其余检查结果与隔离边界

- Windows `platform_default_*` 和 `platform_get_x_display` 由已有 `suite-putty` 正式 Windows 对象提供；自有 Unix 默认值以 `_WINDOWS` 条件排除。Windows 终端归档补正式 `windows/no-jump-list.c`，未引入第二份默认值或自造跳转列表实现。
- 产品沿用 Qt 的图形子系统入口方式。初始 Qt 字体为 Consolas；正式 FontSpec 默认值仍由上游提供。完整字体配置、OEM／POORMAN 等全部产品配置映射仍属于后续技术门之后的工作。
- `pt_init_unicode` 的 Conf 副本在同步初始化完成后释放，没有外借；测试同时核对原 Conf 的模式和字符串未改。Qt 接收的三张输出表不保留 GDI ACP／OEM 标签。
- CPU 测试只链接 Qt 和生产 CPU 渲染源码，`pt_publish` 为测试接收器。它不链接 `suite-putty`／`putty-runtime`，不调用 Conf、Terminal、Backend、存储或 PRNG。Windows 平台测试只显式创建所需 Conf 字段，正式默认值／Unicode 函数不进入存储；两项 MIC 测试调用生产函数原文及包／编码接口，不创建核心。
- 原 `putty-terminal-tests` 仍参与 Windows 编译链接，Windows 不注册其 `render-bounds`、认证或报告路径执行。它含 `random_ref`／`do_defaults`，本轮不得直接运行它或主程序取得验收通过。
- Release 断言由 `suite-config` 的 `/UNDEBUG` 传给依赖上游的目标；不链接该配置的 CPU 与 SSPI 窄夹具独立保留 `/UNDEBUG` 和 `/utf-8`。新增 CTest 没有 skip 返回码。
- MIC 故障回归即使在 Windows 执行，也不能记为原生 SSPI 认证；本轮没有改已通过 Linux 真实认证的算法或交互路径。

## 验证

环境：Arch Linux x64、GCC16.2、Qt6.12；没有 MSVC、Windows SDK、MinGW 或 Wine。

- 增量构建：`.tmp/build/suite-windows-terminal-local` 的 `putty-qt`、`putty-terminal-tests`、`putty-terminal-cpu-tests`、`putty-gss-mic-tests`、`putty-sspi-mic-shim-tests` 全部成功。日志 `.tmp/windows-terminal-review/incremental-build.log`。
- CTest：该目录 `-L terminal --no-tests=error` **5/5通过、0跳过、0.41秒**，分别为 CPU、GSS MIC、SSPI 窄夹具、宽帧、报告路径。JUnit `.tmp/windows-terminal-review/local-terminal-results.xml`。容器测试关闭；本轮未重复已有真实 SSH／GSS 或 PuTTYgen 全矩阵。
- 严格语法／警告检查：`pt_terminal.c`、`pt_platform.c`、`main.cpp`、`cpu_render_test.cpp`、`gss_mic_test.c`、`sspi_mic_shim_test.c` 的 `-fsyntax-only -Wall -Wextra -Werror` 全部退出0。SSPI 夹具提取的上游函数保留既有未用参数 `lib`，该翻译单元仍用已记录的 `-Wno-unused-parameter`，未修改或隐藏其它警告。日志 `.tmp/windows-terminal-review/strict-checks.log`。
- OEM 窄探针：严格编译退出0，修改前退出1、修改后退出0；独立加 ASan／UBSan、默认 LSan 的执行退出0，没有检测关闭或抑制。该次链接的上游 Conf 库未重新做全面插桩，不宣称覆盖完整 Windows／上游核心。证据 `.tmp/windows-terminal-review/oem-probe-before.log`、`oem-probe-run.log`、`oem-probe-asan-run.log`。
- `git diff --check -- apps/putty` 通过。Windows 专属翻译单元未在本机原生编译，不能将 Linux 语法检查写为 MSVC 通过。

## CI 可提交点与尚未验证项

组件改动可作为下一次原生编译检查点提交。Windows 开启 `PUTTY_BUILD_PUTTY=ON` 后，`windows-terminal` 必需注册集合保持恰好以下四项：

1. `terminal-cpu-render`
2. `terminal-windows-platform`
3. `terminal-gss-mic-failures`
4. `terminal-sspi-mic-failures-shim`

根 runner 应分别核对四项预注册、JUnit 真正执行且通过，拒绝缺失、重复和跳过。Linux 的必需集合应补 `terminal-cpu-render`，该目标在所有平台均注册；本检查未修改根 runner。

尚未取得本轮 Windows MSVC／Qt6.8.3 编译链接和上述四项原生执行结果。真实 Windows SSH／PPK、原生 GSS／SSPI、存储保护下完整核心、字体／IME／剪贴板桌面及关闭仍未验证；桌面按用户选择由其人工验收。没有发现其它可以凭当前源码证据安全局部修复的确定错误。完整产品配置语义和原生桌面缺口继续留在任务阶段门内。

## 公共 CI 接线追加检查

同日接受主代理独立检查委派，范围为 Windows `run_ctest.py`、其公共回归、Windows 工作流／README、Linux `run_linux_ci.py` 的新增必需项，以及 `suite-terminal.md` 的 OEM 契约。没有修改公共代码、运行时、其它组件或任务状态。

- Windows `--require-terminal` 先以 `windows-terminal` 标签查询注册集合，要求恰好是上述四个名称；缺失、增加、重复或名称替换均拒绝。CTest 执行前仍删除旧 JUnit，整体执行返回非零不能成功。JUnit 再要求这四项各自存在且唯一，拒绝 `failure`／`error`／`skipped` 子项及 notrun／disabled／skipped 状态，不能用注册存在或旧报告代替实际通过。
- 工作流在本轮完整 Release 配置显式开启 `PUTTY_BUILD_PUTTY=ON`，调用 runner 同时携带 `--require-hive --require-storage --require-terminal`，每个失败退出码继续停止步骤。Pageant 开关未开启，默认仍为 OFF。原多配置 CLI-only 检查与完整配置的 `--components plink pscp psftp --assertions` 保持；终端目标未新增安装规则，三个 CLI 的安装集合和摘要继续精确核对。
- 对照组件 CMake，Windows 只新增四项无存储终端执行，真实 SSH／GSS、宽帧完整核心和报告路径测试仍仅在原生 Linux 条件内注册。旧 `putty-terminal-tests` 在 Windows 只构建，不因 runner 无标签执行全部已注册 CTest 而进入其存储／认证模式。
- Linux `REQUIRED` 已增加 `terminal-cpu-render`。现有注册／JUnit集合一致、重复拒绝及失败／跳过拒绝逻辑保留。它保证 CTest 层结果，不额外推定内部 unittest 的分支覆盖。
- `suite-terminal.md` 新增 OEM 段与审查自修相符：Conf 副本、系统 OEM SCO 表、OEMONLY 正式 CP437／ACP 分支、模拟非 CP437 输出表及 CPU 用例不链接存储／PRNG均有实现或明确测试边界。README 保留真实 PPK／SSPI和桌面未验状态。

独立复跑 `python3 -B -m unittest discover -s tools/suite/windows/tests -v`：**17/17通过，0.285秒**；Linux CI 契约：**4/4通过，0.005秒**。三个变动 Python 文件 AST 解析、Windows 工作流 YAML 解析、指定六个公共文件 `git diff --check` 全部通过。没有项目专用 Python 类型检查器配置，本轮以 AST 和契约测试核查语法／数据契约，没有声称运行 Windows 原生程序。

公共接线未发现新增确定缺陷，可与组件作为原生 CI 检查点提交；实际 MSVC 编译／链接和四项执行结果仍待下一次仓库运行。
