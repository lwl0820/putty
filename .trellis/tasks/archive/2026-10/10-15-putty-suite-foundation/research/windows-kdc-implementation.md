# 正式 KDC 夹具实施记录

日期：2026-10-15。所有权为 `tools/suite/windows/gss_kdc/`，没有修改 MIT ABI、公共 PrivateData、CMake 或工作流。正式入口和公开契约见该目录 README。

## 从研究原型迁入的边界

正式代码使用 .NET SDK 8.0.426、Kerberos.NET 4.6.168 和六个固定 NuGet 归档；SDK 全量归档复核、离线锁定还原及独占缓存均已实际运行。Linux SDK 为 4704 个文件，正式源码与产物状态匹配。自有有界 `TcpListener(127.0.0.1:0)` 调用官方 `KdcServer.ProcessMessage`，不再使用原型的先占后绑或无限 accept 的上游 listener。

首次体验隔离进一步收紧：Windows NuGet MigrationRunner 的 KnownFolder 不遵从 `LOCALAPPDATA`，Linux 旧数据迁移锁也有系统临时目录落点。正式构建直接执行 SDK `MSBuild.dll`，绕过 CLI 首次体验/迁移，禁 workload resolver/共享编译/server，显式离线配置；完整 SDK 在构建前后逐项与官方原归档核对，额外空目录同样拒绝。封闭子进程环境和旧成功标记失效均有回归。

Windows 使用公共 PrivateData 与句柄删除；挂起子进程先加入 Job 才恢复执行。Linux 真实后代持有管道、超时、输出溢出、元数据缺换行、单流关闭异常均会在共享期限内清理。运行成功删除私有 keytab/cache/config/trace 根，失败根只留本地，不上传。

## 本机真实验证

Linux / Python 3.14 / MIT Kerberos 1.22.2：

- 安全回归当前 17 项：16 通过，Windows 挂起/Job 专项 1 项明确按平台跳过；Windows 清理普通树的用例在 Windows 会原生运行。
- 真实 `kinit -k` 成功，`klist` 仅有本域 TGT；`kvno -k` 成功并由 MIT 独立解密服务票据，kvno=1。最终真实命令耗时1.667889秒，代理完整读到TGS后的延迟为1.600098秒，不能把 sleep 或假响应当作认证。
- AS `-f`、未知 SPN、跨域请求拒绝；八个官方库编码的无认证负例经真实 TCP 精确返回策略错误，包括 Forwarded、Proxiable、Proxy、ConstrainedDelegation、CNameInAdditionalTicket、EncTktInSkey、CrossRealm、AdditionalTicket。该项证明入口策略，不能当作有效凭据委托认证。
- 超大、零长、畸形帧和64次累计 accept预算实际触发；四个并发 KDC worker 之外的连接被拒绝。代理修正为最多八个 worker/16个 socket，不能只检查 accept 时已登记的单侧 socket 数。
- 正常及在途关闭后 KDC/代理均为零活跃 socket/worker，KDC 前台进程正常退出；四个KDC在途读取、四个并发拒绝、八个代理在途读取及一个预算拒绝共同关闭耗时0.109676秒；两场景 `private_data_removed=true`。
- 本轮各次真实执行前后只读核对个人 `.dotnet/.nuget/.aspnet` 文件的 inode、大小、mtime和SHA256，没有后续变化。快照保留在本地私有验证目录，仅公开布尔。

完整最终摘要在 `.tmp/windows-kdc-formal/validation-report.json`；私有原始快照和失败诊断留在 `.tmp/windows-kdc-formal/verification-<uuid>/`，不上传。准备及各次失败均已如实保留，没有删除旧事故记录。

## 已发现并修正的真实兼容问题

正常 MIT TGS 首次被过严白名单拒绝。固定官方源码 `KrbKdcRep.DefaultFlags` 为内部常量 `Renewable | Forwardable`，AS handler 直接使用，导致 MIT 根据 TGT 能力位请求 Forwardable。经主会话明确同意，TGS 仅额外允许该能力位，仍拒实际转发/代理/附加票据，AS显式 `-f` 仍拒绝。不改官方包、不自行重签票据；报告使用 `forwardable_as_rejected` 与 `forwarding_rejected`，不声称票据能力位为 false。后续 GSS 实现已获告知，需要核验无委托标志和凭据。

固定源码依据：[KrbKdcRep](https://github.com/dotnet/Kerberos.NET/blob/b55166eb991c47e32826e9d955fa9d13cc4a3a92/Kerberos.NET/Entities/Krb/KrbKdcRep.cs)、[AS handler](https://github.com/dotnet/Kerberos.NET/blob/b55166eb991c47e32826e9d955fa9d13cc4a3a92/Kerberos.NET/Server/KdcAsReqMessageHandler.cs)。

## 尚未验收

Windows SDK ZIP 解包、MSBuild 离线还原、Job/Toolhelp恢复、ACL/句柄清理以及 KfW 1.13 的真实 AS/TGS仍待仓库 Windows CI。上述 Linux 结果不替代它们，也不替代 Microsoft SSPI正向认证或终端 GUI 长 GSS 调用的心跳/关闭测试。原型首次个人 store 事故的五个新增文件已精确删除，原有 CRL被刷新且无法恢复旧字节的事实继续保留于 `windows-kdc-portable.md`。
