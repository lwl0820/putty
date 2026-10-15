# Arch Linux 包契约

## 1. 范围与触发

适用于 `packaging/arch/`、组件 `Packaging.cmake` 的 Arch 安装分支和 Actions 的独立 Arch x86_64 任务。用户追加 Arch 发布要求，系统 Qt 包与既有 Windows／Ubuntu 私有 Qt 发布分开验证。

## 2. 命令与开关

```bash
python packaging/arch/build_arch.py
bash packaging/arch/smoke_arch.sh
```

`PUTTYGEN_ARCH_PACKAGING=ON` 是原生系统安装模式：仅支持 Arch Linux x86_64、Release 和 `/usr` 前缀；与 `PUTTYGEN_RELEASE_PACKAGING` 互斥。其安装结果由 `makepkg` 打包，不生成 CPack 开发归档或执行私有 Qt 部署。

## 3. 源码、依赖、安装与产物契约

- 在 Arch 环境中由普通用户执行 `makepkg`；容器 root 只负责准备环境、依赖和隔离安装验证。禁止对宿主执行 pacman 安装／卸载。
- 构建源码、PKGBUILD 和版本来自同一检出 HEAD。使用产品文件白名单归档，真实计算源码 SHA256 交由 makepkg 校验，不使用 `SKIP`，不从远端分支重取不同提交。
- 构建中间文件、源归档、包与日志均在项目 `.tmp/`。源码不包含 `.git`、AI 配置、研究资料、`.tmp` 或测试私钥；安装包不包含测试工具、开发源码或 Qt 副本。
- 使用系统 Qt 6.8 以上，依赖由 PKGBUILD 声明并交由 pacman 管理。中文标准控件翻译使用系统 `qt6-translations`；不改变 Windows／Ubuntu 锁定 Qt 的规则。
- `/usr/bin/puttygen-gui` 是实际程序；desktop、图标、文档及许可按系统目录安装。不安装 `/opt` 包装器、私有 Qt 库／插件、`qt.conf` 或私有 RPATH。图标与 desktop 缓存交由系统事务 hook 处理。
- `.tmp/packages/arch/` 提供原生 `.pkg.tar.zst`、SHA256、对应源码与配方、构建环境信息及 CTest 日志。Actions 上传这些产物和独立报告目录，不递归上传冒烟运行目录。
- Arch 滚动包记录实际提交、工具链与包版本；不宣称固定历史 Arch 系统兼容，也不把校验和称为发行者签名。

## 4. 校验与错误矩阵

| 条件 | 预期 |
| --- | --- |
| root 执行 makepkg 或目标不是 Arch x86_64 | 拒绝构建，不生成假成功包 |
| 两种打包模式同时开启、前缀错误或非 Release | CMake 配置失败 |
| 源码校验不符、构建或 CTest 失败 | 非零退出，保留日志 |
| 依赖或平台插件缺失、GUI 提前退出 | 冒烟失败，仍尝试卸载并保留已有报告 |
| 容器已安装同名应用 | 拒绝覆盖，不破坏原安装 |
| 卸载后仍残留本包文件 | 验证失败，不标为通过 |

## 5. 正常、基线与错误案例

正常：检出提交在 Arch 中由普通用户构建包，另一个干净容器通过 `pacman -U` 安装其声明的运行依赖并从应用入口启动。基线：Windows ZIP 和 Ubuntu DEB 仍使用各自原有路径与版本检查。错误：把 Ubuntu 二进制重新装入 `.pkg.tar.zst`，或放宽私有部署检查以假装支持 Arch。

## 6. 必需验证

实际执行 makepkg 和完整核心／互操作／GUI CTest，检查 `.PKGINFO` 中的包名、版本、x86_64 与运行依赖；核对真实安装文件清单、desktop 格式、ELF 动态库和 RPATH。仅在隔离 Arch 环境验证 pacman 安装、offscreen／XCB／Wayland 启动及卸载，确认运行依赖从包元数据安装。验证失败继续向 job 传播、报告可由普通 runner 读取，且运行时 `0700` 权限保持。修改共用 CMake 或报告收集时检查 Windows／Ubuntu 接线未改变；实际远端 Actions 与人工桌面验收另行记录。

## 7. 错误与正确做法

错误：root 容器直接运行 makepkg，或将整个工作区归档并设置源码校验为 `SKIP`。

正确：root 准备隔离构建环境，普通账户构建，使用 HEAD 产品白名单与真实源码校验，并保存对应配方、提交及环境版本。
