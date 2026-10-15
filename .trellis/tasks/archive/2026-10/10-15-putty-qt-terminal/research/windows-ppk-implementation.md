# Windows 真实 PPK／SSH 纵切实施

日期：2026-10-15。此检查点只补受控测试宿主与隔离能力，尚未取得 Windows 原生执行结果；没有勾选跨平台技术门，没有展开完整配置界面。Windows 原生 SSPI／KDC、Windows10／11 桌面及完整终端功能仍未验。

## 文件与构建边界

- `apps/putty/tests/windows/guarded_terminal.c` 直接包含生产 `pt_terminal.c`，只重命名模块导出；`guarded_module.c` 包裹真实四个入口。控制器、CPU 渲染器、平台适配和终端测试流程继续使用产品代码，没有复制终端或 SSH 算法。
- 独立 `putty-terminal-windows-tests` 使用受控 `storage_variant.c`／`seed_guard.c`；原 `putty-terminal-tests` 在 Windows 仍只编译，不注册真实存储执行。MAP 拒绝生产 `storage` 和原 `pt_terminal` 对象，要求受控模块、真实 settings／noise／sshrand／PRNG 及相关符号。`random_clear` 通过链接保留选项保持可审计符号，未改变正式 `random_unref` 的调用策略。
- `windows/keygen/` 是独立 CMake 子工程，通过 `add_subdirectory(apps/puttygen)` 复用正式 pg-core／pg-putty 构建，关闭 GUI 和核心测试，不复制源清单，也不与 suite-putty 同进程链接。父工程转交编译器、工具链、各配置优化／链接标志、运行库和实际配置。自有 C 入口启用 MSVC `/we4013`，避免未声明函数的隐式整数截断指针。
- helper 使用 pg-platform 的公开 `filename_from_str` UTF-8 契约读取口令；通过 `pg_generate` 生成 Ed25519，再调用正式 `pg_save` 创建中文空格路径 PPK。PPKv3／Argon2id／8192KiB／单线程自动1500ms校准在受测系统和相同配置执行。没有使用固定快速加密夹具或 Linux 校准值替代 Windows 校准。
- 自动毫秒与固定轮数在上游 `ppk_save_parameters` 中共用 union。最初怀疑 pg-core 字段赋值有误，经独立核查及真实保存／解密反证，确认现有映射正确；没有改 pg-core，也没有在测试复制校准算法。累计保存包含多次校准试算，不能将累计保存耗时当作一次解密的预算。

## 全过程 HKCU 映射与线程移交

原 `suite_hive_pause/resume` 会解除全进程 HKCU 映射，不能用它在 QApplication 存活时移交线程。本轮为测试隔离器增加极窄 `suite_hive_release_owner`／`suite_hive_claim_owner`：

1. 原生 bootstrap 在调用任何 Qt 入口前创建私有 hive，配置该 hive 自带的 seed，再释放操作权；HKCU 映射、HKEY、进程槽与固定路径句柄始终保留。
2. 每个唯一核心线程在真实模块 create 前原子 claim，随后 seed guard bind；之后才允许真实 Conf／PRNG 初始化。公共运行时更早的 Windows `sk_init` 只装载 WinSock／建网络树，不调用 Conf、存储或 PRNG，而且 HKCU 此时已经映射。
3. destroy 先委托真实模块。正式 `random_unref` 在最后一引用时保存种子、清 PRNG 并撤销其计时器；wrapper 检查 `random_active == 0`，汇总 seed 统计、解绑 guard 后才 release 操作权。
4. 五个核心全部安全关闭后，主线程在 QApplication 析构前 claim；析构完成后才核验私有主机密钥缓存与读写计数，close 并 cleanup。

owner 使用 Interlocked CAS。claim 先取得操作权，成功后才读取 mapped/key；失败竞争者不会与其它所有者的字段修改竞争。非所有者不能 release／pause／close，双 claim 返回 BUSY。调用方仍负责上下文生命周期，不能并发释放正在传递的上下文。

`hive_test.c` 增加实际原生线程主→工作→主移交，检查错误线程、双 claim、无 owner 和工作线程持有时的 HKCU 随机标记、原始 hive 句柄反查、第二映射及活动 cleanup 拒绝。hiverunner 必需检查由10增为12，增加 `owner-handoff` 和 `continuous-mapping`。这些新增原生断言在本机尚未执行。

## 私有数据与真实对端

hive 根只服务注册表、seed 及系统 hive 日志。PPK、口令、运行目录、离屏图像和结果位于同一私有父目录下的独立 `data-<随机值>` 根，不存入 hive 树。

`private_data.py` 使用原生句柄逐级固定路径，拒绝 reparse、路径别名和文件硬链接，直接以当前用户／SYSTEM 两个 SID 的受保护 DACL 创建目录和口令。PPK 权限由正式 pg-save 设置并独立核验。Qt 生成的结果／PNG 最初只继承该私有目录的两 SID 权限，读取前再在实际文件句柄上设置保护并核验精确 owner／ACE。QSettings 测试默认格式和路径固定到私有 INI 目录；这里的保证是进程 HKCU 映射及本测试数据边界，不声称重定向了全部 Windows／Qt 系统文件读取。

SSH 对端复用 `tools/suite/windows/ssh_peer.py` 的固定 API，仅测试依赖 Paramiko5.0.0，产品继续复用 PuTTY 唯一协议核心。只接受本次 helper 公钥、固定用户 suite 和两个固定命令；不执行 shell 或依赖系统账户。客户端复用原测试五个场景：主机密钥拒绝、10.2秒真实 Unicode 输出／精确输入及高 DPI 不可变帧、KDF 中异步关闭、口令提示取消、KDF 背压期间642字节文本／方向键有序输入。Windows ordered 分支使用固定命令标记。

runner 预先拒绝非 Windows、非专用二进制名、越界／reparse／硬链接路径、源 storage 摘要变化、MAP 缺失或生产对象混入。它验证实际 PPK 参数／公钥身份、helper 与客户端配置／编译器一致、锁定 Qt6.8.3、心跳250ms阈值、KDF≥500ms、关闭调用<25ms、队列限额、两次实际认证／命令完成、五次连接、对端零错误和零存活线程。成功要求五个核心销毁、真实 seed 读写、私有主机缓存反查和全部 hive／数据清理。没有 skip 返回码。

仅写固定 `terminal-ssh.json` 白名单摘要，包含版本、计数、公开性能及二进制摘要。不会复制 PPK、口令、seed、截图或子进程原始日志。失败时保留已创建的私有数据供本机诊断，不自动上传；失败不宣称清理完成。

原生宿主提供固定阶段标记、CHECK 源码行号及异常代码／偏移。runner 只扫描前16KiB／128行，最多选12条已知诊断；不转发任意异常文字、上游日志、模块名、路径、密钥或命令。进程超时也先转成固定说明，防止 Python 默认异常把完整启动命令写入摘要。合同覆盖伪造前缀、截断上限、任意敏感文字及超时／失败两条路径。

## 接线契约

- `terminal-windows-ssh-kdf-render`：120秒 CTest 上限，runner 的 keygen／客户端各30／70秒上限。
- `terminal-windows-ssh-runner-contract`：20秒上限。
- 两者标签 `windows-terminal-ssh`，与已有四项 `windows-terminal` 分开；CTest 正则应使用 `^windows-terminal$`／`^windows-terminal-ssh$`，避免前缀误匹配。
- 新 runner 参数为 `--binary`、`--keygen`、`--parent`，父目录必须是项目 `.tmp` 直接子目录；报告默认 `.tmp/windows-ci/reports/terminal-ssh.json`。Windows CI 的 Python 需安装固定 Paramiko5.0.0。根会话负责工作流及实际 CTest 集合门。

## 本机检查与证据边界

环境为 Arch Linux x64／GCC16.2／Qt6.12，没有 MSVC、Windows SDK、MinGW 或 Wine。

- 新 runner Python 合同11项全部通过，0跳过；hive runner5项、storage runner／schema9项全部通过。它们只证明拒绝／报告逻辑，不证明 Win32 API 或 Windows SSH 通过。
- 独立 helper 在 `.tmp/build/windows-terminal-keygen-local` 以 Release 无 Qt 构建成功。真实生成 PPKv3 Argon2id 8192KiB／单线程自动1500ms，实际377轮；在 Linux 私有隔离环境中由固定上游 puttygen 真实解密，公开身份一致。生成／解密后隔离目录已清理，没有导出口令或私钥。此证据不能替代 Windows 同配置校准和解密。
- 增量构建原 Linux `putty-terminal-tests` 成功；本机 terminal 标签连同根会话新接入的 SSPI 初始化故障 shim 共6/6通过、0跳过、0.39秒。首次只构建终端目标后新增 shim 缺失，补构建该目标再执行完整标签已通过。没有重复已通过的 Linux 容器／GSS／全组件矩阵。
- `git diff --check` 对本轮拥有文件通过。所有新 Windows 专属 C／C++、移交线程断言、ctypes ACL 和真实认证仍待仓库原生 CI／独立检查，不能写成已通过。

生产 UTF-8 私钥路径转换缺口由根会话负责正式修复与平台回归，本测试保留中文 PPK 路径以取得真实端到端证据。完整 SSPI／KDC、Windows 桌面字体／输入法／剪贴板／鼠标、完整配置及生产种子回退没有被本检查点开放。
