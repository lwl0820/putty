# Windows 便携 KDC 前台原型

- 日期：2026-10-15。
- 目标：为 Windows 正式 MIT GSS 路径提供独占测试 KDC，不加入 AD、不注册系统服务、不修改 HKLM，不依赖个人票据。
- 写入边界：本报告与 `.tmp/windows-kdc-portable/`；未修改生产源、现有夹具或 CI。
- 当前证据：Linux x64 上真实 .NET KDC 与 MIT 客户端跨实现 AS/TGS、受控真实 TGS 网络延迟已通过。协作 GSS 代理另已证明同一 .NET KDC 的真实 MIT 相互认证、MIC 和正式 Plink SSH。**Windows 上尚未执行，Microsoft SSPI 正向与终端 GUI 技术门仍未通过。**

## 1. 选择及固定依赖

使用 Kerberos.NET 官方 `KdcServiceListener`，自行实现最小 `IRealmService`、`IPrincipalService`、`IKerberosPrincipal`。未依赖官方测试项目的 FakeRealm，也未模拟 AS/TGS 响应。最终使用 `Krb5Config.Default()` 纯内存属性默认；早期原型使用 `Kdc()` 会检查系统默认 KDC 配置，最终源码核对后已修正，避免依赖其不存在。监听器只配置 `127.0.0.1:<本次动态端口>`；不监听 UDP、系统 88 端口或非回环接口。

依赖锁为 `.tmp/windows-kdc-portable/dependencies-lock.json`；C# 的传递依赖锁另在 `app/packages.lock.json`。实际下载来自官方微软 SDK 和 NuGet HTTPS 源；SDK 使用微软发布元数据 SHA512，NuGet 同时固定下载归档 SHA256/SHA512 和 NuGet 自身的 content hash。NuGet 的 lock content hash 与最终签名归档哈希分别记录，不能混为一个值。

| 依赖 | 固定版本 | 归档 SHA256（SDK 使用官方 SHA512） |
| --- | --- | --- |
| .NET SDK Linux x64 | 8.0.426 | SHA512 `8141d01bdc53f604aed3bb661b604707a64209f8a947cd81bf95b4f518951593d77864069191f54ea5e84362a409da6b0f69074bf893b283d7e0cf93e2bf20a8` |
| .NET SDK Windows x64 ZIP | 8.0.426 | SHA512 `10261657109f1c62126f106a9ac966739b025b5f6b4a9f88ab7c6e73cacce9aec771895daa3267b8da933a83c32ee91bf458dc6704e1ddcd9bcf0d5edd036abb`；本轮只锁元数据，未下载或执行 Windows SDK |
| Kerberos.NET | 4.6.168 | `9c5f3630467b67d4cfbf7cedd194d1c0f54d24550922e6957b03c0633bfee1b0` |
| Microsoft.Extensions.DependencyInjection.Abstractions | 8.0.2 | `51f2df1100245f10da54f0bb7e813f277155117777d4fbbab902214e27372606` |
| Microsoft.Extensions.Logging.Abstractions | 8.0.2 | `707a5ef17d818186b90f3ba565fab6e2b83c3b62b92e6ab63a22e1a320158097` |
| System.Memory | 4.6.0 | `3a10042b350ce9e11a1fdf4370668ccf6a452c6feafcde0a556aaa881614385a` |
| System.Security.Cryptography.Pkcs | 8.0.1 | `28c348909df243fe4ee962213e3c8021aaecbc8321929dba03a69bcb92a49de5` |
| System.Threading.Tasks.Extensions | 4.6.0 | `3b0201d1da5c767c9fbd35148fa8107ca5b65c5da95ac421ca4c0cd473421ea6` |

Kerberos.NET 包 nuspec 指明源码提交 `b55166eb991c47e32826e9d955fa9d13cc4a3a92`。原型调用接口及关键错误路径均对照该提交的官方源，而非仅依赖可能过期的 README 示例。SDK 和包未安装到系统，已用独立全新 NuGet 缓存和本地固定 feed 实际通过 `restore --locked-mode` 与 Release 构建，零警告、零错误。

## 2. 域、主体、密钥与文件接口

| 项目 | 值 |
| --- | --- |
| realm | `SUITE.TEST`，精确匹配；没有外域 referral/trust |
| client | `suite@SUITE.TEST` |
| service/SPN | `host/localhost@SUITE.TEST`，客户端 hostname 固定 `localhost` |
| TGT 主体 | `krbtgt/SUITE.TEST@SUITE.TEST` |
| 加密与 kvno | 仅 AES256 CTS HMAC SHA1-96，kvno 1 |
| 固定测试密钥 | SHA256(`PuTTY suite fixed test key v1 ` + 不含 realm 后缀的组件名)，分别为 `suite`、`host/localhost`、`krbtgt/SUITE.TEST`；仅公开测试密钥，不用于产品 |
| salt | 使用原始 32 字节 AES key 与标准 keytab，不执行密码 string-to-key，因而互操作不依赖盐；KDC 的 ETYPE_INFO2 仍由库产生，客户端以 keytab 取得现成 key |
| client keytab | `<本次根>/client.keytab`，只有 `suite` 项 |
| service keytab | `<本次根>/server.keytab`，只有 `host/localhost` 项；MIT `kvno -k` 已独立验证可以解密 KDC 签发的服务票据 |
| cache/config | `<本次根>/client.ccache`（显式 `FILE:`）、`krb5.conf`；每轮重新 `kinit -k` 后仅有 TGT，不能用已缓存的服务票据跳过 TGS |
| replay | `KRB5RCACHETYPE=dfl` 和私有 `KRB5RCACHEDIR`；移除 `file2` 以兼容 KfW 1.13；配置内使用正斜杠路径 |

`PortableKDC` 类位于 `portable_kdc.py`，上下文创建独占根并调用前台 .NET 进程，`metadata` 含 realm/client/service、实际 KDC 端口、标准 keytab/config/cache 路径。`environment` 供测试子进程显式继承；`reset_client(kinit_path)` 通过真实 MIT 工具取新 TGT。不存在自动回退个人缓存的代码。原型运行根必须在本目录下，名字默认随机；按根代理协调，GSS 实施者可经此 API 使用 `run-gss-interop-<uuid>` 专用根，不修改本人的代码或其它运行目录。

配置明确关闭 KDC/realm DNS 查找、反查及主机名规范化，强制 TCP；客户端请求 `forwardable=false`、`proxiable=false`。原型主体只开放加密时间戳预认证，PKINIT/证书与跨域支持关闭。正式夹具还应增加对 KDCOptions 的拒绝断言，不能因客户端请求未转发就声称所有恶意请求均被 KDC 策略拒绝。

## 3. 真实协议验证结果

最终本地报告 `.tmp/windows-kdc-portable/validation-report.json`：

- 主机为 Linux，系统 MIT `Kerberos 5 release 1.22.2`；`.NET SDK 8.0.426 / runtime 8.0.32`。
- `kinit -k -t client.keytab suite@SUITE.TEST` 成功；实际 `klist -e` 只有 TGT，没有服务票据。
- `kvno -k server.keytab host/localhost@SUITE.TEST` 成功，实际输出含 `kvno = 1, keytab entry valid`；实际缓存出现 AES256 服务票据。这是不同实现的加密互操作，未只比较本库自生成数据。
- TCP 代理完整读到真实应用标记 12 的 TGS_REQ 后才启动 1.6 秒受控延迟；随后原样转发至真实 KDC，收到真实应用标记 13 的 TGS_REP。成功命令耗时 **1.6211 秒**，三次观察到的延迟为 **1.60008/1.60011/1.60010 秒**（含不存在服务的负例）。
- `host/not-permitted@SUITE.TEST` 真实返回 `Server not found`，未从其它域得到票据。
- 重新 `kinit -k` 后服务票据消失，下一场景必须重新发 TGS_REQ。
- 汇总为 AS_REQ 4、TGS_REQ 3；AS_REP 2、TGS_REP 1、KRB_ERROR 4；代理结束时 0 活跃连接、0 活跃 worker；前台 KDC 进程退出。没有将响应错误当作成功。
- 修正隔离后，此次运行前后只读比较原用户 `.dotnet/.aspnet/.nuget` 文件元数据，未发现后续变化；这不是对整个主机文件系统的完整审计，不能覆盖首次执行事故。

TCP 代理不修改认证字节、不生成票据；每连接只承载一个真实请求/响应，长度上限 64KiB，最大 16 个已登记 socket、64 次累计 accept、共享关闭期限 3 秒。上游监听器在正常 EOF 后可能等 ReceiveTimeout，本原型代理在收完单个真实响应后以 SO_LINGER 复位其独占上游连接，避免空闲 EOF 阻塞后续接收；这不代替实际 TGS 延迟或生成成功响应。进程有 90 秒总取消期限、stdin 显式 stop，Python 等待关闭 5 秒后杀死并报错。

协作 GSS 代理的独立结果在 `.tmp/windows-gss-portable/dotnet-interop-report.json`：同一 .NET KDC 下，MIT GSS 建立真实相互认证、验证 client identity、双向 MIC、拒绝篡改 MIC；正式 Plink 通过 Paramiko 4.0.0 正式 `gssapi-with-mic` 状态机执行固定命令，所有线程与 KDC 已关闭。具体 GSS 绑定和 SSH 对端变更由其研究报告负责，本报告不将这些 Linux 结果当作 Windows 或 SSPI 证据。

## 4. 原型实现期间的偏差与修正

首次 .NET 构建仅设置了 `DOTNET_CLI_HOME` 和项目 TMPDIR，并错误依赖 `DOTNET_SKIP_FIRST_TIME_EXPERIENCE=1`；该变量自 .NET Core 3 起已不受支持。微软明确 CLI_HOME 约束 CLI 支撑文件，但不覆盖用户 X509 store。首次体验默认生成 ASP.NET 开发证书，NuGet 签名验证还刷新 CRL，导致隔离根之外有写入。

精确清单与清理证据在 `.tmp/windows-kdc-portable/cleanup-manifest.json`，包含路径、uid、inode、mtime、birth、SHA256、判定和删除结果：

1. 4 个 CRL 与 1 个 PFX 的 birth/mtime 均为本轮 2026-10-15 10:58，来源为首次构建；按根代理明确指示，删除前逐项核对上述属性后，仅删除这 5 个新文件，未清空任何 store 或目录。
2. `/home/user/.dotnet/corefx/cryptography/crls/bd6662f5.8ba5d856.crl` 的 birth 为 2026-07-22，mtime 被本轮刷新。它是原有缓存，已保留；没有原字节备份，**无法恢复刷新前内容**。不能宣称本轮个人环境完全未改变。
3. 已将所有后续 .NET 执行统一经 Python 子进程环境，`HOME/USERPROFILE/APPDATA/LOCALAPPDATA/XDG_DATA_HOME/XDG_CONFIG_HOME/XDG_CACHE_HOME`、CLI/NuGet/bundle/TMP 全部指向本原型 `.tmp`，并禁用开发证书、全局工具 PATH、遥测、后台工作负载广告、诊断端口；不改变调用进程自身环境。
4. 本轮完整用户目录隔离后的构建、AS/TGS 和互操作继续成功。保留事故和不可恢复限制，不以最终通过覆盖首次失败。

其它已解决的真实失败：第一次原型把不支持 enctype 抛异常，而官方 `PaDataETypeInfo2Handler` 会枚举算法，正确契约是返回 NULL；已修正。第一次 TGS 主体匹配使用 `FullyQualifiedName`，但 NT_SRV_HST 显示为 `host@localhost`；改为严格匹配实际 Name 组件，成功取得并由 MIT 解密服务票据。第一次主动调用 `Stop` 会与上游接收循环 finally 的 Stop 竞争；改为 Dispose 触发取消并等待单次 completion，最终进程正常退出。

## 5. 正式迁入前仍必须完成

本目录是可复现研究原型，**尚不满足直接接 Windows CI 或发布夹具的完整防护要求**：

- Windows 敏感根与文件必须复用现有 `PrivateData`/句柄校验，证明精确受保护 DACL、owner、非 reparse、单链接及路径在私有根；本机 `mode=0700/umask=077` 不是 Windows ACL 验证。
- 当前环境封装清除了继承的 KRB5/DOTNET/NuGet 变量并重设用户落点，但尚未对 `COMPlus_*`、`CORECLR_*`、`COR_*`、`LD_*`、`DYLD_*`、SSL/OpenSSL 变量、注入钩子等建立完整最小环境 allowlist。正式版本应先封闭所有可影响加载器、profiling 与用户 store 的输入，再运行 .NET/库。
- 当前 stdout `readline()` 未设字节上限；stderr 为独占日志但没有硬大小上限。正式版本须限制元数据一行字节数、错误日志体积、stdin/stdout/stderr 的异常关闭及启动失败时已打开资源的清理，并测试超时、非 JSON、超长输出与提前退出。
- 当前 metadata 与本地复现报告含绝对私有根路径，只能保留本地；CI 上传摘要必须只含公开版本、计数、布尔和时间，不能上传 cache/keytab/trace/hive 或带私有路径的原始元数据。
- bootstrap 固定了官方源和归档哈希，tar 使用安全 data filter、ZIP 逐路径检查；正式迁入还须显式拒绝重复成员、符号/硬链接、Windows junction/reparse、大小/文件数炸弹、异常 partial 残留以及已有 SDK 目录被篡改，并对 SDK/包来源校验建立负例。不得把当前哈希锁等同于完整安全解包器。
- 官方 `KdcServiceListener` 未公开动态端口返回，原型先取得 OS 动态端口再绑定，竞争将启动失败且不回退。正式夹具应保留失败可见，并审查其监听、accept、单连接任务与取消异常边界；目前仅 Python 代理有显式并发/消息上限。
- Windows KfW 1.13 对该 KDC、AES256、标准 keytab、FILE cache 与 dfl replay 的真实互操作尚待仓库 Windows CI；KDC 可运行不等于已完成正式终端 GUI 的长同步 GSS 心跳/关闭测试。
- 不能以 MIT DLL 路径的成功代替 Microsoft SSPI；后者的正向票据/KDC 寻址问题仍保留。

## 6. 精确复现及审核文件

在仓库根执行（需允许回环 socket；网络仅首次官方下载；全部生成物留 `.tmp`）：

```text
python3 .tmp/windows-kdc-portable/bootstrap.py
python3 .tmp/windows-kdc-portable/validate_kdc.py
python3 -m py_compile .tmp/windows-kdc-portable/bootstrap.py .tmp/windows-kdc-portable/portable_kdc.py .tmp/windows-kdc-portable/kdc_proxy.py .tmp/windows-kdc-portable/validate_kdc.py
```

审核源码清单：`app/KdcPrototype.csproj`、`app/Program.cs`、`app/packages.lock.json`、`app/NuGet.Config`、`dependencies-lock.json`、`bootstrap.py`、`portable_kdc.py`、`kdc_proxy.py`、`validate_kdc.py`。证据清单：`validation-report.json`、`isolation-snapshot-before.json`、`isolation-snapshot-after.json`、`cleanup-manifest.json`、各次运行日志与 `metadata.json`；后者及生成的 cache/keytab/trace 都不上传。`debug_error.py` 与 `source/` 仅为定位时的官方源码/错误诊断材料，不属于推荐迁入清单。

## 7. 官方依据

- [Kerberos.NET KDC 示例](https://github.com/dotnet/Kerberos.NET/blob/b55166eb991c47e32826e9d955fa9d13cc4a3a92/Samples/KerberosKdcHostApp/Program.cs)：监听、realm locator 的公开 API。
- [固定提交 IRealmService / IPrincipalService / IKerberosPrincipal](https://github.com/dotnet/Kerberos.NET/blob/b55166eb991c47e32826e9d955fa9d13cc4a3a92/Kerberos.NET/Server/IRealmService.cs)：原型自行实现的接口。
- [固定提交 ETYPE_INFO2 handler](https://github.com/dotnet/Kerberos.NET/blob/b55166eb991c47e32826e9d955fa9d13cc4a3a92/Kerberos.NET/Server/PaDataETypeInfo2Handler.cs)：不支持算法返回 NULL 的实际调用契约。
- [固定提交 Krb5Config](https://github.com/dotnet/Kerberos.NET/blob/b55166eb991c47e32826e9d955fa9d13cc4a3a92/Kerberos.NET/Configuration/Krb5Config.cs)：`Kdc()` 会探测系统文件，最终 `Default()` 仅构造内存属性默认。
- [固定提交 SocketListener](https://github.com/dotnet/Kerberos.NET/blob/b55166eb991c47e32826e9d955fa9d13cc4a3a92/Kerberos.NET/Server/SocketListener.cs)、[ServiceListenerBase](https://github.com/dotnet/Kerberos.NET/blob/b55166eb991c47e32826e9d955fa9d13cc4a3a92/Kerberos.NET/Server/ServiceListenerBase.cs)：构造时绑定、Start 的任务是生命周期结束而非 readiness、取消路径。
- [微软 .NET CLI 环境变量](https://learn.microsoft.com/en-us/dotnet/core/tools/dotnet-environment-variables)：禁开发证书/工具/工作负载变量及 CLI_HOME、旧首次体验变量限制。
- [官方 .NET8 发布元数据](https://builds.dotnet.microsoft.com/dotnet/release-metadata/8.0/releases.json)：固定 SDK 下载和 SHA512。
- [NuGet 固定 Kerberos.NET 包](https://www.nuget.org/packages/Kerberos.NET/4.6.168)：固定包与依赖；包 nuspec 的源码 commit 已实际读取。
