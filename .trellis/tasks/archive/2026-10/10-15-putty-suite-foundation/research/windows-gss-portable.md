# Windows 便携 MIT GSS 与受控 SSH 接收端原型

日期：2026-10-15。范围：测试原型；只写 `.tmp/windows-gss-portable/` 与本文。另经 KDC 实施者协调，在其 `.tmp/windows-kdc-portable/run-gss-interop-<uuid>` 下通过 `PortableKDC` API 创建、使用和关闭本轮专用域；未修改该实施者源码或其它运行目录。未修改生产程序、现有 Paramiko 5 对端、构建或 CI。

## 结论与证据边界

便携 MIT 客户端的下载、纯解包、x64 PE 常规及延迟依赖闭包已经实测。原生 Windows 加载探针可由下一轮 CI 直接执行，但本机 Linux 尚未执行 Windows DLL，本文不填写任何 Windows 或 Microsoft SSPI 通过结果。

Linux 原理验证已经完成两条真实链路：正式 MIT KDC，以及另一实施者的便携 Kerberos.NET KDC；后者与 MIT 客户端、GSS 接受端、原版 Plink 已交叉成功。两种 KDC 均观察到真实 TGS 请求后的网络延迟。独立固定 Paramiko 4 的正式认证状态机负责 SSH GSS 认证，ctypes 适配调用真实 MIT 库；没有重写 SSH 或 Kerberos 协议状态机。

这些结果证明下一 Windows 原生验证有具体可执行方案，不能替代 Windows 正向 GSS、长调用期间 Qt 心跳／关闭、原生 SSPI 正向、桌面验收。

## 1. 依赖选择和准确提取方案

MIT 官方分发页当前仍列 KfW 4.1，提供 x64 MSI；其发行说明说明基于 krb5 1.13 系列。不要把 Unix 最新版本当作 Windows 便携二进制版本。[MIT 分发页](https://web.mit.edu/Kerberos/dist/)、[KfW 4.1 说明](https://www.mit.edu/~kerberos/kfw-4.1/kfw-4.1.html)

已真实下载并固定：

- URL：`https://web.mit.edu/Kerberos/dist/kfw/4.1/kfw-4.1-amd64.msi`
- 文件大小：11,071,488 字节。
- SHA256：`cdcb7ec4addd9716c0e0c74fe0944cb97c83bfeebc2c267e63e8cac2ad3dc872`。
- MSI 内嵌 `Disk1` 为 9,892,257 字节的 CAB；其 SHA256 与每个选定成员的大小／SHA256 固定在 `.tmp/windows-gss-portable/kfw-lock.json`。

没有运行 `msiexec`、MSI 自定义操作、服务注册或注册表写入。`prepare_kfw.py` 用 `7z e -so` 读取固定 MSI 的 `Disk1`，再从固定 CAB 读取逐个白名单成员，写为准确运行时文件名；所有输出只在本原型目录。官方 7-Zip 明确支持 MSI/CAB 解包；Windows Server 2022 runner 当前软件清单含 7zip，可复用已有工具，不安装一套 MSYS2。[7-Zip 支持格式](https://www.7-zip.org/)、[runner 官方清单](https://raw.githubusercontent.com/actions/runner-images/main/images/windows/Windows2022-Readme.md)

白名单共 10 个文件、2,960,208 字节：

| 类别 | 文件 |
| --- | --- |
| 正式 GSS 及直接依赖 | `gssapi64.dll`、`krb5_64.dll`、`comerr64.dll`、`k5sprt64.dll`、`wshelp64.dll` |
| MSI 自带 VC2010 运行库 | `msvcr100.dll` |
| 测试用票据工具 | `kinit.exe`、`klist.exe`、`kvno.exe`、`kdestroy.exe` |

不提取 Ticket Manager、MFC、CCAPI 服务器、`ms2mit`、`mit2ms`、登录模块。PE 导入闭包实际检查为 x64 PE32+；非白名单项只允许锁定的 Windows 系统 DLL。额外检查延迟导入：`gssapi64.dll` 为 `ADVAPI32.dll`，`krb5_64.dll` 为 `ADVAPI32.dll`／`Secur32.dll`。不能只看静态普通导入便声称闭包完整。GSS DLL 确实导出正式 PuTTY 所需的函数和探针使用的 `gss_acquire_cred_from`／`gss_accept_sec_context`。

此 SHA256 是本次经官方 HTTPS 下载后固定的内容锁，尚未在 Windows 执行 Authenticode 验证；没有将本机 PE 静态检查说成 Windows loader 通过。MSYS2 的当前 `gss` 包是 GNU GSS／Shishi 路径，本次未找到可验证的官方 MIT 原生 krb5 包，因此没有把它替换为 MIT 实现。

## 2. 文件票据隔离和 GSS ABI

MIT 的 Windows 文档规定环境变量高于注册表，支持 FILE/MEMORY 缓存，MSLSA 则读取 Windows 登录会话缓存。探针显式指定配置、FILE 票据、FILE 服务 keytab、缺失的默认客户端 keytab和私有 replay／临时目录，不触碰 MSLSA/API，不运行个人票据复制工具。[MIT Windows README](https://raw.githubusercontent.com/krb5/krb5/master/src/windows/README)

共同接口固定为 realm `SUITE.TEST`、客户端 `suite@SUITE.TEST`、服务 `host/localhost@SUITE.TEST`、AES256、仅动态 loopback TCP KDC。配置关闭 DNS realm/KDC 寻址、反向解析及主机规范化，强制 TCP。AS 先直接取得仅 TGT 的本次 FILE 缓存；之后 GSS 的真实 TGS 请求通过延迟代理。每轮重新 kinit，不能复用服务票据来绕过同步网络调用。

`mit_gss.py` 的 ABI 依据 MSI 自带正式头文件：`OM_uint32` 固定 32 位，buffer 长度为 `size_t`，OID 长度为 `OM_uint32`，OID set 数量为 `size_t`；x64 下三种结构均 16 字节、指针偏移 8。Windows 使用 `WinDLL` 对应头文件的 `__stdcall`，并限定搜索为 DLL 所在目录和 System32；原生 ABI 探针还逐个检查已装入模块的真实路径及固定字节。Linux 使用正式 `libgssapi_krb5.so.2`，只作跨实现原理验证。

接收端通过 `gss_acquire_cred_from` 的显式 FILE keytab、固定完整服务主体取得 acceptor credential；客户端原型使用显式 FILE ccache和固定完整客户端主体。正式 API 是 import/acquire/init/accept/get_mic/verify_mic/release/delete，所有库输出按所属库释放，拒绝委托凭据和非固定主体；仅凭 token 非空不判断成功。主体验证、相互认证、完整性、上下文寿命和两端最终完成均检查。正式 PuTTY 只请求 mutual／integrity，接收端没有擅自要求它未请求的 sequence 标志。[MIT GSS 开发文档](https://web.mit.edu/kerberos/krb5-1.13/doc/appdev/gssapi.html)

KfW 1.13 不支持现代 `file2:` replay 类型；Windows 后续运行使用 `KRB5RCACHETYPE=dfl` 和本次 `KRB5RCACHEDIR`，并将 `TMP`／`TEMP`／`TMPDIR` 指向本次私有目录。配置内 Windows 路径使用 `/`，显式身份存储均使用 `FILE:` 前缀，避免将驱动器冒号误解成缓存类型。所有秘密文件仍须由现有 `PrivateData` 在 Windows 创建、固定祖先并核验受保护两 SID DACL；本研究原型未取代这项要求。

## 3. SSH 接收端选择及真实上游错误

仓库现有公钥对端固定 Paramiko 5.0.0。其官方变更记录及本机源码均确认已经删除 GSSAPI，不能只增加一个 ServerInterface 回调就宣称支持。没有修改或降级原有 venv。[Paramiko 官方变更](https://github.com/paramiko/paramiko/blob/main/sites/www/changelog.rst)

本原型另建 `.tmp/windows-gss-portable/venv`，固定 `paramiko==4.0.0`；依赖版本写入独立 `requirements.txt`；另已真实下载全部 7 个 Python 3.11 Windows x64 wheel，并生成 `requirements-windows.txt` 的逐 wheel SHA256，后续用 `--require-hashes` 安装。下载成功不代表 Windows 执行成功。`paramiko4_gss.py` 仅替换测试进程内正式 AuthHandler 的 GSS 提供方构造入口与其异常类型集合，并锁定 `auth_handler.py`、`transport.py`、`ssh_gss.py` 原始 SHA256；没有复制/改写认证分支。接受端检查真实 MIT 返回的主体等于固定客户端，并把 SSH MIC 绑定到实际 SSH session id、固定用户名、服务和认证方法，弥补“框架回调成功就默认授权”的不足。[固定 4.0.0 正式认证源码](https://raw.githubusercontent.com/paramiko/paramiko/4.0.0/paramiko/auth_handler.py)

第一次真实原版 Plink 请求触发确定上游错误：`GssapiWithMicAuthHandler._handler_table` 返回类函数，Transport 按 `handler(m)` 调用时缺少绑定的 `self`。首失败保存于 `paramiko4-first-failure.json`。原型只将该原始函数表的方法绑定到当前实例，保留原 token/MIC 函数、包集合及状态迁移。此绑定修正的确切源码边界和哈希必须在正式接线时成为可审查的小补丁，不能藏成没有锁定边界的全局猴子补丁。

`ssh_probe.py` 只接受一个 loopback 连接、固定 SSH 用户及唯一命令 `suite-gss-probe-v1`，不执行系统命令，成功返回固定内容；其它能力由默认拒绝。复用现有对端的 `_BoundedPacketizer`，未改变现有文件。当前是短探针，不是已接入正式终端的 10 秒 Unicode workload，也未完成未来共享夹具的完整资源压力审查。

## 4. 已执行结果

| 实际检查 | 结果与准确范围 |
| --- | --- |
| KfW 纯解包、大小/哈希、10 个 PE 的 x64/普通/延迟依赖、必需 GSS 导出 | 本机通过；Windows loader 未运行。摘要 `extraction-report.json`。 |
| 6 项原型回归 | 全通过：真实 PE 闭包、错架构拒绝、x64 ABI 布局、严格 OID、SSH MIC 精确绑定、正式方法绑定和恢复。 |
| 正式 MIT KDC → MIT GSS token/MIC | 真相互认证、固定主体、双向 MIC、篡改拒绝；真实 TGS 观察延迟 1.5001 秒，总 1.5043 秒。 |
| 正式 MIT KDC → 原版 Plink → Paramiko 4/MIT 接受端 | 真 `gssapi-with-mic` 登录、固定命令和退出成功；真实 TGS 延迟 1.5001 秒；线程回收。 |
| 便携 Kerberos.NET KDC → MIT GSS 与正式 Plink | 最终 3 个真实 TGS 请求、3 个 TGS 回复，最终纯内存配置复核的延迟分别 1.600106／1.600109／1.600102 秒；0 活动连接、0 worker；KDC 关闭。 |
| 三种拒绝 | 缺 FILE 缓存且禁用默认客户端 keytab、错误 SSH 用户、对真实 MIT 产生的 MIC 篡改后由真实 MIT 验证拒绝；均未取得认证会话/命令，所有线程关闭。 |

原型最终报告分别为 `linux-prototype-report.json`、`dotnet-interop-report.json`，均显式 `windows_verified=false`、`sspi_verified=false`。token/MIC 报告始终 `ssh_login_verified=false`；只有真实 SSH 报告填写该字段。Linux 原版 Plink 位于 `.tmp/build/suite-cli-tests/install/bin/plink`，它是已构建的正式上游程序，不是协议替身。

## 5. 下一轮原生 Windows 最小执行顺序

1. 将 `kfw-lock.json`、`prepare_kfw.py`、`mit_gss.py`、`probe.py` 作为受审查测试文件接入；下载准确 MSI 到项目 `.tmp/`。对 MSI 及每个选定成员核验内容锁，任何不符立即失败，不运行安装器。
2. 用 runner 已有 7zip 做纯提取，再以原生 x64 Python 运行 `probe.py --library <本次目录>/portable/bin/gssapi64.dll --mode abi`。它先以现有 `PrivateData` 创建本次私有配置，清除继承的 KRB5/GSS 环境，再检查真实模块路径、机制入口和 `krb5_cc_default_name` 确实选择本次缺失 FILE 缓存；不获取凭据，结束恢复进程环境并清理私有根。此步骤不需要 Paramiko／.NET／Qt。
3. 独立固定 GSS venv 和 .NET 包锁；Windows 私有目录用现有 `PrivateData` 创建并核验。便携 KDC 必须使用最终的纯内存 `Krb5Config.Default()` 版本；早期 `Krb5Config.Kdc()` 会探测默认系统 KDC 配置，不应沿用。启动前台受控 KDC、生成本次私有 keytab、KfW `kinit -k -t FILE:<本次客户端keytab>` 取得新 TGT；缓存、keytab、日志不可上传。
4. 在真实 Windows 先跑 token/MIC、原版 Plink 真实 SSH 和三拒绝；使用实际 TGS 帧代理延迟并取得实际回复，清理结果独立判断。MIT 成功不填 Microsoft SSPI 字段。
5. 再复用受控终端/hive/PRNG 宿主，明确强制唯一自定义 MIT 提供方及 FILE 缓存，禁止失败后尝试 SSPI 默认身份；接入 10 秒 Unicode 负载与实际 KDC 请求标记，测 50ms GUI 心跳、关闭提交和后台安全清理。Windows 正式配置中的默认提供方回退尚未完成审查，不能直接运行普通产品或 CLI 冒险验证。

## 未完成

Windows DLL 原生加载、KfW 取票、Windows 上 .NET KDC/MIT/Plink 交叉、正式终端的 Windows 长 GSS 心跳/关闭均未执行；SSPI 正向仍是独立技术门。原型无需也没有修改系统服务、域配置或注册表。原型源码仍在 `.tmp/`，不能当作已纳入仓库 CI 的长期维护夹具。
