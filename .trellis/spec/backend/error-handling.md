# 密钥核心契约

## 1. 范围与触发

适用于 `src/core/` 和调用该模块的 Qt 工作对象。第三方密码实现位于 `vendor/putty/`，自有接口位于 `src/core/pg_core.h`；不要把上游 C 结构暴露给 C++。新增接口、改变错误或密钥替换行为时同步本规范与测试。

## 2. 接口签名

```c
int pg_core_init(pg_error *error);
void pg_core_cleanup(void);
int pg_generate(const pg_generate_params *params, const void *entropy,
    size_t entropy_size, pg_progress_fn progress, void *context,
    pg_key **result, pg_error *error);
int pg_load(const char *path, const char *passphrase,
    pg_key **result, pg_error *error);
int pg_key_get_info(const pg_key *key, pg_key_info *info, pg_error *error);
int pg_save(pg_key *key, const char *path, pg_save_format format,
    const char *passphrase, const pg_ppk_params *params, pg_error *error);
```

完整定义以头文件为准；输出内存使用 `pg_key_free`、`pg_key_info_free`、`pg_file_info_free` 释放，禁止直接 `free` 上游对象。

## 3. 请求、响应与线程约束

- 所有字符串是 UTF-8；Windows 适配转换为宽字符路径，Linux 使用原路径字节。
- 一个工作线程串行调用随机初始化、密钥操作及清理。上游 PRNG 包含全局状态，不能从 GUI 线程并发读取或销毁。
- `pg_generate_params` 包含算法、位数、素数策略及强素数标记；曲线以相应位数选择。`pg_ppk_params` 包含版本、Argon2 变体、内存 KiB、自动时间或固定轮数以及并行度。
- 返回 `1` 表示成功，`0` 表示失败；`pg_error.code` 是业务判断依据，`detail[512]` 是诊断详情，不得用英文详情字符串决定 GUI 分支。
- `pg_key_info` 仅含公开信息及能力标记。格式和证书入口使用能力标记，核心仍验证请求是否合法。
- 新生成或加载的密钥只在成功后替换旧句柄。一次性口令及熵缓冲使用 `pg_secure_clear` 清除，不记录日志。

## 4. 校验与错误矩阵

| 输入或状态 | 错误 | 调用方行为 |
| --- | --- | --- |
| 参数非法 | `PG_ERR_ARGUMENT` | 保留当前密钥，展示可翻译提示 |
| 文件读取或写入失败 | `PG_ERR_IO` | 禁止显示成功 |
| 无效密钥或内容 | `PG_ERR_FORMAT` | 保留当前密钥 |
| 口令错误 | `PG_ERR_PASSPHRASE` | GUI 可重试或取消 |
| 系统随机源失败 | `PG_ERR_RANDOM` | 阻止生成，不能退化成伪随机 |
| 无效证书／不匹配 | `PG_ERR_CERTIFICATE`／`PG_ERR_CERT_MISMATCH` | 不更换原密钥 |
| 格式不适用 | `PG_ERR_UNSUPPORTED` | 拒绝执行，即使调用绕过菜单禁用 |

无口令确认、覆盖确认和用户取消属于界面流程；取消不调用保存，也不伪造核心错误。

## 5. 正常、基线与错误案例

- 正常：中文路径保存加密 PPK，再加载，公钥和指纹保持一致。
- 基线：Ed25519 生成后无口令 PPK 保存，在界面完成确认后执行。
- 错误：加载错误口令、添加其他私钥的证书、向不可写目标导出；旧密钥仍可使用，不出现成功提示。

## 6. 必需测试

核心矩阵覆盖全部算法与曲线、三种素数策略、强 RSA、PPK v2/v3 与 KDF、适用外部格式、证书匹配及失败、中文路径和真实写入错误。互操作以固定上游 CLI 和适用 OpenSSH 工具验证公开材料及签名。Release 下测试断言必须执行；本机 Linux 测试不替代 Windows 和两个 Ubuntu 版本的验证。

## 7. 错误与正确做法

错误：`pg_key_free(old); pg_load(path, pass, &old, &error);`，失败时已丢失原密钥。

正确：先 `pg_load(..., &replacement, ...)`，仅成功后释放旧对象并提交新句柄。上游写出函数的返回值还必须覆盖 `fclose` 的最终失败，不能把缓冲区尚未提交当成成功。
