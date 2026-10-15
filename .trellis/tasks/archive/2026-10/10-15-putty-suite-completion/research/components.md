# 套件组件与现有接入边界

## 已批准的产品决定

用户在初次界面方案建议后回复“按你推荐”，确认 PuTTY 和 Pageant 采用 Qt 跨平台 GUI，Plink、PSCP、PSFTP 保留命令行形式。随后用户以“是”确认六组件、Windows/Ubuntu、双语与原版完整功能范围，最终依据见父 PRD。

## 本地证据

| 证据 | 内容 | 对规划的影响 |
| --- | --- | --- |
| 根目录 `CMakeLists.txt` | 只接入 PuTTYgen 组件 | 不能将上游已有源码视为当前套件已实现 |
| `cmake/UpstreamPuTTY.cmake` | 自定义 `pg-putty` 密钥静态库，使用 `NO_GSSAPI`、`NOT_X_WINDOWS`，生成密钥专用配置 | 扩展网络、GSSAPI、X11 或代理时应隔离配置与符号，避免破坏 PuTTYgen |
| `vendor/putty/CMakeLists.txt:25`、`:31`、`:38`、`:54`、`:57` | 分离事件循环、设置、网络、代理与终端模块 | 优先复用固定上游核心；Qt 适配不能仅以外部命令输出替代交互终端 |
| `vendor/putty/windows/CMakeLists.txt:47`、`:51`、`:53`、`:61` | Windows 事件等待、设置存储、网络套接字、串口适配 | 需要原生 Windows 验证，Linux 检查不能替代 |
| `vendor/putty/unix/CMakeLists.txt:38`、`:42`、`:44`、`:54` | Unix 描述符循环、设置存储、网络套接字、串口适配 | Qt 事件循环需与上游平台适配协调，不能阻塞 GUI |
| `vendor/putty/windows/CMakeLists.txt:80`、`vendor/putty/unix/CMakeLists.txt:252` | 两平台 Pageant 入口不同 | 共享密钥代理核心与 Qt 界面，平台传输单独适配 |
| `vendor/putty/CMakeLists.txt:135`、`:150`、`:158` | Plink、PSCP、PSFTP 已有 CLI 目标 | 尽量复用原版参数、标准流与退出状态；不引入不必要 GUI |

## 原版功能对照入口

- `vendor/putty/doc/using.but`：终端操作、日志、字符集、X11、端口转发、串口、Raw、Telnet、Rlogin、SUPDUP 与命令行入口。
- `vendor/putty/doc/config.but`：会话与日志、键盘与终端、窗口与字体、编码与复制、代理、SSH 密钥交换与主机密钥、认证、GSSAPI、TTY、X11、转发、服务器兼容、串口及其它协议配置。
- `vendor/putty/doc/pageant.but`：密钥列表、添加移除、启动命令、代理转发、延迟解密；Windows 命名管道、OpenSSH 及 WSL 相关入口需按平台适用性单列。
- `vendor/putty/doc/plink.but`：连接与远程命令、批处理、Git 等调用。
- `vendor/putty/doc/pscp.but`：文件传输与参数。
- `vendor/putty/doc/psftp.but`：启动、交互命令、批处理与公钥认证。

## 后续技术研究

以下技术研究已由配套构建、终端与 Pageant 文档补齐；本轮不启动实现或执行产品测试：

1. Qt 与 PuTTY `Terminal`、`TermWin`、`Seat`、`LogPolicy`、平台事件循环之间的契约与生命周期。
2. 原版配置对话框描述到 Qt 控件的映射，以保留完整设置与适用条件；原版存储和 CLI 会话读取的一致性。
3. Windows Pageant 原有 IPC、命名管道与 Unix 代理套接字的复用边界，以及单实例、托盘缺失和关闭策略。
4. 独立上游构建与当前 `pg-putty` 共存时的生成配置、目标名称、构建选项与许可收集。
5. 受控本地服务、伪终端及传输夹具的集成验证；所有账户和数据放项目 `.tmp/`。

## 验证状态

本轮只检查本地固定上游源码、文档和现有任务，没有构建新组件，没有运行目标平台程序，不构成任何新增功能的验收证据。
