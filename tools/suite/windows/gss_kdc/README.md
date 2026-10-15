# 隔离的真实 Kerberos KDC 夹具

本目录仅用于套件自动验收。正式认证、AS/TGS 解析、AES256 票据和错误响应使用固定的 Kerberos.NET 4.6.168；自有宿主仅提供回环 TCP 的长度帧、固定测试身份、限制策略和生命周期。没有系统 KDC 安装、个人票据缓存或产品运行依赖。

## 准备与运行

在仓库根执行；所有下载、SDK、离线 feed、构建和运行资料仅在项目 `.tmp/`：

```text
python -B tools/suite/windows/gss_kdc/prepare.py --root .tmp/windows-kdc-tools
python -B -c "from tools.suite.windows.gss_kdc.security import PrivateTree; PrivateTree('.tmp/windows-kdc-runs').close()"
python -B tools/suite/windows/gss_kdc/verify.py --tools .tmp/windows-kdc-tools --parent .tmp/windows-kdc-runs --kinit /usr/bin/kinit --kvno /usr/bin/kvno --klist /usr/bin/klist --report .tmp/windows-kdc-reports/real.json
python -B -m unittest discover -s tools/suite/windows/gss_kdc/tests -v
```

Windows 的三个 MIT 工具参数必须使用调用方已核验的绝对路径。`gss_mit` 的下载和原生 ABI 验证另有入口，不能只因本夹具在 Linux 成功就认定 Windows KfW 或 SSPI 已通过。

SDK 固定为 8.0.426 / runtime 8.0.32，Windows/Linux 官方 SHA512、六个 NuGet 归档 SHA256、锁定还原的 contentHash 均在源码中。每次准备都以固定归档复核 SDK 的完整文件、目录、内容及执行权限，不接受额外空目录或自签 SDK 文件清单；包从已核验的本地 feed 还原至新的私有缓存。准备失败会先撤销旧成功状态。

构建直接执行 `dotnet <sdk>/MSBuild.dll`，禁用 workload resolver、共享编译和节点复用，显式使用空外部源的 `NuGet.Config` 与本地 feed。这样绕过 .NET CLI 首次体验及 NuGet 的旧用户目录迁移；Windows KnownFolder 和 Linux 迁移锁的落点不能靠 `LOCALAPPDATA`、`DOTNET_CLI_HOME` 或已失效的 `DOTNET_SKIP_FIRST_TIME_EXPERIENCE` 保证隔离。

## Python 接口

```python
from tools.suite.windows.gss_kdc import PortableKDC, PreparedKDC

prepared = PreparedKDC.from_directory(prepare_root)
with PortableKDC(prepared, parent=private_parent, delay_seconds=1.6) as realm:
    realm.reset_client(absolute_kinit)
    # 子进程启动前一次性传入 realm.environment，之后才加载其 MIT DLL。
    # realm.keytab / client_keytab / cache / config / direct_config 仅在运行期有效。
    # realm.delay_started 只在收到真实 TGS_REQ 后置位。
    ...
summary = realm.report()
```

运行根只清理夹具预先登记的确切文件和目录，发现未知内容会失败并保留资料。依赖在运行时生成重放缓存等产物时，可信调用者须先依据固定依赖源码规则和本次真实观察确认名称，再逐项调用 `realm.register_artifact(path, directory=False)`；目录可用 `directory=True` 登记，但父目录必须已登记，接口不递归接纳其内容。登记会核验实际私有权限、普通文件、单硬链接和非 reparse；Windows 文件沿既有 cache 契约收紧到本次私有 ACL。不能通过通配枚举登记全部未知文件，也不能禁用重放缓存躲过此门。

父目录必须已存在且由调用方核验；夹具创建独占的随机 `kdc-<uuid>` 根。Windows 目录/文件复用公共 `PrivateData` 的 owner、受保护 DACL、非 reparse 和单链接检查，Unix 使用当前 owner 的 0700/0600。环境采用最小 allowlist，HOME、USERPROFILE、AppData、XDG、CLI、NuGet、bundle 和 TMP 都在本次私有根；不修改调用进程环境。禁开发证书、工具 PATH、遥测、诊断、工作负载通知和在线 CRL 更新。

关闭先停代理，再结束 KDC，所有连接和线程共享期限。正常完成后核验并删除本次私有根，`report()` 仅在进程成功退出、资源为零且资料删除成功时返回成功。Windows 删除使用已核验、禁止路径替换的对象句柄；原生行为仍须 CI 验证。失败时终止整个进程树并保留本次私有根供本地诊断，禁止递归上传。Windows 子进程先 `CREATE_SUSPENDED`，加入关闭即杀死后代的 Job，再恢复初始线程；Linux 使用独立进程组。

## 身份、策略和证据边界

realm 为 `SUITE.TEST`，client 为 `suite@SUITE.TEST`，唯一服务为 `host/localhost@SUITE.TEST`。AES256 和 kvno=1；三把公开固定测试密钥沿用研究原型的 SHA256 规则。客户端明确使用本次 keytab、`FILE:` cache、固定 KDC 和私有 replay 目录；禁 DNS、反查、跨域、PKINIT 和证书读取。每轮重新 AS 取票并核对仅有 TGT，避免缓存服务票据绕过真实 TGS。

官方包的 `KrbKdcRep.DefaultFlags` 是内部常量 `Renewable | Forwardable`，AS handler 不按请求移除此能力位。因此 AS 显式 `-f` 被本夹具拒绝，但正常 MIT TGS 可以携带 Forwardable 能力位。该兼容不允许实际 Forwarded、Proxiable、Proxy、附加票据或委托操作，不能报告“所有票据的 forwardable=false”。GSS 调用方还须独立核验无 `DELEG_FLAG`、无 delegated credential。

`verify.py` 使用真实 MIT 工具完成 AS、TGS、服务 keytab 解密、重置和未知 SPN 拒绝；另将官方库编码的八个无认证策略负例经真实 TCP 发送，精确检查 `BADOPTION`/`POLICY`。这些负例证明入口策略拒绝，不是带有效身份的委托认证测试。帧最大 64KiB，KDC 最多四个并发、64次 accept；代理最多八个 worker、16个 socket、64次 accept。只有完整读到真实 TGS_REQ 后才延迟，随后转发真实 KDC 字节。

公开报告只包含版本、布尔、计数、固定公开身份和时间。准备状态、keytab、cache、trace、原始构建输出和用户缓存元数据快照不能上传；验收开始会先持久化 `passed=false` 并原子替换旧公开报告，最终结果同样原子提交，失败不能留下过期成功或半份 JSON。

原型首次运行曾在个人 .NET store 新增五个文件并刷新一个原有 CRL：五个新增文件已按核验清单精确删除；原有 CRL 保留，旧内容没有备份、无法恢复。本目录保留这一边界，后续元数据无变化不抵销首次事故，详见任务研究记录。
