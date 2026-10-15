# PuTTY 套件集成与发布验收实施计划

## 启动条件

- [x] 用户在最终方案摘要后以“是”明确批准实施。
- [x] 本目录 PRD/design/implement 与两份 JSONL 已通过本轮规划静态验证；实施前需重查。
- [ ] 已满足下述实施依赖，重新读取工作区与有效 AGENTS/层规范，保留并行改动。

依赖 foundation、cli、putty-qt-terminal、pageant-qt-agent 四子任务达到各自可集成门，以及现有 PuTTYgen 可回归。可提前设计安装规则，但所有组件实际可用和适用平台证据齐全前不能完成发布验收或父任务。

实施前必须同时读取父任务 design.md/implement.md 及本目录三份规划文档。实施目标是本目录，不是父任务。只有批准后才运行 task.py start。实施/检查默认分别派 trellis-implement/trellis-check，首行提供 Active task: .trellis/tasks/10-15-putty-suite-release。原生注入缺失则按此路径读取文档和清单；实施前使用 trellis-before-dev。

## 顺序与检查点

- [ ] 先读取实时并行改动，收敛组件安装登记与唯一 CPack 所有者，避免破坏独立 PuTTYgen/Arch。
- [ ] 生成 Windows ZIP、分别对应两 Ubuntu 的 DEB 和校验/来源材料，核对安装内容与系统工具共存。
- [ ] 在干净目标系统执行安装/启动/认证/传输/卸载，验证动态依赖和桌面/同包入口。
- [ ] 收集 Windows10/11、Ubuntu X11/Wayland 的真实桌面证据，不能以 CI Windows Server 冒烟代替这两个桌面系统。
- [ ] 执行六组件联合回归及父 AC 映射，单列失败/不适用/未执行。
- [ ] 完成最后质量检查和真实规范更新，仅在验收齐全后准备完成记录；不自动发布到外部服务。

## 验证命令与证据

按父 implement.md 的 suite-debug 配置/构建，选择本任务需要的组件；先验证隔离，再运行：

```bash
ctest --test-dir .tmp/build/suite-debug --output-on-failure -L suite
```

以上标签/新构建选项是拟定契约，需在实现后注册；没有用例时不能将 CTest 的空集合成功当作通过。必须记录实际用例数、系统/工具链、命令和退出状态，并逐条映射本任务 AC 与原版对照编号。受控服务、注册表 hive、配置、socket、私钥、截图和报告仅在项目 .tmp/；不读取个人代理/会话。Windows 原生、X11、Wayland、离屏和真桌面分别记录。

涉及公共构建/安装改动时补 PuTTYgen 既有 CTest、CLI-only/全部关闭配置，以及影响到的既有 Arch 模式回归。对照矩阵和父 AC 的最终检查不能由仅版本/帮助或上游空绘制测试代替。

## 文件所有权与回退

cmake/SuitePackaging.cmake、tools/suite/ 发布/安装冒烟、.github/workflows/ 的套件工作流、根 README/docs、各组件安装清单；对既有 PuTTYgen Packaging/Arch 改动逐项协调。

默认串行实施；其它代理不独占代码库，不回退他人改动。公共文件变更先与所有者协调，完成后同步调用方及测试。CPack 配置被子组件覆盖、重复 Qt/插件不匹配、系统工具路径冲突、root runtime 报告不可读和既有 Arch 被错误部署私有 Qt。套件打包可独立回退，保持现有组件发布入口。

## 完成门

- [ ] 本目录 AC 与所有适用对照项有实际结果，跨子任务依赖的验收已补齐。
- [ ] trellis-check 完成对应范围检查，无遗漏的构建/运行失败。
- [ ] 基于真实实现更新相关规范，不将规划写成已落地惯例。
- [ ] 提交前复核所有权和验证证据；记录未执行平台，缺口未收齐则保持进行中。

规划阶段已结束；本任务已获实施授权，按检查点推进并保留真实平台验证边界。
