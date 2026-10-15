# 终端纵切的帧、输入与认证契约

## 1. 范围与触发

适用于 `apps/putty/src/core/pt_terminal.*`、`render/cpu_renderer.*`、`app/terminal_controller.*` 和 `ui/terminal_window.*`。这些是已实现的最小技术纵切，完整配置、IME、鼠标、剪贴板等按任务后续实施。Linux Qt6.12 及 Ubuntu22.04/24.04、Qt6.8.3 离屏已验；Windows 原生、X11/Wayland 桌面证据必须另记，不能由离屏结果推定。

## 2. 接口与载荷

```cpp
pr_status TerminalController::sendInput(const QByteArray &input);
pr_status TerminalController::sendKey(pt_key key);
pr_status TerminalController::configure(pt_render_config config);
pr_status TerminalController::reply(uint64_t request, int answer);
void TerminalController::close();
```

`pt_terminal.h` 是自有 C 边界。`pt_options` 仅在进程内携带固定绘制入口，不能持久化；`pt_connection` 携带固定容量的 UTF-8 字段。`pt_packet` 包含 magic、type、render_generation、尺寸／状态参数、length 和 revision，尾随恰好 length 字节。跨线程消息不含 Terminal、Conf、Backend、QFont 或私钥对象。

密钥路径经 `pt_filename_from_utf8` 转为正式 Filename：Windows 使用上游 `filename_from_utf8`，Unix 保留原路径字节构造。不能把 Qt 的 UTF-8 路径交给 Windows 按 ANSI 代码页解释的 `filename_from_str`。原生平台用例核对中文及空格路径的宽字符结果，真实认证另用同类路径读出实际 PPK。

## 3. 所有权、顺序和容量

- 唯一核心线程拥有 Terminal、Ldisc、Backend、Conf、客户端 PRNG、QFont 和可写 QImage。GUI 只消费独立完整帧与公开状态；禁止同步调用 GUI 字宽或等待 GUI 返回。
- 生产与消费都按最多 `4 * 1024 * 1024` 总像素验证帧，不能再加不一致的4096单边限制。消费者同时检查正尺寸、RGBA8888的 `stride == width * 4`、总载荷字节和 DPR1000–3000。字体／DPI配置带严格递增代次；过期帧丢弃，旧图像快照必须保持不可变。
- 核心命令最多16项，事件最多4项且合计32MiB。渲染拥塞只保留最新完整帧；一般状态按类型有界合并，一次性确认由 request 身份管理。
- 文本与方向键共用操作队列，保留调用顺序。文本每次发送最多4096字节，相邻待发文本可合并；总输入最多64KiB，操作数量最多256项（计入在途操作）。单项等待完成回执，核心背压时保留原内容并重试；GUI 容量拒绝必须可观察。关闭清零待发及在途敏感字节，随后输入返回 `PR_CLOSING`。
- `renderRequest_`／`inputRequest_` 只有非零才表示在途。OPEN、GET_SCREEN等无请求号命令的完成事件可能为0，不能以0匹配并提交尚未设置的配置。
- 主机密钥、弱算法及日志确认使用核心请求号；拒绝或关闭只完成／撤销一次。同步 PPK 解密、GSS 网络调用仍在核心线程，GUI 保持事件循环，关闭不等待计算完成。
- GSS MIC 输出先清零，只有成功且非空才写包；包复制后用所属库 `free_mic` 释放。失败输出即使带缓冲也须归还，两种调用者均明确终止，不能把空包入队。

## 4. 错误矩阵

| 状态 | 结果 |
| --- | --- |
| 输入超过字节／操作限额 | `PR_BACKPRESSURE`，错误信号可见，不能声称已接受 |
| 核心队列临时满 | 已接受的前端输入保留原序重试 |
| 关闭后的文本／方向键 | `PR_CLOSING`，不再入队 |
| 字体配置代次不增／已有配置在途 | `PR_STALE`／`PR_BUSY` |
| 帧载荷或尺寸非法 | 拒绝并发出错误，不构造越界图像 |
| MIC 失败、空成功、SSPI签名失败 | 不生成认证包，归还已分配缓冲并终止认证 |

## 5. 正常、基线与错误案例

正常：真实 PPK 解密时核心命令满，256项混合文本／方向键仍按序到达 SSH 对端。基线：OPEN完成后字体代次保持1，修改DPI到2后GET_SCREEN不能改回0。错误：240列高DPI图像总像素合法，却因宽度超过4096被消费者拒绝。

## 6. 必需验证

终端标签必须包含真实 SSH／加密 Argon2id PPK、真实 KDC／GSS 延迟与关闭、宽帧、MIC错误及报告路径回归。真实输入在对端逐字节比较，不能只看本地队列；不可变帧直接保留实际共享快照再比像素，不能先深复制测试自己。50ms GUI 心跳最大间隔须符合任务250ms阈值；关闭调用与后台安全清理分别计时。ASan／UBSan及默认LSan保持开启，不用抑制掩盖关闭队列或MIC泄漏。

按目标毫秒数校准 Argon2id 的测试密钥，必须使用与被测客户端相同目标系统和构建配置的 keygen。不能用宿主 Release 工具校准后让容器内 Debug 客户端解密：优化差异会把正常同步 KDF 放大到服务超时。保留首次失败记录，使用匹配工具重新生成夹具并保持心跳与认证阈值；不得只抬高超时来掩盖环境失配。

`PUTTY_TERMINAL_ISOLATED_ROOT` 和报告实际解析路径均限定项目 `.tmp/`。Windows逐进程存储隔离未接入前不执行原存储初始化；SSPI窄调用夹具只证明生产错误分支，不冒充原生系统验证。各目标 Qt／系统／桌面结果分别记录，技术门未齐不得铺完整配置界面。

Windows Unicode 绘图适配只在配置副本初始化，不能改变原 Conf。解除 GDI 字形标签时仍须保留 `VT_XWINDOWS`／`VT_OEMANSI` 的系统 OEM SCO 表；`VT_OEMONLY` 先走正式上游 CP437 字形／ACP 输入分支，再还原标签。不能通过统一强制 `VT_UNICODE` 改掉字符集语义。原生平台回归必须覆盖与 CP437 不同的 OEM 表；CPU 渲染器独立用例不链接上游存储或 PRNG。

## 7. 错误与正确做法

错误：方向键直接 `runtime.submit()` 并忽略失败；`if (event.request == renderRequest_)` 在两个值均0时把未初始化配置提交。

正确：文本／方向键共同排队，由非零请求号对应回执推进；使用 `if (renderRequest_ && event.request == renderRequest_)` 后才修改当前配置。
