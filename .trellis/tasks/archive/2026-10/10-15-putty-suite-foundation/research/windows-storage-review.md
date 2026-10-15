# Windows 受控上游存储独立审查

日期：2026-10-15。审查范围为 `tools/suite/windows/storage/`、`run_ctest.py` 及其公共回归、真实 Windows storage 的 CA 所有权修补及来源。公共 CMake 和 Windows 工作流只读核对，没有覆盖；并行的终端和 Pageant 文件未修改。没有提交代码、没有将任务标记完成。

## 结论与可提交范围

本批可以作为独立检查点提交并触发真实 MSVC CI；本机静态审查及 Python 拒绝门通过，没有剩余已确认的局部代码缺陷。**本批 Windows 存储目标从未在本机原生编译或运行，不能把该结论当作 19 项原生检查成功。** 前置 hive 在第三轮 Windows CI 的成功只属于前置探针。

本批必须一起提交 `storage.c`、`0009-host-ca-delete-handle.patch`、`0010-host-ca-load-buffers.patch`、来源、整个 storage 宿主以及公共接线。审查后当前 storage 源码的换行归一 SHA256 为 `e2c323be831900ac99ac8075409f0e9c1516dd51c9a39de8a2305d5b469e928a`；运行器已经更新锁定值，不能使用初次实施报告中的旧摘要。源锁变更只涉及下述 CA 内存释放，不改变随机种子候选或失败回退源码。

## 已修复及已复核问题

### CA 加载临时内存泄漏：本审查修复

`vendor/putty/windows/storage.c::host_ca_load` 原来未释放 `get_reg_sz` 返回的 `PublicKey`、`Validity` 堆字符串，也未释放 `get_reg_multi_sz` 返回的旧格式 `MatchHosts` 字符串缓冲。注册表辅助实现明确为这些结果分配内存，CA 析构只释放转换结果，无法替代临时输入的释放。

主会话在本轮审查中明确授权扩展至该函数。最小修复把临时 `s` 改为拥有所有权的 `char *`，两种转换结束后分别 `sfree(s)`，旧格式表达式构建完毕后 `strbuf_free(sb)`；另记录 `0010` 与来源。

已沿实际转换路径确认所有权：`base64_decode_sb`、`percent_decode_sb` 分配新缓冲；`strbuf_to_str` 转交解码输出而非注册表输入；`cert_expr_builder_add` 复制每个 wildcard，`cert_expr_expression` 再生成独立字符串。三个新释放均在输入最后一次使用之后，返回 CA 的公钥与表达式仍有效。

新增 `storage_test.c::legacy_ca_roundtrip`，在已验证私有 hive 中移除现代 `Validity`、写入实际 `REG_MULTI_SZ MatchHosts`，调用真实 `host_ca_load` 后核对固定公钥、选项与 `*.example || legacy.example` 表达式，再释放 CA、恢复现代数据并重新验证。它归入现有 `host-ca` 检查，19 项集合不变。正常格式仍由原有 CA 往返覆盖。尚无 Windows 堆泄漏检测证据；本机结论是源代码所有权确认，原生行为待 CI。

### CA 删除 HKEY 泄漏：主会话修复，本审查复核

`host_ca_delete` 成功打开父键后原来直接返回，没有关闭 `rkey`。本审查确认 `0009` 只在删除和字符串释放后补一个 `close_regkey(rkey)`，保留缺父键返回及原删除语义。原生宿主确实覆盖父键缺失、成功删除后原始 hive 反查、父键仍存在时重复删除 128 次并比较实际进程 handle 数。该动态回归尚待 Windows CI。

## 实际调用路径与隔离边界

- 测试翻译单元直接 include 固定 storage 源，ANSI API 宏只在该翻译单元生效。每次上游 HKCU 获取均先检查本线程绑定和 `suite_hive_ready`，随后返回真实预定义伪句柄供 RegOverride 映射；hive 原始句柄用于独立反查。
- 缺少或类型损坏的 `RandSeedFile` 在首次 `load_system32_dll` 回退处直接退出，未执行个人候选查询。错误 seed 在真实打开前拒绝；读写打开失败、IO 失败、短写、flush 或关闭失败直接以专用非零状态结束，不能返回上游继续候选循环。
- 写 seed 使用 `OPEN_EXISTING | FILE_FLAG_OPEN_REPARSE_POINT`，在同一实际句柄核对磁盘普通文件、单硬链接、最终路径、当前用户 owner、仅当前 SID 与 SYSTEM 的受保护精确 DACL，再截断和写入。上游传入参数也被校验。读路径同样检查实际句柄。
- 六个拒绝子进程并非仅模拟返回值：未绑定、缺配置、错误类型、错误候选及实际独占锁引发的读写打开失败均要求精确退出码。需要存储的子进程自己加载 hive 并绑定，父进程不假设映射会继承；父进程恢复映射后修复私有配置并核验原 seed 字节。
- PRNG 通过真实 Windows noise、系统随机源、sshrand、PRNG 和 timing 实现；初始化、读取、保存与销毁均在 hive 映射和 guard 有效期内。宿主核对读写计数和清理后 timer 队列，随机输出用后清零。此设计不声称随机质量评估。
- MAP 审计要求变体、guard、settings、noise、sshrand 对象和实际 PRNG／timer 符号，拒绝生产 storage 对象；源码锁和 Windows 平台门在执行前生效。源锁、MAP 或 Python 测试均不能代替原生执行。
- `run_ctest.py --require-storage` 同时要求 hive，执行前要求各标签恰好注册一个对应宿主，删除旧 JUnit，执行后拒绝缺失、重复、失败和跳过。CLI、旧 runtime 与生产回退仍保留未验标签。工作流保留唯一 `private-storage.json` 摘要白名单，没有递归上传私有运行根。

## 验证

| 检查 | 本次实际结果 |
| --- | --- |
| 公共 Windows Python 契约 | 15/15，通过，0 跳过，最终复跑 0.182 秒 |
| storage 运行器 Python 契约 | 6/6，通过，0 跳过，最终复跑 0.062 秒 |
| Python 静态语法／内存编译 | 4 个文件通过；不生成源码目录字节码 |
| 新目录 UTF-8、尾换行、行尾空白；已跟踪源码差异检查 | 通过 |
| C 与 Python 19 项摘要集合 | 完整且一致 |
| 0009 与 0010 各自反向检查 | 均退出 0 |
| 在项目 `.tmp/windows-storage-review/` 按序正向、逆序反向应用 | 正向与当前 storage 字节精确相同，反向与 HEAD 精确相同；临时副本已清理 |
| Windows C 类型／链接检查、原生 19 项 | 未执行；本机无 MSVC、Windows SDK、MinGW 或 Wine |

仓库没有为本批 Python 配置独立 lint 或静态类型检查器；语法和格式检查通过。C 类型检查必须以真实 Windows 构建为准，不能填写通过。本轮核对了使用的 Win32 API 签名和已有声明，未发现确定的编译／链接阻塞，仍需实际 CI 确认。

## 未修复项与后续验证

没有已确认且未修复的本批局部缺陷。原生编译、精确 ACL、六类子进程退出、跨进程 hive 清理、真实 PRNG/timer 及 CA 句柄守恒均等待本批 Windows CI，不能从前置 hive 或本机 Python 结果外推。生产 seed 失败回退、原版 CLI 存储、旧 runtime 存储以及 Windows 10/11 桌面仍不开放。

本轮未修改公共 spec，以免把尚未原生执行的变体策略写成产品惯例。原生结果确认后，由主会话在运行时／测试隔离规范中记录该逐进程绑定、受控翻译单元、失败即结束和证据边界；保持生产策略与测试变体的区别。

## 首次原生 CI 失败后的局部修复

仓库 [Windows run 38667665115](https://github.com/lwl0820/putty/actions/runs/38667665115)，提交 `02841d5f5fdbc9ce72b0f2831d7a31ef47055fad`：真实 MSVC 编译和安装成功。执行时 `runtime-windows-storage` 退出 `3221226505`，日志明确为 `utils/conf.c:310` 的 `conf_key_info[primary].value_type == CONF_TYPE_STR` 断言。本宿主未完成，因此不能声称 19 项成功。相同 CI 的 handles、events 与 hive 已通过，旧 runtime-lifecycle 仍明确跳过。

根因位于 `storage_test.c::verify_session`：`CONF_username` 在固定 PuTTY 0.85 `conf.h` 主表中是 `STR_AMBI`，测试却调用仅接受 `STR` 的 `conf_get_str`。保存端 `conf_set_str` 支持 `STR_AMBI`，不是错误来源。修复仅将用户名读取改为 `conf_get_str_ambi(conf, CONF_username, NULL)`，上游断言、存储算法、隔离与 19 项摘要集合均保持原状。

新增 `storage/tests/test_conf_schema.py`，解析真实上游主表后核对宿主 11 次配置调用（5 读、6 写）。主机名、端口、协议、FontSpec、Filename 的原访问器均与主表一致。回归主动重建首次失败的用户名读取方式，确认被判定为 `STR_AMBI` 类型不匹配；另核对两种字符串编码写入均合法，以及错误整型读取主机名会失败。

本地还从修复后的宿主提取实际用户名 getter 表达式，生成 `.tmp/storage-ci-schema/conf_probe.c`，使用 `-std=c99 -Wall -Wextra -Werror -UNDEBUG` 编译并链接已构建的真实 `libsuite-putty.a`。没有初始化存储、PRNG 或网络。实际 `Conf` 中先写 ANSI 用户名、再写中文 UTF-8，修正表达式均正确读出，编码标记也保持一致；故意运行旧 `conf_get_str` 则以 `SIGABRT` 精确触发同一个 `conf.c:310` 断言。子进程禁用 core dump，源、二进制和结果均在项目 `.tmp/storage-ci-schema/`。这证明本地回归能重现和覆盖该平台无关的配置类型缺陷，不能替代 Windows 注册表、种子和 PRNG 的原生执行。

修复后实际验证：storage Python 9/9，通过，0 跳过，0.093 秒；公共 Windows Python 当前 17/17，通过，0 跳过，0.309 秒（另一个代理同时添加了终端结果门回归）；本地真实 Conf 正例退出 0、旧代码负例退出 -6；目标源码差异检查通过。Python 未配置独立 lint／静态类型检查器；本地 C 探针严格类型编译通过，修复后 Windows C 编译和 19 项执行仍待下一轮 CI。没有提交或推送，没有修改授权范围以外文件。
