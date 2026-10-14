# 研究： Arch Linux 原生包与 Actions 接入

- 问题： 在现有 Actions 产物下载中增加 Arch Linux x86_64 原生包，保留 Windows ZIP 与 Ubuntu DEB 的既有发布方式。
- 范围：内部代码与官方外部资料
- 日期： 2026-10-15

## 研究结果

### 推荐边界

新增独立 Arch job，使用 Arch 官方容器中的发行版 Qt，通过 PKGBUILD 和普通用户 `makepkg` 生成 `puttygen-gui-<版本>-<修订>-x86_64.pkg.tar.zst`，连同 SHA256、CTest 日志和冒烟报告上传为独立 artifact。用户本轮没有要求发布到 AUR 或维护 pacman 仓库；不引入这些额外服务。

Arch 为滚动系统，不能复用 Ubuntu 编译的二进制或把 DEB 改后缀。现有 Qt 6.8.3 锁定与私有部署继续用于 Windows/Ubuntu；Arch 包使用运行时依赖管理，不复制 Qt 库、插件、翻译或 SDK。

### 找到的项目文件与安装缺口

| 文件 | 作用与证据 |
| --- | --- |
| `apps/puttygen/cmake/Packaging.cmake:7` | `PUTTYGEN_RELEASE_PACKAGING` 表示锁定 Qt 的便携/独立部署，Arch 应保持 OFF。 |
| `apps/puttygen/cmake/Packaging.cmake:11` | ON 模式校验 Qt 6.8.3，Linux 分支只允许指定 Ubuntu，不能通过放宽检查把它变成 Arch 模式。 |
| `apps/puttygen/cmake/Packaging.cmake:55` | 当前所有 Linux 安装都设置私有 RPATH；系统安装应避免此行为，也可由 Arch 配置 `CMAKE_SKIP_INSTALL_RPATH=ON` 明确关闭。 |
| `apps/puttygen/cmake/Packaging.cmake:60` | 已有可执行文件、项目/PuTTY 许可、文档安装，可供 `DESTDIR` 包装。 |
| `apps/puttygen/cmake/Packaging.cmake:104` | desktop/icon 当前仅在 Ubuntu 私有部署中安装，系统模式需要补充最小安装分支。 |
| `apps/puttygen/packaging/puttygen-gui.desktop.in:7` | 使用安装前缀配置绝对 Exec；前缀 `/usr` 时结果 `/usr/bin/puttygen-gui` 可用，无需 Ubuntu `/opt` 包装器。 |
| `apps/puttygen/CMakeLists.txt:20` | 仅需求 Qt Core/Widgets，测试增加 Test；应用本身不使用 Linguist。 |
| `apps/puttygen/src/app/language_manager.cpp:31` | 标准 Qt 中文翻译从 `QLibraryInfo::TranslationsPath` 加载，发行版翻译可直接满足。 |
| `apps/puttygen/tests/core/CMakeLists.txt:49` | Linux 默认构建固定上游 CLI，互操作测试另需 Python 和 `ssh-keygen`。 |
| `apps/puttygen/packaging/smoke_package.py:64` | 当前仅 ZIP/DEB，需要增加 `.pkg.tar.zst` 分支与显式隔离安装参数。 |
| `.github/workflows/puttygen.yml:95` | 冒烟文本通过收集器定点复制后上传；Arch 必须复用这项权限边界，不能递归上传原运行目录。 |

最小 CMake 支持是系统安装分支：可执行文件安装到 `CMAKE_INSTALL_BINDIR`，desktop 与 SVG 使用相对 `CMAKE_INSTALL_DATADIR` 的标准路径；系统安装无私有 Qt RPATH、无部署脚本、无 `qt.conf`、无 `/opt` 包装器。`DESTDIR="$pkgdir" cmake --install <构建目录>` 负责安装到打包根。

### PKGBUILD 与依赖

官方 `PKGBUILD(5)` 定义 `build()`、`check()`、`package()` 的职责；`package()` 在 fakeroot 下建立包根，其余阶段用调用者身份执行。`makepkg(8)` 明确 root 调用返回错误码 10，所以容器 root 只安装依赖和准备普通构建账户，随后 `runuser -u builder -- makepkg`。

建议声明：

```bash
arch=('x86_64')
license=('MIT')
depends=('glibc' 'libgcc' 'libstdc++' 'qt6-base>=6.8' 'qt6-translations'
         'hicolor-icon-theme' 'noto-fonts-cjk')
makedepends=('cmake' 'ninja')
checkdepends=('python' 'openssh' 'desktop-file-utils')
```

`base-devel` 是构建环境基础，由容器统一安装；无须把整组塞入组件的 `makedepends`。当前 Arch 的 GCC 运行库已经拆分为 `libgcc` 与 `libstdc++`，直接声明实际链接依赖比只写历史 `gcc-libs` 清晰。中文字体依赖保持默认中文界面可读，与 Ubuntu 包已有字体策略一致。

2026-10-15 官方页面展示 `qt6-base 6.12.0-3`；文件列表缓存与本机为 `6.12.0-2`。两者属于同一滚动版本更新，不固定补丁修订。

- `qt6-base` 当前已经包含 Qt Test、Qt WaylandClient 与 `libqwayland.so`、`libqxcb.so`、`libqoffscreen.so`。
- `qt6-wayland 6.12.0-1` 当前主要提供合成器侧能力，不能照旧经验认定客户端插件仍在该包。本应用使用当前系统 Qt 时不必单独依赖它。
- `qt6-tools` 对本项目没有必要，应用翻译来自嵌入 JSON，标准控件翻译由 `qt6-translations` 提供；不构建 Qt，故也不引入 qdoc/Clang 依赖。

推荐构建参数：

```bash
cmake -S "$srcdir/<源码目录>" -B "$srcdir/<源码目录>/.tmp/arch-build" -G Ninja \
  -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX=/usr \
  -DCMAKE_INSTALL_LIBDIR=lib -DCMAKE_SKIP_INSTALL_RPATH=ON \
  -DPUTTYGEN_RELEASE_PACKAGING=OFF -DBUILD_TESTING=ON
cmake --build "$srcdir/<源码目录>/.tmp/arch-build" --parallel 4
QT_QPA_PLATFORM=offscreen ctest --test-dir "$srcdir/<源码目录>/.tmp/arch-build" --output-on-failure
DESTDIR="$pkgdir" cmake --install "$srcdir/<源码目录>/.tmp/arch-build"
```

源码归档、makepkg 的 build/package/source/cache/log 根、CMake 构建和所有测试输出均放项目 `.tmp/`。归档应由确定的源码与许可目录构造，真实计算 SHA256 并交由 makepkg 校验，不使用 `SKIP`。本机验证尚未提交的改动时不能仅 `git archive HEAD`，否则打包的是旧提交。

配置 `PKGEXT='.pkg.tar.zst'` 明确压缩格式；用 `makepkg --packagelist` 获取实际产物，避免广泛 glob 包含旧包或 debug 包。校验包元数据与内容至少包括 `.PKGINFO` 中正确包名/版本/`arch = x86_64`、声明的依赖、预期可执行文件/desktop/icon/许可，以及不存在 Qt 副本、测试二进制、私钥夹具或 `.tmp` 内容。`sha256sum` 生成可由用户验证的旁文件；SHA256 不代表发行者签名。

### Desktop/icon 事务处理

Arch 的 `desktop-file-utils` 提供 `update-desktop-database.hook`；`gtk-update-icon-cache` 提供同名 icon hook。它们在安装、升级、卸载的事务结束后按系统路径触发。`hicolor-icon-theme` 提供主题目录，本身没有缓存 hook。应用应安装标准文件，交给已有系统 hook，不复制 Ubuntu `postinst/postrm`，也无需新建重复 `.install` 脚本。干净冒烟环境可安装 `desktop-file-utils` 进行 desktop 格式验证，并安装 icon cache 工具观察系统事务行为。

### 干净容器冒烟与 Actions 结构

1. 独立 Arch 构建容器执行完整 `pacman -Syu --needed ...`，避免只刷新数据库后部分升级。依赖满足后切到普通用户 makepkg；缓存如引入，应记录镜像和完整 `pacman -Q` 版本，不能声称滚动仓库完全可复现。
2. 新建另一个干净 Arch 容器，先准备 Python、desktop 校验及图形后端工具；不预装构建 Qt SDK。`pacman -U --noconfirm <包>` 会从同步仓库安装声明的运行依赖，能检验 depends 是否完整。
3. 安装前检查未安装同名应用，安装后 `pacman -Qip/-Qlp`、`pacman -Qk`、`ldd /usr/bin/puttygen-gui`、`desktop-file-validate`，用已安装 `/usr/bin/puttygen-gui` 启动，清除构建用 `QT_PLUGIN_PATH`、`LD_LIBRARY_PATH` 等环境变量。
4. 分别运行 offscreen、XCB、Wayland。XCB 安装 `xorg-server-xvfb`（官方文件列表确认已含 `xvfb-run`）及 `xorg-xauth`；Wayland 安装 `weston`，当前 Weston 15 使用 `--backend=headless --renderer=pixman --no-config --idle-time=0 --socket=...`。保留 `0700` runtime 与实际 compositor socket。
5. `finally` 中 `pacman -R --noconfirm puttygen-gui`，随后核验 binary、desktop、icon、项目许可文件均已消失；失败非零继续阻断 job，不关闭检查或跳过依赖。仅在隔离容器安装/卸载，宿主只可普通用户打包。
6. 独立 Actions artifact 包含 `.pkg.tar.zst`、`.sha256`、本轮 CTest 日志及收集后的报告目录。保留现有 `if: always()` 报告收集方式，不能对包含 root `0700` 目录的原冒烟树递归上传。

三秒启动仍只是启动冒烟，不等于人工桌面功能验收。Arch job 使用当前滚动依赖，不能把成功外推为固定历史 Arch 快照兼容。

### 官方外部来源

- [PKGBUILD(5)](https://man.archlinux.org/man/PKGBUILD.5.en)：字段、构建/测试/打包阶段与 fakeroot。
- [makepkg(8)](https://man.archlinux.org/man/makepkg.8.en)：构建流程、`--packagelist` 与 root 错误码。
- [makepkg.conf(5)](https://man.archlinux.org/man/makepkg.conf.5.en)：输出目录变量、`PKGEXT` 支持 `.pkg.tar.zst`。
- [pacman(8)](https://man.archlinux.org/man/pacman.8.en)：`-U` 自动解析同步仓库依赖、安装查询与卸载选项。
- [Qt Base 包](https://archlinux.org/packages/extra/x86_64/qt6-base/)与[文件列表](https://archlinux.org/packages/extra/x86_64/qt6-base/files/)：依赖包命名、Qt Test、平台插件归属。
- [Qt Wayland 包](https://archlinux.org/packages/extra/x86_64/qt6-wayland/)与[Qt Tools 包](https://archlinux.org/packages/extra/x86_64/qt6-tools/)：当前模块职责与额外依赖。
- [Qt Translations 包](https://archlinux.org/packages/extra/any/qt6-translations/)：系统翻译包存在；页面缓存落后，不据此固定版本。
- [desktop-file-utils 文件](https://archlinux.org/packages/extra/x86_64/desktop-file-utils/files/)、[gtk-update-icon-cache 文件](https://archlinux.org/packages/extra/x86_64/gtk-update-icon-cache/files/)及 [alpm-hooks(5)](https://man.archlinux.org/man/alpm-hooks.5.en)：标准事务 hook。
- [Xvfb 文件列表](https://archlinux.org/packages/extra/x86_64/xorg-server-xvfb/files/)与 [Weston 手册](https://man.archlinux.org/man/extra/weston/weston.1.en)：无显示服务器冒烟工具与当前参数。
- [CMake 安装指南](https://cmake.org/cmake/help/latest/guide/tutorial/Installation%20Commands%20and%20Concepts.html)：安装路径与 `DESTDIR` 暂存。

### 相关项目规范

- `.trellis/spec/backend/directory-structure.md`：组件打包配置应在 `apps/puttygen/packaging/`，不新建根级 packaging。
- `.trellis/spec/backend/build-tools.md`：失败状态、临时目录、独立报告上传目录与权限边界。
- `.trellis/tasks/10-15-putty-cross-platform-gui/design.md`：现有 Windows/Ubuntu 锁定 Qt 方案应保留，Arch 作为新增系统依赖模式。

## 局限与未验证项

- ArchWiki 两个打包指南与 Arch Docker 的 GitLab 官方页面被 Anubis 挡住；不把阻挡页当技术证据。本结论基于可访问官方 manpage、官方二进制包元数据/文件列表和本机只读 `pacman` 查询。
- 外部包网页与文件列表缓存有版本时间差；实际 CI 记录镜像标识、`pacman -Q` 及 Qt 版本，不能把网页版本当运行结果。
- 本研究没有执行 makepkg、容器安装或 GUI 测试；以上命令与测试要求是实施方案，不是通过记录。
- 当前 PRD 原先排除其他发行版，需要主会话按用户此次新增要求同步 Arch 支持边界；研究角色没有修改需求、规范或代码。
