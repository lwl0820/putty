# 共享 PuTTY 运行时

`putty-runtime` 把固定的 `suite-putty` 完整核心接入 Qt 事件循环，不链接 PuTTYgen 的 `pg-putty`。公开头文件不暴露上游结构；终端及 Pageant 的 C 模块可以在其实现文件包含上游头。

## 所有权与调用

- `Runtime` 是 GUI 门面，构造时复制模块选项。模块 `create/command/drained/destroy` 均在其唯一核心工作线程运行。进程已有活动核心时，第二个运行时以 `PR_BUSY` 失败并异步关闭。
- 使用客户端 PRNG 的模块在 `create/destroy` 内各调用一次 `random_ref/random_unref`，生命周期覆盖模块内全部会话。运行时不直接引用任何 `random_*` 符号；Pageant 保留原版禁止随机调用的断言及相应噪声策略，不能同时拉入客户端 PRNG。
- C 模块通过 `pr_core_emit` 发布公开状态，输入、事件载荷按字节传递。文本编码由产品 C 适配声明，不能把协议字节统一解码为 UTF-8。事件不得携带口令或私钥。
- 命令包含代次、对象及请求标识；旧代次直接失败。`REPLY` 还核对登记的对象，成功取走一次性请求后重复回复返回 `PR_STALE`。关闭时对未回复请求执行取消器。
- `submit` 有命令数和字节数上限，满载返回 `PR_BACKPRESSURE`。调用方保留尚未接收的输入，并在 `capacityAvailable` 后重试；不把失败当作命令已执行。
- `pr_core_emit` 有事件数及字节数上限，满载返回 `PR_BACKPRESSURE`。宿主在复制载荷前检查总额和剩余额度，检查、复制及入队持有同一把锁；被拒绝的载荷不会被读取。模块必须保留数据、冻结生产者或合并可替代的完整快照；`drained` 通知其重试。不能丢失协议输入、请求或最终状态。事件队列为内部完成事件保留一个位置。
- 默认命令上限 128 项／1 MiB，事件上限 128 项／4 MiB，回调每轮最多 32 个；可以按实际产品帧大小调整有界容量。`commandHighWater/eventHighWater` 提供实测高水位。每个核心的宿主回调／计时包装器最多 4096 个，一次性请求最多 128 个，注册失败返回 0。

## 事件与清理

Unix 以 `QSocketNotifier` 跟随 `uxsel` 注册变更，每次注册有独立编号。就绪分批交付，先检查异常，再读、再写；每一步重查注册，旧 fd 通知不会交付给复用后的对象。

Windows 保留原版 `select-gui.c` 的隐藏消息窗口、`WSAAsyncSelect`、顶层回调及 moribund socket 过滤。HANDLE 使用独立 `QWinEventNotifier`，上游每次等待注册分配进程内不复用的身份。快照、C 边界及 Qt 通知器同时携带 HANDLE 值和注册身份，撤销后新注册即使复用同值 HANDLE，也会更换通知器；迟到激活在 C 边界返回 `PR_STALE`。上游快照也在访问回调前核对当前注册，不能解引用已经释放的等待项。此桥不将 HANDLE 当作 fd，也不轮询等待集合；原版等待集合仍保留最多 64 项的明确断言。

顶层回调按预算让出事件循环，真实上游绝对 tick 转为单次 Qt 定时器，使用平台 `unsigned long/long` 宽度处理回绕。取消的普通回调立即回收；取消的定时包装器保留至原期限后回收，避免上下文地址复用使旧计时器复活。

`close` 立即拒绝新输入，异步等待当前核心调用结束。GUI 不阻塞 join、不强制终止线程。随后停止外部通知，取消请求，调用模块析构，最后再次清理模块／宿主回调、计时器与隐藏窗口消息，再释放核心所有权。模块负责先释放其 Backend、Terminal、Socket 等上游子对象；析构期间不能排队引用其它已释放子对象的回调。C `begin_close` 可从回调发起，`destroy` 仅允许在顶层安全点调用，运行时以断言保护这一契约。门面提前析构时后台清理仍持有独立状态直至完成；应用退出流程须保持主事件循环，直到收到 `closed`，再退出进程。

`modalfatalbox` 输出诊断并中止不可恢复的进程错误；`nonfatal` 发布结构化平台错误并保留标准错误诊断，不把存储失败投影为成功。Unix 默认值采用上游 `main-gtk-application` 相同的非 Xresources 路径，持久会话仍由原版 storage 负责。

## 验证

```bash
cmake -S . -B .tmp/build/suite-runtime -G Ninja -DCMAKE_BUILD_TYPE=Debug -DPUTTY_BUILD_PUTTYGEN=OFF -DPUTTY_BUILD_RUNTIME=ON
cmake --build .tmp/build/suite-runtime --target putty-runtime-tests suite-handle-wait-tests --parallel
ctest --test-dir .tmp/build/suite-runtime --output-on-failure -R '^runtime-(lifecycle|handle-registration)$'
```

`runtime-lifecycle` 先通过公共隔离夹具逐进程验证完整 PuTTY/XDG/TMP 覆盖及代理清除，再在源根 `.tmp/runtime-tests-<构建路径哈希>/` 的 0700 私有目录运行；直接运行测试二进制会拒绝未验证环境。实际使用回环 TCP 服务、完整字节比对、持续输出背压、注销后连接复用、真实上游计时与顶层回调、请求误路由／重复回复、析构末尾排队、回调中关闭、耗时操作期间 GUI 心跳及门面提前销毁。超限探针从不可读页发布超过总额和剩余额度的载荷，要求返回背压且不触碰内存。

Unix 的 `runtime-handle-registration` 用窄平台类型适配编译真实 `windows/handle-wait.c` 及生产声明，使用真实上游树结构；覆盖普通激活、撤销后旧快照、同值 HANDLE／树索引复用、同快照其它有效等待项及回调内自注销。它验证等待注册算法，不能代替 Windows 原生 Qt 激活排队、自动／手动复位事件或 HWND 验收。

Windows 的 `runtime-lifecycle` 在未取得已验证的测试身份／注册表与随机种子隔离时返回 77，CTest 明确标记跳过。另有两个不启用存储或客户端 PRNG 的真实 Win32 宿主，分别验证 HANDLE 等待与 Qt／HWND 网络事件；接线、严格结果核对和待验范围见 [Windows CI 说明](../../tools/suite/windows/README.md)。新增工作流尚待实际执行，不能把跳过或 Linux 算法检查当作 Windows 通过。真实 GSS/KDF、产品终端绘制及桌面验收由产品纵切另行记录。
