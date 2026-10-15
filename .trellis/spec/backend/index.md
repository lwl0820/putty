# 套件核心开发规范

本目录用于 PuTTY 套件工程，文档及新增注释使用中文。

## 开发前检查

阅读 [directory-structure.md](directory-structure.md) 和 [error-handling.md](error-handling.md)，再读取当前任务 PRD、设计和研究。其余文件尚为初始化模板，不套用 Web 或数据库约束。

修改公共构建脚本或 CI 时，还需阅读 [build-tools.md](build-tools.md) 的标准流编码与可选依赖发现契约。

修改 `cmake/Suite*`、组件选择及共享运行时集成时，还需阅读 [suite-build.md](suite-build.md) 的配置、PRNG 符号与多配置安装契约。

修改公共运行时或产品 `pr_module` 时，还需阅读 [suite-runtime.md](suite-runtime.md) 的线程、容量、请求、通知代次与两阶段关闭契约。

修改终端模块、CPU 渲染、帧消费或文本／按键输入时，还需阅读 [suite-terminal.md](suite-terminal.md)。该模块仍处于跨平台技术纵切验证阶段，不能据此视为完整 PuTTY 产品。

修改私钥文件保存或 Windows 平台适配时，还需阅读 [private-file-permissions.md](private-file-permissions.md)。

修改 Windows 套件夹具、原生依赖准备及认证证据门时，还需阅读 [suite-windows-fixtures.md](suite-windows-fixtures.md)。

修改 Arch Linux 原生包、系统 Qt 安装或对应 Actions 时，还需阅读 [arch-package.md](arch-package.md)。

## 质量检查

按 [error-handling.md](error-handling.md) 的接口、错误和测试契约检查。运行当前工程 CTest，并将本机、目标系统构建、图形后端测试和人工桌面验收分别记录。
