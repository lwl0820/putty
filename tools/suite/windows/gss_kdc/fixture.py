"""正式KDC与真实TGS延迟代理；敏感路径只在进程内传递。"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import time
import uuid

from tools.suite.windows.support import ValidationError, project_path
from .archive import SafeArchive
from .prepare import LOCK, source_hash
from .process import BoundedProcess, run_bounded
from .security import PrivateTree, closed_environment, digest
from .proxy import DelayedKDCProxy


class PreparedKDC:
    def __init__(self, root, dotnet, assembly, state):
        self.root, self.dotnet, self.assembly, self._state = root, dotnet, assembly, state

    @classmethod
    def from_directory(cls, root):
        root = project_path(root, must_exist=True)
        tree = PrivateTree(root, existing=True)
        sdk_tree = None
        try:
            path = tree.check_file(root / "prepared.json")
            if path.stat().st_size > 32768:
                raise ValidationError("KDC准备状态过长")
            state = json.loads(path.read_text(encoding="utf-8"))
            rid = "win-x64" if os.name == "nt" else "linux-x64"
            if (state.get("schema") != 1 or state.get("source_sha256") != source_hash() or state.get("rid") != rid or
                    not isinstance(state.get("files"), dict) or not 2 <= len(state["files"]) <= 32):
                raise ValidationError("KDC准备状态与当前源码/目标不符")
            artifact_name = state.get("artifact", "")
            if not re.fullmatch(r"artifact-[0-9a-f]{32}", artifact_name):
                raise ValidationError("KDC准备状态目录非法")
            artifact = tree.path(root / artifact_name)
            tree.directory(artifact, existing=True)
            actual = {path.name for path in artifact.iterdir()}
            if actual != set(state["files"]):
                raise ValidationError("KDC宿主产物集合已变化")
            for name, expected in state["files"].items():
                if (not re.fullmatch(r"[A-Za-z0-9_.-]+\.(dll|json)", name) or
                        not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected) or
                        digest(tree.check_file(artifact / name)) != expected):
                    raise ValidationError("KDC宿主文件已变化")
            sdk = next(value for value in LOCK["dotnet_sdk_files"] if value["rid"] == rid)
            archive = tree.check_file(root / "downloads" / sdk["url"].rsplit("/", 1)[1])
            import hashlib
            value = hashlib.sha512()
            with archive.open("rb") as source:
                while data := source.read(1024 * 1024):
                    value.update(data)
            if value.hexdigest() != sdk["hash"]:
                raise ValidationError("KDC SDK归档已变化")
            sdk_tree = PrivateTree(root / "sdk", existing=True)
            with SafeArchive(archive) as package:
                package.verify(sdk_tree)
            return cls(root, root / "sdk" / ("dotnet.exe" if os.name == "nt" else "dotnet"), artifact / "KdcFixture.dll", state)
        finally:
            if sdk_tree:
                sdk_tree.close()
            tree.close()


class PortableKDC:
    def __init__(self, prepared: PreparedKDC, *, parent, delay_seconds=1.6):
        if not isinstance(prepared, PreparedKDC):
            raise ValidationError("必须使用已核验的KDC准备结果")
        self.prepared = prepared
        parent = project_path(parent, must_exist=True)
        self.root = project_path(parent / ("kdc-" + uuid.uuid4().hex))
        self.child = self.proxy = None
        self.kdc_port = None
        self._summary = None
        self._closed = False
        self._data_cleaned = False
        parent_guard = PrivateTree(parent, existing=True)
        try:
            self.tree = PrivateTree(self.root)
        finally:
            parent_guard.close()
        try:
            self.environment = closed_environment(self.tree)
            self.cache = self.tree.expect_file(self.root / "client.ccache")
            self.tree.expect_file(self.root / "trace.log")
            self.keytab = self.tree.write(self.root / "server.keytab", b"")
            self.client_keytab = self.tree.write(self.root / "client.keytab", b"")
            self.policy_probes = self.tree.write(self.root / "policy-probes.json", b"")
            self.config = self.root / "krb5.conf"
            self.direct_config = self.root / "direct.conf"
            self.delay_seconds = delay_seconds
        except BaseException:
            self.tree.close()
            raise

    def _configuration(self, port):
        root = self.root.as_posix()
        return f"""[libdefaults]
 default_realm = SUITE.TEST
 dns_lookup_kdc = false
 dns_lookup_realm = false
 dns_canonicalize_hostname = false
 rdns = false
 udp_preference_limit = 1
 default_ccache_name = FILE:{root}/client.ccache
 default_keytab_name = FILE:{root}/server.keytab
 default_client_keytab_name = FILE:{root}/absent-client.keytab
 default_tkt_enctypes = aes256-cts-hmac-sha1-96
 default_tgs_enctypes = aes256-cts-hmac-sha1-96
 permitted_enctypes = aes256-cts-hmac-sha1-96
 forwardable = false
 proxiable = false
[realms]
 SUITE.TEST = {{
  kdc = 127.0.0.1:{port}
 }}
[domain_realm]
 localhost = SUITE.TEST
""".encode("ascii")

    def start(self):
        if self._closed or self.child is not None:
            raise ValidationError("KDC已经启动或关闭，不能重复启动")
        try:
            # 启动前及启动后核验同一私有文件；C#只打开既有文件，不替换ACL。
            self.tree.check_file(self.keytab)
            self.tree.check_file(self.client_keytab)
            self.tree.check_file(self.policy_probes)
            self.child = BoundedProcess([self.prepared.dotnet, self.prepared.assembly, self.root],
                cwd=self.root, environment=self.environment, timeout=90)
            ready = json.loads(self.child.line().decode("utf-8"))
            if (set(ready) != {"schema", "ready", "realm", "client", "service", "kdc_port"} or
                    ready["schema"] != 1 or ready["ready"] is not True or ready["realm"] != "SUITE.TEST" or
                    ready["client"] != "suite@SUITE.TEST" or ready["service"] != "host/localhost@SUITE.TEST" or
                    type(ready["kdc_port"]) is not int or not 1024 <= ready["kdc_port"] <= 65535):
                raise ValidationError("KDC启动元数据非法")
            self.kdc_port = ready["kdc_port"]
            self.tree.check_file(self.keytab)
            self.tree.check_file(self.client_keytab)
            self.tree.check_file(self.policy_probes)
            self.proxy = DelayedKDCProxy(self.kdc_port, delay_seconds=self.delay_seconds)
            self.tree.write(self.config, self._configuration(self.proxy.port))
            self.tree.write(self.direct_config, self._configuration(self.kdc_port))
            self.environment.update({"KRB5_CONFIG": str(self.config), "KRB5CCNAME": "FILE:" + str(self.cache),
                "KRB5_KTNAME": "FILE:" + str(self.keytab),
                "KRB5_CLIENT_KTNAME": "FILE:" + str(self.root / "absent-client.keytab"),
                "KRB5RCACHETYPE": "dfl", "KRB5RCACHEDIR": str(self.root),
                "KRB5_TRACE": str(self.root / "trace.log")})
            return self
        except BaseException:
            self.close(failed=True)
            raise

    def reset_client(self, kinit):
        if self._closed or self.child is None:
            raise ValidationError("KDC尚未运行")
        self.tree.check_file(self.client_keytab)
        if not Path(kinit).is_absolute() or not Path(kinit).is_file():
            raise ValidationError("kinit必须是已核验的绝对工具路径")
        environment = {**self.environment, "KRB5_CONFIG": str(self.direct_config)}
        run_bounded([kinit, "-k", "-t", self.client_keytab, "suite@SUITE.TEST"], cwd=self.root,
                    environment=environment, timeout=15, output_limit=8192)
        self.tree.check_file(self.cache, harden=True)
        trace = self.root / "trace.log"
        if trace.exists():
            self.tree.check_file(trace, harden=True)
        self.proxy.reset()

    @property
    def delay_started(self):
        if not self.proxy:
            raise ValidationError("KDC代理尚未就绪")
        return self.proxy.delay_started

    def delay_report(self):
        if not self.proxy:
            raise ValidationError("KDC代理尚未就绪")
        return self.proxy.report()

    def register_artifact(self, path, *, directory=False):
        """只登记可信调用者按固定生产规则确认的确切文件或目录。"""
        if self._closed:
            raise ValidationError("KDC已经关闭，不能登记产物")
        return self.tree.register_artifact(path, directory=directory)

    def close(self, *, failed=False):
        if self._closed:
            return
        self._closed = True
        deadline = time.monotonic() + 6
        try:
            if self.proxy:
                self.proxy.close(deadline=deadline)
            if self.child:
                if failed:
                    self.child.abort()
                else:
                    self.child.finish(b"stop\n", deadline=deadline)
                    summary = json.loads(self.child.line(timeout=1).decode("utf-8"))
                    expected = {"schema", "stopped", "accepted", "completed", "rejected", "malformed",
                                "as_requests", "tgs_requests", "active_connections", "active_workers"}
                    if (set(summary) != expected or summary["schema"] != 1 or summary["stopped"] is not True or
                            any(type(summary[name]) is not int or summary[name] < 0 for name in expected - {"schema", "stopped"}) or
                            summary["active_connections"] or summary["active_workers"]):
                        raise ValidationError("KDC关闭摘要非法或资源仍活跃")
                    self._summary = summary
        finally:
            try:
                if self.child:
                    self.child.abort()
                if self._summary is not None and not failed:
                    self.tree.remove()
                    self._data_cleaned = True
            finally:
                self.tree.close()

    def report(self):
        if not self._closed or self._summary is None or not self._data_cleaned:
            raise ValidationError("KDC尚无成功关闭证据")
        return {"schema": 1, "kind": "isolated-real-kdc", "passed": True,
                "realm": "SUITE.TEST", "encryption": "aes256-cts-hmac-sha1-96",
                "private_data_removed": self._data_cleaned,
                "native_windows": os.name == "nt", "kdc": self._summary, "proxy": self.delay_report()}

    def __enter__(self):
        return self.start()

    def __exit__(self, kind, *_):
        self.close(failed=kind is not None)
