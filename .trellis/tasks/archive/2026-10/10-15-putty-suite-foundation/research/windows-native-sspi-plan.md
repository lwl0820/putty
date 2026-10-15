# 研究：服务终端任务的 Windows 原生 SSPI 技术门

- 查询：在 Windows Server 2022 托管 CI、无 AD 域和个人凭据、无系统配置修改的条件下，PuTTY 正式 SSPI 路径可自动验证什么；真实 Kerberos 成功、长调用与关闭还需要什么。
- 范围：混合；只读仓库源码和微软、MIT、依赖项目的官方资料，不实施或安装。
- 日期：2026-10-15。
- 归属：Trellis hook 的活动任务为 `10-15-putty-suite-foundation`；经主会话明确协调，将服务于 `10-15-putty-qt-terminal` 的本报告写在基础任务研究目录。

## 发现

### 1. 结论与证据边界

当前 CI 可以立即增加**无默认凭据访问的真实 Windows SSPI 系统调用负例**，验证系统库加载、包信息、非法句柄失败、MIC 输出清零和释放。它有价值，但不能证明 Kerberos 登录成功、有效上下文的签名或长 SSPI 调用期间的终端关闭。

真实 Windows 长 GSS 验证可以使用上游已经支持的 MIT GSSAPI DLL、自有文件票据缓存和受控 KDC；这不要求加入 AD。它验证 Windows 上的 MIT GSS 路径，**不能替代 Microsoft SSPI 的成功认证证据**。在本次“所有夹具文件只在 `.tmp/`、不修改系统域/KDC 配置、不借用现有登录凭据”的限制下，尚未找到能直接套用到当前 runner 的、已验证的原生 SSPI 正向长调用方案。此缺口须继续保留，不改变既定技术门。

研究没有运行 Windows API，也没有生成任何新的通过结果。本文提出的新增测试名、工具和步骤均为待实现方案。

### 2. 文件与现有代码模式

| 文件 | 职责与关键位置 |
| --- | --- |
| `vendor/putty/windows/gss.c` | 正式平台 GSS 选择；208–228 从系统目录加载 `secur32.dll` 并解析真实函数；352–387 获取当前上下文凭据；443–488 同步初始化上下文；587 起生成 MIC。 |
| `vendor/putty/ssh/gssc.c` | 通用 MIT GSSAPI 适配；41–100 获取 Kerberos 凭据，103–148 初始化上下文，236–260 生成、验证和释放 MIC。 |
| `vendor/putty/ssh/gss.h` | GSS 接口及第 38 行 `GSS_NO_EXPIRATION == (time_t)-1`。 |
| `vendor/putty/ssh/userauth2-client.c` | 正式 SSH GSS 认证调用者，约 1420 行调用提供方的 `init_sec_context`。 |
| `vendor/putty/ssh/transport2.c` | GSS 密钥交换和票据过期策略；约 2269 行使用同一提供方接口及过期输出。 |
| `apps/putty/src/core/pt_terminal.c` | 终端核心拥有 Conf、Backend、PRNG；368–395 创建，397–421 启动真实 SSH，513–529 同线程销毁。 |
| `apps/putty/tests/sspi_mic_shim_test.c` | 第 7 行明确声明是窄夹具；29–42 行自行定义查询/签名函数；44 行引入正式函数原文。因此即使在 Windows 运行也没有调用原生 SSPI。 |
| `apps/putty/tests/gss_mic_test.c` | SSH MIC 认证包错误和释放契约；不是 Kerberos 票据或系统提供方认证。 |
| `apps/putty/tests/CMakeLists.txt` | 128–162 行按源码边界提取正式函数，构建 MIC/SSPI 窄夹具；167 行以下真实 KDC 运行器目前限 Linux。此文件有并行实施者，不应在研究中改动。 |
| `apps/putty/tests/terminal_test.cpp` | 284–309 行等真实 KDC 请求标记，再断言关闭调用、后台清理和 50ms GUI 心跳；不是收到某条认证日志就认为同步工作已经开始。 |
| `apps/putty/tests/run_terminal_gss_test.py` | 第 22 行重新取得仅含 TGT 的缓存；43–48 行强制真实 TGS 请求及实际延迟证据。 |
| `tools/suite/gss_fixture.py` | 24–141 行回环 TCP KDC 代理；144–269 行前台 MIT KDC；287–301 行清除服务票据并统计 TGS；325–353 行容器 OpenSSH GSS 对端。当前明确 `require_linux()`，不能直接在 Windows 使用。 |
| `tools/suite/windows/run_ctest.py` | 15–16 行当前四项终端集合含 SSPI **shim**；71–77 行仍保留 `terminal-native-sspi` 未验证。不能仅扩大标签便去掉该缺口。 |
| `.github/workflows/putty-suite.yml` | Windows Server 2022、MSVC、固定 Qt；临时变量和报告在 `.tmp/`；当前 GUI/PPK 接线正并行变化，本文不据其存在推断执行成功。 |
| `apps/putty/tests/windows/guarded_terminal.c` | 正式终端适配只改导出名的受控构建变体；后续真实 GSS 应复用其存储/PRNG 生命周期，不另造产品终端。 |

上游有三个明确提供方：`windows/gss.c:39–48` 的 MIT、SSPI、自定义 DLL。SSPI 在 366–374 行固定请求 `Kerberos`，而非 `Negotiate`；principal 和 `pAuthData` 均为 NULL。微软说明这意味着使用执行安全上下文的默认凭据，且 `AcquireCredentialsHandle` 本身不是网络登录。因此“Acquire 成功”也不等于已取得可用服务票据或远端认证成功。[微软 AcquireCredentialsHandle（Kerberos）](https://learn.microsoft.com/en-us/windows/win32/secauthn/acquirecredentialshandle--kerberos)

### 3. 应先修复的时间输出失败路径

`windows/gss.c:460` 定义未初始化的 `TimeStamp localexp`；464–475 行调用真实 API 后，477 行无条件调用 `localexp_to_exp_lifetime`。该转换在参数非空时会读取 `localexp`。系统调用失败时，不能假设这些输出已经形成有效的上下文过期时间。微软将 `ptsExpiry` 定义为可选输出，并明确上下文/安全属性需依据调用成功状态处理；文档没有承诺失败时填充一个可转换的时间戳。这里是调用方的输出有效性缺口，不能凭某台机器恰好写零来消除。[微软 InitializeSecurityContext（Kerberos）](https://learn.microsoft.com/en-us/windows/win32/secauthn/initializesecuritycontext--kerberos)

主会话提出的最小修复边界合理：

1. 若调用者提供输出，先给 `expiry = GSS_NO_EXPIRATION`、`lifetime = 0` 的确定默认值；保持现有转换函数的默认语义。
2. 仅在该适配已识别的 `SEC_E_OK` 或 `SEC_I_CONTINUE_NEEDED` 返回分支转换时间，不读取其它失败状态的时间输出。
3. 保留原有令牌的所有权、继续协商状态及失败返回方式；不借机换提供方、改为异步 SSPI 或新增取消 API。
4. 使用现有“从正式源码提取函数”的模式建立窄回归：API 返回错误且完全不写 `localexp` 时，转换器调用次数必须为零，输出是确定默认值；两个成功状态应仍转换；两输出均空也合法。该测试只证明正式错误分支，仍标记为窄夹具。
5. 补丁应与 vendor 来源/校验记录同步；等待当前 `tests/CMakeLists.txt` 所有者释放后接线。研究者不修改源码。

注意：上游还会在 acquire 失败后调用 `FreeCredentialsHandle`，并在 release 时调用 `DeleteSecurityContext`。这两处的真实无效句柄返回可记录为后续审查证据；不要把“释放函数被调用”当作“释放了成功取得的凭据/上下文”。本报告没有证明这些调用存在新的安全漏洞。

### 4. 最小立即实施：真实系统负例宿主

建议新增独立 `terminal-windows-sspi-negative`，先独立 CTest 标签 `windows-sspi-negative`，通过后再由主会话有意调整总结果门。不要塞进已有四项集合后顺手把 `terminal-native-sspi` 当成完整通过。

**构建/调用方式：**使用 Windows SDK 的真实 `security.h` 类型；编译正式 `windows/gss.c` 的测试变体或原文提取的完整相关函数，调用地址必须是系统 `secur32.dll` 的真实导出。公开符号需重命名，避免与 `suite-putty` 归档中的相同对象重复。不得用本地 `p_QueryContextAttributesA` / `p_MakeSignature` 实现替代 OS。无需 Qt、网络服务、票据文件或 PRNG；若没有运行完整终端，也不需要创建私有 hive。

最小断言如下：

| 操作 | 必须观察到的实际结果 | 能证明的范围 |
| --- | --- | --- |
| 安全加载系统库并解析当前上游八个 SSPI 导出 | 真实模块路径为系统目录；各必要地址非空 | 系统 ABI、提供方入口存在。 |
| `QuerySecurityPackageInfoA("Kerberos")` | 成功、非空包信息、合理 token 上限；用真实 `FreeContextBuffer` 释放 | Kerberos 包可用。它不获取登录凭据。[微软接口](https://learn.microsoft.com/en-us/windows/win32/api/sspi/nf-sspi-querysecuritypackageinfoa) |
| `SecInvalidateHandle` 初始化的上下文，调用正式 `ssh_sspi_get_mic` | 真实 QueryContextAttributes 失败，输出始终 NULL/0，无分配遗留 | 正式 MIC 的系统失败分支；不能宣称执行过正常 MakeSignature。 |
| 同样明确无效的句柄，直接调用真实 MakeSignature 和 VerifySignature，使用完整合法的缓冲描述 | 返回失败；记录 SDK 符号及实际数值，不照抄网页可能错误的十六进制常量 | 两个 OS 入口确实拒绝无效上下文；没有有效签名。[微软 MakeSignature](https://learn.microsoft.com/en-us/windows/win32/api/sspi/nf-sspi-makesignature)、[VerifySignature](https://learn.microsoft.com/en-us/windows/desktop/api/sspi/nf-sspi-verifysignature) |
| 正式 init 包装器传入明确无效的 **非 NULL** credential handle | 真实 InitializeSecurityContext 失败；修复后的默认时间输出确定；无有效令牌发布 | 原生错误及初始化后的清理。不能改传 NULL handle 去请求隐式默认凭据。 |
| 重复执行并比较进程句柄数；对所有成功取得的包信息显式释放 | 有界、结果稳定，无资源持续增长 | 局部失败宿主资源回收，不代表完整 SSH 认证关闭。 |

非法句柄应使用 SDK 的无效句柄初始化方式；不能构造指向任意无效内存的指针，也不能借已有真实用户的句柄。`GetProcAddress` 地址可由真实透传计数器记录；计数器必须调用原地址且不修改参数、状态或延迟，报告明确其用途。

报告仅写 `.tmp/windows-ci/reports/sspi-negative.json`，含系统/架构、系统库路径、实际状态码、每项断言、调用次数、清理计数及 `kerberos_authentication_verified=false`。不输出 token、票据、用户名、口令或现有缓存内容。CI 超时是宿主失败，不能返回 77 混成通过。这个小宿主应先在当前 Windows CI 实际运行，再谈进一步覆盖。

**可追加的凭据获取负例，不能直接冒进：**可以先单独验证 `ImpersonateAnonymousToken` 成功，然后在同一线程执行正式 acquire；微软确认该 API 使线程使用系统匿名令牌，并以 `RevertToSelf` 取消。[微软匿名令牌 API](https://learn.microsoft.com/en-us/windows/win32/api/securitybaseapi/nf-securitybaseapi-impersonateanonymoustoken) 但它没有规定 `AcquireCredentialsHandle` 必然立即返回哪个状态；有些凭据错误可能推迟到初始化上下文。该前置验证失败必须停止，不可回退原身份。首个最小宿主可完全不调用 acquire，以避免在未证实匿名上下文契约前接触默认凭据。

另一候选是 `LogonUserW(LOGON32_LOGON_NEW_CREDENTIALS, LOGON32_PROVIDER_WINNT50)` 的专用测试凭据会话；微软说明它克隆本地令牌并替换出站凭据，不能据此推断凭据已被 Kerberos 验证。[微软 LogonUserW](https://learn.microsoft.com/en-us/windows/win32/api/winbase/nf-winbase-logonuserw) 这不是最小负例的前提，也不能使用 runner 的现有凭据填空。

### 5. 为什么无票据负例不能填平长调用技术门

真实 SSPI API 对无票据/非法句柄可能立即失败。延迟返回一个本地错误、在函数前插入 sleep、强制等待事件、让 SSH 服务器晚发认证包，都不能证明 Windows Kerberos 服务票据获取在核心线程发生过同步阻塞。SSH 包的异步等待尤其不能代替 KDC 内的同步调用。

完整长调用至少需要：有效测试 TGT、匹配 `host/<目标>` 的服务主体与对端密钥、客户端尚无该服务票据、真实 TGS 请求通过受控延迟点、最终 GSS/Kerberos/MIC 成功，以及相同正式终端的 GUI 心跳和安全关闭。沿用 `terminal_test.cpp:284–309`：请求标记来自代理实际读到网络请求，关闭调用 <25ms，50ms 心跳最大间隔 ≤250ms；关闭提交和安全清理分别计时。一次真实失败也不能伪装为正向成功。

有效 Kerberos 上下文还需要对端处理返回 token；只见 `SEC_I_CONTINUE_NEEDED` 或一个非空 AP-REQ，不能称 SSH GSS 登录完成。微软也说明应按实际最终结果判断上下文建立，并正确释放系统分配的输出。[微软初始化协商过程](https://learn.microsoft.com/en-us/windows/win32/secauthn/initializesecuritycontext--kerberos)

### 6. MIT KfW + 受控 KDC：可用方向及维护成本

上游 `windows/gss.c:234–292` 已有自定义 GSSAPI DLL 的绝对宽字符路径和依赖目录加载，绑定正式 MIT GSS 接口。测试可只选提供方 id=2，指向 `.tmp/` 中锁定的官方 DLL；不要改产品 provider 逻辑或为产品新增 SSH 栈。需要保证其它提供方不会被尝试；只改变偏好排序而未禁用回退不足以证明路径唯一。

MIT 官方分发页本次查询列出 **KfW 4.1** 的 x64 MSI 和源码，发布说明指出它基于 krb5 1.13 系列。本报告没有把 Unix 当前 krb5 版本误当成 KfW 版本，也没有验证该老二进制在 Server 2022 上的结果。[MIT 分发页](https://web.mit.edu/Kerberos/dist/)、[KfW 4.1 说明](https://web.mit.edu/kerberos/www/kfw-4.1/kfw-4.1.html)

MIT 官方 Windows README 规定 `KRB5_CONFIG`、`KRB5CCNAME` 优先于注册表，并支持标准 FILE/MEMORY 缓存；`MSLSA:` 则读取 Windows 登录会话缓存。因此本测试应使用显式 `.tmp/krb5.ini` 和 `FILE:<本次私有目录>/client.ccache`，完全不用 API/MSLSA 默认缓存，也不运行会复制个人票据的 ms2mit。所有子进程都清除继承的 KRB5 变量，再设置本次确定值。[MIT Windows README](https://github.com/krb5/krb5/blob/master/src/windows/README)

固定身份/固定测试密钥可以复用，**固定序列化票据不能作为长期 CI 夹具**：票据有开始/截止时间、服务和会话密钥，缓存已有服务票据还会跳过所需 TGS 延迟。应每次从隔离 KDC 取得新 TGT，并在两种终端场景前刷新为仅有 TGT 的缓存。MIT 的 kinit/缓存文档支持此生命周期。[MIT 用户环境与票据说明](https://web.mit.edu/Kerberos/krb5-latest/doc/user/user_config/kerberos.html)

当前仓库 MIT KDC 是 Linux 前台进程，SSH GSS 对端依赖 Linux 容器。不能只下载 KfW 就认为它们出现在 Windows。最小可维护的推进顺序是：

1. 先完成上节原生负例和时间失败修复；它们不依赖新下载工具。
2. 单独做便携 MIT 客户端能力探针：官方版本/下载和校验锁定；只解包选定库到 `.tmp/`，不运行系统安装、不注册服务、不改 HKLM。验证完整依赖链及 FILE 缓存选择，依赖缺失即明确失败。MSI 的管理安装可能使用系统安装器状态，不能未经审查就当作“纯解包”。
3. 再选并锁定测试 KDC 与 GSS 接收端。一个值得窄原型验证的方案是 **测试专用** Kerberos.NET 前台 KDC，加 MIT 库接收端/SSH GSS 夹具；该项目官方给出了 `KdcServiceListener` 及回环监听样例，能够避免 Windows 系统服务。其示例依赖测试用 realm 实现，不能直接复制称成熟夹具；必须自行限定主体/密钥/加密类型、禁转发及外部 realm，锁包版本和 NuGet 依赖，并通过 MIT 客户端/接收端交叉验证 AS、TGS、相互认证与 MIC。[Kerberos.NET 官方项目与 KDC 样例](https://github.com/dotnet/Kerberos.NET)、[回环样例源码](https://github.com/dotnet/Kerberos.NET/blob/develop/Samples/KerberosKdcHostApp/Program.cs)
4. 对上述真实 TGS TCP 流量使用现有 `_KDCProxy` 的有界原理，按收到实际请求后延迟 ≥1.5 秒；先证明 MIT 客户端能真实取票，再接正式 Windows 终端的 workload/close，保持既定阈值。

第 3 步增加 .NET 测试依赖、realm 实现和 SSH GSS 接收端，工作量明显大于当前公钥 SSH 对端；它不是已经落实的最小补丁。此研究没有挑选其当前 NuGet 版本，也没有宣称样例能直接通过。若选择该方向，需要独立实施与审查，所有 SDK 缓存、包、配置、票据、数据库、私钥及日志都限定 `.tmp/`，敏感文件按本项目 Windows 私有 DACL 契约处理，报告只给计数/布尔/时间摘要。

当前 GitHub 官方 Windows 2022 镜像说明列出 WSLv1 已启用；它并不代表已有可直接使用的 Ubuntu KDC 或 Linux Docker 服务。引入/注册 WSL 分发会改变系统/用户状态，不能在本次约束下直接用安装发行版来绕过隔离要求。[官方 runner 镜像清单](https://github.com/actions/runner-images/blob/main/images/windows/Windows2022-Readme.md?plain=1)

### 7. 原生 SSPI 正向仍需单独证明

MIT `FILE:` 缓存不会自动成为 Microsoft Kerberos 包的默认凭据；两者是不同提供方。KfW 能访问 MSLSA 也不等于可安全向独立 SSPI 会话注入 FILE 缓存，更不等于可仅用 `KRB5_CONFIG` 控制 Windows 系统 KDC 寻址。

微软文档说明 `ksetup /addkdc` 把 realm 映射写入 HKLM，并要求重启后使用；绑定缓存 API 又明确要求 `SeTcbPrivilege`。前者违背本次不改系统配置/不重启的范围，后者不能从“CI 是管理员”推定可用，不应临时提权/改策略。[微软 ksetup](https://learn.microsoft.com/en-us/windows-server/administration/windows-commands/ksetup-addkdc)、[绑定缓存 API](https://learn.microsoft.com/en-us/windows/win32/api/ntsecapi/ns-ntsecapi-kerb_add_binding_cache_entry_request)

系统枚举中虽有 `KerbSubmitTicketMessage`，本次未取得足够官方文档来确认所需 KRB-CRED、独立登录会话、最低权限、目标 SPN/realm 寻址和清理契约。不建议把票据提交当作已解决的捷径。即使未来成功提交**新生成**服务票据，也只解决缓存认证，不能证明该 SSPI 调用真的经过延迟 TGS 请求。[微软消息枚举](https://learn.microsoft.com/en-us/windows/win32/api/ntsecapi/ne-ntsecapi-kerb_protocol_message_type)

因此，原生 SSPI 的正向最终需要一个可控而且实际可寻址的 Kerberos 域/KDC、隔离登录凭据或经审查的票据导入方案。它未必需要 AD，但确实需要有效凭据和正确服务主体。NTLM、Schannel、仅有匿名 acquire、仅有 MIT GSS 成功，均不能填补这个结果字段。当前已知约束不足以直接声明该方案可在现托管 CI 零系统改动地实现；先推进已确定的小宿主和 MIT 便携原型，并继续保留此技术门。

### 8. 相关规范

- `.trellis/spec/backend/suite-terminal.md`：第 3/6 节，唯一核心线程、MIC 输出/所有权、真实 KDC 延迟、GUI 心跳、关闭与后台安全清理分开计时；SSPI 窄夹具不能冒充原生结果。
- `.trellis/spec/backend/directory-structure.md`：组件内放测试，共享夹具放 `tools/suite/`，所有临时产物放 `.tmp/`。
- `.trellis/spec/backend/error-handling.md`：失败不可投影为成功，敏感字节不记录日志，目标系统验证不能互相推定。
- `.trellis/tasks/10-15-putty-qt-terminal/prd.md`：AC2 的 Windows/Linux 长 KDF/GSS 响应性；AC4 的 Win10/11 桌面由用户另行验收。
- `.trellis/tasks/10-15-putty-qt-terminal/design.md`：同步 GSS 不承诺强制取消时限，关闭继续响应并等待安全点；技术纵切未齐不铺完整配置 UI。

## 限制 / 未找到

- 本机 Linux 无 Windows SDK/运行时；没有执行真实 SSPI，也没有 Windows 上的 MIT DLL、KDC 或 GSS SSH 正向结果。
- 研究时并行实施者正在修改 Windows PPK/CI 接线；本文引用的是读取时源码，不把正在生成的测试注册视为已通过证据。
- 未找到支持“仅设置 MIT 环境变量便让 Microsoft Kerberos 包使用项目 FILE 缓存/KDC”的微软契约。
- 没有把失败负例、模拟延迟、NTLM 或第三方 KDC 样例当作既定技术门完成。
- 最小明确下一步：主会话实施时间失败输出修复及原文提取回归；待当前 CMake 所有者释放后新增无默认凭据访问的原生 SSPI 负例宿主，在 Windows CI 取得真实系统结果；MIT 便携客户端/KDC 原型和原生 SSPI 正向另保留待办，不需要再次询问用户环境或开始权限。

## 后续只读核查：隔离登录会话候选

2026-10-15 主会话补查微软当前文档。`KERB_PROTOCOL_MESSAGE_TYPE` 对 `KerbSubmitTicketMessage` 的说明明确：访问另一个登录账户的票据缓存需要 SeTcbPrivilege；官方 windows-rs SDK 投影还公开 `KERB_SUBMIT_TKT_REQUEST` 的 LogonId、Key、KerbCredSize／Offset 布局。这给“由本次受控 KDC 新生成服务票据，仅向本次专用登录会话导入，再验证正式 SSPI 相互认证／MIC”提供进一步研究方向，**并未证明该方案可用或已获准操作个人缓存**。

微软 `LogonUserW` 文档允许 NEW_CREDENTIALS／WINNT50 产生替换出站凭据的登录上下文，但还须真实验证独立 AuthenticationId、线程模拟／子进程归属、LSA 对当前会话的选择、导入格式和完整清理。`LsaConnectUntrusted` 文档本身只承诺非可信连接及查询用途，不能单凭获得句柄就推断票据提交权限。未确定这些前置契约前，不实现导入、不调用任何个人缓存查询、清空或票据转移。

即使未来该候选成功，预缓存服务票据也会绕开真实 TGS 长调用；它只能单独补原生 SSPI 有效上下文证据，不能替代 MIT／SSPI 长调用响应性门。此补记仍为只读研究，没有 Windows 执行结果。

来源：[微软消息类型](https://learn.microsoft.com/en-us/windows/win32/api/ntsecapi/ne-ntsecapi-kerb_protocol_message_type)、[微软 SDK 结构投影](https://microsoft.github.io/windows-docs-rs/doc/windows/Win32/Security/Authentication/Identity/struct.KERB_SUBMIT_TKT_REQUEST.html)、[LogonUserW](https://learn.microsoft.com/en-us/windows/win32/api/winbase/nf-winbase-logonuserw)、[LsaConnectUntrusted](https://learn.microsoft.com/en-us/windows/win32/api/ntsecapi/nf-ntsecapi-lsaconnectuntrusted)。
