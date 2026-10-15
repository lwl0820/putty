# 共享运行时实施与验证记录

日期：2026-10-15。实施范围为 `libs/putty-runtime/` 及经主会话授权的 Unix 补充熵、Windows 等待注册补丁；根 CMake、完整核心与 CLI 源集合、公共隔离工具由其它文件所有者负责。此记录不修改任务状态，不表示六组件已经交付。

## 实际变化与接口

- `putty-runtime` 链接独立 `suite-putty`，公开 `core.h` 不透明 C 边界及 `runtime.h` Qt 门面；没有 Qt 控件包含上游结构。
- 一个进程只有一个活动 C 核心，第二个运行时明确以 `PR_BUSY` 失败。模块入口、实际 fd、回调、计时与销毁均在同一工作线程执行。
- 所有命令带运行时代次及对象标识。一次性请求还校验请求对应对象，错对象或重复回复返回 `PR_STALE`；关闭显式取消在途请求。
- Qt 命令和事件队列同时限制项数与字节数。返回背压的输入不表示成功，模块以 `drained` 恢复冻结的数据源；顶层回调按预算执行，跨线程唤醒合并成单个排队项。可查询真实队列高水位。
- Unix 使用真实 `uxsel` 注册配合 Qt 通知器，注册编号防止旧 fd 事件投递给复用对象；就绪时再次探测 OOB，并逐项按异常、读、写顺序核对仍有效的注册。
- Windows 源码保留原版 `select-gui.c` 的 `WSAAsyncSelect` 隐藏 HWND、顶层回调及 moribund socket 过滤。HANDLE 从原版集合动态获取，通过 `QWinEventNotifier` 激活前重新核对，不按 Unix fd 处理，也不以轮询取代注册。
- 关闭先停止外部输入、取消请求，再在安全点调用模块析构，最后清扫析构中新增的模块／宿主回调、计时和桥消息。C 回调可调用 `begin_close`，只能在顶层安全点 `destroy`；Qt 门面始终异步关闭，不阻塞等待或强杀线程。门面提前析构及事件槽立即删除门面均受独立状态和 `QPointer` 保护。
- 运行时不直接引用 `random_*`。客户端模块在 `create/destroy` 内拥有一次 PRNG 引用，覆盖全部会话；Pageant 保留其原版禁止随机读取的安全契约，不能同时链接客户端 PRNG 的另一套 `random_read`。
- `modalfatalbox` 保留不可恢复错误中止语义；`nonfatal` 发结构化错误并保留 stderr，不吞掉持久化失败。Unix 默认值选择上游 `main-gtk-application` 相同的非 Xresources 路径。

## 隔离与上游补丁

初次源码闭包验证发现，原版 Unix `noise_get_heavy` 通过 shell 列出系统临时目录。仅覆盖 PUTTYDIR 不能隔离这一只读访问；初次测试结果不作为最终隔离验收。主会话授权后已将该项补充熵替换为成功取得的进程 ID、父进程 ID、时钟及进程资源元数据。结构先清零；不读取临时目录、不拼接环境变量到 shell。原 32 字节 `/dev/urandom`、进程列表补充熵及保存种子均保留，元数据不被当作系统强随机替代。共享核心及独立 CLI 共用此修正。

差异为 `vendor/patches/0002-project-test-entropy.patch`，来源说明同步至 `vendor/来源.md`，反向补丁检查已通过。

运行时 CTest 通过公共 `IsolationEnvironment` 创建源根 `.tmp/runtime-tests-<构建路径哈希>/` 下的 0700 私有目录，每轮使用独立 UUID；完整覆盖 PuTTY 五项路径、XDG 和 TMP 系列并清除个人代理及 askpass 环境。先运行真实子进程环境探针，随后才运行核心。环境探针本身不冒充上游存储证据；测试另实际写读上游 session，并核对 session 与非空随机种子确实位于私有目录。直接执行二进制而没有隔离运行器标记会在核心初始化前失败。

## 本代理实际验证

本机为 Linux，GCC 16.2.1，Qt 6.12.0。Qt 6.8.3 和目标 Ubuntu 的验证由主会话另行运行，不用本机版本替代目标版本。

```bash
cmake -S . -B .tmp/build/suite-runtime -G Ninja -DCMAKE_BUILD_TYPE=Debug -DPUTTY_BUILD_PUTTYGEN=OFF -DPUTTY_BUILD_RUNTIME=ON
cmake --build .tmp/build/suite-runtime --target putty-runtime-tests --parallel 4
ctest --test-dir .tmp/build/suite-runtime --output-on-failure -R '^runtime-lifecycle$'
```

前轮普通回归 1/1 通过，退出 0，耗时 1.16 秒。构建日志 `.tmp/build/suite-runtime/build.log`，CTest 标准记录在该构建的 `Testing/Temporary/`。受执行沙箱本地监听限制，真实回环 TCP 测试使用自动审批通过的受控提权命令；没有将 socket 禁止误判为产品网络错误。

另使用 `-fsanitize=address,undefined` 配置 `.tmp/build/suite-runtime-asan`，同一回归 1/1 通过，退出 0，耗时 1.26 秒，没有 sanitizer 诊断。构建日志为该目录的 `build.log`。这轮验证针对析构、异步生命周期与缓冲区安全，不代替目标系统验证。

实际测试行为：

1. 第二个活动核心被拒绝，旧代次命令被拒绝，模块调用线程一致。
2. 上游真实顶层回调链跨预算执行，真实上游计时器到期；重复取消回调／计时器无迟到业务回调，tick 回绕差值正确。
3. 请求错对象、正常回复、重复回复及关闭取消走不同路径，不能误接受。
4. 与独立 Qt TCP 服务连接、双向 256 KiB 完整字节比对、消费者暂停时冻结及恢复、队列高水位受限、关闭并重新建立连接、对端断开。
5. Unix 实际 `MSG_OOB` 及前后普通字节顺序，观察到上游 urgent 标记。
6. 核心同步阻塞 300 ms 时 GUI 心跳继续，关闭调用立即返回；排队输入停止接收，安全点完成销毁。
7. 模块析构末尾故意新排队回调和计时器，未在释放后执行；回调中发起关闭、门面先销毁、事件槽删除门面且同批仍有其它事件均通过。
8. 原版 storage 的实际 session 写读及真实 PRNG 种子写入位于验证过的私有目录。

`git diff --check` 及 `git apply --reverse --check --directory=vendor/putty vendor/patches/0002-project-test-entropy.patch` 均通过。

## 追加审查 R1／R2 的修复与验证

独立审查见 `runtime-review.md`。以下修改由主会话确认后实施，审查文件保留原始发现；本节记录修复后的证据。

### R1：复制前限制事件载荷

`Worker::eventHost` 在读取载荷前获取队列锁，按项数、总字节限额及当前剩余额度判断背压；复制和入队继续持有同一把锁。不会为了拒绝过限事件先构造大 `QByteArray`。内部无载荷完成事件沿用预留槽，不改变背压返回语义。

`eventCopyLimit` 配置 64 字节限额，暂停 GUI 消费者后发布 48 字节合法数据，再从 `PROT_NONE` 不可读页分别发布 32 字节和 128 字节：前者只超剩余额度，后者超总限额。两个探针都必须返回 `PR_BACKPRESSURE`，且消费者最终只收到原 48 字节数据。探针在核心线程完成后以原子计数通知测试，再恢复 GUI；旧实现会在拒绝前读取不可读页。普通及 ASan／UBSan 回归均通过此路径。

### R2：HANDLE 等待注册身份贯穿上游与桥

上游 `HandleWait` 每次注册分配进程内不复用的 64 位单调身份。等待快照存树索引及身份，不再存供迟到激活直接解引用的旧对象指针；激活先在当前树查找同索引，核对身份后才访问回调。快照中的其它未变注册仍可正常激活，回调内注销自身后再次激活旧快照也会被拒绝。

公开原生 `add_handle_wait/delete_handle_wait/get_handle_wait_list/handle_wait_activate/handle_wait_list_free` 函数签名保留；声明拆入真实 `windows/handle-wait.h` 并由 `platform.h` 引入，新增头保留上游许可证原文。已搜索原生 `cliloop.c/pageant.c/window.c` 的全部调用点，无需改函数调用方式。差异为 `vendor/patches/0003-handle-wait-registration.patch`，来源说明同步至 `vendor/来源.md`。

运行时 `pr_handle_watch` 同时携带 HANDLE 值及注册身份，C 激活时按两者核对当前注册，旧事件返回 `PR_STALE`。Qt 映射以注册身份为键；同值 HANDLE 的新注册撤销旧通知器并新建通知器。Qt 捕获原注册身份，交付前后均核对；不依赖“Qt 不会排队旧激活”的假设。原版等待集合最多 64 项的断言仍保留，没有静默漏掉第 65 项。

`runtime-handle-registration` 在 Linux 用窄平台类型适配直接编译真实 `windows/handle-wait.c`，包含生产新头并链接真实上游树实现。实际覆盖普通激活、撤销后的旧快照、相同 HANDLE 值及树索引复用、新注册正常激活、同快照其它仍有效项、回调内自注销及重复迟到激活。它没有复制被测算法，也没有用测试结构替代生产声明。

本轮实际命令：

```bash
cmake --build .tmp/build/suite-runtime --target putty-runtime-tests suite-handle-wait-tests --parallel 4
ctest --test-dir .tmp/build/suite-runtime --output-on-failure -R '^runtime-(lifecycle|handle-registration)$'
cmake --build .tmp/build/suite-runtime-asan --target putty-runtime-tests suite-handle-wait-tests --parallel 4
ctest --test-dir .tmp/build/suite-runtime-asan --output-on-failure -R '^runtime-(lifecycle|handle-registration)$'
git diff --check
git apply --reverse --check --directory=vendor/putty vendor/patches/0002-project-test-entropy.patch
git apply --reverse --check --directory=vendor/putty vendor/patches/0003-handle-wait-registration.patch
```

普通 2/2 通过，退出 0，合计 1.13 秒（生命周期 1.12 秒）；ASan＋UBSan 同 2/2 通过，退出 0，合计 1.25 秒（生命周期 1.23 秒、算法 0.01 秒），无 sanitizer 诊断。两个构建均为本机 Qt 6.12.0；构建日志及 CTest 记录在各自构建目录。补丁反向检查及空白检查均退出 0。固定 Qt 6.8.3 的 Ubuntu 集成复验交由主会话另行记录。

此轮证明载荷拒绝前不读取内存及 Windows 等待注册算法复用安全；没有 Windows SDK 或原生运行环境，不能据此声称 Windows Qt 通知器、原生自动／手动复位事件、真实已排队激活或 HWND 关闭路径通过。原生编译、同值句柄复用和事件状态用例仍是明确后续验收项。

## 验收边界与未执行项

| 本任务条目 | 本记录提供的证据 | 尚未覆盖 |
| --- | --- | --- |
| R2、AC2 的 Linux 部分 | 真实回调／timer、TCP read/write/OOB、注销与复用、一次性请求、两阶段销毁及 sanitizer | 原生 Windows socket/HANDLE、所有产品上游子对象的析构覆盖 |
| R3、AC3 的 Linux 部分 | 持续数据、背压与恢复、异步关闭、同线程检查 | 真实 KDF/GSS 的长操作、10 秒产品 GUI 性能门、终端绘制、桌面环境 |
| R4、AC4 的存储隔离部分 | 完整环境探针、上游 session 与种子真实落点、系统临时目录熵扫描修正 | Windows HKCU/随机种子隔离、主机密钥／CA 完整写读、代理 IPC、个人环境快照对照 |
| R1、R5、AC1 | 运行时实际链接完整核心，未依赖 pg-putty | 根构建矩阵、PuTTYgen／Arch、CLI 与发布由其它实施和主会话记录收齐 |

Windows 当前没有可用 SDK/交叉工具链、原生运行或注册表隔离证据，测试入口返回 77 并由 CTest 标记跳过，不能算通过。Linux 真桌面、X11/Wayland、终端和 Pageant 产品行为也未由本测试代替。下游模块须遵守 PRNG 归属、事件背压及上游子对象主动清理契约；后续真实产品联调可能补充新前端策略符号，不把静态归档全量编译当成所有产品均已链接。

Qt 官方 API 核对通过 context7-mcp 的 Qt 6.8 文档完成：[QSocketNotifier](https://doc.qt.io/qt-6.8/qsocketnotifier.html)、[QWinEventNotifier](https://doc.qt.io/qt-6.8/qwineventnotifier.html)、[定时器线程归属](https://doc.qt.io/qt-6.8/timers.html)。这些来源用于实现 API 契约，不替代实际测试。
