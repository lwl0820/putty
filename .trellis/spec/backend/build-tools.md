# 公共构建脚本编码契约

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
