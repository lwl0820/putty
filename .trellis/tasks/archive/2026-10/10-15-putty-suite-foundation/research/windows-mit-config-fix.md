# Windows MIT 配置默认值修复

## 变更边界

修复 `af4919b` 首次 Windows 原生 MIT 探针在 `configuration-defaults` 失败的诊断与精确比较，并落实跨 CRT 的进程创建环境边界。行为位于 `gss_mit/mit_gss.py` 默认值检查和 `probe.py` 原生调用入口；新增 `native_probe.py` 仅作为内部隔离入口，回归与 README 同步。保留固定 MSI、PE 闭包、实际 DLL 句柄路径、私有随机 realm、缺失 FILE 缓存及无凭据 API 白名单。不修改公共 ACL、KDC、SSH、产品、CMake 或 Actions。

## 源码定位

Windows run `38678912619`、MIT job `116238729331` 的日志位于 `.tmp/suite-validation/windows-mit-first-ci.log`。实际准备过程和 Windows 13 项 MIT／7 项 ACL 回归通过；真实探针 0.31 秒后在合并的 `configuration-defaults` 阶段失败。旧报告无法区分哪个 getter 或比较失败，不能凭这份报告声称其余配置检查已通过。

核对 MIT 官方 `kfw-4.1-final`，提交 `aa75c3374ce654f3dcfd6ec50fc1f1dfeee673b9`：

- `src/lib/krb5/os/init_os_ctx.c` 的 Windows 路径先从 `getenv("KRB5_CONFIG")` 选择配置；不能假设 Windows 忽略该配置。
- `src/lib/krb5/os/ccdefname.c` 读取 `libdefaults/default_ccache_name` 后调用 `k5_expand_path_tokens()`。
- `src/lib/krb5/os/expand_path.c:524` 的 `_WIN32` 分支逐字把结果中的 `/` 转为 `\`。

前两份原始文件由主会话提供，第三份本次只读下载至同一项目 `.tmp/suite-validation/mit-official-source/`。本次确定发现：旧代码将配置写成 `FILE:C:/...`，随后仍用该正斜杠原文比较 getter，必然拒绝按固定 Windows 代码展开的 `FILE:C:\...`。这是确定的比较缺陷；首次 CI 没有更细阶段，仍需新 CI 证明它是否也是该次失败的唯一原因。

## 实现

仅按上述固定转换生成唯一预期字节，完整路径、随机目录和 `FILE:` 类型继续精确匹配；没有改成路径归一化、basename 或后缀比较。配置失败拆成五个固定阶段，初始化和 realm getter 失败可附带原生数值返回码，报告不包含真实 realm、缓存名或配置路径。

父探针不再 `clear/update os.environ`，也不加载 MIT DLL。它持有私有根的目录保护，创建配置后以 `env=`、`cwd=` 和 `-I -S -B` 启动独立原生子进程。子进程在目标 DLL 全部未加载时核对实际初始环境，随后复用原来的绝对路径和 0x900 搜索限制、逐句柄验证、机制释放、真实默认值 getter、缺失凭据文件和目录复核。父进程只接受严格白名单协议，在子进程退出及精确清理后提交最终报告。

父进程固定 60 秒 watchdog 保留，失败占位在启动子进程前已可靠落盘，覆盖父子崩溃。原生子进程另有不超过 45 秒、且小于父剩余时间减七秒的 watchdog；父进程还有管道输出／时间限制。子进程固定失败 JSON 送达后，公开 CLI 必须仍返回 1；内部退出 0 不计原生通过。

## 验证边界

本机 Linux 的回归使用真实 ctypes 指针覆盖固定 Windows 路径转换、错误目录与类型拒绝、各配置错误阶段和资源释放；不将它们记作原生 Windows getter 通过。实际新 Python 子进程验证封闭初始环境、隔离启动标志和父环境未改变；真实挂起子进程验证父、子硬期限，旧成功报告不能残留。原生失败后的已知运行根会精确清理，未知内容继续保留并使测试失败。

待独立审查及主会话触发 Windows CI 后补充真实原生结果。完整 Kerberos、SSH、Microsoft SSPI 正向认证和 Windows 10/11 人工桌面验收仍未由本次修复完成。

2026-10-15 本机验证：`python3 -B -m unittest discover -s tools/suite/windows/gss_mit/tests -v` 共 19 项通过、零跳过；8 个 Python 文件 AST 解析通过；本目录 `git diff --check` 通过。此结果未运行 Windows DLL，不改变首次原生 CI 仍失败的历史结论。
