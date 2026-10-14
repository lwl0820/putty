# Wayland 鼠标熵实际输入诊断

## 结论

2026-10-15，在 Ubuntu 22.04 与 Ubuntu 24.04 的原生 Qt 6.8.3 构建上，均通过 XTEST → Weston X11 后端 → Wayland 指针事件 → Qt 主窗口的真实事件通路，完成鼠标熵 `254/254` 收集，随后后台核心成功生成 Ed25519 255 位密钥。两次独立驱动均退出 `0`，生成截图均已人工查看。

本轮没有修改产品代码、现有 QtTest 或其他代理的临时脚本。没有发现需要修复的产品 `mouseTracking`／事件过滤问题。原 QtTest 使用光标位置设置的用例仍有 Wayland 环境限制，本轮没有删除、跳过或把它改报为通过。

## 验证环境与边界

| 项目 | Ubuntu 22.04 | Ubuntu 24.04 |
| --- | --- | --- |
| 容器系统实际输出 | Ubuntu 22.04.5 LTS | Ubuntu 24.04.5 LTS |
| 编译器 | GCC 11.4.0 | GCC 13.3.0 |
| Qt | 6.8.3，`platformName=wayland` | 6.8.3，`platformName=wayland` |
| CMake | 3.30.9 | 3.28.3 |
| Weston | 9.0.0 | 13.0.0 |
| xdotool | 3.20160805.1 | 3.20160805.1 |
| 显示通路 | Xvfb 1600×1200，Weston X11 后端 1400×1100，带输入 seat | 同左 |
| 最终结果 | `PG_OK`，密钥存在，EdDSA，255 位 | 同左 |

驱动直接链接对应发行版已经构建的 `libpg-gui.a`、`libpg-core.a`、`libpg-putty.a`，创建真实 `MainWindow`，由现有 QAction 选择 Ed25519 并进入生成。临时观察器只记录鼠标事件，不构造鼠标事件，不直接填充熵，不调用核心生成替代 GUI 流程。输入由外部 `xdotool mousemove` 发送至 Weston 的 X11 窗口；应用收到的原生鼠标事件可见 `spontaneous=true`。

这是嵌套合成器中的实际 Wayland 输入验证。它补充自动测试证据，不等价于人工在完整 GNOME／KDE 桌面用硬件鼠标操作，也不等价于 Windows 原生验收。整个仓库的其余平台结果由质量记录统一维护。

## 失败原因与排查证据

1. 现有 QtTest 的 `QTest::mouseMove` 尝试设置光标位置时，Qt Wayland 插件输出 `Setting cursor position is not possible on wayland`。因此这条合成移动路径不能证明真实 Wayland 指针事件是否可用。
2. 无 seat 的 Weston headless 环境缺少输入通路；换成 Weston X11 嵌套后端后可以接收真实指针事件。不能由 headless 无输入推断产品事件过滤失效。
3. 原先固定向宿主 `x=350..450` 附近投递输入，但诊断整屏截图显示该次应用实际客户区约位于 `x=677..1343, y=247..943`，两者不相交。旧尝试即使发送成功，也没有证明命中应用。Wayland 返回的窗口几何从客户端原点开始，不能直接当作嵌套宿主 X11 上的绝对窗口位置。
4. `xdotool search --name 'Weston Compositor'` 未找到该窗口，而枚举窗口并用 `getwindowname` 读取标题可以找到；诊断脚本保留窗口清单，避免将搜索早退误判成鼠标事件失败。
5. `scan2` 的单轮网格投递实际触发了鼠标事件，熵进度达到 `140/254`，证明产品采集链可用，但该轮 20 秒超时。另一次固定局部坐标尝试没有收到事件，其定位／输入条件不可靠；未将该失败作为产品缺陷。最终使用同一全屏网格重复扫描，驱动等待上限延长到 45 秒，在两个系统均达到 `254/254` 并生成成功。

观察器会同时记录原始事件及控件向父级传播的事件，同一次物理移动可能对应多条日志；日志条数不能直接当作熵位数。生产代码的时间限速与熵计数继续由产品自行执行。

## 成功证据

所有临时文件都在仓库 `.tmp/wayland-diagnosis/`，配置、运行时、构建和截图也使用该根。路径相对于仓库根：

| 证据 | 路径 |
| --- | --- |
| Ubuntu 22.04 完整事件／核心结果 | `.tmp/wayland-diagnosis/22.04/scan-success/input.log` |
| Ubuntu 22.04 生成截图 | `.tmp/wayland-diagnosis/22.04/scan-success/generated.png` |
| Ubuntu 22.04 整屏窗口位置 | `.tmp/wayland-diagnosis/22.04/scan-success/screen.png` |
| Ubuntu 22.04 环境版本 | `.tmp/wayland-diagnosis/22.04/scan-success/environment.txt` |
| Ubuntu 24.04 完整事件／核心结果 | `.tmp/wayland-diagnosis/24.04/scan-success/input.log` |
| Ubuntu 24.04 生成截图 | `.tmp/wayland-diagnosis/24.04/scan-success/generated.png` |
| Ubuntu 24.04 整屏窗口位置 | `.tmp/wayland-diagnosis/24.04/scan-success/screen.png` |
| Ubuntu 24.04 环境版本 | `.tmp/wayland-diagnosis/24.04/scan-success/environment.txt` |
| 两版 Weston 事件环境 | 各成功目录的 `weston.log` |
| 初次有效但未采够的记录 | `.tmp/wayland-diagnosis/22.04/scan2/input.log`、`screen.png` |

两版成功日志都含以下连续结果：

```text
PROGRESS 240 254
PROGRESS 254 254
PROGRESS 1000 1000
RESULT 0 true 3 255 ...
```

`RESULT` 依次记录错误码、密钥存在、算法枚举、位数及公开 SHA256 指纹。Ubuntu 22.04 指纹为 `SHA256:mOHNq8OKWRTNixMySGDj55kcsZsBp0AVkCy4i1BvZH4`，Ubuntu 24.04 为 `SHA256:EoMk54sqNNUJ1nmPe2c6lZVRx9hYEOPbJzMGIAEIsv8`。二者为本次专用随机测试密钥，不要求结果一致；日志和截图没有私钥材料或口令。

## 复用方法

临时驱动源码为 `.tmp/wayland-diagnosis/main.cpp`，工程文件为同目录 `CMakeLists.txt`，启动脚本为 `run.sh`。前提是对应发行版产品库与 Qt 前缀已分别存在于 `.tmp/build/ubuntu<版本>` 和 `.tmp/qt-ubuntu<版本>`。

Ubuntu 22.04 使用已含输入工具的专用镜像：

```bash
docker run --rm --name puttygen-wayland-diagnosis-2204 --network host \
  -v /home/user/projects/putty:/work -w /work \
  puttygen-input-ready:ubuntu2204 \
  bash .tmp/wayland-diagnosis/run.sh 22.04 scan-success
```

Ubuntu 24.04 的 `run24.sh` 先在该新容器安装 `xdotool` 和 `xauth`，下载日志保存在本任务临时根，然后调用同一个 `run.sh`：

```bash
docker run --rm --name puttygen-wayland-diagnosis-2404 --network host \
  -v /home/user/projects/putty:/work -w /work \
  puttygen-build-ready:ubuntu2404 \
  bash .tmp/wayland-diagnosis/run24.sh
```

两命令应串行运行，脚本使用同一测试显示编号 `:127`。重新运行时可把 `run.sh` 第二参数换成新的实验目录名，以保留此前完整证据。`focused*` 名称是失败定位实验入口，成功复验使用 `scan-success` 或其他不以 `focused` 开头的名称。所有驱动产物均为诊断材料，不进入发布包。
