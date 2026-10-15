# Windows MIT 夹具正式化：第一阶段

## 所有权与目标

本轮只拥有 `tools/suite/windows/gss_mit/` 和本文；共同 `PrivateData` 由 KDC 代理提升维护，CI 由根代理接线。没有修改生产 GSS、CMake、现有 Paramiko 5 公钥 peer 或工作流。后续 Paramiko 4 接受端、真实 Kerberos 及终端长调用需要单独分配所有权。

目标是让仓库 Windows CI 执行无凭据原生 ABI 和默认 FILE 配置选择探针。原型中的 acquire、token/MIC、SSH 代码没有迁入此正式阶段。

## 固定接口

```powershell
python tools/suite/windows/gss_mit/prepare_kfw.py --parent .tmp/windows-gss-mit --sevenzip 'C:\Program Files\7-Zip\7z.exe'
python tools/suite/windows/gss_mit/probe.py --directory .tmp/windows-gss-mit/portable/bin --parent .tmp/windows-gss-mit --report .tmp/windows-ci/reports/gss-mit-abi.json
```

准备器输出固定目录，stdout 不返回私有路径。复用 runner 已安装 7z，只记录实际公开版本，不安装解包器；MSI、CAB、每个输出文件均有独立完整 SHA256 内容门。离线 `--archive` 必须位于项目 `.tmp/` 并通过同一锁。

## 来源和实现依据

2026-10-15 再次核对 [MIT 官方分发页](https://web.mit.edu/Kerberos/dist/)，其 Windows 当前发行仍为 KfW 4.1。官方 MSI 为 11,071,488 字节，SHA256 `cdcb7ec4addd9716c0e0c74fe0944cb97c83bfeebc2c267e63e8cac2ad3dc872`。API 的 x64 类型和调用约定以该 MSI 内官方头文件为准，并用 Context7 的 `/krb5/krb5` 核对配置与默认名语义。

官方 `krb5_get_profile()` 在此 DLL 有导出，`profile_get_string()` 没有导出，因此不虚构 profile API 验证。探针通过真实 `krb5_get_default_realm()` 读回本次随机 realm，配合 `krb5_cc_default_name()` 精确返回配置指定的缺失 FILE 名，证明本次私有配置及其默认缓存选择实际生效。该阶段不设 `KRB5CCNAME`，避免覆盖配置默认名而误称已验证配置选择；所有继承的 Kerberos/GSS 变量被删去。

## 加固边界

- 官方 HTTPS 单一 URL，不重定向、不继承代理；长度、内容、网络及管道输出均有上限。
- 两层纯提取，不执行 MSI；目录清单固定为 MSI 64 成员、11,006,516 展开字节，CAB 97 成员、30,298,193 展开字节。拒重复、大小写冲突、路径、目录与链接；每次只将固定单成员输出到管道。
- 输出固定十文件／六 DLL／2,960,208 字节。全部逐个 SHA、x64 PE32+、普通和延迟依赖闭包核验，拒额外文件。PE 解析检查文件、节、RVA、表和字符串边界及结束标记。
- Windows 用共享两 SID 私有 ACL、句柄路径和祖先固定；拒硬链接及 reparse。新文件排他创建，不复用中间产物。
- 首次 DLL 加载前一次封闭环境。Python UCRT 的后续 `os.environ` 修改不能证明 MSVCR100 的 getenv 副本已同步；不在同进程切换配置，也不把 restore 当成卸载。后续有凭据测试必须由全新独立子进程在创建时接收固定 KRB5 环境。
- 无凭据 API 白名单仅机制枚举、释放、配置上下文与默认字符串 getter；不绑定缓存 open/resolve、acquire 或安全上下文认证入口。
- 用户及 XDG 目录显式指向本次私有根。报告开始写失败占位，结束原子替换，拒绝旧成功残留；精确删除本次已知配置与两个空目录，核验随机运行根移除，不递归删除未知内容或准备的 bin。
- stdout 和 JSON 报告只有固定阶段码、公开版本、哈希、计数及布尔，不含私有路径或完整日志。根代理只上传报告，不能上传运行根。解包进程超时后最多五秒等待退出，再以共享五秒预算收尾两个 daemon 管道线程；超限、超时或线程未退出均失败。回归核验真实超限／超时子进程退出且无遗留命名工作线程。

## 本轮真实证据

Linux 上用 `/usr/bin/7z` 26.04 对真实官方 MSI 执行正式脚本离线模式，随后再次运行正式默认联网模式，从固定 MIT HTTPS 直连下载并完整 MSI→CAB→十个精确文件纯提取成功；十文件 PE 普通与延迟依赖实际解析通过。两份准备报告明确 `windows_loader_verified=false`、`credentials_acquired=false`、`kerberos_authentication_verified=false`、`sspi_verified=false`。

首批 10 个无网络回归已通过：实际 PE 解析器及变形输入、归档重复／路径／链接／展开长度、硬链接、额外和缺失产物、哈希与依赖闭包、继承环境清除、解包输出限额和超时及线程收尾、旧成功报告的原子失败替换／脱敏、准确清理本次根、无凭据 API 白名单。非 Windows 原生 CLI 真实返回失败且报告不含私有路径。

新增 7 个 Python 源文件按 Python 3.11 语法解析通过。扩大运行 Windows 公共工具 39 项回归通过（包括共享 PrivateData 的默认双 SID 与显式 owner-only 模式）；没有修改共同模块。`git diff --check` 通过。

Windows 原生加载、原生 ACL 和实际配置 getter 尚未执行，应以根代理随后 Windows CI 原始结果更新。此前研究的 Linux MIT／.NET KDC／原版 Plink 登录仍仅是研究证据，不能据此把本阶段 ABI 或 Microsoft SSPI 记为通过。
