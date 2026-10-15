# Linux CI 与容器 SSH 夹具独立复核

日期：2026-10-15。由主会话在 Pageant 独立审查结束后追加派发。审查范围为 `tools/suite/container_ssh_fixture.py`、两份 `container_tests`、`run_linux_ci.py`/`ci_tests` 和 `.github/workflows/putty-suite-linux.yml`。本轮只拥有夹具/相关测试和本记录；工作流/CI 运行器修订由主会话执行，没有覆盖公共构建或其它代理文件。

## 发现与修订

### R1：启用真实 GSS 测试后缺少 Plink 路径（主会话已修）

- 初读工作流仅有 `PUTTY_SUITE_GSS_TESTS=1`，没有 `PUTTY_SUITE_GSS_PLINK`。`gss_tests/test_gss_fixture.py::setUp` 会在创建 KDC/容器前要求该变量指向已构建文件。
- 确定失败探针：显式移除该变量、启用 GSS 后运行原发现命令，得到 **4/4 setUp 失败，退出 1**，没有启动服务。证据 `.tmp/suite-final-review-temp/ci-gss-missing-client.log`。
- 已向主会话报告并核对 `SuiteCLI.cmake` 的真实单配置产物路径。最终工作流已加入 `${{ github.workspace }}/.tmp/build/suite-linux/suite-cli/plink`。这项是接线修订与源审查结果，未将本机失败探针改称 Ubuntu GSS 运行通过。

### R2：新 Pageant 回复边界回归未进入必需集合（主会话已修）

- Pageant 独立审查增加 `pageant-gui-oversizedPassphraseKeepsPrompt` 后，CI 的旧必需集合仍只有七个 GUI 场景。虽然实际已注册测试仍须全部执行，但若将来漏注册该场景，旧集合不会发现。
- 主会话已补入新名称。最终 CI 契约 **4/4** 通过，包含完整组件必需集合和重复名称拒绝。

## 夹具实际路径检查

- 默认镜像只接受 Arch 或目标 Ubuntu22.04/24.04，并按 `os-release` 选本机已有匹配标签；未知系统要求显式镜像。`image inspect` 取得固定 image ID 后才 create/start，夹具使用 `--pull=never`，未新增下载步骤。工作流的显式拉取与夹具本身的无拉取契约分开。
- 所有宿主工具从固定系统 PATH 查找，包含 Ubuntu 的 `/usr/sbin`，不把 setup-python、venv 或个人 bin 路径挂进容器。入口 sshd、Python、shell、nologin 使用查得的实际路径；远端 PATH 同样固定。
- `_system_mounts()` 仅允许真实系统目录或解析到 `/usr` 内的系统链接；保留 `/usr`、`/bin`、`/sbin`、`/lib`、`/lib64` 的目的位置，动态加载器缓存只接受普通文件。系统挂载全部只读；可写挂载仅是本次项目 `.tmp` 隔离根及其 temp/runtime 子目录，passwd/group/shadow/nsswitch 为本次专用文件。
- 容器继续使用当前普通用户 UID/GID、断网、只读根、无 capabilities、禁止提权、限进程/内存/CPU；专用账户只允许公钥，未访问个人账号、密钥或代理。Docker CLI 固定本地 daemon 和私有 config，清除继承的 DOCKER_*。
- 新真实回归核对容器实际看到的 sshd、Python 和 x86_64 加载器 SHA256，能发现 Docker daemon 属于不同宿主文件系统导致挂载字节不一致。报告同时保留实际宿主发行版、镜像和 source/destination 信息。
- 关闭路径先停本地监听/代理线程，再核对本次标签并移除自己容器，最后回收 relay 子进程；未清理用户其它容器。活动命令关闭、二进制 exec/SCP/SFTP 和回环转发均有真实本机复跑。

## CI 实际结果门检查

- 两个 job 使用 `runs-on: ubuntu-22.04/24.04` 的宿主执行器，未配置 job 容器来冒充目标系统；固定 Qt6.8.3 与 GCC11/13，运行时及新增五组件全部启用。PuTTYgen 本轮关闭，所需测试 keygen 为隔离辅助目标。
- 配置明确开启容器测试；真实容器/KDC 依赖和所需环境已接入。完整 `ctest --show-only=json-v1` 必须包含每个必需名称且不能重复；执行不加标签筛选，拒绝空测试集合。JUnit 名称集合必须与注册集合一致，拒绝 failure/error/skipped/notrun/disabled 和非法耗时；非零 CTest 退出仍失败。
- 每次运行先删除预期 JUnit，防止未产生新结果时误收旧成功。上传仅 `.tmp/linux-ci/reports/*.json` 的用例名称/耗时/状态摘要，未递归上传私钥、运行根、KDC 缓存或原始 system-out。
- 实际在本机 Arch 调用运行器，**退出 1**，报告 `passed=false` 且明确要求 Ubuntu22.04/24.04 x64；没有启动伪造 Ubuntu 跑法。证据 `arch-ci-rejection.log`、`arch-ci-rejection.json`。

## 本轮验证

| 检查 | 结果 | 证据（均在 `.tmp/suite-final-review-temp/`） |
| --- | --- | --- |
| CI 契约测试，修订前 | **4/4，0 skip** | `ci-contract-tests-before.log` |
| CI 契约测试，必需集合修订后 | **4/4，0 skip** | `ci-contract-tests-final.log` |
| 独立容器创建接线测试 | **3/3，0 skip**，未启动 Docker | `container-setup-review.log` |
| 真实本机容器与接线合跑 | **9/9，0 skip，9.450 秒**；其中 6 项真实 Arch+3 项接线 | `container-all-review.log` |
| 缺少 GSS Plink 环境的初始失败 | **4 失败、退出 1**，证明确切原因 | `ci-gss-missing-client.log` |
| Arch 平台拒绝探针 | **退出 1 / passed=false** | `arch-ci-rejection.log/json` |
| Python AST | **5 个自有源通过** | container fixture、runner、container_tests 两源、ci_tests 一源 |
| 空白检查 | **git diff --check 通过** | 真实工作树命令退出 0 |

没有修改夹具或 CI 测试源，因为当前所审实现没有需要局部自修的确定性缺陷；两项工作流/运行器接线问题已交所有者修正。未设置 suppressions，也未把真实互操作替换为 mock。

## 尚未修订的边界

1. 本轮真实容器结果是 **Arch 宿主**；模拟 `os-release` 的两 Ubuntu setup 接线测试只能证明镜像和 sshd 参数，不能证明 Ubuntu 宿主/Qt6.8.3/真实桌面运行。本轮没有运行或重复父会话已有 Ubuntu 环境，也没有使用嵌套 Docker 冒充目标宿主。
2. 当前严格跳过拒绝针对 **CTest/JUnit 测试项层级**。内部 Python unittest 如果自行 `OK (skipped=1)` 而外层返回 0，JUnit 不会自动产生 skipped 节点；例如 Pageant `--branches` 若被未来接线遗漏，内部提示失败分支会 skip。当前 Pageant 实际注册传该参数、本轮 17/17 无 skip；当前容器 9/9 亦无 skip。已建议主会话如需将契约扩至内部用例，采用包装驱动将 `result.skipped` 转成非零或结构化结果，而不是扫描任意日志子串。本轮未擅改其它产品测试驱动。
3. 工作流配置与本轮本机复验不能替代这份新工作流在 GitHub 两个 Ubuntu 宿主上的实际完成结果；X11/Wayland 与发布包也仍另验。任务状态保持进行中。
