# 终端纵切实施证据

日期：2026-10-15。范围仅为实施计划第一项技术纵切；不修改任务状态或检查点。

## 实现切分

- `apps/putty/src/core/pt_terminal.c`：真实 Terminal／Seat／Backend／Ldisc／LogPolicy，唯一核心线程中的客户端 PRNG 与异步确认。
- `src/core/pt_terminal.h`：自有标量／字节契约、字体代次和完整帧，不向 Qt 控件暴露上游结构。
- `src/render/cpu_renderer.cpp`：核心线程私有字体、任意 Unicode 度量和字体回退、CPU 完整帧；最高 4M 物理像素，拥塞时仅保留一个最新完整帧。
- `src/app/terminal_controller.*`：数据校验、代次过滤、最多 64KiB 输入及分块回执、异步关闭。
- `src/ui/terminal_window.*`：最小终端、输入和非阻塞确认；不呈现开发需求文字。
- `tests/`：公共私有环境／受控 SSH 容器、真实高成本加密 PPK、持续输出和心跳、渲染代次与安全关闭。

根构建衔接由主代理所有，本目录只增加自己的目标。完整后端列表放在最终可执行目标，避免静态归档扫描顺序漏出 `appname`／`backends`。PPK 测试工具通过公共 `SuiteTestSupport.cmake` 的幂等函数取得，不安装。

## Qt 资料与实现依据

按 context7 技能检索 Qt 6.8 官方资料。Qt 允许辅助线程绘制 QImage，同一绘制设备不跨线程并发；QWidget 只在 GUI 线程使用：[线程与模块](https://doc.qt.io/qt-6.8/threads-modules.html)。度量绑定具体字体和绘制设备，字体更新后重建：[QFontMetricsF](https://doc.qt.io/qt-6.8/qfontmetricsf.html)。帧包含物理像素和 DPR，QPainter／GUI 根据 DPR 使用逻辑尺寸：[QImage](https://doc.qt.io/qt-6.8/qimage.html)。

上游 Terminal 同步调用字宽时使用同一核心线程的实际 QFontMetricsF；不是预计算有限字符表。QTextLayout 的真实 glyph run 记录实际回退字体。发送后 GUI 复制图像，生产者下一轮绘制前 detach，不共享可写缓冲。

## 首轮实测

环境为 Linux、GCC 16.2.1、Qt 6.12.0、offscreen。配置及目标：

```bash
cmake -S . -B .tmp/build/suite-terminal -G Ninja -DCMAKE_BUILD_TYPE=Debug \
  -DPUTTY_BUILD_PUTTYGEN=OFF -DPUTTY_BUILD_PUTTY=ON -DPUTTY_SUITE_CONTAINER_TESTS=ON
cmake --build .tmp/build/suite-terminal --target putty-terminal-tests putty-qt --parallel 4
ctest --test-dir .tmp/build/suite-terminal --output-on-failure -L terminal
```

首轮正式 CTest 1/1 通过，22.95 秒。报告 `.tmp/terminal-reports/212581c99b7f5e1b.json`：持续 12881ms、255 次 50ms 心跳，最大 96ms；真实 AES-256-CBC／Argon2id PPK 为 memory=8192KiB、passes=34、parallelism=1，认证调用响应耗时 2241ms。该耗时是上游同步认证调用回到真实定时器安全点的延迟，包含 PPK 工作，不声称为 Argon2 内部纯计算时间。

认证计算期间 close 调用 0ms 返回，安全清理 2153ms，期间仍产生 43 次 GUI 心跳。实际字体回退包括 DejaVu Sans Mono、Noto Color Emoji、Noto Sans Mono CJK KR；字体／DPI144／DPR2 切到代次2，完整帧 2080×1200。事件队列高水位4、命令1、输入34字节。

最初短口令触发 `QByteArray::first(4096)` 范围断言，gdb 在同一隔离夹具定位；已改为 `min(4096, 实际输入长度)`。现在失败路径也异步关闭并泵事件至安全清理，防止 QApplication 字体系统先于核心销毁。诊断 stdout／stderr 保留在报告旁，未记录口令或私钥。

后续本轮结果在下文追加，首轮数据不代替最终复验。

## 最终普通构建与真实 GSS

最终普通构建 `putty-qt`／`putty-terminal-tests` 成功，`ctest --test-dir .tmp/build/suite-terminal --output-on-failure -L terminal` 实际 2/2 通过，共 38.07 秒：PPK 用例22.91秒，GSS用例15.16秒。

PPK 最终负载12706ms、250次心跳、最大127ms；真实认证工作2108ms。认证中close调用0ms、安全清理2104ms、42次心跳。另验证主机密钥拒绝和口令提示关闭；拒绝后未进入认证，关闭后输入返回PR_CLOSING。最终屏幕快照要求GUI已收到与上游屏幕对应的完整帧版本，截图确认FINAL／INPUT_OK及中文／组合音标／emoji可见，没有用旧帧代替终态。

GSS 使用主代理提供的真实MIT KerberosRealm和GSSSSHServer，未修改工具实现。受测进程无PPK／口令且原版CONF_tryagent=false；默认真实GSSAPI库发起认证，实际日志包含Trying gssapi-with-mic和GSSAPI authentication loop finished OK。首次缓存仅有TGT，代理收取真实TGS请求并对真实响应延迟1.5001167秒；KDC日志TGS_REQ计数1。持续负载12076ms、239次心跳、最大83ms。实际KDC等待中close调用0ms，安全清理1556ms、35次心跳、最大51ms；另一次TGS_REQ计数1、真实延迟1.5001136秒。报告 `.tmp/terminal-reports/212581c99b7f5e1b-gss.json`。

阻塞工作期间部分日志可能仍在核心待发缓冲，因此关闭开始点来自代理已收取真实TGS请求的项目私有标记，而非GUI日志投递时机。日志拥塞按顺序合并至16KiB，`PT_LOG.b`明确计数溢出，不无限追加或声称保留完整历史。

## 关闭队列修复与内存检查

ASan／UBSan第一轮真实负载的功能断言全部通过，LeakSanitizer发现104字节PktIn泄漏。追溯上游`ssh_shutdown`先释放PPL，再调用`ssh_bpp_free`；后者只释放BPP结构而没有归还已解包、未消费的in/out队列。经主代理明确授权，仅在`vendor/putty/ssh/common.c`的`ssh_bpp_free`先`pq_in_clear`／`pq_out_clear`，再删除其回调、释放结构；未在模块析构后盲跑回调，未新增全局drain接口。

补丁为`vendor/patches/0006-bpp-close-queues.patch`，`git apply --reverse --check`通过；来源记录由主代理所有。修复后默认启用泄漏检测的ASan／UBSan真实SSH／PPK用例1/1通过，24.92秒，无104字节泄漏。最终双用例sanitizer复验结果待补在下文。

双用例sanitizer首轮中PPK用例通过28.02秒；GSS功能断言通过后LeakSanitizer发现另外28字节MIC泄漏：上游`ssh2_userauth_gss_packet`取得库分配的MIC并复制到SSH包后直接返回，没有调用既有`free_mic`。经主代理明确授权，仅补在`put_string`复制之后立即调用该释放接口，保留现有协议和错误分支；独立补丁`0007-gss-userauth-mic-release.patch`反向校验通过。

待独立核对：`get_mic`返回状态目前未校验；需要同时检查调用者的NULL／失败路径，以及GSSAPI／Windows SSPI各自返回与缓冲所有权契约。本轮仅修确定的成功路径泄漏，没有声称已验证认证失败分支。

最终sanitizer构建使用`-fsanitize=address,undefined -fno-omit-frame-pointer`，没有禁用LeakSanitizer或增加泄漏抑制。PPK真实用例在双项命令中1/1通过28.02秒；修复0007后仅对受影响的真实GSS用例复验：

```bash
ctest --test-dir .tmp/build/suite-terminal/asan --output-on-failure -R terminal-gss
```

GSS 1/1通过18.08秒，未再报告28字节泄漏或其它ASan／UBSan错误。负载12454ms、245次心跳、最大109ms；实际TGS响应延迟1.5000937秒。等待中close调用0ms、安全清理1635ms、38次心跳、最大50ms，实际TGS响应延迟1.5001034秒。PPK最终sanitizer报告负载13311ms、264次心跳、最大84ms、认证工作2308ms；认证中close0ms／cleanup3678ms。报告分别为`.tmp/terminal-reports/afe4c0128ab46a22.json`和同前缀`-gss.json`。

`git diff --check`通过。主代理另外实际完成Ubuntu22／24的Qt6.8.3、GCC11／13目标编译和链接，日志为`.tmp/suite-validation/ubuntu22-terminal-build.log`、`ubuntu24-terminal-build.log`；该证据仅为源码兼容，不能当目标环境终端／字体运行通过。

## 未完成边界

完整 TermWin／键盘／IME／鼠标／剪贴板／平台默认值／配置描述桥等保持后续步骤范围，逐项见本目录 README。Linux 离屏不能替代真实桌面。Qt 6.8.3 Ubuntu 由主代理后续验证；Windows 无可运行原生环境，既未编译也未运行，不将本轮结果投影到 Windows 或两平台技术门。
