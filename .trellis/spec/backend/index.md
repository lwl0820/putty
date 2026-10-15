# PuTTYgen 核心开发规范

本项目只交付 PuTTYgen，文档及新增注释使用中文。

## 开发前检查

阅读 [directory-structure.md](directory-structure.md) 和 [error-handling.md](error-handling.md)，再读取当前任务 PRD、设计和研究。其余初始化模板不套用 Web 或数据库约束。

修改构建工具或 CI 时阅读 [build-tools.md](build-tools.md)。修改私钥保存或 Windows 适配时阅读 [private-file-permissions.md](private-file-permissions.md)。修改 Arch 包时阅读 [arch-package.md](arch-package.md)。

## 质量检查

运行新布局的完整 CTest、工具与打包脚本回归，并验证 `PG_BUILD_GUI=OFF` 独立核心构建。分别记录本机、目标平台 CI、图形后端和人工桌面验收结果。目录变更同时核对资源、安装、源码归档、CI 和文档引用。
