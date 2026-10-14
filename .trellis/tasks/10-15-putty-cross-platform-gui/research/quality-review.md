# 质量审查记录

## 第一轮：构建与发布

2026-10-15，检查子代理按 PRD、设计、G01–G15 和已落地的核心／Qt 规范审查。核心和 GUI 代理仍在补齐测试，本轮只修改已交接的发布文件。

### 已修复

- `.github/workflows/build-packages.yml`、`docs/构建与发布.md`：Windows 测试程序运行于构建目录，在打包部署前不会自动从源码 Qt 的安装目录找到 Qt DLL。为 CTest 增加同版本 Qt `bin` 的 PATH。包启动脚本仍移除 SDK 路径，避免开发运行库掩盖发布依赖缺失。

### 已检查

- 发布配置强制 Qt 6.8.3、x64、MSVC 2022 或对应 Ubuntu／GCC，开发归档有明确名称。
- Windows 收集本地 CRT、Qt DLL 和平台插件；Ubuntu 单独安装到 `/opt/puttygen-gui`，桌面入口和卸载脚本不操作用户密钥。
- Qt 来源锁、各模块源码归档和完整许可随包安装；PuTTY 原许可证完整保留。
- `packaging/*.py` AST／语法检查通过；Shell 脚本 `sh -n` 通过；CI YAML 用 PyYAML 解析通过。
- 本机 Qt 6.12.0 的发布配置负向检查成功：CMake 在版本锁处拒绝发布构建。检查目录 `.tmp/review/release-lock-negative/`，退出码 1 是预期结果。

### 交给实施代理修复

- `src/ui/ppk_dialog.cpp` 的接受逻辑原先只检查正整数，缺少原版的完整参数校验。GUI 代理已改为 `ValidateParameters` 工作线程请求，使用 64 字节占位口令执行核心校验，非法内存／并行度有错误反馈且不提交设置；回归通过。

## 第二轮：完整产品代码与本机行为

本轮目录仍为布局迁移之前的 `src/`、`resources/`、`tests/`。用户补充仓库为完整 PuTTY 套件之后，主代理将单独安排组件目录迁移；此记录不把布局尚未迁移视为已经完成。

### 已修复的实际问题

- `src/ui/main_window.*`：输入新注释后立刻加载坏文件或取消错误口令重试，后台忙碌暂停计时器，而失败／取消早返回没有恢复提交，导致公钥与字段注释不一致。新增 `resumeCommentUpdate()`，只在完整操作／模态流程结束且无冲突状态时恢复；加载、证书选择、PPK 和保存模态流程开始前暂停，避免 Comment 抢占随后发起的 Load／Save。GUI 代理先复现两条失败，再验证修复；另验证文件选择框保持 400ms 时不提交 Comment，取消后恢复。
- `src/app/language_manager.*`、`packaging/verify_runtime.cmake.in`：原先只安装自有 JSON 翻译，Qt 文件对话框的 Back／Forward／Parent Directory／Create New Folder／List View／Detail View 实际为英文。现在先加载官方 `qtbase_zh_CN.qm`，再装应用翻译；英文时移除两者。正式包必须包含该 qm。临时 Qt 探针确认六项工具提示及显示隐藏文件菜单变为中文，原生文件对话框未被强制替换。
- `tests/gui/gui_test.cpp`：GUI 代理把 QSettings 测试切为 IniFormat，防止 Windows 测试写原生注册表；窗口图标改为 PNG，避免发布包无 qtsvg 插件时缺少图标。
- `src/CMakeLists.txt`：GUI 测试根按构建目录哈希隔离，配置、运行时、文件、截图都位于 `.tmp/tests/gui-<哈希>/`；并发 Ubuntu 构建不再共享 CA 或语言配置。相应测试路径已由 GUI 代理同步。
- `.trellis/spec/frontend/state-management.md`：同步参数完整校验、注释与模态任务时序、Qt 官方翻译和 Windows／并发测试隔离契约。

### 本轮核验结果

- 专用 `.tmp/review/debug/` 使用 GNU 16.2.1、Qt 6.12.0 完整编译成功；自有 C／C++ 警告选项下无警告。此为本机类型／编译检查，不是 Qt 6.8 或目标平台构建证据。
- 核心 CTest 2/2 通过，8.87 秒；额外使用固定版本上游 CLI 运行完整互操作脚本通过，覆盖全部算法／适用格式、公钥、SHA256／MD5、签名和证书。
- 最终完整 CTest 3/3 通过，15.58 秒；其中 GUI 7.29 秒，Qt Test 输出 16 passed、0 failed、0 skipped，包含新保存／覆盖／取消、剪贴板、两条注释恢复回归及官方标准文件控件中英翻译回归。GUI 代理另报告 2 倍缩放通过。此前追加同名翻译回归一度产生重复声明，已保留一份完整回归并清除重复，最后编译及上述测试均通过。
- 核心代理 ASan／UBSan 日志 `.tmp/build/core-sanitize/Testing/Temporary/LastTest.log` 为 2/2 通过、31.40 秒；使用 `detect_leaks=0`，因此该结果不构成 LeakSanitizer 检测通过声明。
- `nm` 核验生产 `libpg-core.a` 无 `pg_test_` 符号，故障注入与测试签名／证书助手只在测试库中。
- 官方归档 SHA256 再次比对一致；逐一比对 `vendor/putty` 所有普通文件，无缺失，仅 `import.c` 有已记录的三处导出流错误／关闭错误补丁。
- 读取真实 PRNG 路径确认 `prng_seed_begin` 将已有 generator 作为 keymaker，再混入新种子；系统随机种子后的鼠标重播种保留此前状态。不存在用鼠标替代系统随机源的退化。
- 审查单工作线程、密钥替换与错误保留、C 缓冲区释放、后台进度排队、对象销毁和关闭等待；没有发现尚未修复的具体本机代码问题。
- 查看英文 1 倍、中文 2 倍成钥截图：标签完整、口令隐藏、无任务或开发需求文字。截图是离屏界面证据。

### AC 与 G01–G15 范围

| 范围 | 本轮证据与边界 |
| --- | --- |
| G01、G13／AC2、AC5 | 主窗／菜单共用动作、状态投影、助记键和控件条件已审查；截图与 GUI 状态回归通过。未进行 Windows 原版运行对照。 |
| G02–G05／AC2、AC7 | 全部算法／曲线、三种素数策略、强 RSA 传入原核心，生成及适用格式测试通过；概率进度为真实步骤估计。 |
| G03／AC10 | 鼠标事件限速和后台真实完成验证；退出等待保持事件循环。离屏合成鼠标事件不替代真实桌面鼠标体验。 |
| G06、G08、G15／AC9、AC10 | 加密加载、错误口令重试／取消、损坏文件、口令不一致／无口令警告、错误后保留密钥以及注释恢复已验证。 |
| G07、G09、G11／AC7、AC9 | 独立指纹、公钥、全部适用格式往返、实际剪贴板及文件选择保存／覆盖／取消回归通过。 |
| G10、G12／AC2、AC9 | PPK v2/v3、三种 Argon2、自动／固定参数、非法参数不提交、证书匹配／不匹配／详情／移除已验证。 |
| G14／AC4、AC8 | 关于与上游许可资源、双语离线帮助、官网入口和默认／持久语言逻辑审查；Qt 标准文件控件翻译遗漏已修。 |
| AC3、AC6 | 发布目标限制已检查，临时产物与测试根均放项目 `.tmp/`。用户新增组件布局要求另需迁移复查。 |
| AC1、R1、R10 | 发布脚本静态审查通过；目标 Qt、Ubuntu 包和原生 Windows 证据尚不能由本机检查代替。 |

### 尚待最终审查／验收

- 用户新增套件／组件布局迁移后的 CMake、资源、测试、打包和文档路径复查。
- 主代理正在执行两个 Ubuntu 的 Qt 6.8.3 原生构建与包检查，本轮不重复构建，不将尚未完成的日志当通过证据。
- Windows 原生编译、Windows 10／11 桌面和两 Ubuntu 的用户桌面验收。CI 配置和静态审查不能替代这些结果。


## 第三轮：套件目录迁移与完整产品复查

本轮审查当前迁移后的全部自有接口、核心适配、Qt 窗口／线程／翻译、测试、共享上游构建、源码工具、组件安装规则及 CI。先前 G01–G15 行为契约未因目录迁移改变，新增 AC11 另行验证。没有把其他 PuTTY 工具列为本期已实现组件。

### 本轮修复

- `apps/puttygen/cmake/Packaging.cmake`：两个 Ubuntu 的真实 Qt 6.8.3 发布打包同时暴露中文标准控件翻译缺失。Qt 的通用部署默认把输入目录合并为 `qt_zh_CN.qm`，应用实际加载的是 `qtbase_zh_CN.qm`；仅依赖自动部署无法满足这个文件契约。现在根据 Qt 导出的安装前缀与翻译目录显式安装 `qtbase_zh_CN.qm`，配置阶段检查源文件存在，两平台均通过 `NO_TRANSLATIONS` 禁止自动合并，英文直接使用 Qt 源文本。原有安装后强制翻译存在性检查保持。Qt 6.8.3 实际 CMake 实现已确认该参数受支持；目标打包复验由主代理执行，结果单独记录。

### 迁移与范围核验

- 套件根 `project(putty)` 与 `PUTTY_BUILD_PUTTYGEN` 为唯一组件入口。独立 `.tmp/review/component-off/` 配置使用 `-DPUTTY_BUILD_PUTTYGEN=OFF -DCMAKE_DISABLE_FIND_PACKAGE_Qt6=TRUE`，配置与构建均通过，关闭组件确实不调用 Qt 查找。
- `apps/puttygen/` 集中组件源码、资源、测试、安装和包验证。共享上游 `vendor/putty/`、`cmake/UpstreamPuTTY.cmake`、Qt 来源工具 `tools/qt/` 与根许可保持唯一来源。核心接口仍服务于 PuTTYgen，未加入无实际用途的套件业务抽象。
- 核对组件 CMake 所有源码和测试路径、Qt 资源清单全部文件、共享 PuTTY 许可证相对路径、工作树 `.tmp/` 测试根及固定上游 CLI 的构建／互操作入口。根 CPack 配置输出、安装材料和开发包名称、Windows／Ubuntu CI 与文档中的调用均使用迁移后路径。
- 再次读取全部 C 适配和 Qt 业务代码：单线程核心、错误分类、失败后旧密钥、临时口令／熵清除、证书替换和关闭等待与已记录契约一致；未发现迁移带来的新业务缺陷。G01–G15 的原有入口、适用条件和错误／取消处理保持。
- 发布配置固定 Qt 版本、目标架构和工具链，材料包含 Qt 模块完整许可证、来源锁、源码归档及重建脚本。新打包修复不会把测试夹具或上游 CLI 加入安装树。

### 本轮检查与证据

- Python AST 检查：`tools/qt/build_qt.py`、组件 `smoke_package.py` 和 `interop_test.py` 全部通过；检查不写入 `__pycache__`。Qt 来源锁与中文 JSON 解析通过；GitHub Actions YAML 解析通过；QRC 所有引用存在。
- `sh -n` 检查 Ubuntu 依赖安装脚本、组件启动脚本及安装／卸载维护脚本全部通过。工程没有另外配置独立 linter；本轮语法检查与编译告警分别记录，不把 YAML 解析描述为实际 Actions 运行。
- 迁移后 `.tmp/review/component-debug/Testing/Temporary/LastTest.log`：核心、固定 CLI 互操作与 GUI 共 3/3 通过，总计 18.66 秒，分别 8.54、4.04、6.08 秒。GUI 为 16 passed、0 failed、0 skipped；该构建是本机 Qt 6.12.0／GNU 16.2.1，不作为锁定 Qt 或 Windows 证据。
- 迁移后根 CPack 开发 TGZ 实际生成成功，归档主程序、PuTTY 许可与第三方通知存在，SHA256 相符；未包含测试私钥、测试程序或固定 CLI。此归档按名称明确为开发包，未宣称包含发布 Qt。
- 主代理已报告 Ubuntu 22.04／GCC 11 与 Ubuntu 24.04／GCC 13、Qt 6.8.3 的迁移后 Release 编译和全部 CTest 通过；本轮最初 CPack 失败的翻译缺失已如上修复。打包、安装卸载、图形后端结果待主代理实际复验后补入平台报告。

### 保留的验收边界

- 本轮自有代码静态审查、脚本语法与本机完整测试没有剩余已知产品缺陷。最终发布完整性仍取决于上述真实目标包验证，不用本机开发归档代替正式 DEB／ZIP。
- Windows 原生 MSVC 编译、ZIP 启动及 Windows 10／11 桌面未验证；GitHub 认证当前不可用，已有工作流文件不能视为已触发或通过 Actions。
- XCB／Wayland QtTest 和安装包启动即使通过，也不等同于真实桌面人工 G01–G15 对照验收。Windows 原版运行对照、真实鼠标操作与桌面体验仍需单独完成。
- 前轮 ASan／UBSan 结果仍仅是核心已运行的范围，且 `detect_leaks=0`；没有增加 LeakSanitizer 或全 GUI sanitizer 通过声明。


## 第四轮：目标包插件依赖修复

翻译修复后，两 Ubuntu 的配置与 CPack 翻译检查通过，随后插件强制检查揭示第二个真实问题：暂存包没有任何 Qt 插件，生成目录中也没有 `puttygen-gui-plugins.cmake`。Qt 6.8.3 的实际 CMake 路径表明，普通 `add_executable` 没有自动完成 Qt finalizer，部署 API 因而缺少插件清单。此外，Qt 默认平台仅为 XCB，Wayland 和离屏插件不默认收集；Wayland shell／绘图／装饰插件属于单独模块。

- 修复仅涉及组件 `cmake/Packaging.cmake`：在正式 Linux 分支查找并链接 `Qt6::WaylandClient`，显式选择 XCB、Wayland generic／EGL 及离屏平台，以及默认不收集的 IBus／Compose 输入法和 XdgDesktopPortal 主题插件；两平台生成部署脚本后调用 `qt_finalize_target`。使用官方插件依赖图与部署流程收集各插件及其动态依赖，避免只复制平台 `.so` 而遗漏 Wayland 子插件或 Qt 库。Windows 分支不查找 Linux 模块。
- 保留翻译和 XCB／Wayland 插件强制检查。新依赖只影响发布目标的合理重链接，不改密钥接口或用户业务流程。主代理已确认重新执行目标配置、构建、测试、打包与干净环境启动。
- 本轮主代理新增原生后端证据：Qt 6.8.3 的 XCB QtTest 在 Ubuntu 22.04 和 24.04 均 16 项通过。Headless Weston 不提供 `wl_seat`，两平台 Wayland 测试各 14 项通过、鼠标熵与剪贴板 2 项失败；日志保留，不忽略失败或删测试。主代理继续用提供输入 seat 的合成器和实际输入验证，不能将当前 Wayland 结果描述为全通过。

修复后已读取两个目标构建生成的 `.qt/puttygen-gui-plugins.cmake`，包含 XCB、Wayland generic／EGL、离屏平台和 Wayland shell／绘图／装饰插件，证明 finalizer 修复实际生效。后续补入的三个输入／主题插件仍需在最终目标清单和包内复验；它们补全 Linux 中文输入与桌面文件框所需的原有 Qt 插件能力，不新增 Windows 依赖。


## 第五轮：目标打包的 UTF-8 环境

主代理确认 Ubuntu 22.04 首次完整 DEB 已生成，插件强制检查已经通过。Ubuntu 24.04 的 CMake 3.28／`LC_ALL=C` 在 CPack 处理 `构建与发布.md` 时发生 `Can't lstat`；没有改动或删除中文文件名。

- `.github/workflows/puttygen.yml`：仅 Ubuntu job 设置 `LC_ALL: C.UTF-8`；Windows 环境保持原样。
- `docs/构建与发布.md`：两个 Ubuntu 发布环境统一使用固定 CMake 3.30.9，构建／打包示例明确 `export LC_ALL=C.UTF-8`，说明中文文件名要求。主代理在两目标环境复验最终包。
- YAML 解析、Ubuntu locale 值及 Windows 未新增该环境项的检查通过。
- Wayland 新环境使用 Xvfb 上的嵌套 Weston 并提供输入 seat。Ubuntu 22.04 除鼠标熵之外的 15 项 GUI 测试全部通过，包含此前因缺少 seat 失败的剪贴板。Wayland 不支持 `QTest::mouseMove` 的指针 warp，鼠标熵改用独立的真实外部输入验证，结果仍待主代理报告；未修改产品 QtTest、删除断言或把 headless 限制视为产品缺陷。


## 第六轮：最终 DEB 清单与冒烟临时路径

主代理在两个 Ubuntu 环境使用 UTF-8 locale 与 CMake 3.30.9 生成最终 DEB。检查子代理独立通过 `ar`／`tar` 读取两个包的真实 data 归档，逐项确认 XCB、Wayland generic／EGL、离屏四种平台插件，IBus、Compose、XdgDesktopPortal 三个桌面插件，Wayland xdg-shell 及 `qtbase_zh_CN.qm` 全部存在。前述中文路径与插件缺失打包问题的目标检查已经通过；干净环境的实际安装、启动和卸载仍由主代理验证。

`apps/puttygen/packaging/smoke_package.py::launch` 原先在 Linux 调用环境缺少 `XDG_RUNTIME_DIR` 时可能让 Qt 隐式使用系统临时目录。现在仅缺省时创建本次冒烟工作目录的 `runtime/`，设置权限 0700 并导出；已有 Wayland 运行时目录保持原样，避免破坏合成器 socket。使用替代进程分别检查缺省路径／权限与既有 Wayland 目录保留，两分支均通过；Python AST 检查通过。该工具不进入 DEB，修复无需重包。


## 第七轮：最终 Ubuntu 发布与质量门收束

本轮不重复运行已经完成的测试；检查子代理直接读取目标构建日志、CMake 工具链记录、六份冒烟 JSON 与动态库结果，并重新计算两个最终 DEB 的 SHA256。以下结果取代前轮相应“待复验”状态，历史失败日志仍保留。

### 最终发布构建与测试

| 目标 | 工具链 | 最终 Release CTest | 实际 CPack |
| --- | --- | --- | --- |
| Ubuntu 22.04 x64 | Qt 6.8.3、GCC 11.4.0、CMake 3.30.9、UTF-8 locale | 3/3 通过，11.34 秒；核心、固定上游 CLI 互操作、GUI | DEB 与 SHA256 均生成 |
| Ubuntu 24.04 x64 | Qt 6.8.3、GCC 13.3.0、CMake 3.30.9、UTF-8 locale | 3/3 通过，12.08 秒；核心、固定上游 CLI 互操作、GUI | DEB 与 SHA256 均生成 |

证据为 `.tmp/platform/{22.04,24.04}/repackage3-verify.log`、`.tmp/build/ubuntu{22.04,24.04}/CMakeCache.txt` 及其 `Testing/Temporary/LastTest.log`。两个 Release 的 GUI 均为 QtTest 16 passed、0 failed、0 skipped；这是真实目标版本证据，与此前本机 Qt 6.12.0 检查分开。

### 无 Qt SDK 的安装包验证

主代理使用各自干净的官方 Ubuntu 环境执行最终 DEB 安装、依赖检查、桌面入口校验、程序启动与卸载。每个发行版分别完成 offscreen、XCB、Wayland 三轮，合计六轮全部成功。每份 `结果.json` 仅在程序保持运行、卸载和受跟踪文件残留检查全部完成后写出成功。检查子代理逐一读取六份 JSON，确认其包 SHA、平台和成功结果匹配，另读取全部 `动态库.txt` 确认主程序没有 `not found`。

- 结果目录：`.tmp/package-smoke/puttygen-gui-0.1.0-ubuntu{22.04,24.04}-amd64/{offscreen,xcb,wayland}/`。
- 安装／卸载日志：`.tmp/platform/{22.04,24.04}/clean-verify.log`。`desktop-file-validate` 只有 Security 分类补充建议，没有错误；该提示不阻止桌面入口有效性或程序运行。
- Ubuntu 22.04 最终包 SHA256：`cc6f03b45d76f98cd0b6574832a97dc96d95d0cafec5784354e835a4844697e6`。
- Ubuntu 24.04 最终包 SHA256：`3c3f63de831d80a9f8f7e19ea6e77f00895a91b8b37a42d4021306dfdaeae8f3`。

检查子代理对两个实际 DEB 重新计算 SHA256，与六份 JSON 和以上数值均一致。该结果证明两种 Ubuntu 的发布物不依赖构建机 Qt SDK 即可安装并在三种插件配置下启动，不能延伸为全部桌面功能的人工验收通过。

### 图形后端与 Windows 辅助证据

- `.tmp/platform/{22.04,24.04}/gui-xcb.txt`：各自 Qt 6.8.3、XCB 下完整 QtTest 16 项通过，含鼠标熵、剪贴板、文件保存与覆盖及错误恢复。
- `.tmp/platform/22.04/gui-wayland-nested.txt`：带输入 seat 的嵌套 Weston 下，排除鼠标熵后的 15 项通过，包含实际剪贴板回读。这里是明确选取的 15 项测试，不能写为完整 16 项通过。Wayland 指针 warp 与真实外部输入的鼠标熵验证仍待结果，之前无 seat 环境的失败日志没有删除或隐藏。
- `.tmp/platform/24.04/windows-core-migrated.log`：迁移后的 Windows x64 核心与测试程序使用 MinGW 交叉编译、链接成功，产出 `.exe`。没有运行该程序，没有编译或运行 Windows Qt GUI，因此不能替代原生 MSVC、Windows ZIP 或 Windows 10／11 验证。

### 当前整体结论与未完成项

1. 产品实施、套件组件目录、全部 G01–G15 的代码入口与自动化功能覆盖、脚本语法及本机／目标 Ubuntu 的编译和核心互操作检查已完成。审查发现的参数校验、注释时序、翻译、部署插件、UTF-8 打包及冒烟临时目录问题均已修复，没有剩余已知未修复的产品代码缺陷。
2. AC3–AC9 的相应实现及已有自动化检查、AC11 的目录与可选构建已核实。AC1 的两 Ubuntu 发布安装与启动部分通过；AC2／AC10 的已测试功能与后台生命周期有证据，但仍保留原生桌面人工对照及 Wayland 实际熵验证边界。已有 Actions 配置不构成实际运行证据。
3. 完整跨平台验收门尚未全部通过：Windows 原生 MSVC 构建、Windows ZIP、Windows 10／11 运行和原版 Windows GUI 人工逐项比对尚未完成；Linux 真实桌面人工体验及 Wayland 实际鼠标熵仍待验证。不能据当前 Ubuntu 自动化结果宣称整个用户目标全部完成或关闭任务。
4. 核心 ASan／UBSan 的既有检测范围与 `detect_leaks=0` 限制保持；不新增 LeakSanitizer、完整 GUI sanitizer 或未执行平台的通过声明。


## 第八轮：Wayland 实际鼠标熵验证完成

检查子代理已直接核对 `.tmp/wayland-diagnosis/{22.04,24.04}/scan-success/input.log`、各自 `environment.txt`，查看两张 `generated.png`，并读取独立诊断报告 [wayland-input.md](wayland-input.md)。本轮只追加质量记录，没有修改产品代码、打包产物或既有 QtTest。

| 目标 | 实际 Wayland 输入链结果 | 证据 |
| --- | --- | --- |
| Ubuntu 22.04／GCC 11.4.0／Qt 6.8.3 | 外部 XTEST → Weston X11 后端 → Wayland → Qt 主窗，收集 254/254，随后 PG_OK、密钥存在、Ed25519 255 位及公开 SHA256 指纹 | `22.04/scan-success/input.log` 与 `generated.png` |
| Ubuntu 24.04／GCC 13.3.0／Qt 6.8.3 | 相同实际输入通路，收集 254/254，随后 PG_OK、密钥存在、Ed25519 255 位及独立公开 SHA256 指纹 | `24.04/scan-success/input.log` 与 `generated.png` |

表中证据路径均相对于 `.tmp/wayland-diagnosis/`。两个日志首行都确认 `PLATFORM "wayland" QT 6.8.3`，包含 `spontaneous true` 的原生输入事件及连续的 `PROGRESS 254 254`、`PROGRESS 1000 1000`、`RESULT 0 true 3 255`。截图显示实际成钥后的公钥、指纹和可用保存操作。两次独立驱动均退出 0；驱动仅观察现有产品窗口及信号，不直接填充熵或调用核心生成替代 GUI 流程。

诊断驱动的 Ubuntu 22.04 CMake 是 3.30.9，Ubuntu 24.04 是 3.28.3，后者只链接对应发行版已构建的正式静态库；此诊断工具版本不改变第七轮两份正式发布包均使用 CMake 3.30.9 的记录。

### 更新后的质量边界

- 解除此前“Wayland 实际鼠标熵待验证”：两个 Ubuntu 的实际 Wayland 输入、限速收集及后台成钥链路已验证成功。结合既有嵌套环境精选测试、XCB 完整测试、两平台 Release CTest，以及无 Qt SDK 的三后端安装包启动验证，本期 Linux 自动化与实际输入证据已具备。
- 不把独立输入驱动和嵌套精选测试拼写为“原 QtTest Wayland 16/16 通过”。原 `QTest::mouseMove` 的 Wayland 指针 warp 限制仍然存在，相应旧失败日志完整保留，既有测试没有删除、跳过或改报通过。
- 真实输入来自嵌套合成器的外部事件注入，验证范围包含原生事件链与产品实际处理；仍不能替代用户在完整 GNOME／KDE 桌面用硬件鼠标进行的人工体验验收。
- 剩余完整验收项目是 Windows 原生 MSVC／Qt GUI 构建、Windows ZIP 及 Windows 10／11 实际运行、固定原版 Windows GUI 的人工逐项比对，以及真实用户桌面人工操作。GitHub Actions 尚未实际运行，MinGW 核心交叉编译仍仅是辅助证据。没有剩余已知未修复的产品代码缺陷；本次代码审查收束，但整体跨平台任务继续保留上述未完成验收状态。

## 第九轮：Windows Actions 的 Python 标准流编码

用户提供了实际 Windows Actions 失败日志：Python 3.11.9 在 `tools/qt/build_qt.py::run` 输出“执行：”时，因重定向标准流采用 cp1252 而触发 `UnicodeEncodeError`。这取代此前“Actions 尚未实际运行”的状态，但不代表 Windows 产品构建或运行已通过。

- 修复入口在参数解析前将可重配的 `stdout`／`stderr` 设为 UTF-8；Windows job 同时设置 `PYTHONIOENCODING=utf-8`。没有设置全局 `PYTHONUTF8`，没有改变 MSVC 捕获输出的本机编码解码方式。
- `run` 继续让构建子进程直接写标准流，并保留 `check=True`。回归使用真实子进程输出非 UTF-8 字节，确认字节没有被捕获解码；失败子进程仍导致非零状态并保留 `CalledProcessError` 和原退出状态诊断。
- 审查子代理独立执行 `python -B -m unittest discover -s tools/qt/tests -v`，本机 Python 3.14.8 下 5/5 通过：中文帮助、中文路径错误、非法并行数、中文命令及原始子进程输出、失败诊断。各测试主动覆盖 `PYTHONIOENCODING=cp1252:strict`／`PYTHONUTF8=0`，临时文件只写项目 `.tmp/`。
- 两份 Python 文件的 Python 3.11 语法兼容解析通过；YAML 解析、两平台回归在 Qt 构建前执行、Windows 标准流配置、构建脚本变更使缓存键失效的检查通过；`git diff --check` 通过。项目没有配置 Python 专用 lint／静态类型检查工具，本轮没有把语法检查写成静态类型检查通过。
- 旧版同一中文日志的 cp1252 失败复现保存在 `.tmp/qt-encoding-before/旧版原始日志错误.txt`。本轮没有运行 Windows，本机回归不能替代修复后 Windows Actions 的 Qt 构建、测试和 ZIP 启动结果；也没有重复运行无关的 C／Qt 产品矩阵。

本轮审查未发现需要额外修改的代码问题。公共构建编码契约与构建指南已同步；Windows 修复后的原生流水线结果仍待重新运行。

## 第十轮：Ubuntu Actions 的可选 Clang 包发现

用户的实际 Ubuntu Actions 日志确认：Qt Tools 配置在 `FindWrapLibClang.cmake` 加载 runner 的 Clang 导出配置，因引用缺失的 `libclangBasic.a` 而失败。此前没有 Clang 的干净 Ubuntu 构建无法覆盖这一环境差异，不能将其与 Windows 标准流错误混为一谈。

### 修复与审查发现

- `tools/qt/build_qt.py` 将已有单模块配置命令抽为 `module_configure_command`，仅给 Qt Tools 增加 `CMAKE_DISABLE_FIND_PACKAGE_WrapLibClang=TRUE` 与 `FEATURE_clang`／`FEATURE_clangcpp=OFF`。Qt Base、Translations、Wayland 原有参数保持；Linguist 保留，版本锁和系统依赖未变。构建命令仍由 `run` 的 `check=True` 传播失败。
- 审查发现新增依赖回归实际调用 CMake／Ninja，但两个 CI job 的测试步骤仍先于固定工具安装。已将其移至工具安装之后、Qt 缓存恢复及构建之前，避免依赖 runner 预装工具；步骤名改为“检查 Qt 构建脚本”。缓存键继续包含构建脚本哈希。
- 构建指南、公共构建规范及其索引已同步包发现边界和测试顺序；两份 JSONL 的 11 项上下文路径均存在，任务继续保持 `in_progress`。

### 本轮验证证据

- 检查子代理独立执行轻量回归，Python 3.14.8 下 9/9 通过。其中三个配置测试实际执行 CMake 并使用引用缺失库的 `ClangConfig.cmake`：旧参数失败、只关闭功能仍失败、新参数成功且未进入损坏包；测试不只检查参数字符串。此前 5 项 cp1252 子进程回归继续通过。
- 三份 Python 文件均通过 Python 3.11 语法兼容解析；工作流 YAML、两平台“固定工具安装→测试→缓存→Qt”顺序及缓存键检查通过；`git diff --check` 通过。没有专用 Python lint／静态类型检查配置，不将语法检查记为静态类型检查通过。
- 实施代理在 Ubuntu 22.04 和 24.04 使用真实 Qt 6.8.3 Qt Tools 源码与各自既有 Qt SDK 验证。检查子代理直接读取两份 `result.json`、配置日志及缓存：旧参数和仅 feature 参数均退出 1 并进入损坏 Clang 包，新参数均退出 0 且不进入该包；缓存中 Clang／ClangCPP 关闭、Linguist 开启。
- 两系统的 `lrelease`、`lupdate`、`lconvert` 目标构建命令均成功，原生执行前两者报告 6.8.3，`lconvert -help` 正常。最终复验日志显示 Ninja 无需重建；首次采集误用不受支持的 `lconvert -version`，实施代理改为 `-help` 后复验通过，该误参不涉及产品脚本。

实际 Qt 验证证据位于 `.tmp/actions-ubuntu-failure/libclang-regression/{22.04,24.04}/` 的 `old.log`、`features-only.log`、`fixed.log`、`linguist-build.log` 和 `result.json`，驱动为同目录上层的 `verify.py`。本轮没有重编整套 Qt、重跑完整产品矩阵，也没有运行修复后的 GitHub runner；远端 Actions、Windows 原生产品与人工桌面验收仍按真实结果另行确认。


## 第十一轮：Windows 私钥文件权限与原生 OpenSSH 互操作

用户的 Windows MSVC Actions 真实结果为 CTest 2/3：核心往返及 GUI 通过，互操作在原生 OpenSSH 读取新导出的私钥时因继承 `BUILTIN\\Users` 权限被拒绝。本轮修复位于产品 Windows `f_open` 私钥写入分支，不在互操作脚本中修补 ACL 或跳过原生检查。

### 修复与审查

- 私钥新建使用当前进程 TokenUser 作为所有者及单条完整权限 ACE，并保护 DACL；覆盖旧文件在同一排他句柄上设置权限并读回核验，再转为 CRT 文件流，最后才截断。权限失败返回文件错误，旧内容不被截断。公钥、读取及 Linux 分支保留原行为。
- 逐项核对句柄→描述符→文件流的单次所有权移交、失败清理、Win32 到 errno 映射、文本／二进制／追加／读写模式及中文路径；Windows API 仅使用已记录的公开接口，适配库及测试库链接 `advapi32`。未发现需要进一步修改的产品逻辑问题。
- 新 Windows `core-private-acl` 测试验证实际磁盘 owner、受保护 DACL、单 ACE 和无继承，覆盖 PPK、OpenSSH 自动／新格式及 ssh.com 的新建与覆盖、权限失败保留内容、公钥行为及文件模式。审查补充新建路径权限失败断言：报告 `PG_ERR_IO`／`EACCES`，仅留下受保护的零字节文件；SID／ACL 测试缓冲改为 DWORD 存储以保证 API 对齐约定。
- 新增的权限失败 hook 仅由 `PG_TESTING` 编译。检查子代理使用 MinGW `nm` 核验生产 `libpg-core.a` 无 `pg_test_*` 定义，测试库包含权限失败 hook。互操作脚本保持未修改，原有读取、公钥、签名与证书验证继续启用。
- 构建指南及 Windows 私钥权限规范、规范索引和任务上下文已同步；本次产品修复不进入 vendor，不改变公共 `pg_core.h` ABI。

### 验证与边界

- 实施代理在既有 Ubuntu 24.04 离线构建镜像中完成 MinGW x64 Release 核心、测试库及两个测试 EXE 编译／链接，日志 `.tmp/windows-acl-cross.log`。检查子代理在补充断言及对齐修正后独立重编 `pg-private-acl-tests.exe` 成功，没有编译警告；这只是 Windows 辅助编译证据，没有执行 Windows 程序。
- 检查子代理读取 `.tmp/build/gui-dev/Testing/Temporary/LastTest.log` 确认本轮 Linux Debug CTest 3/3 通过：核心及互操作合计 8.05 秒、GUI 5.75 秒，共 13.80 秒；GUI QtTest 16 passed、0 failed、0 skipped。该开发构建使用本机 Qt 6.12，不代替 Windows 或发布 Qt 6.8.3 的运行证据。
- `git diff --check` 通过；C99 编译／链接检查通过。仓库没有独立 C lint 或静态类型检查配置，不将这些检查记为专用 lint／类型检查通过。
- 修复后的 Windows 原生 MSVC、权限测试、OpenSSH 互操作及 ZIP 仍须由 Actions 重新运行；不能将此次交叉编译记为原生 ACL／签名验证通过。任务继续保持 `in_progress`，完整桌面人工验收边界不变。


## 第十二轮：容器冒烟报告上传的权限边界

用户提供 Ubuntu 24.04 的 `upload-artifact` 实际失败日志：上传器递归扫描 `.tmp/package-smoke/**` 时进入容器生成的私有 `offscreen/runtime`，宿主 runner 因目录权限产生 `EACCES`。该日志定位的是上传步骤，不代表前置产品测试或安装冒烟失败；Node 的 `punycode` 弃用提示不是本次退出原因。

### 修复与独立审查

- 新收集器 `apps/puttygen/packaging/collect_smoke_reports.py` 仅枚举源目录第一层包名，定点读取 offscreen、XCB、Wayland、native 的 `结果.json`、`启动输出.txt`、`动态库.txt`，另收集根目录 `weston.log`。内容复制到宿主用户创建的独立 `.tmp/package-smoke-reports/`，不遍历运行目录、不继承容器所有者和权限，不复制配置、socket、解压包或私钥。
- Ubuntu 和 Windows 均在上传前通过 `if: always()` 执行收集，上传器仅扫描报告暂存目录。两平台原包、SHA256、CTest 日志路径及构建、测试、安装／卸载命令保留；原失败步骤继续非零，收集或上传均未配置 `continue-on-error`。
- 源缺失表示前置步骤尚未产出报告，允许空收集；GUI 失败时仍收集已有启动输出与合成器日志，不要求成功 JSON。已有报告读写失败继续非零退出并保留诊断。平台和报告符号链接不会被跟随，源或目标解析后越出项目 `.tmp/`、两目录相同或互相包含时，参数解析拒绝执行。
- 检查子代理未发现需要进一步修改的收集器或工作流问题；补充两项 CLI 边界回归，覆盖源和目标越界、相同目录及两个包含方向。构建指南与公共构建规范已同步真实报告清单、权限与退出状态契约。

### 验证与边界

- 检查子代理以本机普通用户 UID 1000 独立执行 `python3 -B -m unittest discover -s apps/puttygen/packaging/tests -v`，8/8 通过、0 skipped，包含不可访问运行目录、报告读写失败、部分失败日志、无源目录、包／平台隔离及参数错误。
- 另外针对项目已有真实冒烟树独立执行收集。`.tmp/package-smoke/puttygen-gui-0.1.0-ubuntu24.04-amd64/offscreen/runtime` 属于 UID 65534、权限 `0700`，普通审查进程直接遍历被拒绝；收集器成功复制 18 份报告到当前用户新建暂存目录，全部可递归读取且逐字节与原报告一致。前后运行目录 UID 与权限均未变。证据 `.tmp/smoke-reports-review-verification.json`，与实施代理既有 `.tmp/smoke-reports-permissions-regression.json` 结果一致。
- 两份新增 Python 文件通过 Python 3.11 语法兼容解析；YAML 解析及两平台“收集→上传”、`always()`、上传路径和失败保留检查通过。仓库没有独立 Python lint／静态类型检查配置，不将语法检查记为专用 lint 或类型检查通过。`git diff --check` 通过。
- 本轮仅检查报告收集与 CI 接线，没有重编 Qt、重跑无关核心矩阵或执行修改后的 GitHub Actions。远端 runner 的最终上传与新一轮 Windows 原生结果仍待实际运行；本机回归不替代上述远端状态，也不改变整体桌面人工验收边界。任务保持 `in_progress`。

## 第十三轮：Arch Linux 原生包与独立 Actions 产物

用户追加在 Actions 发布 Arch Linux 包，本轮按新的 PRD 范围加入 x86_64 滚动系统包；没有引入 AUR、托管 pacman 仓库或 GitHub Release 发布，也没有变更 PuTTYgen 功能范围。

### 实现与独立审查

- 新 Arch job 在官方 `archlinux:base-devel` 容器安装构建依赖，随后以与宿主 runner 相同 UID 的普通账户运行 `build_arch.py`／makepkg。源码归档白名单、版本和 PKGBUILD 都来自同一 HEAD，源码 SHA256 交由 makepkg 实际校验；产物路径来自 `--packagelist`，要求唯一应用包，不使用包通配符挑选历史文件。
- CMake 的 `PUTTYGEN_ARCH_PACKAGING` 分支仅允许 Arch x86_64、Release 和 `/usr`，与锁定 Qt 的便携部署互斥。直接安装 ELF、desktop、SVG、文档及许可，由 pacman 管理系统 Qt、翻译、运行库和中文字体；不复制私有 Qt、`qt.conf`、`/opt` 包装器或 RPATH。Windows／Ubuntu 原有 job、Qt 版本检查、部署与冒烟命令未改动。
- 独立 `archlinux:base` 容器通过 `pacman -U` 安装包声明的运行依赖。验证实际包元数据、安装文件、ELF、ldd、无 RPATH 与 desktop 后，分别启动 offscreen、XCB、Wayland；启动错误仍向 job 传播，已安装应用在 `finally` 中卸载，并检查残留。容器安装操作没有在宿主执行。
- Arch 收集和上传步骤均保留 `always()`；上传包目录及独立报告目录，不递归上传含私有 runtime 的源树。构建指南、组件说明、PRD、设计及 Arch 包规范已经同步。检查子代理未发现需要自修的产品代码或接线缺陷。

### 验证证据

- 实施代理在宿主 Arch 以普通用户完成真实 makepkg、Release 构建与 CTest 3/3，总计 10.31 秒（核心 2.25 秒、互操作 2.29 秒、GUI 5.77 秒）；独立审查直接读取 `.tmp/arch-validation/build.log`、包内 `.PKGINFO`、实际文件清单和对应源码归档。测试环境为 Qt 6.12.0-2、GCC 16.2.1、CMake 4.4.4、Python 3.14.8，不是 Windows／Ubuntu 的 Qt 6.8.3 证据。
- 真实包为 `puttygen-gui-0.1.0-1-x86_64.pkg.tar.zst`，SHA256 `92c77a183551239017ca40314cb120117adfaf79b6e930fbca2a3d5fd1c124a4`，版本、架构和七个声明的运行依赖均经独立核验。包 SHA256 旁文件、源码 SHA256、临时测试仓库提交与构建记录一致；源码不含 `.git`、`.trellis`、`.agents` 或 `.tmp`。包内安装树没有测试程序、源码构建目录或 Qt 副本。
- 实施代理先在新建 `base-devel` 容器验证三后端，再使用实际 workflow 的最小 `archlinux:base` 镜像验证三后端安装／启动／卸载，分别记录在 `smoke.log`、`smoke-base.log`。检查子代理直接核对后者三份成功记录及日志中由 `pacman -U` 安装 `qt6-base 6.12.0-3` 的事务，未把预装构建 SDK 当运行依赖检查。
- 检查子代理以普通用户将真实 Arch 冒烟树中的十份报告复制到 `.tmp/arch-review-reports/`，全部可遍历和读取，三后端结果的包 SHA256 一致；运行时、账户配置与 socket 未被复制，原 `0700` 目录保持。此检查继续覆盖此前 Ubuntu 上传权限故障的边界。
- 检查子代理独立执行打包轻量回归，12/12 通过，包括新增 Arch 元数据、缺少安装文件、私有部署与源码杂项拒绝，以及既有报告权限／失败／路径回归。`bash -n`、两份 Python 的 Python 3.11 语法兼容解析、YAML 与 Arch 的 `always()`／上传路径接线、`git diff --check` 均通过。仓库没有独立 Python lint／静态类型检查配置，未将语法检查写为专用 lint 或类型检查通过。

### 本轮证据边界

- 未提交代码的完整构建使用项目 `.tmp/arch-validation/source` 临时 Git 夹具，提交 `b17aaf2388f7ea4586740793d200b81297e7b107`；没有用尚不含 Arch 实现的原仓库 HEAD 冒充新源码。构建使用的 `Packaging.cmake` 与 PKGBUILD 和最终文件逐字节一致。首次完整构建后，编排及冒烟脚本补充环境版本记录与容器验证细节，归档内这些脚本不是最终版本，不能将旧源码归档表述为最终提交的完整发布物。
- 最终 `smoke_arch.py`／`.sh` 已在上述两个干净容器实际执行。最终 `build_arch.py` 的受控编排检查实际执行 Git 归档、同提交配方读取、`makepkg --packagelist`、pacman 版本查询和产物／日志复制，但明确复用已验证的包，没有再次完整编译；证据 `final-orchestration.log`。构建容器前置依赖、普通用户 `makepkg --verifysource`／`--packagelist` 另有 `build-container-preflight.log`。检查子代理核对 `.tmp/arch-validation/最终文件SHA256.json` 的七个最终关键文件均与工作区一致。
- 新增 job 尚未作为最终提交在远端 Actions 执行，最终 artifact 上传结果与真实 Arch 桌面人工体验仍须分别确认；不把宿主构建、容器预检与受控编排拼写为完整远端流水线通过。本轮没有重复编译固定 Qt 或运行无关平台矩阵，整体任务保持 `in_progress`。
