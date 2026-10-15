# Windows PPK 终端退出种子与 hive 收尾修复

日期：2026-10-15。此检查点只修复终端退出保存和受控宿主的证据；Windows 原生最终结果仍须由提交后的仓库 CI 提供。

## 已有失败证据

Windows run `38678572546`、job `116237674882` 的实际日志保存于项目 `.tmp/suite-validation/windows-ppk-sixth-ci.log`。`terminal-windows-ssh-kdf-render` 执行到“终端摘要完成”及“私有 hive 核验与清理”后，以代码 13 失败。不能把前面的阶段完成当作整个纵切通过；当时的日志没有分别打印存储计数、关闭结果及清理结果，也不能仅凭 13 断言所有清理调用失败。

## 源码确定的失败条件

- `apps/putty/tests/windows/hive_bootstrap.c` 保持五个核心各读打开一次、写打开两次的强门，即合计读打开 5、写打开 10。
- `vendor/putty/sshrand.c` 的 `random_create` 在 PRNG 活动期间调用 `random_save_seed`，完成启动写入。
- 同文件的 `random_unref` 先将最后一引用递减至零，再调用 `random_clear`；后者调用的 `random_save_seed` 受 `random_active` 保护，此时不会写出。不能把 `random_clear` 中存在保存调用误读成最后一次 `unref` 一定保存。
- 原版 `vendor/putty/windows/window.c` 的 `cleanup_exit` 和 `vendor/putty/unix/putty.c` 的 `cleanup_exit` 在退出前显式调用 `random_save_seed`。
- 自有 `pt_terminal.c` 原来的 `destroy` 只有 `random_unref`，因此退出保存缺失，写打开 10 的门在旧实现中必然失败。原日志未拆分其它字段，仍须保留可能还有其它失败条件的边界。

## 修改

经根协调者批准扩展文件所有权后，在真实 `pt_terminal.c` 的 `destroy` 中、释放最后 PRNG 引用之前显式调用 `random_save_seed`。调用仍位于唯一核心线程，先释放 Backend、Terminal、绘制器、确认请求及 Conf，再保存并释放 PRNG。正常完成、拒绝主机密钥、取消提示、KDF／GSS 在途关闭都共用该销毁路径；不改上游 PRNG，不在测试 wrapper 伪造补写，不降低十次写打开门。

`hive_bootstrap.c` 在失败时只输出固定字段的生命周期计数、种子读写／打开／字节计数，以及分别保存的核验、关闭、清理错误码和清理是否执行。无路径、SID、注册表内容、随机字节、口令或任意上游日志。首个失败状态仍决定退出码，核验失败后仍尝试关闭和准确清理。成功摘要增加真实打开与字节计数，运行器要求这些字段存在且满足原生强门。

`run_ssh_test.py` 的诊断白名单只接收完整的固定中文字段和有界十进制数字；附加路径／口令等内容会令整行被拒绝。既有 16 KiB、128 行及最多 12 条诊断上限保持。

## 本机实际验证

1. `python -B -m unittest discover -s apps/putty/tests/windows -p test_ssh_runner.py -v`：12 项通过，零跳过。新增数值诊断允许及私有后缀拒绝、缺失实际打开／字节字段和低于门槛计数拒绝；既有 MAP、PPK、心跳、输入、平台与认证计数门继续通过。
2. 重建 `.tmp/build/suite-integrated-all` 的 `putty-terminal-tests`。本机 Linux／Qt6.12／Release 的真实 `terminal-ssh-kdf-render`、`terminal-gss-render-close` 2/2 通过，分别为 21.63 秒及 14.66 秒，零跳过。
3. 重建 `.tmp/build/suite-terminal/asan` 的同一目标；C、C++ 均启用 `-fsanitize=address,undefined -fno-omit-frame-pointer`。上述真实用例 2/2 通过，分别为 23.87 秒及 15.32 秒，零跳过；未禁用默认 LSan，未设置泄漏抑制。
4. 临时差分探针 `.tmp/suite-validation/ppk-hive-finish/verify_seed_lifecycle.py` 对实际生产终端静态库、真实 `sshrand.c`、真实隔离存储和渲染宿主进行链接器包装，只记录每次最终 `unref` 前实际 `write_random_seed` 调用数，并仍委托正式写入。旧变体仅删除本次显式保存：实际观测 1 次并在强断言终止；新变体实际观测 2 次并退出 0。报告为同目录 `seed-lifecycle.json`，`native_windows_verified` 明确为 false。此探针不修改正式构建目录产物，旧库、对象、日志都保存在该项目临时目录。
5. 所属代码 `git diff --check` 通过。

## 剩余边界

本机没有 Windows SDK／MSVC。新增 C 诊断、真实注册表计数、原生种子十次写入、hive 关闭与删除必须由下一轮 Windows CI 实际确认；本记录不能预先标记 PPK 整项通过，也不替代 Windows 10/11 桌面人工验收。其余 Pageant、MIT、KDC 与工作流文件没有由本修复修改。
