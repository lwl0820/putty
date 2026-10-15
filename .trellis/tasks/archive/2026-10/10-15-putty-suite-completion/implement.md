# PuTTY 套件实施计划

## 当前阶段与启动门

用户已在最终摘要后明确批准实施。10-15-putty-suite-foundation 已进入 in_progress，其余子任务按依赖启动；父任务继续作为协调与整体验收入口。

- [x] 界面路线、六组件、平台、双语、原版完整功能及排除项已确认。
- [x] 本地上游及现有工程研究已落盘。
- [x] 父任务与全部子任务的 PRD、设计、实施计划和上下文清单通过静态一致性检查；研究评审问题已修正。
- [x] 用户在最终方案摘要后以“是”明确同意实施。
- [x] 启动首个子任务前重新读取工作区状态、有效规范与实时并行改动；实施分支为 codex/putty-suite-completion。

本文件的未勾选项不表示已执行。后续命令、选项和测试标签是拟定实现契约，当前并不全都存在。

## 执行顺序与交付门

### 1. 共享核心与平台事件桥

- [ ] 保持 pg-putty 与完整 suite-* 核心的配置、符号、源列表及缓存隔离，保留原版断言和完整能力。
- [ ] 定义不透明 C 句柄、请求/事件和单核心线程归属、代次、回调完成、队列背压和异步关闭。
- [ ] 接通 Unix fd、Windows HWND/socket/HANDLE、顶层回调及真实计时，不使用周期轮询或 no-timing 代替。
- [ ] 建立 .tmp/ 内的服务、存储、随机种子与代理夹具；验证 Windows 注册表隔离后才做持久化测试。
- [ ] 完成原版配置读取、两阶段清理与核心析构后的最终回调清扫、最小真实连接和高负载/销毁验证，继续运行 PuTTYgen 回归。

门：两个 GUI 可复用同一个明确的运行时契约；三 CLI 可独立配置构建；全部关闭/CLI-only 不找 Qt。未满足先修复基础，不能将 stub 作为最终实现。

### 2. CLI 工具

- [ ] 复用上游独立配置，只构建选择的 Plink/PSCP/PSFTP，补齐产物依赖、Windows 资源和安装规则。
- [ ] 建立完整参数/命令对照，验证 C01–C04、原版会话互读、主机密钥失败、标准流和退出码。
- [ ] 完成受控 SSH/SFTP 文件摘要和批处理错误验证；新 Pageant 可用后补 C05/I01/I03。

门：每个 CLI 单独能执行真实任务；不能以版本输出替代连接和传输验证。

### 3. PuTTY Qt 主程序

- [ ] 第一阶段先验证专用核心线程、QImage 字宽/绘制、任意 Unicode/回退/DPI、真实网络和长 KDF/GSS；按设计测 GUI 心跳及队列，Windows/Linux 均需验证。
- [ ] 实现 TermWin/Seat/LogPolicy、真实信任标记、输入/IME/鼠标/剪贴板/滚屏/日志/窗口请求。
- [ ] 映射完整 dlg_* 和平台扩展，保留 Conf 草稿及原版存储/字体兼容，覆盖动态控件、取消和会话中重配置。
- [ ] 完成全部适用协议、认证、代理链、连接共享和转发、原版菜单与高级选项。
- [ ] 完成双语、默认中文和状态保留；逐项验证 PT-01–PT-22，联合 Pageant 完成代理矩阵。

门：不能把最小 SSH 纵切报告为完整交付；若线程/绘制方案不成立先返回设计，不能削减功能。

### 4. Pageant Qt 密钥代理

- [ ] 复用上游密钥/证书/延迟解密与请求核心；GUI 仅接收公开快照。
- [ ] Windows 双 IPC、权限和单实例；Linux SSH_AUTH_SOCK、多实例和原生命周期；桌面使用 pageant --gui。
- [ ] 实现完整管理、重加密、在途提示、托盘/无托盘控制与双语，保留显式 CLI 行为和适用 OpenSSH/WSL1 接口。
- [ ] 完成 P01–P18、I01–I14 的适用项，单列新旧客户端逆向互操作、错误/删除/退出并发。

门：原版客户端能使用新代理，新客户端能使用原版代理；不能只验证同一新实现的两端。

### 5. 套件集成与发布

- [ ] 顶层唯一套件 CPack 和六组件安装清单；保留 PuTTYgen 独立发布/Arch 模式。
- [ ] Windows ZIP、Ubuntu 22.04/24.04 DEB 与原版包共存，运行库/插件/帮助/翻译/许可齐全。
- [ ] 干净系统启动/安装/卸载和六组件协作；定点收集报告，不上传私有 runtime 或密钥。
- [ ] Windows10/11、两 Ubuntu X11/Wayland 真桌面检查，核对原版操作、中文路径、输入法、字体、剪贴板、托盘、串口及平台专属能力。
- [ ] 逐项将父 AC1–AC11 关联至子任务记录，任何未执行项保留，不提前完成父任务。

## 拟定开发与验证命令

所有构建在项目 .tmp/。以下选项/标签需由相应子任务实现后才能执行；Windows 在 MSVC 环境执行等效命令。测试进程还必须注入各平台的配置/代理隔离，不直接读取个人环境。

```bash
cmake -S . -B .tmp/build/suite-debug -G Ninja -DCMAKE_BUILD_TYPE=Debug -DBUILD_TESTING=ON -DPUTTY_BUILD_PUTTY=ON -DPUTTY_BUILD_PAGEANT=ON -DPUTTY_BUILD_PLINK=ON -DPUTTY_BUILD_PSCP=ON -DPUTTY_BUILD_PSFTP=ON
cmake --build .tmp/build/suite-debug --parallel
ctest --test-dir .tmp/build/suite-debug --output-on-failure
ctest --test-dir .tmp/build/suite-debug --output-on-failure -L runtime
ctest --test-dir .tmp/build/suite-debug --output-on-failure -L cli
ctest --test-dir .tmp/build/suite-debug --output-on-failure -L terminal
ctest --test-dir .tmp/build/suite-debug --output-on-failure -L pageant
ctest --test-dir .tmp/build/suite-debug --output-on-failure -L suite
cmake -S . -B .tmp/build/suite-cli -G Ninja -DPUTTY_BUILD_PUTTYGEN=OFF -DPUTTY_BUILD_PUTTY=OFF -DPUTTY_BUILD_PAGEANT=OFF -DPUTTY_BUILD_PLINK=ON -DPUTTY_BUILD_PSCP=ON -DPUTTY_BUILD_PSFTP=ON
cmake --build .tmp/build/suite-cli --parallel
cmake -S . -B .tmp/build/suite-empty -G Ninja -DPUTTY_BUILD_PUTTYGEN=OFF
cmake --build .tmp/build/suite-empty
```

套件 Release 配置另加拟定 PUTTY_SUITE_RELEASE_PACKAGING=ON，全部六组件开启，然后用对应构建的 CPackConfig.cmake 输出至 .tmp/packages/suite；不能与 PuTTYgen 独立发布/Arch 模式混用。平台依赖前缀沿用既有 tools/qt 锁定构建方法。

## 协作与上下文

实施前读取 trellis-before-dev。实施/检查默认使用 trellis-implement/trellis-check 子代理，派发第一行必须是 Active task: <实际实施子任务路径>，不能让子代理把父任务当实施目标。原生上下文注入优先，缺失则读取指定子任务 PRD/design/implement 和对应 JSONL。

默认串行派发；若分工，写清文件所有权、共享工作区和不得覆盖他人修改。共享 C 契约、CMake、平台桥和打包规则不由两个代理同时修改。主会话负责跨子任务接口、规范、验收与完成记录。

## 检查、风险与回退

- 每个子任务在其代码落地后跑对应检查；最终使用 trellis-check 全范围核对原版对照与父 AC。只添加能够捕捉边界错误的测试，不为文档或菜单文本写镜像测试。
- 风险重点为原生 Windows 事件/线程、QImage 字宽与字体、同步 GSS/KDF、配置刷新/存储、代理在途回调及部署所有权。纵切门失败先回到设计，不把未验证项标通过。
- 开始和每次集成前复查现有 Arch/PuTTYgen 并行改动；没有授权不得回退、覆盖或归档其它工作。
- 每个子任务保持独立改动点和关闭开关回归。提交前完成规定检查、基于实际实现更新规范，并仅提交本任务拥有的文件；不自动带入未知修改。
- 实施获批后可以构建、检查及提交本任务改动；外部发布和全部验收完成前的归档仍按既定范围处理。实施与验收全完成后才按 trellis-finish-work 记录和收尾；平台缺口保留任务进行中。
