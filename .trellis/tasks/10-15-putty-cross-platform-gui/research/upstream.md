# PuTTY 上游与操作逻辑初步调研

调研日期：2026-10-15。当前仅进行任务规划；官方 0.85 源码归档已下载到项目 `.tmp/` 用于只读研究，尚未导入产品目录、安装依赖或确定技术栈。

## 本地项目事实

- `git status --short` 显示本地仅有未跟踪的初始化配置，无产品代码。
- `get_context.py` 显示没有历史提交，也没有已完成的开发记录。
- `.trellis/spec/frontend/index.md` 和 `.trellis/spec/backend/index.md` 的各项规范均为待填充模板；不能假定使用 Web 前端、数据库或某种 GUI 框架。
- 保留已有 `00-bootstrap-guidelines` 任务；本次独立创建移植任务，不修改其状态。

## 上游来源

| 来源 | 已核实信息 | 对规划的影响 |
| --- | --- | --- |
| [用户指定的 GitHub 镜像](https://github.com/github/putty) | 用于客户端测试的镜像，2023-10-20 归档；根目录有 `config.c`、`dialog.c`、`windows/`、`unix/` 等源码。 | 可作为用户指定的项目参考；是否直接使用其代码需评估与官方版本的差异。 |
| [镜像 README](https://github.com/github/putty#readme) | 已提供 Windows 和 Unix 构建方式，Unix 图形程序依赖 GTK。 | 只能据此确认部分程序具有 Unix GUI，不能推断整个套件的 GUI 已跨平台。 |
| [官方主页](https://www.chiark.greenend.org.uk/~sgtatham/putty/) | PuTTY 支持 Windows 和 Unix，包含终端模拟器。 | 可对照原版既有行为，避免无意改写终端操作。 |
| [官方发布页](https://www.chiark.greenend.org.uk/~sgtatham/putty/latest.html) | 调研时稳定版为 0.85，官方源码仓库为 `https://git.tartarus.org/simon/putty.git`；套件含多个独立工具。 | 建议评估官方稳定版作为源码基线，该建议尚未确认；需固定实际采用的版本及提交。 |

## 原版操作参考

### PuTTY 主客户端

根据 [0.85 入门说明](https://the.earth.li/~sgtatham/putty/0.85/htmldoc/Chapter2.html)，原版基本操作是：

1. 打开配置对话框，填写主机、连接类型和端口。
2. 点击打开，首次 SSH 连接会出现主机密钥信任选择。
3. 进入终端窗口完成认证和交互。
4. 退出后按照设置决定是否关闭窗口。

根据 [0.85 配置说明](https://the.earth.li/~sgtatham/putty/0.85/htmldoc/Chapter4.html)，后续需逐项梳理的行为包括：

- 会话设置的保存、加载、删除和双击连接。
- 默认设置与每个已保存会话独立的配置语义。
- 会话运行时修改部分设置。
- 终端、键盘、窗口、字体、字符编码、鼠标选择和剪贴板相关选项。
- 代理、认证、主机密钥、隧道与串口等配置项。

这些是原版事实与对照候选，不代表首版已承诺完整覆盖。

### PuTTYgen 的平台差异

用户指出 Linux 的 PuTTYgen 只有命令行界面。经核对，[0.85 官方手册首页](https://the.earth.li/~sgtatham/putty/0.85/htmldoc/index.html) 明确说明手册主要描述 Windows 版本，并将 Unix 的 `puttygen` 和 `pageant` 列为命令行工具。因此，PuTTY 主客户端支持 Unix GTK GUI 与 PuTTYgen 具有跨平台 GUI 是两个不同事实，之前对套件跨平台程度的概括不完整。

根据 [0.85 PuTTYgen 使用说明](https://the.earth.li/~sgtatham/putty/0.85/htmldoc/Chapter8.html)，Windows GUI 的操作对照候选包括：选择密钥类型与参数、生成或加载密钥、查看指纹和公钥、修改注释与口令、保存公私钥，以及通过转换菜单导入或导出其他密钥格式。

用户已明确首版先完成 PuTTYgen 的跨平台 GUI，并完整覆盖 Windows 原版 GUI 功能。其他套件工具不纳入首版；主客户端的操作参考仅作为之前调研资料保留，不属于本次实施范围。最终范围、系统和语言以 `prd.md` 为准。

### 可复用代码的初步证据

[用户指定镜像的 Recipe](https://github.com/github/putty/blob/master/Recipe) 对程序类型与依赖的定义提供了以下证据：

- Windows `puttygen` 目标定义为 `[G]` GUI 程序，入口依赖 `winpgen`。
- Unix `puttygen` 目标定义为 `[U]` 命令行程序，入口依赖 `cmdgen`。
- 两个目标均依赖 `KEYGEN`、密钥读写和外部格式导入相关模块；这是复用核心逻辑、替换平台界面的候选依据，尚不能认定其接口可以直接作为稳定库使用。
- `cmdgen.c` 亦可在镜像中查阅；具体模块边界须按最终采用的源码基线核对。

官方 Git 浏览页面未能访问后，改为下载官方 0.85 发布归档并核对源码。归档为 `.tmp/puttygen-research/putty-0.85.tar.gz`，SHA256 为 `13fd4db2936d03b73812a7bcc2a658e4dd29cc776a56c3670a7fc6f1a0ee8af8`，与[官方校验清单](https://the.earth.li/~sgtatham/putty/0.85/sha256sums)一致。仅将需要研究的普通文件读取到 `.tmp/puttygen-research/putty-0.85/`，未执行源码中的程序或构建脚本。

0.85 的 `windows/CMakeLists.txt:137` 定义 GUI 目标，链接 `keygen`、`crypto`、`utils` 与 Windows GUI 辅助模块；`unix/CMakeLists.txt:90` 定义共享对象，`unix/CMakeLists.txt:99` 使用 `cmdgen.c` 构建 CLI 并链接密钥与密码处理模块。可复用核心的证据得到补充，但抽取独立接口仍需设计。完整 GUI 核对结果见 `gui-parity.md`。

## 技术研究待办

- 固定上游源码并研究密钥生成、密钥文件读写、格式转换与 Windows GUI 的边界，确定可以复用的部分。
- 比较直接复用上游核心与封装上游命令行后端的 GUI 方案，依据行为兼容性、口令传递、平台集成与维护成本作出技术选择。
- 按 Windows 原版 PuTTYgen 梳理普通与高级功能，确认首版范围后建立行为和文件兼容性验收矩阵。
- 选定系统支持矩阵后，制定 Windows/Linux 的构建、打包、自动化验证与人工验收方式。
- 在源码基线与交付范围明确后，再评估许可证保留、第三方依赖和发布所需材料。
