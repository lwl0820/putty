# Windows 受控存储接线边界

日期：2026-10-15。用户已选择仓库 Windows CI，目标 Windows 桌面交用户人工验收。

## 最小行为差距与文件责任

已有事件／HANDLE／hive 探针的实际成功不能证明正式 settings、seed 和 PRNG 调用。新增 `tools/suite/windows/storage/` 由独立实施代理负责，仅运行受控存储变体；主会话负责公共测试 CMake、`run_ctest.py`、契约回归、工作流和本记录。原版 CLI 与既有运行时存储路径仍不执行。

公共 CMake 注册唯一 `runtime-windows-storage`、`runtime;windows-storage` 标签及独立 map。CI 新增存储运行器契约回归、`--require-storage` 和唯一摘要 `private-storage.json` 白名单；不得递归上传私有运行根或 hive。结果门在执行前核对唯一注册，在执行后拒绝缺失／重复／跳过／失败，且须同时强制 hive 隔离成功。旧 JUnit 先删除。

## 必要上游句柄修复

`vendor/putty/windows/storage.c::host_ca_delete` 打开父键后缺少关闭。主会话在删除及释放临时字符串之后补唯一 `close_regkey(rkey)`，保留缺父键提前返回和既有返回语义；差异为 `0009-host-ca-delete-handle.patch` 并同步来源。反向应用检查退出0。存储宿主将检查真实删除、重复删除和父键缺失，实际 Windows 结果随后补记，不能以源码推定无句柄泄漏。

## 本机接线验证与原生边界

主会话在 Arch／Python3.14 执行 Windows 公共契约回归 **15/15，0 skip**；新增用例验证存储结果须配合 hive、必须唯一实际成功，并保留生产回退与旧运行时未验标签。这些是脚本边界测试，不是 Win32 执行证据。新存储宿主尚待独立源码审查及真实 MSVC CI，现有 runtime-lifecycle 的77跳过不移除。
