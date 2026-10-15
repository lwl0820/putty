# 单产品目录与构建契约

## 1. 范围与触发

本项目只交付 PuTTYgen。源码、资源、测试和打包放在根目录，修改目录、构建入口、资源引用和发布链路时遵守本规范。

## 2. 命令与入口

```bash
cmake -S . -B .tmp/build/dev -G Ninja
cmake --build .tmp/build/dev
ctest --test-dir .tmp/build/dev --output-on-failure
cmake -S . -B .tmp/build/core -G Ninja -DPG_BUILD_GUI=OFF
```

## 3. 路径和依赖契约

| 路径 | 职责 |
| --- | --- |
| `CMakeLists.txt` | 单产品入口，`PG_BUILD_GUI` 控制 Qt 界面 |
| `src/core/` | 自有 C 密钥接口、随机与私钥文件适配 |
| `src/app/`、`src/ui/` | Qt 会话、语言管理和界面 |
| `resources/` | 图标、翻译、帮助和资源清单 |
| `tests/core/`、`tests/gui/` | 核心矩阵、互操作和界面回归 |
| `cmake/UpstreamPuTTY.cmake` | 上游最小密钥库构建 |
| `cmake/Packaging.cmake`、`packaging/` | 安装、发布与包验证 |
| `vendor/putty/`、`vendor/patches/` | 固定官方源树与密钥核心必要补丁 |
| `tools/qt/` | Qt 版本锁与源码构建工具 |
| `docs/` | 构建及发布说明 |
| `.tmp/` | 全部构建、下载、夹具和验证报告 |

保留第三方完整源树及许可证，实际只编译所需密钥代码；互操作参考工具只构建上游 `puttygen`。自有产品无需组件选择开关和共享终端运行时。核心关闭 GUI 后不依赖 Qt。搬移不改变 C 接口、目标名或密钥行为。

## 4. 校验与错误矩阵

| 场景 | 预期 |
| --- | --- |
| 全新 GUI 构建 | 核心与 GUI CTest 均通过 |
| `PG_BUILD_GUI=OFF` | 无 Qt 环境也可构建和运行核心测试 |
| 资源许可证相对路径失效 | 构建失败，必须修正 qrc 引用 |
| Arch 源码来自未提交布局 | 不作为新布局正式打包证据，源码与配方必须同一 HEAD |

## 5. 正常、基线与错误案例

正常：根目录构建，程序位于构建树 `src/`；基线：密钥功能不变；错误：仅移动 C++ 文件而保留 CI 和打包脚本旧路径。

## 6. 必需验证

目录重组后使用新构建树运行完整 CTest、独立核心配置、`packaging/tests` 与 `tools/qt/tests`。检查 Python 脚本的父目录层数、资源许可证、PKGBUILD 生成路径和源码白名单，核对安装与归档文件清单。旧布局的结果不能作为新布局通过证据。

## 7. 错误与正确做法

错误：用全局文本替换代替完整路径审计，遗漏 `Path(__file__).parents` 和构建树生成文件。

正确：按源码、资源、测试、打包和 CI 的实际入口逐一追踪，使用全新构建和脚本回归验证。
