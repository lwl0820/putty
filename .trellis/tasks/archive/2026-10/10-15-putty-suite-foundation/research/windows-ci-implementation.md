# Windows CI 与无存储原生宿主实施记录

日期：2026-10-15。依据父 `research/windows-validation-plan.md` 中用户选择的仓库 Windows CI 路线。此次只实施共享基础及三个 CLI 的构建/安装接入；未修改原 PuTTYgen 工作流、Qt 工具、产品/vendor，也未提交或修改任务状态。

## 文件与协作边界

- `.github/workflows/putty-suite.yml`：独立 Windows Server 2022、MSVC x64 工作流，复用 Qt 6.8.3 构建脚本及缓存版本键；固定 CMake/Ninja。
- `tools/suite/windows/support.py`、`run_native_test.py`、`run_ctest.py`、`check_build.py`：项目路径与 reparse 拒绝、子环境、严格原生报告/CTest 数量、MSVC 链接图、断言及安装摘要核对。
- `tools/suite/windows/tests/test_windows_support.py`、`README.md`：本机可运行的契约回归与执行/未验说明。
- `libs/putty-runtime/tests/windows/native_handle_wait.c`、`event_module.c/h`、`no_storage.c`、`native_runtime_test.cpp`：真实 Win32/HWND/Qt 事件宿主。共享 `tests/CMakeLists.txt` 接线由主代理所有，实施前已逐项协商目标、链接、map、四个 `/INCLUDE`、标签和 TIMEOUT；不由本代理修改。

## 可执行 CI 门

1. 全关闭及 PSFTP-only 在 `CMAKE_DISABLE_FIND_PACKAGE_Qt6=TRUE` 下配置/构建。
2. PSFTP-only 使用 Ninja Multi-Config；只构建 Debug 后 Release 安装必须非零，再分别构建/安装两配置并核对文件集合及各自摘要。
3. Release 构建共享运行时与三个 CLI；审核心实际 compile commands、CLI 缓存及 Ninja 生成编译命令，检查有效 `NDEBUG` 顺序；原生宿主还有编译期 `#error` 拒绝关闭断言。
4. 必须恰好注册两个 `windows-native` CTest，JUnit 也必须包含且真正通过两者；零用例、缺失、重复、原生 skip 均失败。
5. 原生 HANDLE 检查：手动/自动复位、真实等待后排队快照、同值真实 HANDLE 重新注册、旧快照无效、自注销。运行时检查：Qt 通知、核心唯一线程、实际 HWND/WSAAsyncSelect TCP、独立 OOB、本地关闭/重开、远端关闭及待决 HANDLE 中关闭。

两个测试宿主总计 10 个具名检查，包含多条实际断言；**当前未在 Windows 执行，不能填写通过**。没有借用 Linux HANDLE shim 或 API mock 当原生结果。

## 注册表与种子：继续拒绝

本机没有 MSVC、MinGW 或 Wine，未验证 hive 创建或私有 ACL。因此本轮不开放 HKCU/持久化/随机种子测试。事件模块不初始化客户端 PRNG；测试前端把 seed 读写及 settings 入口设为 abort，Release 链接保留并审 map。宿主不定义 `random_read/ref`，不改真实密码代码；map 发现真实 PRNG、storage、重噪声对象即拒绝启动。

普通 Windows 用户的个人 HKCU 不作为测试环境，`GITHUB_ACTIONS` 从未参与授权。CLI 只编译/安装/摘要，不执行包括 `-V`、help 在内的参数入口。现有 Windows `runtime-lifecycle` 仍显式返回 77，CTest 报告列出跳过，不能当完整生命周期通过。

微软官方依据：[RegOverridePredefKey](https://learn.microsoft.com/en-us/windows/win32/api/winreg/nf-winreg-regoverridepredefkey) 的覆盖只作用调用进程；[RegLoadAppKeyW](https://learn.microsoft.com/en-us/windows/win32/api/winreg/nf-winreg-regloadappkeyw) 的参与进程需各自加载取得句柄。没有假定父进程覆盖会自动保护原版子进程。真实 [CreateEventW](https://learn.microsoft.com/en-us/windows/win32/api/synchapi/nf-synchapi-createeventw) 和 [WSAAsyncSelect](https://learn.microsoft.com/en-us/windows/win32/api/winsock/nf-winsock-wsaasyncselect) 的语义分别由原生宿主执行，而非仅静态陈述。

## 本机验证与待运行项

已执行 `python3 -B -m unittest discover -s tools/suite/windows/tests -v`：**13 项通过、0 失败、0 跳过，0.264 秒**。覆盖越界/链接、子环境与个人 profile 保留、假 CI 标记拒绝、非白名单 CLI 拒绝、map 混入真实存储/随机对象、有效断言顺序、配置摘要不符/多余安装文件、缺失/重复测试、原生失败/skip、cp1252 重定向下 UTF-8 帮助。第 13 项用真实 Ninja `compdb -x` 展开响应文件，证明 `-c`/`/c` 及响应文件中的 `NDEBUG` 顺序均被检查，且不会执行伪编译命令。

工作流用 PyYAML BaseLoader 静态解析通过，13 个步骤，触发入口与 windows-2022 平台匹配；不意味着 GitHub Actions 语义或执行已通过。本机后续任何 Python/语法复查结果仅补本机检查，不混记 MSVC/Windows 原生通过。

所有下载、构建、报告和运行根显式放 `.tmp/`；HOME/USERPROFILE/APPDATA 不变。上传白名单只包含 JSON/XML、合法原生测试 EXE 及三 CLI 安装 EXE；不包含私钥、hive、运行根或 cache 树。此阶段没有密钥/hive，也没有完成 Windows 私有 ACL 验证。

未运行：新增 GitHub Windows CI、MSVC 编译/链接/map 实际审查、两个原生宿主、目标 Windows 10/11 桌面、HKCU/种子/原版子进程隔离与 SSH CLI 互操作。CI Windows Server runner 结果不能代替目标客户端系统和用户桌面验收；基础整任务继续进行中。
