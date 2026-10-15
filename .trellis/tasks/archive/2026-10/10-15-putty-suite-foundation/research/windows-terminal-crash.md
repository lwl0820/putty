# Windows 终端首次原生崩溃排查

日期：2026-10-15。范围：Windows CI `38670751882` 的 `terminal-cpu-render` 和 `terminal-windows-platform`；不修改生产 CPU 渲染算法。

## 已有原生证据

该次运行两个用例分别在 0.05 秒和 0.01 秒被 CTest 记为 SegFault，无进程输出。所有产品及测试目标已编译链接成功，真实存储组和 Pageant IPC 组通过。整次 Windows 检查仍失败；这些局部通过不能扩为终端、CLI 或桌面已验收。

日志：`.tmp/suite-validation/windows-pageant-first-ci.log`。

## 确定的路径适配缺陷及修复

`apps/putty/src/core/pt_platform.c` 调用 `filename_from_utf8`，但上游没有任何头文件声明它；它只在 `vendor/putty/windows/utils/filename.c` 内定义和调用。CI 对正式源及测试包含变体均报 C4047，指出返回的 `Filename *` 和 `int` 不匹配。MSVC C 未声明函数按返回 `int` 处理，x64 返回指针会被截断；测试随后调用 `filename_to_wstr` 解引用该值。

修复改用已公开声明的 `decode_utf8_to_wide_string`、`filename_from_wstr`，随后释放临时宽字符缓冲。该文件在 MSVC 下将 C4013 升为错误；不对 vendor 全局增加警告策略。既有原生回归继续核对中文空格路径，无需读取个人配置。

机制依据：[Microsoft C4013 文档](https://learn.microsoft.com/en-us/cpp/error-messages/compiler-warnings/compiler-warning-level-3-c4013?view=msvc-170)。本轮本机不是 Windows；修复后原生运行仍待下一次 CI，不能提前记为通过。

## CPU 夹具条件与待确认故障

CPU 测试不链接上述平台源，不能套用路径根因。固定 Qt 6.8.3 源码提供一项待原生确认的字体条件：

- `src/plugins/platforms/offscreen/qoffscreenintegration.cpp`：Windows 离屏平台实例化 `QFreeTypeFontDatabase`。
- `src/gui/text/freetype/qfreetypefontdatabase.cpp`：`populateFontDatabase` 只扫描 `fontDir()` 下的字体。
- `src/gui/text/qplatformfontdatabase.cpp`：`fontDir()` 首选 `QT_QPA_FONTDIR`，缺省为 Qt 的 `LibrariesPath/fonts`，不是 Windows 系统字体目录。

以上从已校验的 `.tmp/qt-archives/qtbase-everywhere-src-6.8.3.tar.xz` 直接读取。它们说明当前强制 offscreen 的夹具必须提供字体，但尚不能证明现有 SegFault 就由缺字体导致。

两个测试宿主现记录入口、字体、工作线程及绘制阶段并立即刷新 stderr。CPU 打印实际字体族数量，空字体明确失败。Windows 未处理异常过滤器仅输出异常代码、模块文件名和模块偏移，随后继续 Windows 原异常处置；不记录完整个人路径或生成转储，不吞掉失败。过滤器依据：[Microsoft SetUnhandledExceptionFilter 文档](https://learn.microsoft.com/en-us/windows/win32/api/errhandlingapi/nf-errhandlingapi-setunhandledexceptionfilter)。

CPU 的测试 `extern "C" pt_publish` 同时消除 C4297：回调内部捕获异常、记录原子错误并返回 `PR_PLATFORM_ERROR`，最终核查必须报告该错误，不向 `/EHc` C 边界传播异常。生产渲染器未修改。

下一轮 CI 应先原环境执行这两个无存储测试并记录完整退出码，随后在正式 CTest 子进程提供明确字体目录。由阶段、原生异常代码和实际字体数量判断 CPU 失效位置；不能用字体猜测掩盖剩余崩溃。

## 本机验证

Arch Linux，GCC 16.2，Qt 6.12；不能代替 MSVC／Qt 6.8.3 原生证据。

- 构建 `putty-terminal-cpu-tests`、`putty-qt`、`putty-gss-mic-tests` 成功。
- `cpu_render_test.cpp`、`cpu_renderer.cpp`、`pt_platform.c` 的 `-Wall -Wextra -Werror -fsyntax-only` 均成功；日志 `.tmp/suite-validation/windows-terminal-crash-strict.log`。
- 五项定点 CTest 全通过、零跳过，0.47 秒：CPU、GSS MIC、SSPI MIC shim、帧边界、报告路径。
- 修改文件 `git diff --check` 通过。

仍未验证：修复后的 Windows 平台路径、CPU 原始崩溃原因、Windows 字体夹具和桌面。保持任务进行中。
