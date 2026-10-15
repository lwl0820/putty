# Windows MIT 无凭据夹具独立检查

## 范围与结论

检查 `tools/suite/windows/gss_mit/` 全目录，并只读核对 `.github/workflows/putty-suite.yml` 的独立 `windows-mit-abi` 作业。未修改共同 `PrivateData`、工作流、KDC、Pageant、PPK 或生产 GSS 实现。

已读本任务 PRD、设计、实施计划、父设计与实施计划，以及后端目录、错误、构建工具、私有权限规范和现有 GSS 实施记录。此检查只覆盖无凭据第一阶段；真实 Kerberos 登录、Microsoft SSPI 正向认证和终端长调用仍未由本夹具实现。

固定官方 URL、MSI/CAB/十成员长度及 SHA256、两层清单和单成员 stdout 提取、x64 PE 普通与延迟依赖闭包、独立进程封闭环境、无凭据 API 白名单、真实默认字符串 getter、失败占位和原子报告替换、精确运行根清理等路径均已逐一核对。共享 `PrivateData` 的默认双 SID 模式保持不变；本夹具没有收紧产品输出权限来掩盖问题。

## 已修复发现

- 文件：`tools/suite/windows/gss_mit/mit_gss.py`。
- 问题：初始化时允许复用已加载的同路径 DLL。即使句柄路径正确，MSVCR100 仍可能在封闭环境之前保存环境副本，不能据此证明首次加载契约。
- 修复：加载任何测试 DLL 前，六个目标模块必须全部未加载；发现任一预加载模块即失败。加载后的实际句柄路径核验仍保留。
- 回归：新增一个测试，分别覆盖六个目标 DLL 的预加载情况，证明只打开系统 `kernel32` 后即拒绝，不进入测试 DLL 加载。该测试只核对失败控制流，不冒称 Windows 或 CRT 原生验证。
- 文档：同步本目录 README 中的加载前提和原因。
- 文件：`tools/suite/windows/gss_mit/probe.py`、`tests/test_boundaries.py`。
- 问题：原生 DLL／API 没有探针自身的硬期限，CI 作业超时不能保证及时退出并留下可靠的失败状态。
- 修复：初始失败报告完成 fsync 和原子替换后，启动 daemon watchdog；正式 CLI 固定 60 秒预算覆盖探针、清理、报告和标准输出刷新。超过期限直接 `os._exit(124)`，不执行伪清理，不在超时分支写成功；正常返回必须在完成输出后取消 watchdog，取消时也核对绝对期限。
- 回归：真实启动独立 Python 子进程，分别在无原生 API 的替代探针、最终报告提交前挂起，以 0.25 秒内部测试预算证明退出码 124、期限内结束、旧成功报告已被失败占位替换、没有 `private_run_removed` 声明且输出没有私有路径。另以正常失败报告验证 watchdog 已取消，子进程跨过测试期限后仍按原失败码 1 退出。这些测试不加载真实 DLL、不执行凭据 API。

## 未修复发现

没有需要产品决定、接口调整或越出本次文件所有权的确定性代码问题。工作流按独立 Python CLI 顺序运行准备器与探针，并只上传明确报告文件；未发现需要根代理修改的接线问题。

## 验证

- 环境：本机 Linux、Python 3.14；未使用 Windows SDK、Wine 或模拟结果宣称原生通过。
- MIT 回归：`python -B -m unittest discover -s tools/suite/windows/gss_mit/tests -v`，补齐硬期限后 13 项通过，0 跳过。
- 公共工具回归：`python -B -m unittest discover -s tools/suite/windows/tests -v`，41 项通过，0 跳过。
- 内容锁与真实 PE：对既有 `.tmp/windows-gss-portable/portable/bin` 执行正式 `validate_directory()`，十个实际官方提取文件共 2,960,208 字节；各自 SHA256、普通和延迟导入及闭包检查通过。没有执行这些 Windows 文件，也未重复已有联网下载证据。
- 语法：七个 Python 源文件按 Python 3.11 语法解析，并以 `compile(..., "exec")` 检查，通过；不生成源码树缓存。
- Lint：本目录逐文件尾随空白检查和 `git diff --check` 通过。仓库没有配置或安装独立 Python linter，因此不把这些检查说成 Ruff／Flake8 已执行。
- TypeCheck：此工程未配置 Python 类型检查器，本机没有 mypy／Pyright；未执行，不声称通过。语法检查与类型检查分开记录。
- Windows 原生：六 DLL 实际加载、原生 ACL、真实机制枚举和默认 realm／FILE 名 getter 尚待仓库 Windows CI。既有本机检查不替代这些结果。

## 规范同步建议

根代理维护公共规范时应保留：旧 CRT 依赖库必须由新进程在固定环境中首次加载；仅校验已加载模块路径不足以证明环境隔离。同一进程切换 `os.environ` 或恢复 Python 环境也不构成卸载和重置。该契约目前已写入夹具 README；本检查不越权编辑根代理正在修改的公共规范。
