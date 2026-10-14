# Ubuntu Actions 的 QtTools 可选 Clang 探测失败研究

- 问题：已由用户日志确认 qttools 配置读取 runner 不完整 Clang 导出文件而失败，核实 Qt 6.8.3 正确关闭探测的开关。
- 范围：构建脚本与目标环境
- 日期：2026-10-15

## 研究结果

### 已确认的失败边界

主会话转来的用户完整日志给出：`qttools/configure.cmake:20` → `cmake/FindWrapLibClang.cmake:17` → runner `/usr/lib/llvm-14/lib/cmake/clang/ClangTargets.cmake:750`，因缺失 `libclangBasic.a` 而产生 CMake fatal。该证据已超出仅凭 GitHub annotation 的 exit 1，不能再归因于 Python 中文输出问题。

### 最小正确开关

在 **qttools 单模块 CMake 配置**时新增：

```text
-DCMAKE_DISABLE_FIND_PACKAGE_WrapLibClang=TRUE
```

这是阻止读取该损坏包的最小开关。为使选择固定且便于审阅，也可同时明确 `-DFEATURE_clang=OFF -DFEATURE_clangcpp=OFF`，保留现有 `-DFEATURE_qdoc=OFF`。这两个 feature 开关本身不足以避免初次包探测：它们的定义和求值在无条件的包查找之后。

不必为本产品安装完整 LLVM 静态开发库，也不必删除 runner 的 LLVM 目录。产品使用 Qt Widgets、Linguist/翻译编译工具，不使用 Clang 分析器或 QDoc。

### 源码依据

以下源码从项目已有官方归档直接只读解包到内存检查，没有修改上游或执行 Qt 编译：

- `.tmp/qt-archives/qttools-everywhere-src-6.8.3.tar.xz`：固定上游 QtTools 归档；锁位于 `tools/qt/qt-sources.json:7`，SHA256 为 `02a4e219248b94f1333df843d25763f35251c1074cdc4fb5bda67d340f8c8b3a`。
- 归档内 `qttools-everywhere-src-6.8.3/configure.cmake:20`：无条件执行 `qt_find_package(WrapLibClang 8 PROVIDED_TARGETS WrapLibClang::WrapLibClang)`，没有 `REQUIRED`；`:47` 才定义 clang feature，`:55` 定义 qdoc，`:60` 定义 clangcpp。
- 归档内 `qttools-everywhere-src-6.8.3/cmake/FindWrapLibClang.cmake:17`：无条件执行 `find_package(Clang CONFIG)`，先于版本和目标可用性校验（`:35`、`:44`）；因而 runner 上不完整导出文件会直接 fatal，不能退化成可选包未找到。
- `.tmp/qt-archives/qtbase-everywhere-src-6.8.3.tar.xz` 内 `cmake/QtFindPackageHelpers.cmake:88`–`:92`：Qt wrapper 先调用原生 `find_package(... CONFIG QUIET)`；`:139`–`:141` 可能再调用原生 module-mode 查找。二者均受 CMake 标准 `CMAKE_DISABLE_FIND_PACKAGE_WrapLibClang` 控制，wrapper 不按 `FEATURE_clang` 自动跳过这些初次查找。
- QtTools `src/linguist/CMakeLists.txt:8`–`:15`：Linguist 开启就加入 lrelease/lupdate；`src/linguist/lrelease/CMakeLists.txt:30`–`:31` 仅链接 `Qt::CorePrivate`，不要求 Clang。禁止关闭整个 linguist，否则影响后续 qttranslations。
- `tools/qt/build_qt.py:116` 附近：qttools 目前仅显式关闭 assistant、designer、qdoc，仍允许上述可选包探测。
- `tools/ubuntu-build-dependencies.sh`：安装的开发依赖不含 LLVM/Clang；本地干净系统没有 runner 的预装 Clang 导出包，因此本地通过不能证明预装开发工具环境通过。

### 已有本地证据与差异

- `.tmp/qt-build-ubuntu22.04/qttools/CMakeCache.txt:286`、`.tmp/qt-build-ubuntu24.04/qttools/CMakeCache.txt:286` 均为 `Clang_DIR-NOTFOUND`；两者 `FEATURE_clang`/`FEATURE_clangcpp` 为 OFF，`FEATURE_linguist` 为 ON。
- `.tmp/platform/24.04/qt-build.log:5936`–`:5937` 是 Clang 未找到的非致命诊断；后续继续成功构建安装。这与 runner 的“找到包，但包引用文件缺失”不同。
- 两平台 qttools `config.summary` 记录 libclang 和 Clang lupdate parser 不可用，而 Linguist 保持启用。本次修复的明确禁用与已通过本地构建的实际能力一致。

### 外部参考

- [CMake 官方变量文档](https://cmake.org/cmake/help/latest/variable/CMAKE_DISABLE_FIND_PACKAGE_PackageName.html)：`CMAKE_DISABLE_FIND_PACKAGE_<PackageName>=TRUE` 禁止非 REQUIRED 的对应查找；建议初次配置设置。已有缓存可能残留已找到的包变量，应使用新的构建目录或清理对应包缓存。
- 本机 `cmake --help-variable CMAKE_DISABLE_FIND_PACKAGE_PackageName` 给出相同契约。

### 建议验证

实施阶段可在项目 `.tmp/` 中建立最小损坏 `ClangConfig.cmake` 夹具：一旦被查找就 `message(FATAL_ERROR)`，验证 qttools 命令包含包禁用参数，并验证真实 Qt wrapper 与标准 CMake 查找不读取该包。不能只检查 feature 字符串存在。随后重新运行 Ubuntu22/24 CI；无需为本次定位重新编译本地全套 Qt。

## 相关规范

- `.trellis/spec/backend/build-tools.md`：公共构建脚本、失败传播与 `.tmp/` 测试边界。
- `.trellis/spec/backend/directory-structure.md`：共享工具位于 `tools/qt/`，构建与夹具仅放 `.tmp/`。
- 任务 `design.md`：固定 Qt 补丁版、动态链接、Ubuntu 两版本独立发布验证。

## 验证边界

- 本研究不修改产品或 CI，不进行 Qt 编译，不下载工具，不读取认证材料。
- runner 原始完整日志没有由研究代理独立下载；具体 fatal 链由主会话转交的用户日志确认。
- 未在研究阶段声明修复已通过实际 GitHub runner；标准开关与 Qt 源码足以确定最小配置方向，但真实 Actions 结果仍须实施后记录。
- 研究角色不读取 implement/check JSONL；初次尝试的 research.jsonl 不存在，已直接读取 PRD、设计、实施计划及相关规范。
