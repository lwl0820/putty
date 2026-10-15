#!/usr/bin/env python3
"""项目 .tmp/ 内的真实 MIT Kerberos 与 GSSAPI SSH 对端。"""

import os
from pathlib import Path
import selectors
import shutil
import socket
import subprocess
import threading
import time
import uuid

from tools.suite.container_ssh_fixture import ContainerSSHServer
from tools.suite.isolation import IsolationError, private_directory, require_linux
from tools.suite.ssh_fixture import FixtureUnavailable


def _without_kerberos(environment):
    return {name: value for name, value in environment.items()
            if not name.startswith("KRB5") and name != "GSS_MECH_CONFIG"}


class _KDCProxy:
    """只代理回环 TCP；延迟发生于收到实际请求之后。"""

    def __init__(self, target_port):
        self.target_port = target_port
        self.listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(8)
        self.listener.settimeout(0.1)
        self.port = self.listener.getsockname()[1]
        self.started = threading.Event()
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._workers = []
        self._connections = set()
        self._delay = 0.0
        self._requests = 0
        self._delayed_requests = 0
        self._delay_elapsed = []
        self._error = None
        self._thread = threading.Thread(target=self._accept, daemon=True)
        self._thread.start()

    def configure(self, delay):
        with self._lock:
            self._delay = delay
            self._requests = self._delayed_requests = 0
            self._delay_elapsed.clear()
            self.started.clear()

    def report(self):
        with self._lock:
            if self._error is not None:
                raise RuntimeError("本地 KDC 字节代理失败") from self._error
            return {"requests": self._requests, "delayed_requests": self._delayed_requests,
                    "delay_seconds": self._delay, "observed_delays": list(self._delay_elapsed),
                    "active_connections": len(self._connections)}

    def _accept(self):
        try:
            while not self._stop.is_set():
                try:
                    client, _ = self.listener.accept()
                except TimeoutError:
                    continue
                with self._lock:
                    if len(self._connections) >= 16:
                        client.close()
                        continue
                    self._connections.add(client)
                worker = threading.Thread(target=self._relay, args=(client,), daemon=True)
                self._workers.append(worker)
                worker.start()
        except OSError as error:
            if not self._stop.is_set():
                self._error = error

    def _relay(self, client):
        upstream = None
        try:
            client.settimeout(8)
            initial = client.recv(65536)
            if not initial:
                return
            with self._lock:
                delay = self._delay
                self._requests += 1
                if delay:
                    self._delayed_requests += 1
            if delay:
                begin = time.monotonic()
                self.started.set()
                interrupted = self._stop.wait(delay)
                with self._lock:
                    self._delay_elapsed.append(time.monotonic() - begin)
                if interrupted:
                    return
            upstream = socket.create_connection(("127.0.0.1", self.target_port), timeout=8)
            with self._lock:
                self._connections.add(upstream)
            upstream.sendall(initial)
            with selectors.DefaultSelector() as selector:
                selector.register(client, selectors.EVENT_READ, upstream)
                selector.register(upstream, selectors.EVENT_READ, client)
                while not self._stop.is_set():
                    for key, _ in selector.select(0.1):
                        data = key.fileobj.recv(65536)
                        if not data:
                            return
                        key.data.sendall(data)
        except (ConnectionError, TimeoutError):
            # 受测进程在真实认证期间关闭属于有效场景。
            pass
        except OSError as error:
            if not self._stop.is_set():
                self._error = error
        finally:
            for connection in (client, upstream):
                if connection is not None:
                    with self._lock:
                        self._connections.discard(connection)
                    connection.close()

    def close(self):
        self._stop.set()
        self.listener.close()
        with self._lock:
            connections = list(self._connections)
        for connection in connections:
            try:
                connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        self._thread.join(timeout=3)
        for worker in self._workers:
            worker.join(timeout=3)
        if self._thread.is_alive() or any(worker.is_alive() for worker in self._workers):
            raise RuntimeError("KDC 代理线程未结束")


class KerberosRealm:
    """不读取个人配置/票据，也不修改传入隔离环境或进程环境。"""

    realm = "SUITE.TEST"
    principal = "suite@SUITE.TEST"

    def __init__(self, environment):
        require_linux()
        environment.validate()
        self.environment = environment
        self.tools = {name: shutil.which(name) for name in
                      ("krb5kdc", "kdb5_util", "kadmin.local", "kinit")}
        if not all(self.tools.values()):
            raise FixtureUnavailable("真实 GSSAPI 夹具缺少 MIT Kerberos KDC/管理/客户端工具")
        self.root = environment.root / "kerberos"
        if self.root.exists():
            raise IsolationError("同一隔离环境只能创建一个 Kerberos 域")
        private_directory(self.root)
        self._process = self._output = self._proxy = None
        self._prepared = self._closed = False
        self._variables = {}
        self._tgs_baseline = 0

    def __enter__(self):
        return self.start()

    def __exit__(self, *unused):
        self.close()

    def client_environment(self):
        if self._closed or not self._variables:
            raise IsolationError("Kerberos 域没有运行")
        self.environment.validate()
        return {**_without_kerberos(self.environment.environment()), **self._variables}

    def _run(self, arguments, *, direct=False):
        child = self.client_environment()
        if direct:
            child["KRB5_CONFIG"] = str(self.root / "direct.conf")
        result = subprocess.run(list(map(str, arguments)), cwd=self.root, env=child,
                                capture_output=True, timeout=20, umask=0o077)
        if result.returncode:
            raise RuntimeError("测试 Kerberos 命令失败：" + result.stderr.decode("utf-8", errors="replace"))
        return result

    def _configuration(self, port):
        return f"""[libdefaults]
 default_realm = {self.realm}
 dns_lookup_kdc = false
 dns_lookup_realm = false
 dns_canonicalize_hostname = false
 rdns = false
 udp_preference_limit = 1
 default_ccache_name = FILE:{self.root}/client.ccache
 default_keytab_name = FILE:{self.root}/server.keytab
 default_client_keytab_name = FILE:{self.root}/client.keytab
 default_rcache_name = file2:{self.root}/server.rcache
[realms]
 {self.realm} = {{
  kdc = 127.0.0.1:{port}
 }}
[domain_realm]
 localhost = {self.realm}
 127.0.0.1 = {self.realm}
[logging]
 default = FILE:{self.root}/library.log
"""

    def start(self):
        if self._prepared or self._closed:
            raise IsolationError("Kerberos 域不能重复启动")
        self._prepared = True
        try:
            with socket.socket() as reservation:
                reservation.bind(("127.0.0.1", 0))
                self.kdc_port = reservation.getsockname()[1]
            self._proxy = _KDCProxy(self.kdc_port)
            self._variables = {
                "KRB5_CONFIG": str(self.root / "krb5.conf"),
                "KRB5_KDC_PROFILE": str(self.root / "kdc.conf"),
                "KRB5CCNAME": "FILE:" + str(self.root / "client.ccache"),
                "KRB5_KTNAME": "FILE:" + str(self.root / "server.keytab"),
                "KRB5_CLIENT_KTNAME": "FILE:" + str(self.root / "client.keytab"),
                "KRB5RCACHENAME": "file2:" + str(self.root / "server.rcache"),
                "KRB5RCACHEDIR": str(self.root), "KRB5_TRACE": str(self.root / "trace.log"),
            }
            for name, port in (("krb5.conf", self._proxy.port), ("direct.conf", self.kdc_port)):
                self.environment.write_private("kerberos/" + name, self._configuration(port).encode("utf-8"))
            self.environment.write_private("kerberos/kdc.conf", f"""[kdcdefaults]
 kdc_listen = 127.0.0.1:{self.kdc_port}
 kdc_tcp_listen = 127.0.0.1:{self.kdc_port}
[realms]
 {self.realm} = {{
  database_name = {self.root}/principal
  key_stash_file = {self.root}/master.stash
  acl_file = {self.root}/kadm5.acl
 }}
[logging]
 kdc = FILE:{self.root}/kdc.log
 default = FILE:{self.root}/library.log
""".encode("utf-8"))
            self._run([self.tools["kdb5_util"], "-r", self.realm, "create", "-s", "-P", uuid.uuid4().hex])
            for principal in ("suite", "host/127.0.0.1", "host/localhost"):
                self._run([self.tools["kadmin.local"], "-r", self.realm, "-q", "addprinc -randkey " + principal])
            for keytab, principals in (("client", "suite"), ("server", "host/127.0.0.1 host/localhost")):
                self._run([self.tools["kadmin.local"], "-r", self.realm, "-q",
                           f"ktadd -k {self.root}/{keytab}.keytab {principals}"])
            output = self.environment.write_private("kerberos/foreground.log", b"")
            self._output = open(output, "ab", buffering=0)
            self._process = subprocess.Popen(
                [self.tools["krb5kdc"], "-n", "-r", self.realm, "-P", str(self.root / "kdc.pid")],
                cwd=self.root, env=self.client_environment(), stdout=self._output,
                stderr=subprocess.STDOUT, umask=0o077)
            deadline = time.monotonic() + 5
            while True:
                if self._process.poll() is not None:
                    raise RuntimeError("测试 KDC 提前退出")
                try:
                    with socket.create_connection(("127.0.0.1", self.kdc_port), timeout=0.1):
                        break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise RuntimeError("测试 KDC 未在回环地址就绪")
                    time.sleep(0.02)
            self.reset_client()
            return self
        except BaseException:
            self.close()
            raise

    @property
    def delay_started(self):
        if self._closed or self._proxy is None:
            raise IsolationError("Kerberos 域没有运行")
        return self._proxy.started

    def _tgs_count(self):
        try:
            return sum("TGS_REQ" in line for line in (self.root / "kdc.log").read_text(
                encoding="utf-8", errors="replace").splitlines())
        except FileNotFoundError:
            return 0

    def reset_client(self, *, delay_seconds=0.0):
        """重新获取仅有 TGT 的缓存，下一次服务票据请求必经代理。"""
        if not isinstance(delay_seconds, (float, int)) or not 0 <= delay_seconds <= 10:
            raise IsolationError("KDC 故障注入延迟必须为 0 到 10 秒")
        self.client_environment()
        if self._proxy.report()["active_connections"]:
            raise IsolationError("不能在认证仍有连接时替换票据缓存")
        self._run([self.tools["kinit"], "-k", "-t", self.root / "client.keytab", self.principal], direct=True)
        self._tgs_baseline = self._tgs_count()
        self._proxy.configure(float(delay_seconds))

    def delay_report(self):
        if self._proxy is None:
            raise IsolationError("Kerberos 域没有运行")
        return {**self._proxy.report(), "tgs_requests": self._tgs_count() - self._tgs_baseline,
                "kdc_running": self._process is not None and self._process.poll() is None}

    def close(self):
        if self._closed:
            return
        try:
            if self._proxy is not None:
                self._proxy.close()
        finally:
            try:
                if self._process is not None and self._process.poll() is None:
                    self._process.terminate()
                    try:
                        self._process.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        self._process.kill()
                        self._process.wait(timeout=3)
            finally:
                if self._output is not None:
                    self._output.close()
                self._closed = True


class GSSSSHServer(ContainerSSHServer):
    """公钥仅用于基础夹具探针；受测 GSS 客户端不得提供该私钥。"""

    def __init__(self, environment, *, realm, **kwargs):
        if realm.environment is not environment:
            raise IsolationError("GSS 服务端与 Kerberos 域必须共享本次隔离根")
        realm.client_environment()
        self.realm = realm
        super().__init__(environment, **kwargs)

    def _prepare(self):
        super()._prepare()
        path = self.environment.root / "ssh/container_sshd_config"
        configuration = path.read_text(encoding="utf-8").replace(
            "AuthenticationMethods publickey", "AuthenticationMethods publickey gssapi-with-mic")
        configuration += "GSSAPIAuthentication yes\nGSSAPIStrictAcceptorCheck no\nGSSAPICleanupCredentials no\n"
        path.write_text(configuration, encoding="utf-8")
        self.environment.write_private("container/home/.k5login", (self.realm.principal + "\n").encode("ascii"))

    def _docker(self, *arguments, **kwargs):
        arguments = list(arguments)
        if arguments and arguments[0] == "create":
            index = arguments.index("--entrypoint")
            values = []
            for name, value in self.realm.client_environment().items():
                if name.startswith("KRB5"):
                    values.extend(["--env", name + "=" + value])
            arguments[index:index] = values
        return super()._docker(*arguments, **kwargs)
