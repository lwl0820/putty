# KDC 下载字节边界独立检查

日期：2026-10-15。本轮仅检查 `gss_kdc/prepare.py` 的一行转换及 `tests/test_safety.py` 的两个新增回归；未修改产品、公共 API、其它代理文件或提交代码。

## 检查结论

范围内未发现保留的代码问题。下载循环使用 `bytearray` 累积内容；`PrivateTree.write` 的 Windows 分支将内容直接传给 `PrivateData.write`，后者声明 `WriteFile` 的第二个参数为 `ctypes.c_void_p`。该转换不接受 `bytearray`，但接受 `bytes`。下载器在完整哈希比对后执行 `bytes(content)`，保持相同内容和长度，再沿既有私有写入路径落盘；没有改变 URL、路径核验、体积和期限限制、SHA256/SHA512 算法或落盘后的复核。

新增成功回归使用真实 `CFUNCTYPE` 的指针参数转换和 `string_at(pointer, length)` 读取数据；只模拟网络来源，随后执行真实私有文件写入与读取。它先证明 `bytearray` 在指针转换阶段拒绝且未执行回调，再分别覆盖 SHA256/SHA512 下载成功和完整内容一致。负例证明两种算法的错误哈希在调用写入前拒绝，且目标文件未创建。

另在内存中编译当前提交的旧 `fetch` 函数，替换模块函数后执行同一个新增成功回归：两个算法均实际触发第二个参数的 `ctypes.ArgumentError`，回调计数保持零。当前改动版本的两个新增回归全部通过。因此回归能够区分旧实现与修复实现，并非仅检查参数或模拟成功。

## 验证

- 本机 Linux／Python 3.14.8：两个新增回归通过，零跳过，0.009 秒；未重复耗时的全组硬期限测试。
- 两个 Python 文件通过 Python 3.11 语法模式 AST 解析、当前解释器内存编译和空白检查；定点 `git diff --check` 通过。
- Lint／TypeCheck：项目未配置对应 Python 命令，本机没有 ruff、mypy 或 pyflakes；不将语法检查记成静态类型检查通过。
- 公共接口和既有 Windows 私有文件契约未变，不需要变更规范；本节保留具体跨平台字节边界证据。

## 原生边界

此次本机检查没有调用 Windows `WriteFile`、ACL 或真实 SDK 下载／离线构建，也没有取得此前失败 CI 的异常栈。它确认确定的类型缺陷及其修复，不证明该缺陷是 Windows 作业失败的唯一原因。后续仍须以仓库 Windows CI 核实原生下载、SDK 准备和真实 AS/TGS；不将本机回归写为 Windows KDC 已验收。
