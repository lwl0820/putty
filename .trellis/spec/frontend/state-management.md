# Qt 会话与界面状态契约

## 1. 范围与触发

适用于 `apps/puttygen/src/app/session_controller.*`、`apps/puttygen/src/ui/main_window.*` 与语言管理。生成、加载、保存、证书操作和对象销毁跨 GUI／工作线程时必须遵守本契约；所有产品文字只说明用户操作，不展示任务或实施说明。

## 2. 接口签名

```cpp
void SessionController::generate(pg_generate_params params, QByteArray entropy);
void SessionController::load(const QString &path, QByteArray passphrase);
void SessionController::save(const QString &path, pg_save_format format,
    QByteArray passphrase, const QString &comment, pg_ppk_params params);
void completed(int operation, int error, const QString &detail);
void snapshotChanged();
void LanguageManager::setLanguage(const QString &language);
```

公开数据通过 `KeySnapshot` 投影；窗口通过 `updateState()` 同时更新 QAction 与按钮。

## 3. 请求、响应与配置

- `SessionController` 拥有单个工作线程和核心句柄；工作对象排队执行核心操作，返回 GUI 线程后更新 `busy` 和公开快照。
- `busy()` 为真时拒绝冲突请求。Probe 的公开信息与操作完成分开回传，后续 load 必须等忙碌状态解除；不能在尚忙时提交并静默丢失请求。
- 收集熵、后台忙碌、已有密钥分别建模；生成按钮与菜单复用同一操作。
- 语言代码仅 `zh_CN`／`en`，首次默认中文，独立 QSettings 保存 `language`。切换只重译文字，不重建窗口或句柄，不覆盖未提交的注释和口令。
- 中文先从 `QLibraryInfo::TranslationsPath` 加载官方 `qtbase_zh_CN.qm`，再安装自有 JSON 翻译；英文移除两者。官方翻译覆盖文件对话框导航、视图及隐藏文件等标准控件文字。开发环境缺少 qm 时允许自有文字回退；正式包必须包含该 qm，不能只验证应用菜单中文。Qt 自动部署可能将翻译合并为 `qt_zh_CN.qm`，正式包禁用自动翻译合并，显式安装应用实际加载的 `qtbase_zh_CN.qm`；英文使用源文，不要求独立 qm。
- PPK 参数对话框接受前通过 `validateParameters` 在工作线程调用核心完整校验，采用原版的 64 字节占位口令；校验成功才提交设置。正整数检查不能替代内存与并行度等参数关系校验。
- 注释编辑延迟提交时，进入加载、保存、证书选择及参数模态流程先暂停计时器；失败、取消或完整操作结束后通过 `resumeCommentUpdate()` 恢复。禁止在 `busyChanged(false)` 中直接重启：完成信号仍可能进入口令重试模态框，计时器启动的 Comment 会抢占下一次 Load。恢复条件同时检查无后台任务、无熵收集、无待关闭请求、无活动模态框及注释确实待同步。
- 关闭时后台工作不强杀。窗口等待结果并保持事件循环可响应，随后在工作线程释放句柄与随机核心。
- 测试的配置、缓存、运行时和临时路径显式置于项目 `.tmp/`，不污染个人偏好。
- 主窗公钥区域的新增高度分配给 `publicKey` 文本框；说明标签与下方指纹、注释、口令行保持内容高度。不得以固定 `maximumHeight` 限制公钥文本框而让说明标签吸收空白。窗口缩放回归应在中英文分别比较实际控件高度，确认增高后编辑框增长、标签与字段不变，缩回后布局恢复。

## 4. 校验与错误矩阵

| 触发 | 界面结果 |
| --- | --- |
| 后台忙碌 | 禁用冲突动作，拒绝重复提交 |
| 两次口令不一致 | 在选择保存目标之前阻止保存 |
| 无口令保存 | 用户确认后才进入保存 |
| 文件选择／覆盖确认取消 | 不写文件，保持当前编辑状态 |
| 错误口令 | 可重试或取消，原密钥保留 |
| 证书不匹配／核心失败 | 展示类型化错误，不能展示成功 |
| 语言切换 | 标签翻译变化，密钥、注释、口令不变 |

## 5. 正常、基线与错误案例

- 正常：生成后编辑注释和口令，切换英文，输入与指纹保持一致，再保存。
- 基线：首次启动中文，无密钥时只开放生成和加载等适用入口。
- 错误：加载口令取消或参数对话框取消后，当前密钥和原参数均保持不变。
- 回归：输入新注释后立即加载坏文件，关闭错误框后公开公钥与会话注释最终一致；错误口令重试取消也必须恢复注释提交，原密钥指纹保持不变。

## 6. 必需测试

Qt Test 应验证算法切换、动作能力、真实核心完成信号、错误／取消恢复、PPK 参数提交与取消、语言偏好和未提交输入、关闭等待。测试根由 CMake 对构建目录求哈希，使用项目 `.tmp/tests/gui-<构建哈希>/`，避免多个目标环境相互覆盖夹具、配置和截图。测试须在任何配置读写前将 QSettings 默认格式改为 `IniFormat`，并把 UserScope／SystemScope 都指向该根的 `config` 子目录；Windows 的 NativeFormat 不受 XDG 环境变量约束。离屏测试只证明逻辑；XCB／Wayland 运行和 Windows 桌面操作分别记录，不能以离屏通过代替平台实测。Wayland 不支持 `QTest::mouseMove` 所依赖的全局指针 warp；无输入 seat 的 headless 合成器也不能验证剪贴板。需要实际输入时可用专用 Xvfb／Weston 嵌套环境注入鼠标，检查收到事件、熵进度和核心真实完成，保留原始失败边界，不能为凑通过数删改测试。合成器窗口位置以当次运行截图为准，不能硬编码上一次的位置。

## 7. 错误与正确做法

错误：在 GUI 槽中直接调用 `pg_generate`，阻塞界面并与后台全局随机状态并发；切换语言重建窗口清空输入。

正确：使用 `SessionController::generate` 排队调用；通过 `LanguageManager` 和 `retranslate()` 仅刷新文本。敏感字节在工作操作结束后清除，窗口换密钥或退出时清空口令编辑。
