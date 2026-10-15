# 套件核心与命令行构建契约

## 1. 范围与触发

适用于根 CMake、`cmake/SuitePuTTY.cmake`、`SuitePuTTYSources.cmake`、`SuiteCLI.cmake` 以及组件构建入口。PuTTYgen 的 `pg-putty` 是密钥裁剪库，新终端与代理不能直接使用其配置。这里记录已实施的构建边界，不表示完整产品或 Windows 验收完成。

## 2. 目标与命令

- `suite-config`：独立的配置头、包含路径和平台依赖；探针缓存加 `SUITE_` 前缀。
- `suite-putty`：客户端及共用模块静态归档，保留真实计时、网络、GSSAPI、串口、X11 和平台存储。
- `suite-pageant-core`：单独编译 `pageant.c`，公开依赖 `suite-putty`。
- `suite_add_backend_list(target appname SSH SERIAL OTHERBACKENDS)`：产品自己的后端表。
- `suite-plink`、`suite-pscp`、`suite-psftp`：导入的 CLI 可执行目标；测试使用 `$<TARGET_FILE:...>` 获取路径。

```bash
cmake -S . -B .tmp/build/suite-cli -G Ninja -DPUTTY_BUILD_PUTTYGEN=OFF -DPUTTY_BUILD_PLINK=ON -DPUTTY_BUILD_PSCP=ON -DPUTTY_BUILD_PSFTP=ON
cmake --build .tmp/build/suite-cli --parallel 4
```

## 3. 配置、符号与产物契约

- 新组件默认关闭；全部关闭与 CLI-only 不查找 Qt。`PUTTY_BUILD_RUNTIME` 用于独立验证共享 Qt 运行时。
- `pg-putty` 与 `suite-putty` 使用不同目标和生成头；同一产品进程只链接一套所需核心。
- `pageant.c` 自带遇到随机读取就终止的 `random_read`，不能与客户端 `sshrand.c` 放进同一个归档。客户端只链接 `suite-putty`，Pageant 额外链接 `suite-pageant-core`；不使用 weak 符号掩盖冲突。
- 通用运行时不初始化客户端 PRNG。需要随机的产品模块在唯一核心线程内管理其生命周期；Pageant 保留原版无需客户端 PRNG 的行为。
- `stubs/null-*` 是真实模块共享的空方法和 vtable，需要与真实实现一起链接；不能按目录名误当成应排除的 `no-*` 功能替代桩。Unix 日志还需要上游通用 `utils/ltime.c`。
- CLI 从固定 vendor 源码根独立配置，不把上游根 `add_subdirectory` 到本工程。只构建和安装选中产品，不执行上游全量安装。编译器、工具链、架构、运行库和发布断言保持一致。
- 单配置 CLI 产物位于 `suite-cli/<程序>`；多配置位于 `suite-cli/<配置>/<程序>`。构建副产物、导入目标位置和安装路径必须一致，禁止各配置共用一个输出文件。

## 4. 校验与错误矩阵

| 情况 | 必须结果 |
| --- | --- |
| 全部关闭或 CLI-only，显式禁用 Qt 查找 | 配置和构建成功 |
| 同树构建密钥库及完整客户端库 | 两个配置头与能力隔离 |
| 只构建 Debug，按 Release 安装 | 非零失败，不安装 Debug 冒充 Release |
| 已构建 Debug/Release，分别安装 | 每个安装文件摘要与对应配置产物一致 |
| 缺少正式 GUI 入口却打开 GUI 开关 | 配置失败，不生成空产品目标 |
| 静态归档成功但真实模块未链接 | 不能作为完整核心通过证据 |

## 5. 正常、基线与错误案例

正常：仅 PSFTP 多配置构建后，Release 安装只收对应目录的 PSFTP。基线：默认仍只构建 PuTTYgen。错误：强制 `CMAKE_RUNTIME_OUTPUT_DIRECTORY_*` 指向同一目录，最后一次构建覆盖其它配置，导致发布收错程序。

## 6. 必需验证

修改共享源列表后，运行 `runtime-core-link` 的完整后端/终端实际链接和原版配置、日志往返；运行 `runtime-agent-core-link` 的独立 Pageant 初始化/关闭，检查它不引入客户端 PRNG。运行适用 PuTTYgen 回归、CLI-only/全关闭及多配置安装检查。测试产物放 `.tmp/`；本机 Qt、锁定 Qt、交叉编译与原生桌面证据分别记录。

## 7. 错误与正确做法

错误：只执行 `ar` 归档成功就认定完整客户端可链接；把所有配置安装路径固定到同一个程序文件。

正确：用真实 Terminal、后端、settings、storage、logging 的宿主完成链接和执行，并独立验证 Pageant 的随机符号边界；多配置导入目标及安装均按具体配置选取文件。
