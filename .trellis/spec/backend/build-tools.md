# 公共构建脚本契约

## 1. 范围与触发

适用于 `tools/qt/build_qt.py` 及 `.github/workflows/puttygen.yml` 的 Python 调用。Windows 重定向标准流可能默认为 cp1252，中文日志会在编译开始前触发 `UnicodeEncodeError`。

## 2. 命令

```powershell
$env:PYTHONIOENCODING = "utf-8"
python tools/qt/build_qt.py --prefix .tmp/qt --work .tmp/qt-build --jobs 4
python -B -m unittest discover -s tools/qt/tests -v
```

## 3. 标准流与子进程契约

- Qt 构建脚本在处理参数和输出日志之前，将可重新配置的 `sys.stdout`、`sys.stderr` 设置为 UTF-8。
- Windows Actions 使用 `PYTHONIOENCODING=utf-8`，覆盖构建及打包检查脚本的中文输出。
- 标准流编码与 `subprocess` 捕获输出的解码属于不同边界。不要用全局 `PYTHONUTF8=1` 代替标准流设置，以免改变 MSVC 本机编码输出的解码方式。
- 构建子进程输出继续直通，失败退出码继续向上传递；不因日志编码修复吞掉构建错误。
- 测试不下载或构建 Qt；所有临时文件位于项目 `.tmp/`。

## 4. 校验与错误矩阵

| 输入或状态 | 预期 |
| --- | --- |
| cp1252 重定向标准流、中文日志 | 输出 UTF-8，无编码异常 |
| 命令行参数非法 | UTF-8 输出原有参数错误，返回非零 |
| 正常 UTF-8 环境 | 中文内容保持完整 |
| 构建命令失败 | 保留非零退出状态与诊断 |

## 5. 正常、基线与错误案例

- 正常：Windows Actions 中执行中文路径命令，日志可读并进入子进程。
- 基线：脚本 `--help` 在重定向的 cp1252 环境下退出 0，帮助文本按 UTF-8 解码。
- 错误：非法参数产生 argparse 错误，不能被 `UnicodeEncodeError` 替代。

## 6. 必需测试

在 Qt 构建前运行 `tools/qt/tests/`。回归必须主动设置 `PYTHONIOENCODING=cp1252` 并捕获实际进程标准流，检查 UTF-8 中文和预期退出码；仅在当前 Linux 默认 UTF-8 环境执行不能覆盖此缺陷。测试还应验证中文命令日志到达子进程调用。

## 7. 错误与正确做法

错误：只把一条中文日志改为英文，后续成功日志、参数错误和打包 JSON 仍可能失败。

正确：在脚本入口设置标准流编码，在 Windows job 设置 `PYTHONIOENCODING`，并用 cp1252 管道回归验证完整输出边界。

## Qt Tools 可选 Clang 依赖

### 1. 范围与触发

锁定 Qt 6.8.3 的 Qt Tools 配置阶段会无条件查找可选 `WrapLibClang`。预装 runner 的 Clang 导出文件可能引用缺失的静态库，使配置直接失败；干净系统未安装 Clang 时反而能够继续。

### 2. 配置参数

仅在 `qttools` 的 CMake 配置命令中加入：

```text
-DCMAKE_DISABLE_FIND_PACKAGE_WrapLibClang=TRUE
-DFEATURE_clang=OFF
-DFEATURE_clangcpp=OFF
```

### 3. 能力与缓存契约

- `CMAKE_DISABLE_FIND_PACKAGE_WrapLibClang` 在包查找边界阻止加载系统 `ClangConfig.cmake`；只设 `FEATURE_qdoc=OFF` 或 `FEATURE_clang=OFF` 不会跳过前面的查找。
- 保留 Linguist，保证 `lrelease` 与后续 `qttranslations` 可构建；不修改 Qt 版本锁，不补装无用 LLVM 库，不删除系统预装工具。
- 配置变更使用新工作目录验证；Actions 缓存键必须继续包含构建脚本哈希，避免恢复旧配置的 Qt。

### 4. 校验与错误矩阵

| 环境 | 预期 |
| --- | --- |
| 没有 Clang | 配置与翻译工具构建成功 |
| 存在引用缺失库的 Clang 配置 | 禁用包发现后不读取该配置 |
| 仅关闭 Clang 功能，仍允许包查找 | 故障夹具使配置失败，证明回归有效 |
| 非 Clang 的真实配置或构建错误 | 非零退出，不吞掉诊断 |

### 5. 正常、基线与错误案例

正常：预装 Clang 配置不完整，Qt Tools 仍可使用内置解析器与翻译工具。基线：两 Ubuntu 既有构建本来就是 `Clang_DIR-NOTFOUND`、Linguist 开启。错误：为绕开 Clang 故障关闭整个 Linguist，会破坏后续翻译构建。

### 6. 必需验证

保留标准流编码回归，并验证实际 Qt Tools 配置参数接线。使用会引用缺失库或主动报错的 `ClangConfig.cmake` 复现旧配置失败，再验证新配置不读取该包且能构建所需翻译工具。轻量回归会调用 CMake／Ninja，CI 必须先安装固定版本工具再运行测试，测试仍应位于 Qt 构建前。常规轻量测试不得依赖下载 Qt；实际 Qt 验证的源码、构建和日志均留在 `.tmp/`。

### 7. 错误与正确做法

错误：仅加入 `FEATURE_clang=OFF`，因为功能关闭就假设不会查找依赖。

正确：同时禁用对应包发现并明确关闭可选功能，用损坏包夹具验证发现路径确实没有执行。

## 容器冒烟报告与产物上传

### 1. 范围与触发

Ubuntu DEB 冒烟在 root 容器中运行，宿主 Actions runner 以普通用户上传产物。`.tmp/package-smoke/` 含 root 所有的 `0700` 运行时目录，不能对该树使用递归上传通配符。

### 2. 命令

```bash
python apps/puttygen/packaging/collect_smoke_reports.py
python -B -m unittest discover -s apps/puttygen/packaging/tests -v
```

收集器默认从 `.tmp/package-smoke` 读取，写入 `.tmp/package-smoke-reports`，可通过 `--source`、`--destination` 指定测试目录；两路径解析后必须位于项目 `.tmp/` 内，且不能相同或互相包含，非法参数返回非零。

### 3. 路径、身份与退出状态契约

- 宿主 runner 在上传前通过 `if: always()` 运行收集器，Windows 同样使用独立报告目录。
- 源端仅枚举第一层包目录，定点读取 `offscreen`、`xcb`、`wayland`、`native` 下的 `结果.json`、`启动输出.txt`、`动态库.txt`，以及源根的 `weston.log`。`temp`、`wayland-runtime` 为辅助目录，必须在探测子路径前排除；不能把根目录的合成器运行目录当成包目录。不递归运行目录，不复制 socket、解压包、账户配置或测试私钥。
- 目标保留包名与平台层级，使用内容复制创建当前 runner 可读取的报告，不继承容器文件的所有者与权限。
- 上传路径为 `.tmp/package-smoke-reports/`；DEB／ZIP、SHA256 和 CTest 日志保留各自原路径。两平台不得残留 `.tmp/package-smoke/**` 上传模式。
- 原冒烟步骤的非零状态继续使任务失败；报告收集不能伪造成功结果。运行时目录维持 `0700`，既有 Wayland 目录继续连接原合成器。
- 真正的源目录、包目录、平台目录及报告使用会传播权限错误的 `lstat()` 状态查询，只将 `FileNotFoundError` 视为尚未生成；不依赖 Python 3.14 会吞掉系统错误的 `is_file()` 等便捷查询，避免真实报告因父目录不可访问而被漏报。

### 4. 校验与错误矩阵

| 状态 | 预期 |
| --- | --- |
| root 所有且普通用户不可遍历的运行时子目录 | 收集器不进入该目录，报告仍可收集、扫描和读取 |
| GUI 启动失败，只有启动输出 | 收集已有输出，不要求成功结果 JSON |
| 前置构建失败，尚无冒烟报告 | 允许没有报告，原构建失败状态保留 |
| 报告已存在但读取或写出失败 | 收集步骤失败并保留诊断，不能静默丢弃 |
| 源或目标越出项目 `.tmp/`，或两目录重叠 | 参数错误，非零退出，不执行复制 |

### 5. 正常、基线与错误案例

正常：root 容器生成报告后，普通 runner 将文本复制到独立目录并成功递归扫描。基线：原 Windows native 报告以相同目录结构上传。错误：增加上传文件匹配模式却仍以 `package-smoke/**` 扫描源树，会再次访问无权限的运行时目录。

### 6. 必需验证

回归使用不同 UID 或实际无读取权限的目录复现旧扫描的 `EACCES`，同时覆盖包内运行目录和源根辅助运行目录，确认收集器能保留报告内容、包与平台区分，并且普通用户能递归读取目标树。权限边界必须使用目标 runner 的 Python 版本实际验证：Python 3.14 与旧版本的 `Path.is_file`／`is_symlink` 对系统错误的处理存在差异，不能仅凭新版本下不抛异常判断路径可访问。覆盖部分失败日志、尚无源目录、收集错误传播及未复制运行时内容。检查各平台工作流均接入收集步骤，且 `always()`、原测试退出码与安装／卸载验证保留。此修复不需要重编 Qt 或重复无关密钥矩阵。

版本差异的官方依据：[Python 3.14 路径状态查询变更](https://docs.python.org/3.14/library/pathlib.html#querying-file-type-and-status)、[Python 3.12 符号链接状态查询](https://docs.python.org/3.12/library/pathlib.html#pathlib.Path.is_symlink)。前者开始将操作系统错误统一转换为 `False`，旧版会传播权限错误。

### 7. 错误与正确做法

错误：为了让上传步骤扫描成功，对运行目录递归执行 `chmod 755`，或给上传步骤增加 `continue-on-error`。

正确：保持运行目录的原权限，仅把指定报告复制到独立目录，上传该目录并保留原测试失败状态。
