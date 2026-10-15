# 正式 KDC 夹具独立检查

日期：2026-10-15。检查范围为 `tools/suite/windows/gss_kdc/`；已读本任务检查上下文、PRD、设计、实施计划、Windows 夹具规范及实施研究。没有修改 MIT、GSS SSH、公共 PrivateData、工作流或产品代码，没有提交代码。

## 已修复

1. `verify.py` 原先通过公共 `write_report` 直接覆盖公开报告，可能留下半份 JSON。现使用本目录 `publish_report`：新文件独占创建、限制体积、flush/fsync 后同目录原子替换；开始前先提交失败占位。回归验证替换前旧文件完整、新文件完整，最终提交失败仍保留失败占位且无临时报告残留。
2. `PrivateTree.remove` 原先验证普通私有树后递归删除，不能拒绝未知文件。现登记夹具创建的确切文件/目录，cache 和 trace 在执行 MIT 前登记；预检发现未知内容会保留资料并失败。Unix 按目录 fd 定点删除登记路径，再由内核非空目录检查拒绝晚到内容；Windows 按已核验固定句柄删除时再次核对登记集合。回归覆盖未知文件、未知目录、预检后新增文件、链接、确切登记与未登记父目录。
3. 经主会话明确批准，新增 `PortableKDC.register_artifact(path, directory=False)`，只供可信调用方按固定依赖源码规则和真实观察登记确切产物；验证私有根、已登记父目录、owner/ACL、普通单链接及非 reparse，目录不递归吸收内容。已与 GSS SSH 实施者确认接口；重放缓存仍须按各固定 MIT 版本实际命名逐项登记，不能禁用重放缓存或把任意 glob 视为已知。README 已同步。
4. `BoundedProcess` 在 Timer 启动失败时会对尚未启动的线程执行 join，中断后续进程和管道回收。现只 join 已启动的 Timer。真实子进程加注入启动失败的回归证明主进程已回收、三个管道均已关闭。
5. `PortableKDC.start` 原先允许重复启动或关闭后再启动，可能覆盖旧子进程所有权，或在已经关闭的实例中留下新进程。现于任何原生启动前拒绝这两种状态；回归核验没有调用子进程创建。

## 复核结论

- 依赖入口按固定官方 URL、SDK SHA512 和六包 SHA256 校验；SDK 成员包含路径、大小写、链接、数量、单文件/总大小及完整既有文件/目录集合检查。运行期重新验证 SDK 官方归档与完整成员，而非信任自签清单；本轮复用已准备的 4704 成员 SDK，未重新下载。
- 构建继续直接调用 SDK 的 MSBuild.dll，离线 feed、锁定还原、独占 NuGet 缓存，禁 workload resolver、共享编译、节点复用和 CLI 首次体验路径。本轮没有改变 C# 源码和原生构建输入，因此未重复构建相同宿主。
- Windows 进程仍以挂起状态创建，加入关闭即杀死后代的 Job 后，公开 Toolhelp API 必须找到唯一初始线程，再恢复一次；失败会终止和关闭 Job，外层继续回收主进程与各管道。此项仅完成源码检查，Linux 不能证明 Windows API 实际行为。
- 流量上限、输出限额、元数据缺换行、后代持管道、共享清理期限和单流关闭失败回归均通过。真实 KDC 使用官方库解析请求、签发票据；八个策略负例精确返回策略错误，仍仅证明入口拒绝，不能声称有效身份委托认证已测试。
- 正常 TGS 允许官方包 TGT 带来的 Forwardable 能力位，AS 显式 `-f` 及实际 Forwarded/Proxy/附加票据/跨域继续拒绝。报告命名未把所有票据能力位误写为 false。

## 本机验证

```text
python -B -m unittest discover -s tools/suite/windows/gss_kdc/tests -v
```

23 项：22 通过，Windows 原生挂起/Job 专项 1 项按平台跳过。Python 3.11 语法兼容 AST：9 文件通过；14 个文本文件空白检查通过。仓库未配置 Python lint/type-check 命令，本机亦没有 ruff/mypy/pyflakes；不能把 AST 当作静态类型检查通过。

再次使用正式 `PreparedKDC`、`PortableKDC`、`verify` 和真实 `/usr/bin/kinit`、`kvno`、`klist` 执行完整闭环。沙箱内宿主未就绪的一次失败已保留本地诊断；随后两次在已授权的回环权限下成功，最后结果为：

- AS 后只有 TGT，真实服务票据与服务 keytab 独立解密成功，kvno=1；TGS 命令耗时 1.669378 秒，代理实际完整读取 TGS 后延迟 1.600088 秒。
- 八个官方编码策略负例、未知 SPN、AS `-f`、跨域、零长/超大/畸形帧和 64 次总连接预算均通过。
- 四个 KDC worker 及代理八个 worker/16 个 socket 预算生效；在途关闭 0.109079 秒，KDC/代理活跃 socket 和 worker 均为零。
- 正常和在途两运行根均按登记清单删除，`private_data_removed=true`。原始报告和只读元数据快照保留于 `.tmp/windows-kdc-formal/verification-*/`，禁止递归上传。
- 本轮前后个人 `.dotnet/.nuget/.aspnet` 的六个既有文件 inode、大小、mtime 和 SHA256 一致；与先前成功轮次快照亦一致。这不抵销原型首次事故：五个新增文件已精确删除，一个原有 CRL 被刷新且旧字节无法恢复的记录继续保留。

## 剩余边界

Windows SDK ZIP、离线 MSBuild、真实 Job/Toolhelp、ACL/句柄删除和固定 KfW 真实 AS/TGS仍待仓库 Windows CI。Windows 10/11 桌面、Microsoft SSPI 正向认证以及终端长 GSS 调用的绘制/关闭也未由本检查完成。现有 Windows 夹具规范已要求原子报告和仅清已知产物，本次修复恢复该契约；无需把尚未执行的 Windows 行为写成已验收惯例。
