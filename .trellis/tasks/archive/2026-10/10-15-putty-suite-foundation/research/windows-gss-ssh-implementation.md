# 正式 MIT GSS 与 Paramiko 4 SSH 实施记录

## 责任和边界

本轮只拥有 `tools/suite/windows/gss_ssh/` 及本记录，不改公共 PrivateData、MIT 无凭据探针、KDC、Paramiko 5 对端、产品、CMake 或 Actions。Windows 原版 Plink 未有专用 hive 隔离，因此入口硬拒 Windows `--plink`，Windows 报告保持 `formal_plink_verified=false`、`sspi_verified=false`。

## 已实现

- 固定七个 Python 3.11 Windows x64 wheel 的文件名、大小和 SHA256，以 `--require-hashes` 下载/离线安装至新独立 venv，拒绝额外文件。
- 固定 Paramiko 4.0.0 的三个正式认证源文件、可审阅 unified diff 和补丁后哈希。显式小补丁只修复 GSS 处理表原函数绑定及提供方导入，认证 token/MIC 状态机不重写，不运行时猴补，不影响 Paramiko 5。
- 每场景新 child，首次 MIT 加载前由 CreateProcess/exec 传入封闭环境。Windows复用六 DLL 受限加载与真实句柄路径检查；配置/cache 默认名由真实 getter 再核，凭据显式使用 FILE cache/keytab，默认客户端 keytab 缺失。
- 真实 Kerberos token/MIC、MUTUAL/INTEG、固定 client/service、无 DELEG/委托凭据；不声称执行带有效身份的实际委托负例。正式 KDC Forwardable 能力位局限沿其 README准确表述。
- 固定回环 GSS SSH peer：两个活动/16 累计连接、64KiB 窗口、32KiB 包、4096 输入、512KiB 输出、1024 banner、256KiB 原始包、25秒连接期限、所有线程共享三秒关闭预算。仍有原生线程存活时，以隔离进程 125 失败退出，不能先释放其上下文；原生过程整体45秒、外部管理50秒、总命令180秒硬期限。
- token/MIC、SSH成功、缺cache、错SSH用户、篡改MIC五场景独立进程；Linux可追加已证明存储隔离的正式 Plink。短命令固定输出精确，正常 EOF/exit-status/通道 CLOSE 完整。未来十秒 Unicode 常量仅复用，不算本轮长负载证据。
- replay 始终启用；KfW 4.1 显式 `rcache=dfl:suite-gss-replay`，Linux新dfl为 `krb5_<uid>.rcache2`，仅登记确切完整路径。未知文件不自动吸收、不删除。敏感运行资料交正式 KDC 的已知产物清理。
- 先 fsync/原子发布失败占位；输出仅白名单版本、SHA、数值、计数和布尔。原生加载、清理和最终报告挂起均有实际子进程 124 回归；旧成功不会沿用。

## 真实证据

2026-10-15，本机 Arch x64 / Python3.14.8 / 系统 MIT / 固定 Paramiko4 独立环境，正式 KDC准备根 `.tmp/windows-kdc-formal/tools`：

```text
.tmp/windows-gss-portable/venv/bin/python -B -m unittest discover -s tools/suite/windows/gss_ssh/tests -v
.tmp/windows-gss-portable/venv/bin/python -B -m tools.suite.windows.gss_ssh.run --prepared-kdc .tmp/windows-kdc-formal/tools --parent .tmp/windows-gss-formal/runs --linux-library /usr/lib/libgssapi_krb5.so.2 --kinit /usr/bin/kinit --plink .tmp/build/suite-integrated-all/suite-cli/plink --allow-linux --report .tmp/windows-gss-formal/real-report.json
```

15 回归零跳过；真实六场景退出0，AS12/TGS5，TGS实际延迟约1.6001秒。token/MIC双方完成且无DELEG，三个拒绝均有原生凭据申请/用户名分支/MIC验证的独立计数；正式Plink精确输出且退出0。原生对象、peer线程、KDC/代理socket/worker均为零，本次私有根删除成功。报告在 `.tmp/windows-gss-formal/real-report.json`，只上传该公开摘要，不上传运行目录。

最初普通沙箱拒绝回环socket，明确失败，没有跳过或伪造结果；批准的网络权限重跑后真实通过。附加Plink最初认证与命令都完成但退出1，原因是peer先关闭整条transport，缺少通道CLOSE。补齐原生SSH正常关闭后真实退出0，整套结果重新取得。

固定 KfW 4.1 官方源码 commit `f193ed92458e4cde147aef4c31d24570be30e83b` 的 `acquire_cred.c`/`rc_dfl.c`/`rc_io.c`确认显式rcache和KRB5RCACHEDIR规则；Linux `krb5-1.22-final` rc_dfl.c确认uid命名。只读源文在 `.tmp/windows-gss-formal/source/`。Context7官方Paramiko包查询未提供这几个稀有API条目，因此最终以已锁定官方wheel源文和真实互操作为准。

## 未执行项

Windows七wheel准备、实际MIT GSS token/MIC和SSH、KfW replay实际命名/ACL/删除、目标Ubuntu、十秒GUI心跳及计算中关闭、Windows原版CLI/hive和SSPI正向认证均不因本机成功变为通过。Windows后续由主会话CI接线和独立check补证；不提前完成foundation或父任务。

旧 .NET 原型曾新增五个个人store文件并刷新一个原有CRL；五个新增已精确删除，旧CRL原字节无备份无法恢复。后续正式隔离成功不抵销这次已披露事故，也不能写成个人环境从未改变。

## 独立检查补充

独立检查自修完整实际子进程环境核对、严格 JSON 与场景计数校验、标准输出刷新后才提交最终文件的顺序，以及 CI 使用的隔离绝对脚本入口。22 项回归加入重复字段、非有限数值、布尔/整数混淆、u32 越界、错误阶段、缺少原生发起端、额外环境变量、私有根内错误配置路径、真实 stdout 挂起和 `-I -B` 绝对脚本导入；详见 `windows-gss-ssh-review.md`。入口帮助仅计导入证据，不计认证通过。

修复后使用同一固定依赖重跑真实六场景，退出零，AS12/TGS5、实际延迟1.6001416219996827秒，全部原生对象、线程、socket和worker为零，私有根已删除。独立公开报告为 `.tmp/windows-gss-formal/review-report.json`，仍只构成本机 Linux 补充证据。
