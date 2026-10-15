# Qt 桌面界面开发规范

本目录用于 PuTTYgen 桌面工程，文档及新增注释使用中文。

## 开发前检查

阅读 [directory-structure.md](directory-structure.md) 和 [state-management.md](state-management.md)，再读取当前任务 PRD、设计和研究。其余文件尚为初始化模板，不套用 Web 或数据库约束。

终端前端另读 [终端跨层契约](../backend/suite-terminal.md)；文本和方向键须共用有序队列，帧消费者必须遵守生产者的总像素限额。

## 质量检查

按 [state-management.md](state-management.md) 的接口、错误和测试契约检查。运行当前工程 CTest，并将本机、目标系统构建、图形后端测试和人工桌面验收分别记录。
