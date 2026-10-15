# PuTTYgen 单产品整理独立审查

## 审查结论

已读取检查上下文、需求、设计、实施计划及前后端质量规范，完成全范围审查。未发现需要修复的产品、构建、发布、测试或规范问题；本次审查未改动产品代码。

## 范围与证据

- 逐文件对照旧 `apps/puttygen/` 与根目录新位置：核心、界面、资源内容及测试逻辑保持一致；差异限于资源许可证路径、打包脚本根定位、Arch 配方和源码白名单等迁移接线。
- 根 CMake 直接配置 PuTTYgen，保留 GUI 开关和 Qt 无关核心模式。实际 Ninja 目标仅包含 PuTTYgen、自有密钥库及测试和上游参考 puttygen，不包含已删除产品或共享终端运行时。
- 已核对 qrc 每个文件真实存在，许可证引用正确。部署逻辑保留 Windows 本地运行库和 Qt 插件，以及 Ubuntu 的独立 Qt、中文标准控件翻译与 Wayland 插件；Arch 继续使用系统 Qt。
- 已审查 Windows、两个 Ubuntu 版本和 Arch 工作流的脚本路径、失败状态、报告暂存与产物上传。工作流 YAML 解析通过；其它产品的专用工作流已删除。
- 已检查 Arch 脚本的 `parents` 层数、版本读取、同一 HEAD 的源码与配方、产品源码白名单，以及根构建树中的 desktop 路径。正式 Arch 包必须在提交之后由对应 HEAD 验证，当前不以旧提交打包作为迁移通过证据。
- 对照官方归档，SHA256 为 `13fd4db2936d03b73812a7bcc2a658e4dd29cc776a56c3670a7fc6f1a0ee8af8`；第三方全树仅 `import.c`、`unix/noise.c` 两项已记录补丁差异，无额外文件。0003—0011 对应第三方内容均已恢复。
- README、构建发布文档、第三方来源与前后端规范均反映单产品根目录结构。有效产品树、工作流和当前规范中无旧组件路径；系统图标目录 `icons/hicolor/scalable/apps/` 属于正确安装路径。

## 验证

本仓库未配置独立 Python 类型检查器或统一 lint 命令；以下为实际执行或核验的质量门，不将缺少的工具写成已通过。

| 检查 | 结果与证据 |
| --- | --- |
| 静态检查 | `git diff --check`、全部自有 Python AST 解析、相关 shell/PKGBUILD 的 `bash -n`、JSON 与 QRC 解析通过 |
| C/C++ 编译与类型检查 | 新布局 Release GUI 与独立核心均完整编译通过，两个构建日志中无编译警告 |
| 完整 CTest | 3/3 通过，`.tmp/puttygen-only-ctest.log` |
| 无 Qt 核心 CTest | 2/2 通过，`.tmp/puttygen-only-core-ctest.log` |
| Qt 工具单测 | 9/9 通过，`.tmp/puttygen-only-qt-tests.log` |
| 打包脚本单测 | 15/15 通过，`.tmp/puttygen-only-packaging-tests.log` |
| 安装及开发归档 | 安装与 TGZ 中均恰好 6 个文件：主程序、两份文档及三份许可；无其它产品、测试或开发源码；日志 `.tmp/puttygen-only-install.log`、`.tmp/puttygen-only-cpack.log` |

独立审查复核了实现代理的上述构建和测试日志，没有重复运行已经完成的全量矩阵。开发归档实际位于 `.tmp/puttygen-only-packages/`；旧容器生成的不可写目录未修改权限。

## 验证边界

本次证据为当前 Linux 本机的新布局构建、逻辑测试与开发归档。Windows MSVC、两个 Ubuntu 发行版正式 DEB、Arch 对应 HEAD 正式包及其安装冒烟仍由目标平台 CI 产生证据；Windows 10/11 和真实 X11/Wayland 桌面交互由人工验收。已审查的 CI 配置和过去布局的运行结果均不替代本次新布局的平台执行。
