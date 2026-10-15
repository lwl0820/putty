# 共享核心与平台事件桥实施计划

## 启动条件

- [x] 用户在最终方案摘要后以“是”明确批准实施。
- [x] 本目录 PRD/design/implement 与两份 JSONL 已通过本轮规划静态验证；实施前需重查。
- [x] 已满足下述实施依赖，重新读取工作区与有效 AGENTS/层规范，保留并行改动。

本任务首先实施，无其它新增子任务的完成依赖。终端和 Pageant 使用本任务冻结的 C/事件契约；本任务不等待它们形成完整产品。

实施前必须同时读取父任务 design.md/implement.md 及本目录三份规划文档。实施目标是本目录，不是父任务。只有批准后才运行 task.py start。实施/检查默认分别派 trellis-implement/trellis-check，首行提供 Active task: .trellis/tasks/10-15-putty-suite-foundation。原生注入缺失则按此路径读取文档和清单；实施前使用 trellis-before-dev。

## 顺序与检查点

- [x] 加载规范与实时工作区，记录原有 CTest/构建模式基线；先实现独立开关和隔离上游配置。
- [x] 建立 C 公共契约、唯一所有者和有界事件队列，定义关闭/取消与代次失效。
- [ ] 分别实现并验证 Unix 和 Windows 网络、HANDLE、顶层回调及计时桥。
- [ ] 实现 .tmp/ 服务与存储/代理隔离，先验证隔离有效再做持久化或原版代理运行。
- [x] 以最小 C/Qt 宿主验证真实连接、持续事件、关闭和复用；运行新 runtime 标签及 PuTTYgen 回归。
- [ ] 冻结契约并记录被终端/Pageant 使用的接口、平台缺口及验收证据。

当前 Linux 实现与两目标 Ubuntu／Qt 6.8.3 验证见 `research/platform-validation.md`。Windows Server 2022／MSVC19.44／Qt6.8.3 的真实 HANDLE、Qt 网络事件桥及独立私有 hive 探针已在仓库 CI 通过；真实上游存储和种子保护变体正在接入，既有运行时存储组仍明确跳过。完整客户端组合关闭、目标 Windows 桌面与各子任务的实际认证仍需补齐，整任务保持进行中。

## 验证命令与证据

按父 implement.md 的 suite-debug 配置/构建，选择本任务需要的组件；先验证隔离，再运行：

```bash
ctest --test-dir .tmp/build/suite-debug --output-on-failure -L runtime
```

以上标签/新构建选项是拟定契约，需在实现后注册；没有用例时不能将 CTest 的空集合成功当作通过。必须记录实际用例数、系统/工具链、命令和退出状态，并逐条映射本任务 AC 与原版对照编号。受控服务、注册表 hive、配置、socket、私钥、截图和报告仅在项目 .tmp/；不读取个人代理/会话。Windows 原生、X11、Wayland、离屏和真桌面分别记录。

涉及公共构建/安装改动时补 PuTTYgen 既有 CTest、CLI-only/全部关闭配置，以及影响到的既有 Arch 模式回归。对照矩阵和父 AC 的最终检查不能由仅版本/帮助或上游空绘制测试代替。

## 文件所有权与回退

根 CMakeLists.txt、cmake/SuitePuTTY.cmake、cmake/SuiteCLI.cmake、libs/putty-runtime/、tools/suite/ 的公共测试隔离部分；必要上游补丁及来源。不得改写 PuTTYgen 产品接口或组件 GUI。

默认串行实施；其它代理不独占代码库，不回退他人改动。公共文件变更先与所有者协调，完成后同步调用方及测试。Windows 消息线程亲和性、tick 回绕、回调重入、缓存/源配置混用、真实注册表和代理污染。回退新共享目标即可恢复原 PuTTYgen 路径；未通过的平台注册表隔离不得带入后续测试。

## 完成门

- [ ] 本目录 AC 与所有适用对照项有实际结果，跨子任务依赖的验收已补齐。
- [ ] trellis-check 完成对应范围检查，无遗漏的构建/运行失败。
- [ ] 基于真实实现更新相关规范，不将规划写成已落地惯例。
- [ ] 提交前复核所有权和验证证据；记录未执行平台，缺口未收齐则保持进行中。

规划阶段已结束；本任务已获实施授权，按检查点推进并保留真实平台验证边界。
