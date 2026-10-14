# 第三方软件与来源

本程序自有代码使用根目录 `LICENSE` 的 MIT 许可证。第三方组件保留各自的版权与许可，不因本项目的许可而改变。

| 组件 | 固定版本与来源 | 许可材料与源码 |
| --- | --- | --- |
| PuTTY 密钥核心 | 官方 [PuTTY 0.85](https://www.chiark.greenend.org.uk/~sgtatham/putty/latest.html)，[源码归档](https://the.earth.li/~sgtatham/putty/0.85/putty-0.85.tar.gz) | `vendor/putty/LICENCE` 完整保留；发布包中的 `share/licenses/puttygen-gui/PuTTY-LICENCE`。归档 SHA256 为 `13fd4db2936d03b73812a7bcc2a658e4dd29cc776a56c3670a7fc6f1a0ee8af8`。 |
| Qt Base、Qt Tools、Qt Translations、Linux Qt Wayland | [Qt 6.8.3 官方源码](https://download.qt.io/archive/qt/6.8/6.8.3/submodules/)，SHA256 锁定在 `tools/qt/qt-sources.json` | 使用开源构建和动态链接。发布包的 `share/licenses/puttygen-gui/Qt/` 包含各模块完整 `LICENSES`、原始源码归档、校验清单和重建脚本。归档中的第三方组件通知与许可证原样保留。 |
| Microsoft Visual C++ 运行库，仅 Windows | 构建机安装的 MSVC 2022 x64 可再发行运行库，应用本地 DLL | 遵循 [Visual Studio 软件许可](https://visualstudio.microsoft.com/license-terms/)和[可再发行清单](https://learn.microsoft.com/en-us/visualstudio/releases/2022/redistribution)。Windows 构建记录应保存实际工具链版本；运行库没有归入本项目 MIT 许可。 |
| Ubuntu 系统运行库 | DEB 根据本发行版的 ELF 依赖生成系统包依赖 | 系统库由 Ubuntu 包管理器安装，其许可和源码获取方式由发行版包资料提供；不用其他 Ubuntu 版本的系统库替换它们。 |

用户指定的 [github/putty](https://github.com/github/putty) 镜像是项目调研来源；该镜像已归档。实现使用当前固定的官方 0.85 源码，保留与用户参考项目的来源关系。

Qt 在发布包中动态链接，用户可替换兼容的共享库；项目没有增加阻止替换库或为调试此类修改进行逆向工程的限制。完整 Qt 源码可直接从随包归档解压，重建方法见 `docs/构建与发布.md`。源代码中包含的第三方许可应随源码保留，不能只抽取 Qt 模块的顶层许可文件。

发布兼容性基线是固定的 6.8.3；这不等于声称该历史补丁版本包含后续所有修复。发布负责人应在公开发行之前检查适用的官方补丁，修改锁定版本时同步校验、重建材料和三平台兼容验证。开发时使用本机其他 Qt 版本的构建不能替代发布兼容验证。
