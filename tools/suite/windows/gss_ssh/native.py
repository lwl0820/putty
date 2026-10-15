"""独立测试进程的真实 MIT 凭据、上下文和 MIC；不用于产品运行。"""
import ctypes as C
import os
from pathlib import Path
import struct
import sys

from tools.suite.windows.gss_kdc.security import PrivateTree, closed_environment
from tools.suite.windows.gss_mit.mit_gss import Buffer, Oid, OidSet, P, U32, KERBEROS_OID, verify_layout
from tools.suite.windows.support import ValidationError, project_path

UP, PP = C.POINTER(U32), C.POINTER(P)
MAX_TOKEN = 128 * 1024
PRINCIPAL_OID = KERBEROS_OID + b"\x01"
REQUIRED_FLAGS = 2 | 32


class GssError(ValidationError):
    """仅固定操作名和数值状态，不附带主体、票据或库日志。"""
    def __init__(self, operation, major, minor):
        self.operation, self.major, self.minor = operation, int(major), int(minor)
        super().__init__(f"MIT GSS {operation} 失败：major={self.major}，minor={self.minor}")


class KeyValue(C.Structure):
    _fields_ = [("key", C.c_char_p), ("value", C.c_char_p)]


class KeyValueSet(C.Structure):
    _fields_ = [("count", U32), ("elements", C.POINTER(KeyValue))]


def buffer(data):
    if not isinstance(data, bytes) or len(data) > MAX_TOKEN:
        raise ValidationError("GSS 输入必须为有界字节串")
    memory = C.create_string_buffer(data)
    return Buffer(len(data), C.cast(memory, P)), memory


def oid(data):
    memory = C.create_string_buffer(data)
    return Oid(len(data), C.cast(memory, P)), memory


def verify_environment(root, *, missing_cache=False):
    root = project_path(root, must_exist=True)
    tree = PrivateTree(root, existing=True)
    try:
        # 从同一固定规则重建并核对实际进程环境，必须在任何 MIT DLL 加载前完成。
        expected = closed_environment(tree)
        expected.update({
            "KRB5_CONFIG": str(root / "krb5.conf"),
            "KRB5CCNAME": "FILE:" + str(root / ("absent.ccache" if missing_cache else "client.ccache")),
            "KRB5_KTNAME": "FILE:" + str(root / "server.keytab"),
            "KRB5_CLIENT_KTNAME": "FILE:" + str(root / "absent-client.keytab"),
            "KRB5RCACHETYPE": "dfl", "KRB5RCACHEDIR": str(root),
            "KRB5_TRACE": str(root / "trace.log"),
        })
        if sys.platform != "win32" and any(key in os.environ for key in ("PUTTYDIR", "PUTTYRANDOMSEED")):
            tree.directory(root / "putty", existing=True)
            expected.update({"PUTTYDIR": str(root / "putty"),
                             "PUTTYRANDOMSEED": str(root / "putty" / "randomseed")})
        def normalize(environment):
            result = {}
            for key, value in environment.items():
                key = key.upper() if sys.platform == "win32" else key
                if key in result:
                    raise ValidationError("GSS 子进程环境包含重复名称")
                result[key] = value
            return result
        if normalize(os.environ) != normalize(expected):
            raise ValidationError("GSS 子进程实际环境与本次封闭环境不符")
        for key in ("HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "XDG_CONFIG_HOME",
                    "XDG_CACHE_HOME", "XDG_DATA_HOME", "XDG_RUNTIME_DIR", "TMP", "TEMP", "TMPDIR"):
            tree.directory(project_path(os.environ.get(key, ""), must_exist=True), existing=True)
        config = tree.check_file(os.environ.get("KRB5_CONFIG", ""))
        if config != root / "krb5.conf":
            raise ValidationError("GSS 子进程必须使用本次正式 KDC 配置")
        result = {}
        for key, filename in (("KRB5CCNAME", "absent.ccache" if missing_cache else "client.ccache"),
                              ("KRB5_KTNAME", "server.keytab"), ("KRB5_CLIENT_KTNAME", "absent-client.keytab")):
            value = os.environ.get(key, "")
            if not value.startswith("FILE:") or project_path(value[5:]) != root / filename:
                raise ValidationError("GSS 身份存储必须为本次确切的 FILE 路径")
            result[key] = root / filename
            absent = key == "KRB5_CLIENT_KTNAME" or key == "KRB5CCNAME" and missing_cache
            if absent:
                try:
                    result[key].lstat()
                except FileNotFoundError:
                    pass
                else:
                    raise ValidationError("GSS 缺失身份文件已经存在")
            else:
                tree.check_file(result[key])
        if os.environ.get("KRB5RCACHETYPE") != "dfl" or project_path(os.environ.get("KRB5RCACHEDIR", "")) != root:
            raise ValidationError("GSS 必须启用本次私有根的真实 replay cache")
        for key in os.environ:
            if key.upper() in {"SSH_AUTH_SOCK", "SSH_AGENT_PID", "GSS_USE_PROXY", "KRB5_KDC_PROFILE"} or key.upper().startswith("LD_"):
                raise ValidationError("GSS 子进程继承了非允许的加载器或身份环境")
            if key.upper().startswith("KRB5") and key.upper() not in {
                    "KRB5_CONFIG", "KRB5CCNAME", "KRB5_KTNAME", "KRB5_CLIENT_KTNAME",
                    "KRB5RCACHETYPE", "KRB5RCACHEDIR", "KRB5_TRACE"}:
                raise ValidationError("GSS 子进程继承了未允许的 Kerberos 环境")
        return result
    finally:
        tree.close()


class NativeGss:
    def __init__(self, *, root, directory=None, linux_library=None, missing_cache=False):
        self.root = project_path(root, must_exist=True)
        self.files = verify_environment(self.root, missing_cache=missing_cache)
        self.missing_cache = missing_cache
        self.contexts = set()
        self.acquire_failures = []
        verify_layout()
        if sys.platform == "win32":
            if linux_library is not None or directory is None:
                raise ValidationError("Windows GSS 必须选择内容锁定的六 DLL 目录")
            from tools.suite.windows.gss_mit.prepare_kfw import validate_directory
            from tools.suite.windows.gss_mit.mit_gss import MitGss
            validate_directory(directory)
            self.loader = MitGss(directory)
            self.dll = self.loader.gss
            self.loader.mechanism_count()
            self.loader.verify_defaults("SUITE.TEST", "FILE:" + str(self.files["KRB5CCNAME"]))
        else:
            if directory is not None or linux_library is None:
                raise ValidationError("Linux 对照必须显式选择真实 MIT 共享库")
            # 系统库可以是发行版 SONAME 链接，最终真实普通文件必须处于系统 lib。
            library = Path(linux_library).resolve(strict=True)
            if not any(library.is_relative_to(p) for p in (Path("/usr/lib"), Path("/lib"))):
                raise ValidationError("Linux GSS 对照只允许系统 MIT 库")
            self.loader = None
            self.dll = C.CDLL(str(library))
        self.krb5, self._krb5_memory = oid(KERBEROS_OID)
        self.principal_oid, self._principal_memory = oid(PRINCIPAL_OID)
        self.mechs = OidSet(1, C.pointer(self.krb5))
        for name, args in (
            ("gss_import_name", [UP, C.POINTER(Buffer), C.POINTER(Oid), PP]),
            ("gss_release_name", [UP, PP]),
            ("gss_display_name", [UP, P, C.POINTER(Buffer), P]),
            ("gss_release_buffer", [UP, C.POINTER(Buffer)]),
            ("gss_release_cred", [UP, PP]),
            ("gss_acquire_cred_from", [UP, P, U32, C.POINTER(OidSet), C.c_int,
                                      C.POINTER(KeyValueSet), PP, P, UP]),
            ("gss_init_sec_context", [UP, P, PP, P, C.POINTER(Oid), U32, U32, P,
                                     C.POINTER(Buffer), P, C.POINTER(Buffer), UP, UP]),
            ("gss_accept_sec_context", [UP, PP, P, C.POINTER(Buffer), P, PP, P,
                                       C.POINTER(Buffer), UP, UP, PP]),
            ("gss_delete_sec_context", [UP, PP, P]),
            ("gss_get_mic", [UP, P, U32, C.POINTER(Buffer), C.POINTER(Buffer)]),
            ("gss_verify_mic", [UP, P, C.POINTER(Buffer), C.POINTER(Buffer), UP])):
            method = getattr(self.dll, name)
            method.argtypes, method.restype = args, U32
            setattr(self, name, method)

    @staticmethod
    def check(operation, major, minor, *, continuation=False):
        if major not in ((0, 1) if continuation else (0,)):
            raise GssError(operation, major, minor.value)

    def output(self, value):
        try:
            if value.length > MAX_TOKEN or value.length and not value.value:
                raise ValidationError("GSS 返回的输出长度或指针无效")
            return C.string_at(value.value, value.length) if value.length else b""
        finally:
            if value.value:
                minor = U32()
                self.check("release-buffer", self.gss_release_buffer(C.byref(minor), C.byref(value)), minor)

    def name(self, principal):
        if principal not in ("suite@SUITE.TEST", "host/localhost@SUITE.TEST"):
            raise ValidationError("GSS 只允许固定测试主体")
        minor, result = U32(), P()
        value, memory = buffer(principal.encode("ascii"))
        major = self.gss_import_name(C.byref(minor), C.byref(value), C.byref(self.principal_oid), C.byref(result))
        try:
            self.check("import-name", major, minor)
            if not result:
                raise ValidationError("GSS 成功导入却没有名称句柄")
            return result
        except BaseException:
            self.release_name(result)
            raise

    def release_name(self, value):
        if value:
            minor = U32()
            self.check("release-name", self.gss_release_name(C.byref(minor), C.byref(value)), minor)

    def display_name(self, value):
        minor, result = U32(), Buffer()
        major = self.gss_display_name(C.byref(minor), value, C.byref(result), None)
        data = self.output(result)
        self.check("display-name", major, minor)
        return data

    def context(self, *, acceptor):
        return Context(self, acceptor=acceptor)

    def close(self):
        failed = False
        for context in tuple(self.contexts):
            try:
                context.close()
            except (GssError, ValidationError):
                failed = True
        if failed or self.contexts:
            raise ValidationError("GSS 清理后仍有未释放原生上下文")


class Context:
    def __init__(self, api, *, acceptor):
        self.api, self.acceptor = api, acceptor
        self.handle, self.credential, self.name = P(), P(), P()
        self.complete, self.flags, self.steps = False, 0, 0
        api.contexts.add(self)
        try:
            self.name = api.name("host/localhost@SUITE.TEST" if acceptor else "suite@SUITE.TEST")
            variable = "KRB5_KTNAME" if acceptor else "KRB5CCNAME"
            path = ("FILE:" + str(api.files[variable])).encode("utf-8")
            entries = [KeyValue(b"keytab" if acceptor else b"ccache", path)]
            if acceptor:
                # 固定 KfW 4.1 支持显式 rcache；Linux 新 dfl 仍采用 uid 文件。
                entries.append(KeyValue(b"rcache", b"dfl:suite-gss-replay"))
            values = (KeyValue * len(entries))(*entries)
            store = KeyValueSet(len(entries), values)
            minor, lifetime = U32(), U32()
            major = api.gss_acquire_cred_from(C.byref(minor), self.name, 0xffffffff,
                C.byref(api.mechs), 2 if acceptor else 1, C.byref(store),
                C.byref(self.credential), None, C.byref(lifetime))
            try:
                api.check("acquire-explicit", major, minor)
            except GssError as error:
                api.acquire_failures.append((error.major, error.minor))
                raise
            if not self.credential or not lifetime.value:
                raise ValidationError("GSS 凭据成功结果缺少句柄或有效期")
        except BaseException:
            self.close()
            raise

    def step(self, token=b""):
        if self.complete or not self.credential or self.steps >= 8:
            raise ValidationError("GSS 上下文步骤、状态或凭据无效")
        self.steps += 1
        api = self.api
        minor, output, flags, lifetime = U32(), Buffer(), U32(), U32()
        incoming, memory = buffer(token)
        source, delegated = P(), P()
        try:
            if self.acceptor:
                major = api.gss_accept_sec_context(C.byref(minor), C.byref(self.handle), self.credential,
                    C.byref(incoming), None, C.byref(source), None, C.byref(output),
                    C.byref(flags), C.byref(lifetime), C.byref(delegated))
            else:
                target = api.name("host/localhost@SUITE.TEST")
                try:
                    major = api.gss_init_sec_context(C.byref(minor), self.credential, C.byref(self.handle),
                        target, C.byref(api.krb5), REQUIRED_FLAGS, 0, None,
                        C.byref(incoming) if token else None, None, C.byref(output),
                        C.byref(flags), C.byref(lifetime))
                finally:
                    api.release_name(target)
            data = api.output(output)
            api.check("accept" if self.acceptor else "init", major, minor, continuation=True)
            self.complete, self.flags = major == 0, flags.value
            if delegated or self.flags & 1:
                raise ValidationError("GSS 夹具拒绝委托凭据或委托标记")
            if self.complete:
                if self.flags & REQUIRED_FLAGS != REQUIRED_FLAGS or not lifetime.value:
                    raise ValidationError("GSS 缺少相互认证、完整性或有效期")
                if self.acceptor and api.display_name(source) != b"suite@SUITE.TEST":
                    raise ValidationError("GSS 接受端客户端主体不符")
            return data
        finally:
            try:
                api.release_name(source)
            finally:
                if delegated:
                    temporary = U32()
                    api.check("release-delegated", api.gss_release_cred(C.byref(temporary), C.byref(delegated)), temporary)

    def mic(self, message):
        if not self.complete or self.acceptor:
            raise ValidationError("GSS MIC 需要已完成的发起端")
        minor, output = U32(), Buffer()
        data, memory = buffer(message)
        major = self.api.gss_get_mic(C.byref(minor), self.handle, 0, C.byref(data), C.byref(output))
        token = self.api.output(output)
        self.api.check("get-mic", major, minor)
        if not token:
            raise ValidationError("GSS 成功 MIC 不得为空")
        return token

    def verify(self, message, token):
        if not self.complete or not self.acceptor:
            raise ValidationError("GSS MIC 检查需要已完成的接受端")
        minor, quality = U32(), U32()
        data, memory = buffer(message)
        mic, mic_memory = buffer(token)
        self.api.check("verify-mic", self.api.gss_verify_mic(C.byref(minor), self.handle,
                       C.byref(data), C.byref(mic), C.byref(quality)), minor)

    def close(self):
        failures = []
        for attribute, function, operation, extra in (
            ("handle", self.api.gss_delete_sec_context, "delete-context", (None,)),
            ("credential", self.api.gss_release_cred, "release-credential", ()),
            ("name", self.api.gss_release_name, "release-name", ())):
            value = getattr(self, attribute)
            if value:
                minor = U32()
                try:
                    self.api.check(operation, function(C.byref(minor), C.byref(value), *extra), minor)
                except GssError as error:
                    failures.append(error)
                setattr(self, attribute, value)
        if failures or self.handle or self.credential or self.name:
            raise ValidationError("GSS 原生对象清理失败")
        self.api.contexts.discard(self)


def ssh_mic_payload(session_id, username="suite"):
    if not isinstance(session_id, bytes) or not 16 <= len(session_id) <= 128 or username != "suite":
        raise ValidationError("SSH MIC 必须绑定有界会话身份及固定用户名")
    fields = (session_id, b"suite", b"ssh-connection", b"gssapi-with-mic")
    return struct.pack(">I", len(fields[0])) + fields[0] + b"\x32" + b"".join(
        struct.pack(">I", len(value)) + value for value in fields[1:])
