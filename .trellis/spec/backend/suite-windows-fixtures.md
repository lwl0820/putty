# Windows 套件夹具与原生证据契约

## 适用范围

适用于 `tools/suite/windows/`、Windows 测试宿主及对应 Actions。临时数据只写项目 `.tmp/`；测试准备、原生 ABI、真实认证、GUI 负载与人工桌面结果分别记录。

## 固定依赖和加载

`gss_mit/kfw-lock.json` 固定官方 MSI、内嵌 CAB、十个成员的长度及 SHA256。准备器只用现有绝对路径的 7z 列清单、按单成员向有界 stdout 提取，不执行安装器。清单拒绝目录、链接、路径跳转、重复和大小写冲突，并核验成员数、展开体积及 x64 PE 的普通和延迟依赖闭包。已有额外文件和不完整输出都应失败。

MIT 的 MSVCR100 与 Python 的 C 运行库可能保存不同的环境副本。原生探针必须是独立进程，在首次加载六个目标 DLL 之前一次性封闭环境；任一目标模块已加载，即使路径相同也拒绝。不能在加载后修改 `os.environ` 并声称另一个运行库已同步，也不能靠恢复 Python 环境声称 DLL 已卸载。按绝对路径及受限搜索标志加载，逐个核验真实模块句柄路径。

## 文件和用户环境

默认测试数据使用 `PrivateData` 的当前用户与 SYSTEM 两 SID 受保护 DACL，核验真实 owner、ACE、句柄路径、reparse 和硬链接，并固定目录祖先。产品导出私钥沿用单用户契约，见 `suite-terminal.md`；不能通过修改其 ACL 掩盖产品缺陷。

子进程只继承明确列出的系统位置；HOME、USERPROFILE、APPDATA、LOCALAPPDATA、XDG、TMP/TEMP 及库配置均指向本次私有根。环境变量是否被依赖实际采用，必须由真实 API 或文件观察证明，不能只检查字典。无凭据探针不设置 KRB5CCNAME，通过私有配置的随机 realm 和缺失 FILE 缓存默认名由真实 getter 读回；不申请凭据、不打开或遍历个人缓存。

只清理本次已知产物；发现未知内容时失败，不递归删除准备好的依赖或用户指定的其它文件。用户目录的既有缓存发生变化必须如实记录，不能把后续隔离成功写成从未发生变化。

## 结果和必需回归

报告只包含公开版本、哈希、计数及具名布尔，不含私有路径、realm 随机值、票据、keytab 或原始认证日志。执行原生代码前先持久化失败占位，完成后原子替换；崩溃或超时不能留下旧成功。外部进程限制输出、执行时间及收尾时间，CLI 原生调用也必须有独立硬期限。

回归至少覆盖篡改内容、清单越界、额外 DLL、预加载模块拒绝、个人环境去除、旧成功替换及准确清理。模拟结构和 Linux PE 检查不能替代 Windows 加载、ACL 和真实 getter。MIT ABI 报告固定区分 `credentials_acquired: false`、`kerberos_authentication_verified: false`、`sspi_verified: false`；负例 SSPI 也不能算正向认证。

Actions 只上传明确的报告／程序白名单，不上传整个 `.tmp/` 或运行根。Windows Server CI 的通过不替代 Windows 10/11 桌面人工验收。
