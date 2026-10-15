# Windows 终端适配实施

日期：2026-10-15。范围仅为当前终端技术纵切的 Windows 编译接入及安全本地测试；不表示完整前端或双平台技术门已通过。

## 最小变更边界

当前终端以 Unix 默认宿主函数覆盖两平台，而且 Windows 完整链接需要独立的跳转列表宿主。`fontspec_new_default` 和 `init_ucs_generic` 在两平台均由已有 `suite-putty` 提供，不需要新增或复制它们。Windows 的正式默认字体、日志文件和串口由 `windows/utils/defaults.c` 提供，Unix 维持原行为。

预计只修改 `apps/putty/CMakeLists.txt`、`src/core/pt_terminal.c`、`src/app/main.cpp`、`tests/CMakeLists.txt` 和组件内测试／说明。Windows 链接正式 `windows/no-jump-list.c`；不修改共享核心、运行时、注册表工具、上游源码或工作流。新 CPU 渲染用例仅链接 Qt 与生产渲染源码，以测试事件接收器替代 `pt_publish`，无 Conf／Terminal／Backend／PRNG／注册表入口。MIC 用例只调用既有生产函数原文与公开编码／包接口，不创建核心。

原 `putty-terminal-tests` 可参与 Windows 编译，但不注册含 `random_ref`／`do_defaults` 的原生运行用例。逐进程存储 guard 尚未接入前，不能运行主程序、SSH、真实 SSPI 或终端初始化来获得测试通过。不铺完整配置界面，不修改已通过 Linux 真实 SSH／GSS 的认证逻辑。

## 验证边界

实施机为 Arch Linux x64、GCC16.2、Qt6.12；没有 MSVC、Windows SDK、MinGW 或 Wine。本地只证明 Linux 增量构建及测试结果；Windows MSVC／Qt6.8.3 的真实编译和运行由主代理接入仓库 CI。Windows 桌面由用户验收，离屏结果不代替桌面字体、IME、剪贴板或原生 SSPI 登录。

## 已实施的适配

- 新增内部 `src/core/pt_platform.c/.h`，集中平台默认值和 Unicode 初始化；Unix 默认值与 `init_ucs_generic` 的调用保持原行为，Windows 不再覆盖正式 defaults 对象。
- 核对发现 `windows/unicode.c` 的 `link_font` 会把 ASCII／DEC 字符映射成 `CSET_ACP`／`CSET_OEMCP`，原 Win32 绘制器另用 GDI 字体解释这些标签，Qt 不能直接把它们当 Unicode。Windows 适配明确使用 Unicode 字体代码页，在配置副本上初始化，之后把三张输出表的标签还原为对应 Unicode。独立审查已修正模式处理：XWINDOWS／OEMANSI 保留系统 OEM SCO 表，OEMONLY 先走正式 CP437／ACP 分支，详见 windows-terminal-review.md。原 Conf 的 vtmode 不变；不替换或复制上游解码算法。
- Windows 产品链接已有 `windows/no-jump-list.c`，并使用 Consolas 作为当前 Qt 初始字体。Qt GUI 入口沿用既有 PuTTYgen 已使用的 CMake／Qt 图形子系统方式，没有自造 WinMain。
- 新 `terminal-cpu-render` 只编译生产 `cpu_renderer.cpp` 和测试接收器，验证真实工作线程 QFont／QImage、ASCII／中文／组合字符／非 BMP、DPI／DPR、非法尺寸拒绝、两次背压后保留最新完整帧、后续帧更新以及 Qt 队列异步投递。不链接 `suite-putty` 或 `putty-runtime`，不把它称为真实 SSH／终端解析测试。
- 新 Windows 专属 `terminal-windows-platform` 只调用 `conf_new` 和显式字段设置、真实 Unicode 表、FontSpec／Filename／COM1 默认值；检查 ASCII／DEC 线绘、没有残留 GDI 标签及原 Conf 模式保持。没有 `do_defaults`、存储、PRNG、网络或 agent 调用。
- 两项原 MIC 回归现在也可在 Windows 构建／注册。SSPI 窄夹具补 `/utf-8 /UNDEBUG` 或 `-UNDEBUG`，其断言不能被 Release 关闭；Windows 上执行窄夹具也不代表真实 SSPI 系统调用。

## 本地增量验证

普通构建目录 `.tmp/build/suite-windows-terminal-local`，Debug、容器测试关闭。实际构建 `putty-qt`、`putty-terminal-tests`、`putty-terminal-cpu-tests`、`putty-gss-mic-tests`、`putty-sspi-mic-shim-tests`，均退出0。平台函数最后改为在 Conf 副本上初始化后，受影响前三个目标再次增量构建成功。

`ctest --test-dir .tmp/build/suite-windows-terminal-local --output-on-failure --no-tests=error -L terminal`：5/5，0跳过，0.36秒；包括 CPU、GSS MIC、SSPI 窄夹具、真实宽帧和报告路径。此配置没有注册真实 SSH／GSS 容器用例，未重复或冒称重新执行既有认证验证。

CPU 渲染器另以 `-fsanitize=address,undefined -fno-omit-frame-pointer` 构建，默认 LSan 开启，1/1通过。首次沙箱运行 LSan 因无法检查线程而自身 fatal；保留 `.tmp/suite-validation/windows-terminal-asan-ctest.log`，在授权环境重跑通过，日志为 `windows-terminal-asan-ctest-unrestricted.log`。没有设置检测关闭或抑制规则。

自有 `pt_terminal.c`、`pt_platform.c`、`main.cpp`、`cpu_render_test.cpp`、`gss_mic_test.c` 的 `-fsyntax-only -Wall -Wextra -Werror` 均退出0。SSPI 夹具的生成上游函数有既有未使用参数 `lib`，相同严格检查加 `-Wno-unused-parameter` 后退出0，未修改上游函数来消除该警告。Windows 专属文件因无 SDK 未本地编译。`git diff --check -- apps/putty` 通过。

## 仓库 Windows CI 接线

在根已有 Windows 原生配置添加 `-DPUTTY_BUILD_PUTTY=ON`，保持既有目标和真实 storage guard 接线。完整构建须实际产出 `putty-qt.exe` 和 `putty-terminal-tests.exe`；后者此轮仅编译链接，不运行其 `render-bounds` 或认证模式。

Windows 终端必需 CTest 名称恰为下列四项，均有 `windows-terminal` 标签、没有 skip 返回码：

1. `terminal-cpu-render`
2. `terminal-windows-platform`
3. `terminal-gss-mic-failures`
4. `terminal-sspi-mic-failures-shim`

主代理需在 `run_ctest.py` 的预注册集合及 JUnit 成功拒绝门中明确要求这四项；不能只运行一个空标签或接受跳过。Linux 的必需集合另补 `terminal-cpu-render`。本次未修改根 runner／工作流，由主代理所有。

Windows SDK 编译错误、链接闭包及这四项的实际运行尚待 CI；真实 PPK、原生 SSPI、注册表及种子保护下的完整核心、Windows 字体桌面和关闭仍未验证。第一项双平台技术门保持未完成。
