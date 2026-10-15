# PuTTY 套件集成与发布验收设计

## 基线与契约

共同设计以 ../10-15-putty-suite-completion/design.md 为准：构建隔离、唯一核心线程、类型化 C 边界、代次/请求、原版存储、测试身份隔离和独立部署。该文档及本设计共同组成可评审方案；不是已实现能力。

## 责任与数据流

新套件模式使用 PUTTY_SUITE_RELEASE_PACKAGING，和 PuTTYgen 独立发布/Arch 模式互斥。顶层控制 CPack/部署和 /opt/putty-suite 布局，各组件只添加自己的文件，匹配 Qt 共享运行库集中部署。自动冒烟在干净、身份隔离的环境执行，报告从明确文件复制至独立可读目录，runtime/socket 保持私有。发布流程只生成可审阅产物，不自动外部发布。

责任路径（均为拟定实现位置）：cmake/SuitePackaging.cmake、tools/suite/ 发布/安装冒烟、.github/workflows/ 的套件工作流、根 README/docs、各组件安装清单；对既有 PuTTYgen Packaging/Arch 改动逐项协调。

## 依赖与兼容

依赖 foundation、cli、putty-qt-terminal、pageant-qt-agent 四子任务达到各自可集成门，以及现有 PuTTYgen 可回归。可提前设计安装规则，但所有组件实际可用和适用平台证据齐全前不能完成发布验收或父任务。

固定上游来源/校验、Release 断言和相同平台原版行为保持；GUI 偏好不替代会话存储。所有核心句柄由指定所有者释放，响应和过期事件不能访问旧对象，失败不能投影成成功。工程共用修改前读取实时其它工作，不因本设计覆盖并行改动。

## 验证与回退

CPack 配置被子组件覆盖、重复 Qt/插件不匹配、系统工具路径冲突、root runtime 报告不可读和既有 Arch 被错误部署私有 Qt。套件打包可独立回退，保持现有组件发布入口。

本任务验收标准和父任务映射见 prd.md，步骤与命令见 implement.md。原生平台、真实桌面及未执行项分别记录；没有对应环境时保留缺口，不把静态研究作为通过。涉及产品范围变更时先更新父规划并重新评审。
