# Windows CI 与受控测试宿主

本目录由独立工作流 `.github/workflows/putty-suite.yml` 使用，沿用 MSVC 2022 x64、Qt 6.8.3、CMake 3.30.9、Ninja 1.11.1.1。它目前接共享运行时和三个 CLI 的构建与安装；尚未接入终端/Pageant 的 Windows 产品验收，不修改原 PuTTYgen 工作流。

## 执行与结果

在 MSVC x64 开发环境、已构建锁定 Qt 的 Windows 上：

```powershell
python -B -m unittest discover -s tools/suite/windows/tests -v
cmake -S . -B .tmp/build/windows-release -G Ninja -DCMAKE_BUILD_TYPE=Release -DBUILD_TESTING=ON -DCMAKE_EXPORT_COMPILE_COMMANDS=ON -DCMAKE_PREFIX_PATH="$PWD/.tmp/qt" -DPUTTY_BUILD_PUTTYGEN=OFF -DPUTTY_BUILD_RUNTIME=ON -DPUTTY_BUILD_PLINK=ON -DPUTTY_BUILD_PSCP=ON -DPUTTY_BUILD_PSFTP=ON
cmake --build .tmp/build/windows-release --parallel 4
$env:PATH = "$PWD/.tmp/qt/bin;$env:PATH"
python -B tools/suite/windows/run_ctest.py --build .tmp/build/windows-release --require-hive --require-storage
```

每个命令失败应停止；工作流逐条检查退出码。`run_ctest.py` 先要求恰好注册 `runtime-windows-handles`、`runtime-windows-events`，再执行 CTest 并核实 JUnit 中这两个用例存在、未跳过、未失败。`--require-hive` 另要求唯一私有 hive 探针；`--require-storage` 必须同时指定前者，并要求唯一受控上游存储用例。零测试、重复、失败和跳过均不能满足相应要求。执行前删除旧 JUnit，避免误收历史结果。其它 CTest 的实际跳过名称单独列入 JSON；现有 `runtime-lifecycle` 尚未接入受控种子变体，仍返回 77。

| 宿主 | 实际调用路径及检查 |
| --- | --- |
| `suite-windows-handle-tests` | 真实 `CreateEventW`/`WaitForSingleObject`，手动/自动复位；等待完成、回调尚未交付时撤销，并用同一个仍存活的真实 HANDLE 重新注册；旧快照不激活新注册；回调内注销 |
| `suite-windows-runtime-tests` | 真正的 Runtime 核心线程和 Qt `QWinEventNotifier`；手动/自动事件各触发三次；上游隐藏 HWND/`WSAAsyncSelect` 的 256 KiB 双向 TCP、单独 `FD_OOB`、本地关闭/重开/远端关闭、待决事件中关闭及所有者线程核验 |

上述原生源码位于 `libs/putty-runtime/tests/windows/`；由共享测试 CMake 的 `WIN32` 分支接线。两个宿主遇到错误返回非零，没有 skip 分支。`NDEBUG` 仍存在时编译直接失败。事件宿主使用较大有界队列，专门验证平台事件；既有生命周期/背压/存储组合测试的 Windows 验收不能由它替代。

## 无存储边界

事件模块不调用 `random_ref`，不加载客户端 PRNG；专用 `noise_ultralight` 不收集熵。`read_random_seed`、`write_random_seed`、`open_settings_r/w` 在本测试前端中均为致命拒绝入口，不是存储实现。测试没有替换 `random_read` 或密码算法；没有运行真实 SSH 认证。

Release 链接显式保留四个拒绝入口。运行器先审查 MSVC map，要求这些入口存在，并拒绝真实 `sshrand`、`noise`、`storage` 对象以及客户端随机入口。它只接受项目 `.tmp/` 下两个指定名称的测试二进制，不运行 Plink/PSCP/PSFTP；不把普通 CLI 的 `-V`/help 当安全入口，因为上游可能先读取默认会话。构建、清单/摘要和 Ninja 命令核对均不执行 CLI。

随机工作根、TEMP/TMP/TMPDIR、报告和构建产物全部位于项目 `.tmp/`。子进程清除个人代理环境；HOME、USERPROFILE、APPDATA 不变。路径拒绝越界、上级跳转、符号链接和 Windows reparse point。无存储事件宿主不应创建任何运行文件，结束后运行器检查空目录并清理；独立 hive/存储宿主按各自私有权限契约运行。此工具不是任意二进制的权限沙箱。

`PUTTY_SUITE_NATIVE_NO_STORAGE=1` 仅标识无存储测试模式，`GITHUB_ACTIONS` 不参与放行。微软说明 [`RegOverridePredefKey`](https://learn.microsoft.com/en-us/windows/win32/api/winreg/nf-winreg-regoverridepredefkey) 只影响调用进程；[`RegLoadAppKeyW`](https://learn.microsoft.com/en-us/windows/win32/api/winreg/nf-winreg-regloadappkeyw) 要求各参与进程自行获得应用 hive 句柄。因此父进程覆盖不能当作原版 CLI 子进程已隔离。独立 [hive 探针](hive/README.md) 已在 Windows CI 成功；受控存储宿主使用原始上游存储翻译单元和极窄的种子文件保护入口，拒绝个人路径回退。其成功只证明该变体，不开放生产种子回退、旧运行时或原版 CLI。新增接线尚待实际 CI 完成。

## 构建与报告

工作流先在禁用 Qt 查找时执行全关闭和 PSFTP-only 多配置构建；仅有 Debug 时 Release 安装必须失败。随后核对 Debug/Release 安装清单、配置对应的二进制摘要。完整 Release 构建包含共享核心和三个 CLI；`check_build.py` 核对实际核心编译命令、CLI 缓存及 Ninja 生成的编译命令仍启用断言，并要求安装文件集合恰好为选定的 CLI。

只上传 `.tmp/windows-ci/reports/` 下明确的 JSON/XML、`private-hive.json`、`private-storage.json`、具名测试宿主 EXE 和三个安装 CLI EXE。没有上传整个 `.tmp/`、运行私有根、Qt/pip cache、密钥或 hive 树。事件 EXE 需要匹配 Qt DLL，属于测试程序，不是已完成部署的用户交付包。

Windows Server 2022／MSVC19.44／Qt6.8.3 的事件、HANDLE 和 hive 探针已在 [69e7607 对应 CI](https://github.com/lwl0820/putty/actions/runs/38664741693) 实际成功；存储变体和新增 GUI 接线结果分别记录，不能继承旧成功。Windows 10 1809+/11 的字体/DPI、输入法、剪贴板、托盘、窗口和权限交互由用户人工验收，结果另记。
