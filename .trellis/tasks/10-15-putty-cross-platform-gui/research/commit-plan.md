# 已确认的提交计划与执行记录

当前仓库没有已有提交，工作分支为 `codex/puttygen-cross-platform-gui`。仅暂存本任务实际新增或修改的产品、规范及任务资料，不将整个未跟踪工作区一次性加入。用户已于 2026-10-15 确认执行；本地提交进行中，尚未推送。

按以下顺序创建三个提交：

1. `chore: 引入 PuTTY 0.85 密钥核心及来源材料`
   - `vendor/putty/`、`vendor/patches/`、`vendor/来源.md`
   - `cmake/UpstreamPuTTY.cmake`
   - `LICENSE`、`THIRD_PARTY_NOTICES.md`
2. `feat: 实现 PuTTYgen Windows/Linux 跨平台 GUI`
   - `CMakeLists.txt`、`README.md`、`.gitignore`
   - `apps/puttygen/`（组件实现、资源、测试和发布配置）
   - `tools/`、`.github/workflows/puttygen.yml`、`docs/构建与发布.md`
3. `docs: 记录 PuTTYgen 设计规范与验证结果`
   - `.trellis/tasks/10-15-putty-cross-platform-gui/`
   - `.trellis/spec/backend/index.md`、`directory-structure.md`、`error-handling.md`
   - `.trellis/spec/frontend/index.md`、`directory-structure.md`、`state-management.md`

尚未识别为本次修改、不纳入以上提交的初始文件：`.agents/`、`.claude/`、`.codex/`、`.opencode/`、`.gitattributes`、`AGENTS.md`，以及除上列之外的其他 `.trellis/` 文件。`.tmp/` 始终排除，不提交测试私钥、构建产物、下载缓存和运行日志；平台报告以中文研究资料记载必要结果。

本机未配置 Git 作者；本次提交使用明确的自动化身份 `Codex <codex@localhost>`。用户可以一次调整文件分组、提交文字或作者。

本地提交之后，只有在用户一并确认推送并具备有效 GitHub 认证时，才上传到现有 `origin` 的同名工作分支并触发 Actions。远端为 `https://github.com/lwl0820/putty.git`。当前执行环境无法连接 Git 凭据服务，因此尚未运行 Actions，不能声称 Windows MSVC/ZIP 已通过。

Windows 和人工桌面验收仍未完成，任务保持 `in_progress`，不做完成归档或会话收尾提交。

## 本地执行记录

- 第一批 `f73b0eb`：PuTTY 上游源码、最小补丁、共享构建与许可。
- 第二批 `544f55f`：跨平台 GUI、测试、发布包与 CI；上游自身忽略规则排除了 12 份归档自带手册，已在此批显式保留，全部官方归档文件均纳入版本控制。
- 第三批包含本记录及最终质量资料；不在文件中记录其自身哈希。

三批范围已经用户一次确认。Windows Actions 与推送状态以实际 Git 结果为准，不把本地提交当作远端 CI 已运行。
