# 研究：Windows 私钥 ACL 与 OpenSSH 互操作

- 查询：Windows Actions 的原生 OpenSSH 拒绝继承 `BUILTIN\\Users` 访问权限的导出私钥；核实产品保存边界、Win32 安全描述符和 CRT 句柄移交。
- 范围：内部代码与微软、Win32-OpenSSH 官方资料。
- 日期：2026-10-15。

## 发现

### 本项目文件与模式

- `apps/puttygen/src/core/pg_platform.c:73` 原 Windows `f_open` 忽略 `is_private` 并调用 `_wfopen`，新建文件继承父目录 ACL；该位置是产品修复边界。
- `vendor/putty/sshpubk.c:456`、`:1589` 保存 SSH-1、PPK 私钥均调用 `f_open(...,"wb",true)`。
- `vendor/putty/import.c:1061`、`:1636`、`:2323` 外部私钥保存均使用 `wb`，以保持 Unix 换行。
- `apps/puttygen/src/core/pg_core.c:304`、`:400` 公钥写入使用 `w,false`，不应套用私钥权限。
- `apps/puttygen/tests/core/interop_test.py:63` 直接对导出私钥执行原生 `ssh-keygen -y`；不能在测试中放宽 OpenSSH 或额外修补产品输出 ACL。
- `apps/puttygen/CMakeLists.txt` 和 `apps/puttygen/tests/core/CMakeLists.txt` 分别负责生产、测试自有适配库的 Windows 链接依赖。

### 官方 OpenSSH 规则

Win32-OpenSSH 当前官方源码 `authfile.c:97` 对私钥调用 `check_secure_file_permission(...,0)`。权限检查源码 `w32-sshfileperm.c:95–136` 校验所有者，并检查所有 Allow ACE；非受信身份的访问会失败。当前用户、Administrators 和 SYSTEM 受信，新版另含 TrustedInstaller。采用当前进程 TokenUser 作为所有者，并以该 SID 的单条 Allow ACE 构造 DACL，可满足客户端私钥要求，无需加入普通用户组。

来源：[官方权限说明](https://github.com/PowerShell/Win32-OpenSSH/wiki/Security-protection-of-various-files-in-Win32-OpenSSH)、[官方权限检查源码](https://raw.githubusercontent.com/PowerShell/openssh-portable/latestw_all/contrib/win32/win32compat/w32-sshfileperm.c)、[官方私钥读取源码](https://raw.githubusercontent.com/PowerShell/openssh-portable/latestw_all/authfile.c)。检索分支为 `latestw_all`，不等同于 runner 内置 OpenSSH 的固定源码版本；当前用户单 ACE 方案不依赖新版新增受信身份。

### 可执行实现建议

1. `OpenProcessToken(GetCurrentProcess(),TOKEN_QUERY)`，两次 `GetTokenInformation(TokenUser)` 获取当前用户 SID。构造显式所有者与单 ACE 的非空 DACL，使用 `FILE_ALL_ACCESS`、无 ACE 继承标志；新建描述符设 `SE_DACL_PROTECTED`。
2. 私钥宽字符路径使用 `CreateFileW`，传不继承句柄的 `SECURITY_ATTRIBUTES`。使用 `OPEN_ALWAYS` 或新建/已存在分支，禁止在设置权限前使用 `CREATE_ALWAYS`。请求写权限及 `WRITE_DAC|WRITE_OWNER`，排他句柄保持同一对象。
3. 既有文件会忽略创建时安全描述符，必须按句柄 `SetSecurityInfo`，设置 `OWNER_SECURITY_INFORMATION|DACL_SECURITY_INFORMATION|PROTECTED_DACL_SECURITY_INFORMATION`。失败返回保存错误，旧文件内容保留。不能将空 DACL 指针作为正常 ACL，因为这会允许所有人访问。
4. 权限成功后先完成 `_open_osfhandle`、`_fdopen` 包装，再 `_chsize_s(fd,0)`，最后交付可写流。`_fdopen` 的 `w` 对应 `_O_WRONLY`，包装不另开文件，截断由后续明确步骤负责。
5. `wb` 显式 `_O_BINARY`，`w` 显式 `_O_TEXT`，避免改变私钥换行。`_open_osfhandle` 成功后句柄归描述符，不能再 `CloseHandle`；`_fdopen` 成功后描述符归流，关闭只能 `fclose`；失败按尚未移交的层级关闭。
6. 引入 `aclapi.h`、`io.h`、`fcntl.h` 等声明，生产及测试自有库链接 `advapi32`。`_chsize_s` 为公开 CRT 接口，头文件 `io.h`，失败设置 errno。

来源：[CreateFileW](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-createfilew)、[SetSecurityInfo](https://learn.microsoft.com/en-us/windows/win32/api/aclapi/nf-aclapi-setsecurityinfo)、[安全信息标志及权限](https://learn.microsoft.com/en-us/windows/win32/secauthz/security-information)、[保护 DACL](https://learn.microsoft.com/en-us/windows/win32/api/securitybaseapi/nf-securitybaseapi-setsecuritydescriptorcontrol)、[句柄移交](https://learn.microsoft.com/en-us/cpp/c-runtime-library/reference/open-osfhandle?view=msvc-170)、[文件流移交与模式](https://learn.microsoft.com/en-us/cpp/c-runtime-library/reference/fdopen-wfdopen?view=msvc-170)、[明确截断](https://learn.microsoft.com/en-us/cpp/c-runtime-library/reference/chsize-s?view=msvc-170)。

## 相关规范

- `.trellis/spec/backend/error-handling.md`：文件保存失败必须报告 `PG_ERR_IO`，不伪报成功。
- `.trellis/spec/backend/private-file-permissions.md`：新建与覆盖均设置受保护权限，权限失败不得截断旧内容。
- `.trellis/spec/backend/directory-structure.md`：自有平台适配与 vendor 分离，测试和临时材料放项目 `.tmp/`。

## 注意与未发现项

- 未找到 `_dosmaperr` 的微软公开 API/头文件契约；不要因为 MinGW 能编译就假设 MSVC UCRT 可用，建议最小本地 Win32 错误到 errno 映射。
- `GetVolumeInformationByHandleW` 是 Kernel32 的 Vista 起公开 API，符合目标 Windows 基线；但微软明确该卷管理接口不支持 SMB。强制使用它会拒绝网络共享导出，即使共享具备 ACL，需调整查询策略或明确实际限制。参考：[官方卷信息接口](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-getvolumeinformationbyhandlew)。
- 本次仅研究，没有修改产品代码、没有执行 Windows 原生验证；MSVC、实际 OpenSSH 权限和签名互操作仍由对应 Windows 测试确认。
