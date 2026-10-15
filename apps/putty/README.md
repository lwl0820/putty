# PuTTY Qt 终端纵切

本目录实现首轮技术纵切，可使用真实 SSH、上游终端解析和加密 PPK。它还不是完整 PuTTY 前端；任务检查点保持未完成。

## 构建和运行

```bash
cmake -S . -B .tmp/build/suite-terminal -G Ninja \
  -DCMAKE_BUILD_TYPE=Debug -DPUTTY_BUILD_PUTTYGEN=OFF -DPUTTY_BUILD_PUTTY=ON
cmake --build .tmp/build/suite-terminal --target putty-qt
.tmp/build/suite-terminal/apps/putty/putty-qt --host localhost --port 22 --user 用户名 --key 密钥.ppk
```

Qt 下界为 6.8。当前窗口仅呈现终端和中文连接状态，连接参数来自以上最小命令行；没有完整连接配置窗口。该命令行尚不是原版 PuTTY 参数兼容入口。

## 所有权与线程

`src/core/pt_terminal.c` 拥有上游 Conf、Terminal、Seat、Backend、Ldisc、LogContext 和客户端 PRNG，复用 `putty-runtime` 的唯一核心线程。上游对象和声明不进入 Qt 控件。自有 C 契约只传标量、字节和严格在核心线程执行的绘制入口。

`src/render/cpu_renderer.cpp` 在核心线程创建、使用、销毁 QFont、QFontMetricsF、QTextLayout、QPainter 和 QImage。GUI 只收到完整 RGBA 帧并复制为独立图像。绘制开始前 detach，发送失败只保留最新完整帧；尺寸上限为 240×120 字符且最多 4M 物理像素。字体、DPI、DPR 和字符网格统一以渲染代次更新，旧代帧被丢弃。字宽缓存最多 2048 项，字形簇观测缓存最多 256 项。

GUI 文本与方向键共用按原顺序处理的输入队列，最多 64KiB、256 个排队操作及一个在途分块；相邻文本合并，文本分块最多 4096 字节。每项等待核心回执，核心拥塞时保留并重试；容量拒绝显示错误，关闭明确撤销待发送输入。命令队列最多 16 项／128KiB，事件队列最多 4 项／32MiB。普通状态按类型合并，确认请求保留其唯一请求号。主机密钥、弱算法和日志确认以异步消息完成；关闭后拒绝输入，GUI 不等待线程 join。

## 本轮能力与后续工作

已接入真实 SSH、公私钥认证、上游口令输入、主机密钥确认、字母和方向键、输入法提交文本、真实终端输出、真彩色、组合字符、宽字符和 Qt 字体回退。远端退出、断开、错误、可信状态分别上报；本地可信图形不能由远端文本代替。

下列能力仍待后续步骤：完整键盘模式、IME 预编辑、鼠标、选择、剪贴板、滚动条、打印、铃声、特殊命令菜单、非 UTF-8 标题、窗口尺寸联动、完整字体与平台设置、所有后端的产品入口、共享连接、完整配置描述桥、会话保存和双语资源。未接入的上游窗口策略通过 `PT_UNSUPPORTED` 明确报告；本轮不将空方法计为完整能力。窗口尺寸请求明确拒绝并完成上游回执；没有 OS 配色覆盖时使用 Conf 配色。日志对象使用真实上游实现，但日志配置 UI 尚未提供。

## 可重复测试

```bash
cmake -S . -B .tmp/build/suite-terminal -DPUTTY_BUILD_PUTTY=ON \
  -DPUTTY_BUILD_PUTTYGEN=OFF -DPUTTY_SUITE_CONTAINER_TESTS=ON
cmake --build .tmp/build/suite-terminal --parallel 4
ctest --test-dir .tmp/build/suite-terminal --output-on-failure -L terminal
```

测试使用公共 `IsolationEnvironment` 和断网 SSH 容器，所有 PuTTY 路径、代理变量、XDG、临时目录、密钥、种子、主机密钥缓存、日志和截图限定于项目 `.tmp/`。PPK 由真实 PuTTYgen 转换为 AES-256-CBC／Argon2id，测试测量实际同步认证工作的响应耗时，而非把 PPK 参数当性能证据。手动传入 `--puttygen` 时须使用与受测核心相同编译配置和目标环境的工具：Release 工具按时间校准的轮数不能直接用于 Debug 核心。CTest 已通过 `suite-test-keygen` 保持配置一致。

实际工作负载连续远端输出超过十秒，以 50ms GUI 定时器采样，最大心跳间隔要求 250ms；同时验证真实输入回送、字体回退、DPI／DPR 更新、完整帧更新和有界队列。另测认证计算中异步关闭、主机密钥拒绝和口令提示中关闭。报告与截图按构建目录哈希写入 `.tmp/terminal-reports/`，私有凭据在夹具退出时清理。

认证计算中另填满核心命令队列，再排入256项混合文本／方向键；对端逐字节核对全部已接受输入的顺序，容量拒绝须可观察，关闭撤销剩余操作。本地回归覆盖宽度超过4096但总像素合法的完整帧、无请求完成不能改字体代次、报告路径逃逸，以及真实MIC函数的失败输出与缓冲所有权。SSPI错误回归使用窄类型／系统调用夹具编译上游函数原文，不能替代Windows原生API执行。

`terminal-gss-render-close` 另用公共 KerberosRealm／GSSSSHServer：重新取得仅有 TGT 的票据缓存，代理收到真实服务票据请求后等待 1.5 秒再转发给 KDC。受测终端不提供 PPK、不连接 agent，成功用例核对上游 GSSAPI 日志与 KDC 的 TGS_REQ；关闭用例从代理收取真实请求的标记开始，不把日志到达时机或模拟认证函数作为工作起点。Kerberos 数据库、keytab、票据、重放缓存和日志也只放在本次私有根。

Linux 真实认证与本地边界离屏测试已通过，包含 Ubuntu 22.04／24.04 中固定 Qt6.8.3 的真实 PPK、GSS、背压及关闭执行；两版 PPK 最大心跳分别114／84ms，GSS为91／117ms，均低于250ms。上述目标执行使用容器和宿主共享的系统字体，不能替代 X11／Wayland 真桌面，也不代表 Windows 原生通过。Windows 终端构建、离屏字体、GSS／SSPI 和关闭均尚未验证，不能据本轮结果勾选两平台纵切门。
