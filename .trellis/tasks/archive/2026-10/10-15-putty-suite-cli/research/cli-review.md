# 三个 CLI 的独立定向审查

日期：2026-10-15。活动任务：`10-15-putty-suite-cli`。本轮读取本任务 PRD、设计、实施计划、检查清单及引用规范，并对照 `implementation.md`、`parameter-status.md`、`ubuntu-validation.md` 的实际证据。

## 已修复发现

### R1：转发测试在清理超时后可能留下受测进程

- 文件：`tools/suite/cli_checks.py`，`PlinkContainer.test_local_and_dynamic_forwarding`。
- 原路径在 `finally` 中仅发送 terminate，随后 `communicate(timeout=10)`；进程未及时退出时直接抛出超时，缺少 kill 和最终回收。这会使断言失败后的私有目录清理发生在受测进程仍存活时。
- 修复：复用同文件已有 `stop_process`，先 terminate，超时则 kill，再回收；不改变 Plink 或上游参数。
- 回归：新增真实 Python 子进程，先通过管道确认已安装忽略 SIGTERM 的处理器，再调用清理。断言最终由 SIGKILL 结束、已 wait 回收且输出管道关闭。该测试实际走到超时升级路径，没有用假进程对象替代。

### R2：SOCKS 握手把一次 recv 当成完整字段

- 文件：`tools/suite/cli_checks.py`，同一转发测试。
- 原路径使用 `recv(2)` 后立即断言完整 SOCKS5 方法回复；流式传输允许合法短读，会造成错误失败。
- 修复：新增私有 `read_exact`，对两个握手回复按预期长度循环读取，提前 EOF 明确失败；保留原有字节断言。
- 回归：以真实流式 socketpair 分开发送两个字节。发送第二字节的线程必须等到第一次读取已经结束，确保实际短读；同时验证提前 EOF 不能被当作完整回复。

本轮只修改上述测试脚本并新增本审查记录，没有修改产品、CMake、公共夹具、vendor 或其它并行实现，没有提交或推送。

## 构建与行为核对

- `SuiteCLI.cmake` 从固定 `vendor/putty` 独立配置；构建命令明确只列启用的 CLI 目标，安装使用选定程序的具体路径，不调用上游 install。
- 单配置的构建副产物、导入目标和安装均指向 `suite-cli/<程序>`；多配置均包含真实配置目录。编译器、工具链、sysroot、架构相关参数、链接标志和 MSVC 运行库沿调用方传递；Release 断言保留。
- `SuiteTestSupport.cmake` 的测试用 puttygen 使用独立构建及导入目标，没有安装规则；CLI 测试只在已验证的原生 Linux 注册。Windows 和跨编译没有通过环境标记开放运行。
- 实际核对既有单配置缓存已禁止 Qt 查找，三个 CLI 启用、GUI/runtime 关闭；多配置仅启用 PSFTP。通过 CTest 的真实 JSON 命令检查受测 exe 和测试 keygen 均存在，多配置二者都来自 `Release/`。
- 实际枚举两个已有安装根：单配置恰好三 CLI，多配置恰好 PSFTP，均无测试 keygen；每个安装文件 SHA256 与相应构建产物相同。此只读复核不冒称本轮重建了所有配置；缺少 Release 时安装必须失败的既有证据见基础 `research/build-review.md`。
- 受测产品直接使用上游 CLI 入口，不增加参数包装层。标准流测试按字节比较，远端退出 37/19 按原值断言；主机密钥、认证、连接、批次及写入失败有非零/副作用断言。PSFTP `-be` 最终可以为 0、SCP 默认改名限制等均明确保留固定原版行为。
- SSH/SFTP/SCP 使用公共私有 `.tmp/` 隔离环境；受测命令及测试 keygen 均通过该环境运行，真实连接显式禁用个人代理与连接共享。服务、直接启动的客户端与容器按生命周期清理，未修改个人配置或系统服务。

## 本轮实际验证

环境：Linux x64、Python 3.14.8；使用已有 Release `.tmp/build/suite-cli-tests`、本机 OpenSSH 与已有 Arch 容器对端，不等于目标 Ubuntu 或 Windows。

首次直接执行定向 `cli-plink-local` 时，受限沙箱拒绝 socketpair 的 send，报 `PermissionError`；原有参数检查及强制回收探针正常。随后申请受控回环与 Docker 权限，在已批准环境运行完整检查，未改断言或转换为 skip。

```bash
ctest --test-dir .tmp/build/suite-cli-tests --output-on-failure -L cli --output-log /home/user/projects/putty/.tmp/build/suite-cli-tests/review-ctest.log --output-junit /home/user/projects/putty/.tmp/build/suite-cli-tests/review-results.xml
```

退出 0：**8/8 组、70 个内部 unittest、0 失败、0 跳过，46.01 秒**。相较原始 68 项增加上述两个回归，不改写历史记录。

| 组 | 本轮内部用例数 | 结果 |
| --- | --- | --- |
| cli-plink-local | 9 | 通过 |
| cli-plink-sftp | 6 | 通过 |
| cli-plink-container | 10 | 通过 |
| cli-pscp-local | 6 | 通过 |
| cli-pscp-sftp | 10 | 通过 |
| cli-pscp-container | 8 | 通过 |
| cli-psftp-local | 8 | 通过 |
| cli-psftp-sftp | 13 | 通过 |

独立解析 JUnit，确认恰好 8 个实际结果且没有 failure/skipped；从本轮 LastTest.log 核对内部数量之和为 70 并保存为 `review-detailed.log`，未覆盖原 `cli-detailed.log`。完整证据位于 `.tmp/build/suite-cli-tests/`。

Python AST 解析及 `git diff --check` 通过。工程未为本组 Python/CMake 文件配置独立 lint 或静态类型检查，本机没有 Ruff/Mypy；上述动态测试、AST、安装清单及路径检查是实际证据，不写成执行了不存在的类型命令。本轮未改 C/C++，没有必要重复既有核心编译矩阵。

## 未修复项与验收边界

未发现其它需要在本次已交付范围内修复的确定性缺陷。下列是已有未完成验收，不因本轮测试通过而消除：

- C05、新旧 Pageant、公钥签名计数与代理转发仍待代理组件协作。
- 主机 CA、完整参数组合、旧协议/算法、GSS、串口、反向/X11 转发、PSCP 活动传输中断等状态保持 `parameter-status.md` 所记，不能将来源清单或帮助解析当行为通过。
- 两目标 Ubuntu 只有 `ubuntu-validation.md` 的 16 次实际 CLI 进程场景，本轮没有扩写为全部 70 项已在 Ubuntu 执行。
- Windows 原生 CLI 运行仍需逐进程 HKCU/随机种子隔离；新 Windows CI 此阶段只构建、安装及核对摘要，不执行 CLI。

本轮是局部质量修复，没有新增产品或公共构建契约；现有 suite-build 和隔离规范仍适用，无需同步新的 `.trellis/spec/` 规则。CLI 子任务保持进行中。
