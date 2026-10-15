# Windows 原版选项接入边界

日期：2026-10-15。只读代码研究由独立 explorer 完成；不表示选项已实现或原生验收。原版固定0.85。

## 可独立实施

- `-unix` 使用现有 `pa_options.socket_path[108]`，在命名管道外建立独立 listener，共用唯一核心／密钥库；每个 listener 都必须先 shutdown_connections 再 free，端点部分初始化也清理。主 endpoint 仍是命名管道，不发布第二次同类事件覆盖。Windows上游 sun_path 是 ACP 窄字节，不能无测试声称UTF-8。该后端与三传输真实签名测试已单独分配实施，入口选项仍待接。参考 `apps/pageant/src/core/pa_agent.c:31`、`vendor/putty/windows/network.c:1277`、`vendor/putty/windows/pageant.c:1744`。
- `-openssh-config` 主实例在控制器 ready、加载命令行密钥前写准确 `IdentityAgent "//./pipe/..."\n`；不指向AF_UNIX。正常退出仅将本实例已成功写入的文件截断为空，不删除；二次实例完全不写／清。入口记录所有权，closed 核心销毁通知后收尾。参考 `vendor/putty/windows/pageant.c:1714`、`:1838`、`:1937`。
- `-c` 完成初始化和密钥队列后仅启动一次原始命令行余部，不等待子进程；主实例继续驻留，二次实例启动后退出。须在 QApplication 消费参数前保留 GetCommandLineW 与原始余部，不能 QStringList 重新拼接或引入 cmd.exe /c，也不搬Unix子进程寿命语义。复用正式 AuxMatchOpt 与 cmdline_arg_remainder_wide。参考 `vendor/putty/windows/utils/cmdline_arg.c:196`、`windows/pageant.c:1805`、`apps/pageant/src/app/main_windows.cpp:45`。
- `-restrict-acl` 在建核心线程前调用正式 restrict_process_acl，仅影响当前Pageant进程；不发给已有代理，失败不可静默降级。参考 `vendor/putty/windows/utils/security.c:332`。

## 必须跨组件闭环

`-restrict-putty-acl` 只影响托盘“新会话／已保存会话”启动的PuTTY，分别以 `&R`／`&R@会话名` 传递，不能附加到任意 `-c` 子命令。当前AgentWindow没有这些启动动作，Qt PuTTY入口也不识别 `&R`／`-restrict-acl`；仅保存此标志不能计功能完成。参考 `vendor/putty/windows/pageant.c:1283`、`:1357`，`apps/pageant/src/ui/agent_window.cpp:71`、`apps/putty/src/app/main.cpp:14`。

Windows入口应复用正式AuxMatchOpt保留 `--`、双短横与等号参数规则；当前逐项匹配app.arguments()会遗漏它们。`-c`之后立即停止Pageant参数解析。`-keylist`当前被忽略且总显示窗口，与原版默认托盘驻留不同；要同时遵守设计规定的无托盘可见控制出口，不产生隐藏失控进程。

## 必需验证

1. 随机测试命名空间／私有.tmp根中的三传输跨端添加、枚举、正式签名验证、删除；第二监听失败及在途提示关闭；端点收尾与MAP继续拒绝客户端存储／PRNG。
2. 专用命名空间入口变体验证OpenSSH配置精确内容、主实例关闭变空、二次实例不修改；受控子程序记录中文／空格／引号／`-style`余部及代理身份，证明密钥先加载且命令仅启动一次，主代理不随子程序退出。
3. 仅限制测试子进程，实际核其DACL与跨进程访问拒绝；受控PuTTY启动目标核 `&R`／`&R@会话名`、任意 `-c` 无标志，真实PuTTY入口支持后再验ACL闭环。

以上均为批准完整兼容范围的实施拆分，不削减功能或把尚缺接口默认为不适用。
