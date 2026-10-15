# Windows 原生 MIT 无凭据夹具

本阶段只验证固定 MIT KfW 4.1 x64 的纯提取、实际 DLL 路径、GSS ABI、机制枚举和私有配置选择。它不执行 Kerberos 登录、SSH 登录或 Microsoft SSPI 验证。

## 执行接口

在项目根运行，使用已有的绝对路径 `7z.exe`；不下载或安装解包器。父目录必须位于项目 `.tmp/`，上级应已存在，且没有其它准备产物。准备过程独占创建固定的 `source/`、`work/`、`portable/bin/`，已有输出会失败，不复用半成品。

```powershell
$env:PYTHONIOENCODING = 'utf-8'
python tools/suite/windows/gss_mit/prepare_kfw.py --parent .tmp/windows-gss-mit --sevenzip 'C:\Program Files\7-Zip\7z.exe'
python tools/suite/windows/gss_mit/probe.py --directory .tmp/windows-gss-mit/portable/bin --parent .tmp/windows-gss-mit --report .tmp/windows-ci/reports/gss-mit-abi.json
python -B -m unittest discover -s tools/suite/windows/gss_mit/tests -v
```

准备器可通过 `--archive .tmp/某目录/kfw-4.1-amd64.msi` 使用已有离线文件，仍核验同一固定长度和 SHA256。该模式支持 Linux 进行真实纯提取和 PE 元数据检查；原生探针在非 Windows 上返回失败，不跳过为成功。固定 KfW 使用窄字符文件 API，本阶段要求 ASCII 项目路径。

## 固定来源与纯提取边界

[MIT 官方分发页](https://web.mit.edu/Kerberos/dist/) 当前仍将 [KfW 4.1](https://www.mit.edu/~kerberos/kfw-4.1/kfw-4.1.html) 列为 Windows 发行版。`kfw-lock.json` 固定官方 HTTPS URL、11,071,488 字节 MSI 的 SHA256、内嵌 `Disk1` CAB 及十个文件各自的成员名、长度、SHA256、普通和延迟依赖。Unix 新版 krb5 不等于新版 Windows KfW。

准备器禁止重定向和继承代理配置，有固定网络、输出和解包时限。先验证整个 MSI，再用已有 7z 列目录、把唯一内嵌 CAB 读到有界管道，再核验 CAB 并列目录。两层目录均核验固定成员数和展开体积，拒绝路径跳转、大小写冲突、重复字段、重复成员、目录和链接。每次 `7z e -so` 只指定一个固定成员并禁用通配；7z 不向文件系统写入归档路径。固实 CAB 的解码统计不代表提取了所有成员。

输出恰有十个文件、六个 DLL、2,960,208 字节，包含隔离运行所需的 `msvcr100.dll`。每个 PE 必须是 x64 PE32+，节及数据目录有界、不重叠，普通和延迟导入必须与锁一致，并且只能引用白名单 DLL 或明确列出的 Windows 系统 DLL。输出目录的任何额外文件都会失败。准备器不调用 `msiexec`、安装脚本、注册表、服务或系统安装目录。

Windows 目录和文件复用共同的 `tools/suite/windows/private_data.py`，检查当前用户与 SYSTEM 两 SID 的受保护 DACL、真实 owner、句柄路径、reparse point 和单硬链接，固定目录祖先句柄直至结束。Linux 结构验证使用当前用户独占的目录和排他新文件；它不是 Windows ACL 通过证据。

## 无凭据原生验证

探针必须以独立 Python 进程启动。首次加载 MIT DLL 前，只保留启动程序需要的系统环境变量，删除继承的个人 KRB5、GSS、PATH、代理和追踪配置。`HOME/USERPROFILE/APPDATA/LOCALAPPDATA`、所有列明的 XDG 用户目录、`TMP/TEMP/TMPDIR`、KRB5 配置、缺失 FILE 缓存、缺失服务及客户端 keytab、replay 和插件目录均显式指向本次私有根。

本阶段刻意不设置 `KRB5CCNAME`：私有配置内的 `default_ccache_name` 指向本次缺失的 FILE 文件，让真实 `krb5_cc_default_name()` 证明配置默认值已被采用。`krb5_get_default_realm()` 必须读回本次不可预测的随机 realm，作为同一私有配置确已加载的证据。两者只读取名称或配置字符串，不 resolve、open、遍历或读取任何缓存。探针结束还检查缓存与 keytab 均不存在。

Python 与 KfW 的 MSVCR100 可各自保存 C 运行库环境副本。首次 DLL 加载后的 `os.environ` 修改或恢复不能证明另一个 CRT 已同步，也不能卸载旧环境。本探针在 DLL 加载前一次性完成环境设置，不在已加载 MIT 的进程内切换配置。后续有凭据夹具和终端测试也必须启动新的独立子进程，进程创建时即传入本次固定环境，再加载 MIT。

六个 DLL 仅按绝对路径和 `LOAD_LIBRARY_SEARCH_DLL_LOAD_DIR | LOAD_LIBRARY_SEARCH_SYSTEM32` 装入。加载前六个目标模块必须均未加载；即使同名模块路径一致，也不能证明它的 CRT 在环境封闭后才初始化。加载后逐个核验实际模块句柄路径。三个 GSS 结构必须为 x64 的 16 字节、指针偏移 8；真实机制枚举必须包含 Kerberos OID，并真实释放返回集合。

`mit_gss.py` 仅绑定机制枚举、释放集合、初始化或释放 krb5 配置上下文、读取或释放默认 realm、读取默认缓存名称。它不绑定 acquire、init/accept 安全上下文、缓存打开或 keytab 解析入口。本阶段不携带 Paramiko 依赖。

## 报告和证据边界

两个 CLI 的标准输出均是唯一 JSON；失败只含固定阶段码。原生探针开始即把 `--report` 指定文件替换为失败占位，结束用同目录排他临时文件原子替换，避免崩溃后残留旧成功。结束时只删除本次已知配置、两个空目录及随机运行根，核验根已移除；出现未知内容时失败，不递归清理，也不删除准备好的 bin。可上传的内容仅公开版本、哈希、计数和检查布尔，不含私有根、配置内容、realm 随机值、票据、keytab 或完整日志。只能上传报告文件，不能递归上传准备根。

失败占位可靠落盘后，探针启动 daemon watchdog，使用固定 60 秒总预算覆盖 DLL 加载、ABI/API 调用、运行根清理、最终报告和标准输出刷新。超时直接以固定退出码 124 结束进程，不尝试把挂起的原生调用当成已取消，也不在超时分支写成功或声称已清理。结果尚未提交时，报告保留 `native-abi-incomplete` 失败占位；任何非零进程退出均使 CI 失败。短期限仅供回归调用 Python 入口注入，正式 CLI 没有修改期限的参数或环境开关。

`passed: true` 的 `mit-gss-preparation` 不等于原生加载通过。`mit-gss-abi` 也固定保留 `credentials_acquired: false`、`kerberos_authentication_verified: false`、`sspi_verified: false`。只有 Windows CI 实际运行原生探针后才可报告加载和配置选择已验证；Linux 单元测试或静态 PE 检查不能替代它。
