# CLI 参数与交互命令来源、验证状态

固定基线为 PuTTY 0.85。下表直接从本仓库解析器的字符串分支提取，不以帮助输出代替完整清单；后端动态参数单列。源码复用表示产品实现保留解析器，不表示每种参数组合已经运行通过。

状态：**实传**为本机受控 OpenSSH/SFTP 或专用容器实际运行；**本地**为版本、帮助、非法/缺参的标准流与退出码；**待验证**为没有专项行为证据。Windows 原生和目标 Ubuntu 的其余场景仍须另补。

主会话已补两目标 Ubuntu 原生 CLI 的 Plink 字节流/退出码、主机密钥拒绝、PSCP 双协议及 PSFTP 批次场景，见 `ubuntu-validation.md`；本表仍按本机逐参数证据标记，不由该有限场景推导全部参数组合通过。

## 通用参数

| 参数 | 适用程序/平台 | 来源 | 当前状态 |
| --- | --- | --- | --- |
| `-load` | 三 CLI | `vendor/putty/cmdline.c:428` | 实传；缺参本地通过 |
| `-v` | 三 CLI | `vendor/putty/cmdline.c:454` | Plink 实传；PSCP/PSFTP 待验证 |
| `-l` | 三 CLI | `vendor/putty/cmdline.c:459` | 实传；缺参本地通过 |
| `-loghost` | 三 CLI | `vendor/putty/cmdline.c:468` | 待验证 |
| `-preconnectcommand` | 三 CLI | `vendor/putty/cmdline.c:474` | 待验证 |
| `-hostkey` | 三 CLI | `vendor/putty/cmdline.c:480` | 实传；缺参本地通过 |
| `-L` | 仅 Plink | `vendor/putty/cmdline.c:495` | 实传 |
| `-R` | 仅 Plink | `vendor/putty/cmdline.c:495` | 待验证 |
| `-D` | 仅 Plink | `vendor/putty/cmdline.c:495` | 实传 |
| `-nc` | 仅 Plink | `vendor/putty/cmdline.c:553` | 实传 |
| `-m` | 仅 Plink | `vendor/putty/cmdline.c:571` | 实传 |
| `-P` | 三 CLI | `vendor/putty/cmdline.c:603` | 实传；缺参本地通过 |
| `-pw` | 三 CLI | `vendor/putty/cmdline.c:609` | 待验证 |
| `-pwfile` | 三 CLI | `vendor/putty/cmdline.c:630` | 缺参本地通过；密码认证待验证 |
| `-agent` | 三 CLI | `vendor/putty/cmdline.c:661` | 待验证 |
| `-pagent` | 三 CLI | `vendor/putty/cmdline.c:661` | 待验证 |
| `-pageant` | 三 CLI | `vendor/putty/cmdline.c:662` | 待验证 |
| `-noagent` | 三 CLI | `vendor/putty/cmdline.c:668` | 实传 |
| `-nopagent` | 三 CLI | `vendor/putty/cmdline.c:668` | 待验证 |
| `-nopageant` | 三 CLI | `vendor/putty/cmdline.c:669` | 待验证 |
| `-no-trivial-auth` | 三 CLI | `vendor/putty/cmdline.c:676` | 实传 |
| `-share` | 三 CLI | `vendor/putty/cmdline.c:683` | 待验证 |
| `-noshare` | 三 CLI | `vendor/putty/cmdline.c:689` | 实传 |
| `-A` | 仅 Plink | `vendor/putty/cmdline.c:695` | 待验证 |
| `-a` | 仅 Plink | `vendor/putty/cmdline.c:701` | 待验证 |
| `-X` | 仅 Plink | `vendor/putty/cmdline.c:708` | 待验证 |
| `-x` | 仅 Plink | `vendor/putty/cmdline.c:714` | 待验证 |
| `-t` | 仅 Plink | `vendor/putty/cmdline.c:721` | 实传 |
| `-T` | 仅 Plink | `vendor/putty/cmdline.c:727` | 实传 |
| `-N` | 仅 Plink | `vendor/putty/cmdline.c:734` | 实传 |
| `-C` | 三 CLI | `vendor/putty/cmdline.c:741` | 待验证 |
| `-1` | 三 CLI | `vendor/putty/cmdline.c:748` | 待验证 |
| `-2` | 三 CLI | `vendor/putty/cmdline.c:754` | 实传 |
| `-i` | 三 CLI | `vendor/putty/cmdline.c:761` | 实传；缺参本地通过 |
| `-cert` | 三 CLI | `vendor/putty/cmdline.c:770` | 待验证 |
| `-4` | 三 CLI | `vendor/putty/cmdline.c:779` | 实传 |
| `-ipv4` | 三 CLI | `vendor/putty/cmdline.c:779` | 待验证 |
| `-6` | 三 CLI | `vendor/putty/cmdline.c:785` | 待验证 |
| `-ipv6` | 三 CLI | `vendor/putty/cmdline.c:785` | 待验证 |
| `-sercfg` | 仅 Plink | `vendor/putty/cmdline.c:791` | 待验证 |
| `-sessionlog` | 仅 Plink | `vendor/putty/cmdline.c:877` | 待验证 |
| `-sshlog` | 三 CLI | `vendor/putty/cmdline.c:888` | 待验证 |
| `-sshrawlog` | 三 CLI | `vendor/putty/cmdline.c:889` | 待验证 |
| `-logoverwrite` | 三 CLI | `vendor/putty/cmdline.c:901` | 待验证 |
| `-logappend` | 三 CLI | `vendor/putty/cmdline.c:908` | 待验证 |
| `-proxycmd` | 三 CLI | `vendor/putty/cmdline.c:915` | 待验证 |
| `-batch` | 三 CLI | `vendor/putty/cmdline.c:923` | 实传 |
| `-legacy-stdio-prompts` | 三 CLI | `vendor/putty/cmdline.c:932` | Linux 原版返回不可用；Windows 待验证 |
| `-legacy_stdio_prompts` | 三 CLI | `vendor/putty/cmdline.c:933` | Linux 原版返回不可用；Windows 待验证 |
| `-legacy-charset-handling` | 三 CLI | `vendor/putty/cmdline.c:941` | Linux 原版返回不可用；Windows 待验证 |
| `-legacy_charset_handling` | 三 CLI | `vendor/putty/cmdline.c:942` | Linux 原版返回不可用；Windows 待验证 |
| `-restrict-acl` | 仅 Windows 三 CLI | `vendor/putty/cmdline.c:954` | 待验证 |
| `-restrict_acl` | 仅 Windows 三 CLI | `vendor/putty/cmdline.c:954` | 待验证 |
| `-restrictacl` | 仅 Windows 三 CLI | `vendor/putty/cmdline.c:955` | 待验证 |

后端选择由 `vendor/putty/cmdline.c:436` 和 `be_list.c:70` 动态决定：

| 参数 | 适用程序 | 状态 |
| --- | --- | --- |
| `-ssh`、`-ssh-connection` | 三 CLI | 默认 SSH 实传；显式选择和裸连接协议待验证 |
| `-telnet`、`-rlogin`、`-supdup`、`-raw`、`-serial` | 仅 Plink | 待验证 |

## 程序独有参数

| 程序/平台 | 参数 | 来源 | 当前状态 |
| --- | --- | --- | --- |
| plink/Linux | `-s` | `vendor/putty/unix/plink.c:744` | 实传 |
| plink/Linux | `-V` | `vendor/putty/unix/plink.c:747` | 本地通过 |
| plink/Linux | `--version` | `vendor/putty/unix/plink.c:747` | 本地通过 |
| plink/Linux | `--help` | `vendor/putty/unix/plink.c:749` | 本地通过 |
| plink/Linux | `-pgpfp` | `vendor/putty/unix/plink.c:752` | 本地通过 |
| plink/Linux | `-o` | `vendor/putty/unix/plink.c:755` | 待验证 |
| plink/Linux | `-shareexists` | `vendor/putty/unix/plink.c:768` | 待验证 |
| plink/Linux | `-fuzznet` | `vendor/putty/unix/plink.c:770` | 待验证 |
| plink/Linux | `-sanitise-stdout` | `vendor/putty/unix/plink.c:773` | 待验证 |
| plink/Linux | `-sanitize-stdout` | `vendor/putty/unix/plink.c:774` | 待验证 |
| plink/Linux | `-no-sanitise-stdout` | `vendor/putty/unix/plink.c:776` | 实传 |
| plink/Linux | `-no-sanitize-stdout` | `vendor/putty/unix/plink.c:777` | 待验证 |
| plink/Linux | `-sanitise-stderr` | `vendor/putty/unix/plink.c:779` | 待验证 |
| plink/Linux | `-sanitize-stderr` | `vendor/putty/unix/plink.c:780` | 待验证 |
| plink/Linux | `-no-sanitise-stderr` | `vendor/putty/unix/plink.c:782` | 实传 |
| plink/Linux | `-no-sanitize-stderr` | `vendor/putty/unix/plink.c:783` | 待验证 |
| plink/Linux | `-no-antispoof` | `vendor/putty/unix/plink.c:785` | 待验证 |
| plink/Windows | `-s` | `vendor/putty/windows/plink.c:348` | 未执行 Windows 原生验证 |
| plink/Windows | `-V` | `vendor/putty/windows/plink.c:351` | 未执行 Windows 原生验证 |
| plink/Windows | `--version` | `vendor/putty/windows/plink.c:351` | 未执行 Windows 原生验证 |
| plink/Windows | `--help` | `vendor/putty/windows/plink.c:353` | 未执行 Windows 原生验证 |
| plink/Windows | `-pgpfp` | `vendor/putty/windows/plink.c:356` | 未执行 Windows 原生验证 |
| plink/Windows | `-shareexists` | `vendor/putty/windows/plink.c:359` | 未执行 Windows 原生验证 |
| plink/Windows | `-sanitise-stdout` | `vendor/putty/windows/plink.c:361` | 未执行 Windows 原生验证 |
| plink/Windows | `-sanitize-stdout` | `vendor/putty/windows/plink.c:362` | 未执行 Windows 原生验证 |
| plink/Windows | `-no-sanitise-stdout` | `vendor/putty/windows/plink.c:364` | 未执行 Windows 原生验证 |
| plink/Windows | `-no-sanitize-stdout` | `vendor/putty/windows/plink.c:365` | 未执行 Windows 原生验证 |
| plink/Windows | `-sanitise-stderr` | `vendor/putty/windows/plink.c:367` | 未执行 Windows 原生验证 |
| plink/Windows | `-sanitize-stderr` | `vendor/putty/windows/plink.c:368` | 未执行 Windows 原生验证 |
| plink/Windows | `-no-sanitise-stderr` | `vendor/putty/windows/plink.c:370` | 未执行 Windows 原生验证 |
| plink/Windows | `-no-sanitize-stderr` | `vendor/putty/windows/plink.c:371` | 未执行 Windows 原生验证 |
| plink/Windows | `-no-antispoof` | `vendor/putty/windows/plink.c:373` | 未执行 Windows 原生验证 |
| pscp/两平台 | `-pgpfp` | `vendor/putty/pscp.c:2303` | 本地通过 |
| pscp/两平台 | `-r` | `vendor/putty/pscp.c:2306` | 实传 |
| pscp/两平台 | `-p` | `vendor/putty/pscp.c:2308` | 实传 |
| pscp/两平台 | `-q` | `vendor/putty/pscp.c:2310` | 实传 |
| pscp/两平台 | `-h` | `vendor/putty/pscp.c:2312` | 本地通过 |
| pscp/两平台 | `-?` | `vendor/putty/pscp.c:2313` | 本地通过 |
| pscp/两平台 | `--help` | `vendor/putty/pscp.c:2314` | 本地通过 |
| pscp/两平台 | `-V` | `vendor/putty/pscp.c:2317` | 本地通过 |
| pscp/两平台 | `--version` | `vendor/putty/pscp.c:2318` | 本地通过 |
| pscp/两平台 | `-ls` | `vendor/putty/pscp.c:2320` | 实传 |
| pscp/两平台 | `-unsafe` | `vendor/putty/pscp.c:2322` | 实传 |
| pscp/两平台 | `-sftp` | `vendor/putty/pscp.c:2324` | 实传 |
| pscp/两平台 | `-scp` | `vendor/putty/pscp.c:2326` | 实传 |
| pscp/两平台 | `-sanitise-stderr` | `vendor/putty/pscp.c:2328` | 待验证 |
| pscp/两平台 | `-no-sanitise-stderr` | `vendor/putty/pscp.c:2330` | 待验证 |
| pscp/两平台 | `--` | `vendor/putty/pscp.c:2332` | 待验证 |
| psftp/两平台 | `-h` | `vendor/putty/psftp.c:2838` | 本地通过 |
| psftp/两平台 | `-?` | `vendor/putty/psftp.c:2839` | 本地通过 |
| psftp/两平台 | `--help` | `vendor/putty/psftp.c:2840` | 本地通过 |
| psftp/两平台 | `-pgpfp` | `vendor/putty/psftp.c:2843` | 本地通过 |
| psftp/两平台 | `-V` | `vendor/putty/psftp.c:2846` | 本地通过 |
| psftp/两平台 | `--version` | `vendor/putty/psftp.c:2847` | 本地通过 |
| psftp/两平台 | `-b` | `vendor/putty/psftp.c:2849` | 实传 |
| psftp/两平台 | `-bc` | `vendor/putty/psftp.c:2853` | 实传 |
| psftp/两平台 | `-be` | `vendor/putty/psftp.c:2855` | 实传 |
| psftp/两平台 | `-sanitise-stderr` | `vendor/putty/psftp.c:2857` | 待验证 |
| psftp/两平台 | `-no-sanitise-stderr` | `vendor/putty/psftp.c:2859` | 待验证 |
| psftp/两平台 | `--` | `vendor/putty/psftp.c:2861` | 待验证 |

## PSFTP 全部交互命令

全部 28 个命令及别名均实际运行 `help 命令`，验证解析及帮助入口；业务状态如下。

| 命令 | 来源 | 当前状态 |
| --- | --- | --- |
| `!` | `vendor/putty/psftp.c:1920` | 帮助通过；本地命令执行待验证 |
| `bye` | `vendor/putty/psftp.c:1927` | 本地退出通过 |
| `cd` | `vendor/putty/psftp.c:1933` | 实传 |
| `chmod` | `vendor/putty/psftp.c:1941` | 实传 |
| `close` | `vendor/putty/psftp.c:1969` | 实传 |
| `del` | `vendor/putty/psftp.c:1977` | 实传 |
| `delete` | `vendor/putty/psftp.c:1983` | 实传 |
| `dir` | `vendor/putty/psftp.c:1986` | 实传 |
| `exit` | `vendor/putty/psftp.c:1996` | 本地退出通过 |
| `get` | `vendor/putty/psftp.c:1999` | 实传 |
| `help` | `vendor/putty/psftp.c:2008` | 本地帮助通过 |
| `lcd` | `vendor/putty/psftp.c:2016` | 实传 |
| `lpwd` | `vendor/putty/psftp.c:2023` | 实传 |
| `ls` | `vendor/putty/psftp.c:2030` | 实传 |
| `mget` | `vendor/putty/psftp.c:2034` | 实传 |
| `mkdir` | `vendor/putty/psftp.c:2043` | 实传 |
| `mput` | `vendor/putty/psftp.c:2049` | 实传 |
| `mv` | `vendor/putty/psftp.c:2058` | 实传 |
| `open` | `vendor/putty/psftp.c:2070` | 实传 |
| `put` | `vendor/putty/psftp.c:2077` | 实传 |
| `pwd` | `vendor/putty/psftp.c:2086` | 实传 |
| `quit` | `vendor/putty/psftp.c:2092` | 本地退出通过 |
| `reget` | `vendor/putty/psftp.c:2096` | 实传 |
| `ren` | `vendor/putty/psftp.c:2105` | 实传 |
| `rename` | `vendor/putty/psftp.c:2109` | 实传 |
| `reput` | `vendor/putty/psftp.c:2113` | 实传 |
| `rm` | `vendor/putty/psftp.c:2122` | 实传 |
| `rmdir` | `vendor/putty/psftp.c:2126` | 实传 |

## 非独立开关的语法与边界

- Plink 的 `user@host`、已保存会话作为主机参数、`telnet:` URL、`协议,主机` 前缀、命令参数拼接见 `cmdline.c:193` 起；本轮验证 `-load` 读取原格式会话的主机、端口、用户名和 PPK，其余特殊写法待验证。
- PSCP 的远端 `[user@]host:路径` 已用于 SCP/SFTP 双向传输；SFTP 中文空格路径直接传 argv，SCP 远端路径中的空格须保留反斜杠供远端 shell 与原版文件名校验解码。
- PSFTP 的双引号和重复双引号转义、空行/注释、命令内 `-r`/`--` 属交互解析器；双引号路径、`-r`、续传和通配符已有实传，重复双引号、注释及 `--` 组合待验证。
- Linux 原版 `console_set_stdio_prompts` 和 `set_legacy_charset_handling` 均返回 false（`unix/console.c:587`、`:595`）；保留此不可用语义，不将这些参数列作 Linux 已实现新能力。
- PSFTP `-b` 命令失败默认返回 2，`-be` 继续并可最终返回 0，已实测；不能统一改成所有失败都非零。
- PSCP `-ls` 的 SFTP 路径必须为目录；通配符下载另测。原生 SCP 默认拒绝服务端通配符改名，会警告并使用请求的字面文件名；另在受信任夹具中显式验证 `-unsafe`，未给其它连接全局开启。
- PSCP 0.85 SFTP 递归上传到不存在目标的行为存在原版限制：`scp_sftp_targetisdir` 为 false 时嵌套文件仍使用目标目录本身；本轮保留源码并验证向已有目录上传，未把该限制修复为新行为。

## 未收齐项

C05/I01/I03 的新旧 Pageant 认证和禁用代理无签名证据、完整 CA/证书/SSH-1/GSSAPI/代理/连接共享、Plink -R/-X/其它协议与串口、PSCP 传输中断、多种参数组合、Windows 原生和两个 Ubuntu 目标系统仍待补齐；当前不完成 CLI 子任务。
