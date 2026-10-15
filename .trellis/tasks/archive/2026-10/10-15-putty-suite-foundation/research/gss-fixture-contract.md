# 真实 GSSAPI 夹具边界

当前缺口是终端尚未在真实 Kerberos 服务票据请求阻塞时测量 GUI 响应。实施边界为 `tools/suite/gss_fixture.py` 和 `tools/suite/gss_tests/`：复用现有私有目录与受控容器 SSH 对端，建立仅监听本机回环的 MIT KDC，使用本次随机密钥、客户端票据缓存、服务端 keytab 和重放缓存。

延迟在本地 TCP 代理收到真实 KDC 请求之后、转发给真实 KDC 之前注入；不替换 GSS 库、不在核心中模拟认证。重置客户端时通过直连配置取得全新 TGT，受测认证通过代理取得服务票据，保留命中次数、实测延迟和 KDC 的 TGS 请求数。关闭必须中断代理等待并回收进程、套接字和线程。

公共接口为 `KerberosRealm(environment)`、`start/close`、`reset_client(delay_seconds=...)`、`client_environment()`、`delay_started`、`delay_report()`；`GSSSSHServer(environment, realm=realm)` 只将本次 Kerberos 变量注入受控 SSH 容器。环境从公共隔离接口复制，不修改父进程或夹具私有 `_base`。产品与共享运行时均不改动。

首个原型已完成真实 Plink GSSAPI 认证。正式实现需补真实成功、无票据失败、延迟服务票据以及在途关闭回收验证；这些本机 Arch 对端结果不替代目标 Ubuntu 和 Windows 的 GSSAPI 验证。

## 本机实施结果

正式实现的四项真实集成检查已通过，0 跳过，15.308 秒，日志 `.tmp/suite-validation/gss-fixture-tests.log`。初轮两个成功分支只因测试错误地多写了一个日志单词而失败，真实认证和命令输出均已成功；核对原版实际日志并修正断言后完整重跑，未改变产品或放宽认证条件。

实际成功要求 Plink 的 `gssapi-with-mic`、GSSAPI 完成日志、退出 0、精确命令输出与 KDC `TGS_REQ`；延迟用例另要求真实代理请求命中和实测至少 1.5 秒。无票据用例同时指向不存在的客户端缓存/keytab，未提供公钥；在途关闭用例先等代理收到请求，证明客户端仍在运行，再关闭域并验证 KDC/代理端口和线程回收。

参考 MIT 官方 [KDC 命令](https://web.mit.edu/kerberos/krb5-latest/doc/admin/admin_commands/krb5kdc.html)、[KDC 配置](https://web.mit.edu/kerberos/krb5-current/doc/admin/conf_files/kdc_conf.html)、[客户端环境](https://web.mit.edu/Kerberos/krb5-latest/doc/user/user_config/kerberos.html) 和 [重放缓存](https://web.mit.edu/kerberos/krb5-latest/doc/basic/rcache_def.html)。这些是路径与协议配置依据，运行成功仍以实际测试为准。终端 GUI 心跳需由终端任务另行验证。
