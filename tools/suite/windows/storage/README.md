# Windows 受控上游存储宿主

本目录独立于现有 runtime 与原版 CLI。前置的私有 hive 10 项检查已经在 Windows Server 2022、MSVC x64、Qt 6.8.3 的[第三轮仓库 CI](https://github.com/lwl0820/putty/actions/runs/38664741693)通过；这不代表本目录通过。[本宿主首次仓库 CI](https://github.com/lwl0820/putty/actions/runs/38667665115)已经完成真实 MSVC 编译和安装，执行时因测试使用错误的用户名配置访问器而失败。现已改用对应 `STR_AMBI` 类型的 `conf_get_str_ambi`，本地配置类型与真实 Conf 回归通过，19 项原生检查仍待重新执行。本机没有 Windows SDK、MSVC、MinGW 或 Wine。

## 编译与执行

主代理接入目标 `suite-windows-storage-tests`：

- 源为 `storage_variant.c`、`seed_guard.c`、`storage_test.c`、`frontend.c`，另加上游 `windows/no-jump-list.c`。
- 链接 `suite-windows-hive` 和 `suite-putty`，使用其独立 suite-config 头与 Windows 库；无需 Qt 或 putty-runtime。
- C99，MSVC `/utf-8 /UNDEBUG`；定义字符串 `SUITE_STORAGE_PROJECT_TMP="<项目绝对路径>/.tmp"`，通过 `/MAP:<宿主完整路径>.map` 生成链接图。
- CTest 为 `runtime-windows-storage`，标签 `runtime;windows-storage`，超时 120 秒，无 skip。

```powershell
python -B -m unittest discover -s tools/suite/windows/storage/tests -v
python -B tools/suite/windows/storage/run_storage_test.py --binary .tmp/build/windows-release/libs/putty-runtime/tests/suite-windows-storage-tests.exe --parent .tmp/windows-storage
```

父目录必须是项目 `.tmp/` 的直接子目录，由 C hive 层创建精确私有 DACL。Python 不先 mkdir 私有父目录，不依据 GITHUB_ACTIONS 放行，拒绝其它 EXE 名称、越界和路径链接。唯一报告默认 `.tmp/windows-storage-reports/private-storage.json`，也可用 `--report-dir` 指定其它项目 `.tmp/` 报告目录。只上传这一个摘要文件；禁止上传私有 hive、日志、seed 或整个运行树。

## 测试变体的失败边界

`storage_variant.c` 直接编译固定 `vendor/putty/windows/storage.c`。不复制存储算法，不改变 vendor 源或产品构建。宏只收窄该翻译单元：

| 原边界 | 测试限制 |
| --- | --- |
| HKEY_CURRENT_USER | 先确认本线程已绑定、`suite_hive_ready` 为真，再返回真实 HKCU 伪句柄，由已验证的 RegOverridePredefKey 映射；不以 hive 原始句柄代替被测路径 |
| CreateFileA | 路径必须等于本次既有 seed；拒绝不匹配的 API 参数、重入或并发文件句柄 |
| CREATE_ALWAYS | 先 OPEN_EXISTING 且 OPEN_REPARSE_POINT，同一打开句柄核验实际最终路径、普通磁盘文件、单硬链接、owner 和精确受保护 DACL（当前 SID 与 SYSTEM），通过后才在同一句柄截断 |
| ReadFile/WriteFile/CloseHandle | 只接受正在登记的 seed 句柄；I/O 错误、短写或关闭失败立即退出；写句柄 flush 后关闭 |
| load_system32_dll、GetEnvironmentVariableA、GetWindowsDirectoryA、DeleteFileA | 在存储回退/删除边界立即退出；不调用原 API，不展开 APPDATA、HOME 或 Windows 文件候选 |

缺少/错误类型的 RandSeedFile 在进入首次 fallback 的 `load_system32_dll` 钩子处退出；错误路径在调用真实 CreateFile 前退出；正确 seed 的真实独占锁导致打开失败时也立即退出。失败钩子是专用非零 `ExitProcess`，不会返回上游让其继续候选循环。子进程以已知退出码证明各拒绝分支；父进程随后重新显式初始化，恢复私有配置并核验原始二进制内容未改变。

上游原生产错误回退仍存在，本测试变体不覆盖或改变其语义。`cleanup_all`、删除 seed 和原版 CLI 都不运行。运行器另锁定当前 Windows storage 源码 SHA256（仅 CRLF 归一为 LF，兼容 Windows checkout）；变动必须重新审查所有文件候选和宏覆盖后更新摘要，不能静默跟随。链接图强制要求变体、guard、真实 settings/noise/sshrand/PRNG 对象与符号，同时拒绝任何生产 `storage.c.obj`，防止重复对象或 FUZZING PRNG 冒充。

## 原生检查计划

宿主在完整成功后才输出 19 项检查摘要：

1. 真实 `do_defaults/save_settings/load_settings`、会话枚举、FontSpec 与 Filename 往返、删除会话。
2. 真实主机密钥缓存的缺失、匹配、不匹配，host CA 的保存、加载、枚举、选项、删除。另以私有 hive 写入不含 `Validity` 的旧版 `MatchHosts` 多字符串数据，验证真实加载后仍保留公钥、选项及合成表达式，并恢复现代格式。CA 父键不存在分支和存在父键时重复删除 128 次均比较实际进程 handle 数，防止遗漏 HKEY 关闭。
3. 从 hive 原始句柄分别反查会话、主机键值、CA 公共字节/有效表达式/选项和 RandSeedFile，不只让同一上游读写互相证明。
4. 父进程关闭全部子键并 pause 后，子进程自己 open hive、绑定 guard，再读取真实存储与 seed、回写父进程能独立反查的标记；不假定继承 HKCU 覆盖。
5. 真实 seed API 写入含 NUL/高位字节的固定二进制标记，再用真实 seed consumer 和独立 Win32 读句柄核验；每次打开都在实际句柄检查 ACL。
6. 六个独立进程实际触发未绑定（85）、缺失配置（82）、损坏类型（82）、非法 seed（80）、真实读锁失败（81）、真实写锁失败（81）。个人目录候选的 API 尚未调用即退出。
7. 真实 Windows noise、系统临时加密上下文与 sshrand/PRNG 生成和销毁；检查读写计数、两批随机输出不同、显式保存/clear、ref/unref 后真实 timer 队列无剩余。随机字节用后清零，不输出。计数与差异用于执行证据，不是随机质量或熵评估。
8. PRNG 生命周期结束且 hive 仍映射时完成存储核验，然后解除绑定、close、清理私有运行根。成功不允许遗留新根。

前端只提供 timer 通知、致命错误策略及存储协议元数据，不创建网络后端。CA 公共字节和主机键值是固定序列化测试标记，不是网络认证或证书签名验证。真实 `windows/noise.c` 会读取 Windows 公共目录元数据及系统随机源；种子路径钩子只作用 storage 变体，不把它误计为已验证 Windows 桌面环境。[CreateFileW 官方说明](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-createfilew)区分打开、创建、截断及共享模式；[CryptAcquireContextA 官方说明](https://learn.microsoft.com/en-us/windows/win32/api/wincrypt/nf-wincrypt-cryptacquirecontexta)说明 CRYPT_VERIFYCONTEXT 临时上下文。权限和 hive 的真实行为仍须本批原生执行确认。

## 保留缺口

本轮发现 `vendor/putty/windows/storage.c:512` 的 `host_ca_delete` 打开 host_ca_key 后没有关闭 HKEY。主代理已另加最小关闭修复及来源补丁，本宿主恢复真实删除/重复删除/缺父键回归；原生结果仍等待本批 CI，详见实施报告。

独立审查另修复 `host_ca_load` 的三个临时缓冲泄漏，差异记录于 `0010-host-ca-load-buffers.patch`。`base64_decode_sb`、`percent_decode_sb` 和 `cert_expr_expression` 均返回独立结果，完成转换后释放注册表字符串和旧格式缓冲，不改变返回对象的所有权。正常与旧格式加载均已接入本批原生计划；本机仅确认源代码所有权，尚无 Windows 堆泄漏检测结果。

`tests/test_conf_schema.py` 直接读取上游 `conf.h` 的字段主表，核对宿主所有 11 次配置访问器，并用首次 CI 的旧访问器验证回归能够拒绝类型不匹配。`conf_set_str` 可写入 `STR_AMBI`，而 `conf_get_str` 只接受 `STR`；此类问题不能通过关闭上游断言解决。

任何本批通过只允许声称该独立变体宿主的实际结果，不能开放旧 runtime-lifecycle、未初始化的原版子进程或生产回退路径。Windows 10/11 真桌面、原版 CLI 存储和生产错误回退均保留未验；本批 19 项原生检查当前尚未整体通过。
