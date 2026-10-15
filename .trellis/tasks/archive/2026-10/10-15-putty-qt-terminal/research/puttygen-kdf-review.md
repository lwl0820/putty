# PuTTYgen 自动 KDF 参数核查

## 结论

2026-10-15，针对 Windows 真实 PPK 纵切所提出的“`ppk_params()` 写轮数字段，导致自动时间仍为默认 100 毫秒”的假设进行独立核查，确认该问题不存在。未修改产品代码或正式测试，不增加重复计时断言。

固定上游 `vendor/putty/ssh.h:1518` 将 `argon2_passes` 和 `argon2_milliseconds` 定义在同一个匿名联合体中。`apps/puttygen/src/core/pg_core.c:49` 写入 `passes_or_ms` 已同时设置该联合体的存储；`passes_auto` 决定 `sshpubk.c:626` 选择自动校准还是固定轮数。字段命名本身不能证明数据流错误。

## 真实验证

本机 Arch Linux x64、GCC 16.2，复用 `.tmp/build/suite-integrated-all` 的现有 Release `pg-core`／`pg-putty`，生成和解密使用同一套库，不链接 `suite-putty`。先执行 `cmake --build .tmp/build/suite-integrated-all --target pg-core --parallel 2`，结果无需重新编译。

临时核查程序 `.tmp/puttygen-kdf-review/probe.c` 仅通过正式 `pg_*` 接口生成 Ed25519、保存加密 PPK、读取实际 PPK 轮数、重新解密并核对公开指纹。编译使用 `-std=c99 -D_POSIX_C_SOURCE=200809L -O3 -UNDEBUG -Wall -Wextra -Wpedantic -Werror`，无诊断，退出码 0。临时程序与私钥均留在项目 `.tmp/`，未访问个人存储或代理。

| 配置／检查 | 实际结果 |
| --- | --- |
| Argon2id，8192 KiB，并行度 1，自动 1500 毫秒 | 写出 PPK 的 `Argon2-Passes` 为 377 |
| 自动校准及保存累计墙钟时间 | 5133.860 毫秒 |
| 同配置库读取该 PPK 的真实解密时间 | 1825.811 毫秒，成功且公开指纹一致 |
| 固定轮数 2 | 写出轮数确为 2，真实解密成功且公开指纹一致 |
| 加密 PPK v3：自动时间 0、固定轮数 0、并行度 0、内存 0 | 全部拒绝，`PG_ERR_ARGUMENT` |
| 非法 Argon2 变体、PPK 版本 1 | 全部拒绝，`PG_ERR_ARGUMENT` |

命令输出保留在 `.tmp/puttygen-kdf-review/result.log`。本轮仅运行一次自动 1500 毫秒，不重复执行整个密钥矩阵。

## 时间语义与验证边界

`vendor/putty/crypto/argon2.c:563` 按斐波那契轮数递增，直到某次实际派生耗时达到目标，再把该次轮数写入 PPK。因此校准保存耗时包含多次派生，通常明显大于随后一次解密；1500 毫秒不是保存操作的总耗时上限。由于轮数离散、调度噪声和单轮成本，不能把真实解密写成必须恰好 1500 毫秒的断言；本次 1825.811 毫秒为目标的约 1.22 倍，符合该算法的近似校准行为。

现有 `core_test.c` 已覆盖自动模式往返、固定轮数的三种变体以及非法并行度。零值和实际轮数由此次临时探针补充核对，不为已正确行为添加重复源码测试或易抖动的 CI 时间阈值。当前产品与原有核心测试文件差异为空，无需同步接口规范。

此证据只覆盖本机 Release 核心。Windows 与目标客户端须继续使用相同系统和构建配置的生成器校准，并实际记录客户端解密耗时、GUI 心跳及关闭安全点；不能复用本机 377 轮推定 Windows 或 Debug 的耗时。结论与数值已发送给 Windows PPK 实施者，原生验证继续推进。

## 检查状态

- 固定源码及参数数据流：通过，映射错误假设已排除。
- 编译／静态类型及警告检查：临时探针严格编译通过；产品无改动。
- 真实自动加密／解密、固定轮数、六项非法参数检查：全部通过。
- 全平台核心矩阵、Windows 原生：本轮未重复执行，由现有 CI 与 Windows 纵切分别提供证据。
