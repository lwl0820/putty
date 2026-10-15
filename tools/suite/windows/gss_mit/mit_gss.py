"""固定 MIT x64 ABI 的无凭据入口；不绑定申请凭据、缓存打开或认证函数。"""
import ctypes as C
from pathlib import Path
import sys

from .common import ValidationError

U32 = C.c_uint32
P = C.c_void_p
KERBEROS_OID = bytes.fromhex("2a864886f712010202")
DLL_ORDER = ("msvcr100.dll", "k5sprt64.dll", "comerr64.dll", "wshelp64.dll", "krb5_64.dll", "gssapi64.dll")


class Buffer(C.Structure):
    _fields_ = [("length", C.c_size_t), ("value", P)]


class Oid(C.Structure):
    _fields_ = [("length", U32), ("elements", P)]


class OidSet(C.Structure):
    _fields_ = [("count", C.c_size_t), ("elements", C.POINTER(Oid))]


def verify_layout():
    if C.sizeof(P) != 8 or any(C.sizeof(item) != 16 for item in (Buffer, Oid, OidSet)):
        raise ValidationError("MIT x64 结构大小无效")
    if Buffer.value.offset != 8 or Oid.elements.offset != 8 or OidSet.elements.offset != 8:
        raise ValidationError("MIT x64 结构指针对齐无效")


class MitGss:
    """独立进程使用；真实句柄路径须与本次六个内容锁定 DLL 相同。"""

    def __init__(self, directory):
        if sys.platform != "win32":
            raise ValidationError("MIT 原生探针只能在 Windows x64 执行")
        verify_layout()
        self.directory = Path(directory).absolute()
        self.kernel = C.WinDLL("kernel32", use_last_error=True)
        self.kernel.GetModuleHandleW.argtypes, self.kernel.GetModuleHandleW.restype = [C.c_wchar_p], P
        self.kernel.GetModuleFileNameW.argtypes = [P, C.c_wchar_p, U32]
        self.kernel.GetModuleFileNameW.restype = U32
        self.loaded = {}
        for name in DLL_ORDER:
            handle = self.kernel.GetModuleHandleW(name)
            if handle:
                # 同路径也不能复用：其 CRT 可能在封闭环境前已经保存个人变量副本。
                raise ValidationError("MIT 探针必须在六个目标 DLL 均未加载的新进程中执行")
        for name in DLL_ORDER:
            # 固定文件的依赖搜索仅允许该 DLL 目录及 Windows System32。
            self.loaded[name] = C.WinDLL(str(self.directory / name), winmode=0x00000900)
            self._verify_handle(name, self.loaded[name]._handle)
        self.gss = self.loaded["gssapi64.dll"]
        self.krb5 = self.loaded["krb5_64.dll"]
        oid_set_pointer = C.POINTER(OidSet)
        self._bind(self.gss, "gss_indicate_mechs", [C.POINTER(U32), C.POINTER(oid_set_pointer)], U32)
        self._bind(self.gss, "gss_release_oid_set", [C.POINTER(U32), C.POINTER(oid_set_pointer)], U32)
        self._bind(self.krb5, "krb5_init_context", [C.POINTER(P)], C.c_int32)
        self._bind(self.krb5, "krb5_free_context", [P], None)
        self._bind(self.krb5, "krb5_cc_default_name", [P], P)
        self._bind(self.krb5, "krb5_get_default_realm", [P, C.POINTER(P)], C.c_int32)
        self._bind(self.krb5, "krb5_free_default_realm", [P, P], None)

    @staticmethod
    def _bind(library, name, arguments, result):
        function = getattr(library, name)
        function.argtypes, function.restype = arguments, result

    def _verify_handle(self, name, handle):
        value = C.create_unicode_buffer(32768)
        count = self.kernel.GetModuleFileNameW(handle, value, len(value))
        if not 0 < count < len(value) or Path(value.value) != self.directory / name:
            raise ValidationError("实际 DLL 句柄路径与本次内容锁定目录不符")

    def mechanism_count(self):
        minor, value = U32(), C.POINTER(OidSet)()
        if self.gss.gss_indicate_mechs(C.byref(minor), C.byref(value)):
            raise ValidationError("MIT 机制枚举失败")
        try:
            if not value or not 1 <= value.contents.count <= 32 or not value.contents.elements:
                raise ValidationError("MIT 机制集合大小或指针无效")
            count, found = value.contents.count, False
            for index in range(count):
                oid = value.contents.elements[index]
                if not 1 <= oid.length <= 128 or not oid.elements:
                    raise ValidationError("MIT 机制 OID 越界")
                found |= C.string_at(oid.elements, oid.length) == KERBEROS_OID
            if not found:
                raise ValidationError("MIT 机制集合缺少 Kerberos")
            return count
        finally:
            if self.gss.gss_release_oid_set(C.byref(minor), C.byref(value)):
                raise ValidationError("MIT 机制集合释放失败")

    def verify_defaults(self, expected_realm, expected_cache):
        """只读取配置和默认缓存名称字符串，绝不打开、解析或枚举缓存。"""
        context, realm = P(), P()
        if self.krb5.krb5_init_context(C.byref(context)) or not context:
            raise ValidationError("MIT 私有配置上下文初始化失败")
        try:
            if self.krb5.krb5_get_default_realm(context, C.byref(realm)) or not realm:
                raise ValidationError("MIT 配置 realm 读取失败")
            if C.string_at(realm) != expected_realm.encode("ascii"):
                raise ValidationError("MIT 实际配置没有采用本次随机 realm")
            cache = self.krb5.krb5_cc_default_name(context)
            if not cache or C.string_at(cache) != expected_cache.encode("ascii"):
                raise ValidationError("MIT 实际默认缓存名不是本次缺失 FILE 路径")
        finally:
            if realm:
                self.krb5.krb5_free_default_realm(context, realm)
            self.krb5.krb5_free_context(context)
