# 真实 GSSAPI 集成检查

本机需有 MIT Kerberos `krb5kdc`、`kdb5_util`、`kadmin.local`、`kinit`、Docker 权限及编译完成的原版 Plink。SSH 对端默认按宿主选择本机已有镜像：Arch 使用 `archlinux:base`，Ubuntu 22.04／24.04 分别使用 `ubuntu:22.04`／`ubuntu:24.04`；其它宿主须显式传入 `image=`。工具不会拉取镜像，对端镜像本身不能代替目标 Windows／Ubuntu 客户端的实际执行验证。

```bash
PUTTY_SUITE_GSS_TESTS=1 \
PUTTY_SUITE_GSS_PLINK="$PWD/.tmp/build/suite-cli-tests/suite-cli/plink" \
python3 -B -m unittest discover -s tools/suite/gss_tests -v
```

四项真实验证覆盖 GSSAPI 成功、真实服务票据请求的 1.5 秒网络延迟、没有凭据时失败，以及等待票据时关闭后回收进程/线程/套接字。客户端不提供公钥或代理凭据；成功必须同时有实际输出、GSSAPI 完成日志与 KDC 的 TGS 请求记录。

调用方先创建 `IsolationEnvironment`，再用 `KerberosRealm(environment)` 和 `GSSSSHServer(environment, realm=realm)` 的上下文管理器。启动受测子进程时显式传入 `realm.client_environment()`。`reset_client(delay_seconds=1.5)` 直连获取仅含 TGT 的新缓存，下次 GSS 认证通过本机代理取得服务票据。`delay_started` 在代理已收到真实请求时置位；`delay_report()` 返回请求/延迟命中、实际等待时间及 KDC TGS 计数，便于终端用例证明正在测量真实同步认证。

域数据库、master stash、keytab、票据、重放缓存、日志和 Docker 配置均位于同一次项目 `.tmp/` 私有根。所有 Kerberos 环境变量显式覆盖，DNS 发现关闭，KDC 只监听回环，父环境与 HOME 保持。先关闭 SSH 对端，再关闭域，最后清理环境；不得上传这些私有运行目录。
