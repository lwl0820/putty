# CLI 实施与本机验证记录

日期：2026-10-15。本记录覆盖本次实施代理负责的 CLI 测试接线和验证，不将本机结果作为 Windows、目标 Ubuntu 或整个子任务完成的证明。

## 改动边界

三个程序已经由基础任务直接接入固定上游入口；本次补齐其可重复的行为验证，没有增加 shell 包装或重写命令解析器。

| 文件 | 改动 |
| --- | --- |
| `apps/plink/CMakeLists.txt`、`apps/pscp/CMakeLists.txt`、`apps/psftp/CMakeLists.txt` | 在各组件开关下登记对应 CLI 测试 |
| 上述三组件 `README.md` | 构建、安装、原版参数入口与测试边界 |
| `cmake/SuiteTestSupport.cmake` | 三组件共用的固定原版 PuTTYgen 测试工具与 CTest 接线；不安装测试工具 |
| `tools/suite/cli_checks.py` | 字节标准流、原版存储、真实 SSH/SFTP/SCP、转发和失败行为检查 |
| 本目录 `parameter-status.md` | 114 个来源参数分支、动态后端开关及全部 28 个 PSFTP 命令的来源和运行状态 |

公共 `SuiteCLI.cmake`、根 CMake、隔离及服务夹具由其它代理维护；本代理没有覆盖这些文件。未修改上游协议、CLI 入口、现有 PuTTYgen 接口或任务完成状态。

测试只在已验证的 Linux 本机注册，跨编译和 Windows 不自动运行，避免用环境变量冒充 HKCU 隔离。默认注册三个本地和三个 SFTP 测试组；`PUTTY_SUITE_CONTAINER_TESTS=ON` 额外注册 Plink、PSCP 的专用容器测试组。缺少 OpenSSH 工具明确返回 77；已有工具的真实服务、权限和传输错误仍失败，不自动转成跳过。

`suite-test-keygen-build` 从同一 `vendor/putty` 独立配置，仅构建原版 puttygen，把夹具的 OpenSSH 私钥转换为 PPK。它继承编译器、工具链、标志和配置，保留 Release 断言；导入目标 `suite-test-keygen` 与三个受测程序均使用 `$<TARGET_FILE:...>`，支持单配置和 Ninja Multi-Config。此工具没有安装规则。

## 实际环境与隔离

本机 Linux x64、GCC 16.2.1、CMake 4.4.4、Ninja、Python 3.14.8、OpenSSH 10.6p1，非目标 Ubuntu 原生运行。容器对端使用本机已有 Arch 镜像和只读宿主 `/usr`，提供专用账户；其细节与 6 项对端验证见 `container-fixture-implementation.md`。不能据此宣称目标发行版 SSH 服务已验收。

全部配置、随机种子、PPK、主机密钥、临时账户、socket、数据和日志在项目 `.tmp/`。Linux 子进程先执行隔离环境探针，清除继承的代理，再以 `-noagent -noshare` 连接临时回环服务。不读取个人会话或启动个人登录 shell。容器不联网、不拉取镜像，真实 SCP 和远端命令只在专用账户执行。

本机 socket 和 Docker 需要 `require_escalated` 才能执行；审批通过后实际运行，没有把受限沙箱的拒绝视作协议通过。每个测试结束先关闭受控服务/客户端，再删除私有根；新增进程清理辅助保证断言失败时也回收直接启动的受测进程。

## 命令与结果

### 单配置 Release

```bash
cmake -S . -B .tmp/build/suite-cli-tests -G Ninja -DCMAKE_BUILD_TYPE=Release -DBUILD_TESTING=ON -DPUTTY_BUILD_PUTTYGEN=OFF -DPUTTY_BUILD_PLINK=ON -DPUTTY_BUILD_PSCP=ON -DPUTTY_BUILD_PSFTP=ON -DCMAKE_DISABLE_FIND_PACKAGE_Qt6=ON
cmake --build .tmp/build/suite-cli-tests --parallel 4
cmake -S . -B .tmp/build/suite-cli-tests -DPUTTY_SUITE_CONTAINER_TESTS=ON
ctest --test-dir .tmp/build/suite-cli-tests --output-on-failure -L cli --output-junit /home/user/projects/putty/.tmp/build/suite-cli-tests/cli-results.xml
```

配置/构建退出 0，未查找 Qt。最终 **8/8 个 CTest 组、68 个内部 unittest 通过，0 失败、0 跳过，47.75 秒，退出 0**。

| CTest 组 | 内部用例数 | 结果 |
| --- | --- | --- |
| cli-plink-local | 7 | 通过 |
| cli-plink-sftp | 6 | 通过 |
| cli-plink-container | 10 | 通过 |
| cli-pscp-local | 6 | 通过 |
| cli-pscp-sftp | 10 | 通过 |
| cli-pscp-container | 8 | 通过 |
| cli-psftp-local | 8 | 通过 |
| cli-psftp-sftp | 13 | 通过 |

证据：`.tmp/build/suite-cli-tests/build.log`、`cli-results.log`、`cli-results.xml`、`cli-detailed.log`。JUnit 初次传了相对路径，被 CTest 放入测试构建目录的嵌套路径；已将其整理至上述明确位置并清理仅本次创建的空目录，命令示例改用绝对路径。没有因此重复运行或伪造结果。

安装：`cmake --install .tmp/build/suite-cli-tests --prefix /home/user/projects/putty/.tmp/build/suite-cli-tests/install`，退出 0；清单恰好为 `bin/plink`、`bin/pscp`、`bin/psftp`，没有测试用 puttygen 或附加程序。

### 多配置 Release，仅 PSFTP

```bash
cmake -S . -B .tmp/build/suite-cli-tests/multi -G 'Ninja Multi-Config' -DBUILD_TESTING=ON -DPUTTY_BUILD_PUTTYGEN=OFF -DPUTTY_BUILD_PSFTP=ON -DCMAKE_DISABLE_FIND_PACKAGE_Qt6=ON
cmake --build .tmp/build/suite-cli-tests/multi --config Release --parallel 4
ctest --test-dir .tmp/build/suite-cli-tests/multi -C Release --output-on-failure -L cli
cmake --install .tmp/build/suite-cli-tests/multi --config Release --prefix /home/user/projects/putty/.tmp/build/suite-cli-tests/multi-install
```

全部退出 0，**2/2 个 CTest 组、21 个内部用例通过，0 跳过，5.92 秒**；受测程序与测试 keygen 均从各自 `Release/` 路径运行。安装清单只有 `bin/psftp`。证据为 `multi-configure.log`、`multi-build.log` 和 `multi/Testing/Temporary/LastTest.log`。

### 静态检查

`git diff --check`（本代理所负责路径）及所有新增文件行尾检查通过。所有 Python 模块均由上述实际运行加载；未新增依赖，不下载 Qt。

最后补强受测子进程的异常回收后，定向复测 `cli-plink-sftp`、`cli-psftp-sftp`：**2/2 组、19 个内部用例通过，7.08 秒，退出 0**。证据为 `cleanup-recheck.log`；完整 68 项的原始详细记录已保存在 `cli-detailed.log`，不被此定向复测覆盖。

## 可观察行为与验收映射

| 对照/AC | 已有本机证据 | 未收齐部分 |
| --- | --- | --- |
| C01 / AC1 | 版本/帮助/PGP 公开指纹走 stdout；非法参数、缺参走 stderr 且退出 1；真实端口、用户名、PPK、batch、主机密钥拒绝、错误公钥、连接拒绝；原格式会话读出真实服务参数；PSCP/PSFTP 人工确认后写原格式缓存并下次无提示读取 | 全部参数组合、主机 CA、代理链、连接共享、目标系统及 Pageant |
| C02 / AC2 | Plink 传入并完整回传超过 1 MiB 的二进制 stdin/stdout，独立含 NUL/非 ASCII stderr，远端退出 37 原样传回；命令文件退出 19、详细日志与 stdout 分离；SFTP 子系统二进制 VERSION；PTY 开关、`-nc`、`-N/-L`、SOCKS5 `-D` 实际回显 | `-R`、X11、其它协议、串口、Git 与全部高级配置 |
| C03 / AC3 | PSCP 默认/强制 SFTP、强制 SCP 双向空/二进制/中文空格路径摘要；递归、空目录、mtime/文件模式、列表与通配符、真实本地/远端写入错误和缺失文件 | PSCP 传输中真实断线、更多参数组合、目标系统 |
| C04 / AC3 | PSFTP 全部命令帮助入口；get/put、递归、续传、mget/mput、工作目录、改名/删除/权限、close/open、批失败停止、`-be/-bc`、已认证会话断开后不能再创建文件 | 本地 `!` 命令业务、更多引用/组合和目标系统 |
| C05 / AC4 | 当前主动禁用代理，未把直接 PPK 认证写成代理成功 | 新旧 Pageant、公钥签名计数、代理转发及跨 GUI 协作全部待补 |

## 保留的原版语义与风险

1. `-load` 已载入可启动会话时，Plink 后续非选项直接作为远端命令；再次传主机会把它加入命令，不能套用未加载会话的参数模板。
2. Linux 原版不支持 `-legacy-stdio-prompts` 与 `-legacy-charset-handling`，返回不可用；主机密钥确认测试直接用原版 stdin 行输入，未改该行为。
3. SFTP 子系统收到 stdin EOF 后可以在发送 VERSION 前退出。Plink 二进制测试保留输入管道，先读完整真实回复再 EOF；没有删除二进制检查或接受空输出。
4. PSCP SFTP `-ls` 接受目录，通配符另用下载验证。递归上传到不存在目标时原版 `scp_sftp_targetisdir=false` 有嵌套目标限制；本轮保留源码，验证向已有目录递归上传，并记录这一原版限制。
5. PSCP 原生 SCP 的远端路径还经远端 shell；带空格时保留反斜杠引用。默认通配符会警告拒绝服务端改名并使用请求的字面文件名；测试明确验证此限制，另在受信任夹具中显式测试 `-unsafe`，没有给其它连接全局放开。
6. PSFTP `-be` 继续处理后最终可退出 0，这是原版语义；不能为要求统一非零而修改解析器或测试期待。

本代理没有完成 Windows 原生 HKCU/随机种子隔离、目标 Ubuntu 二进制运行、Pageant 或发布包验收。共享构建/既有 PuTTYgen/Arch 的广泛回归由主会话协调，本记录不冒充重复执行。任务保持进行中，由主会话安排独立检查及后续平台、代理验证。
