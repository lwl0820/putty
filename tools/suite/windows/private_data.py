"""Windows 测试数据的私有 ACL；兼容正式私钥的单用户权限。"""
import ctypes as C
from ctypes import wintypes as W
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tools/suite/windows"))
from support import ValidationError, project_path


class _Attributes(C.Structure):
    _fields_ = [("length", W.DWORD), ("descriptor", C.c_void_p), ("inherit", W.BOOL)]


class PrivateData:
    """固定目录句柄并核验权限；默认两 SID，正式私钥显式要求单用户 ACE。"""

    def __init__(self):
        if sys.platform != "win32":
            raise ValidationError("私有数据 ACL 必须在原生 Windows 核验")
        self.kernel = C.WinDLL("kernel32", use_last_error=True)
        self.security = C.WinDLL("advapi32", use_last_error=True)
        self._pins = []
        self._descriptors = []
        self._api()
        token = W.HANDLE()
        self._ok(self.security.OpenProcessToken(self.kernel.GetCurrentProcess(), 8, C.byref(token)))
        try:
            size = W.DWORD()
            self.security.GetTokenInformation(token, 1, None, 0, C.byref(size))
            self._identity = C.create_string_buffer(size.value)
            self._ok(self.security.GetTokenInformation(token, 1, self._identity, size, C.byref(size)))
            self.user = C.c_void_p.from_buffer(self._identity).value
            text = C.c_void_p()
            self._ok(self.security.ConvertSidToStringSidW(self.user, C.byref(text)))
            try:
                sid = C.wstring_at(text)
            finally:
                self.kernel.LocalFree(text)
            if sid == "S-1-5-18":
                raise ValidationError("测试不能以 SYSTEM 身份运行")
            self.system = C.create_string_buffer(68)
            size = W.DWORD(68)
            self._ok(self.security.CreateWellKnownSid(22, None, self.system, C.byref(size)))
            for directory in (False, True):
                inheritance = "OICI" if directory else ""
                descriptor = C.c_void_p()
                sddl = f"O:{sid}D:P(A;{inheritance};FA;;;{sid})(A;{inheritance};FA;;;SY)"
                self._ok(self.security.ConvertStringSecurityDescriptorToSecurityDescriptorW(
                    sddl, 1, C.byref(descriptor), None))
                self._descriptors.append(descriptor)
        finally:
            self.kernel.CloseHandle(token)

    def _api(self):
        declarations = {
            "GetCurrentProcess": (self.kernel, [], W.HANDLE),
            "CloseHandle": (self.kernel, [W.HANDLE], W.BOOL),
            "LocalFree": (self.kernel, [C.c_void_p], C.c_void_p),
            "CreateDirectoryW": (self.kernel, [W.LPCWSTR, C.POINTER(_Attributes)], W.BOOL),
            "CreateFileW": (self.kernel, [W.LPCWSTR, W.DWORD, W.DWORD, C.POINTER(_Attributes),
                                          W.DWORD, W.DWORD, W.HANDLE], W.HANDLE),
            "GetFinalPathNameByHandleW": (self.kernel, [W.HANDLE, W.LPWSTR, W.DWORD, W.DWORD], W.DWORD),
            "GetFileInformationByHandle": (self.kernel, [W.HANDLE, C.c_void_p], W.BOOL),
            "WriteFile": (self.kernel, [W.HANDLE, C.c_void_p, W.DWORD, C.POINTER(W.DWORD), C.c_void_p], W.BOOL),
            "FlushFileBuffers": (self.kernel, [W.HANDLE], W.BOOL),
            "OpenProcessToken": (self.security, [W.HANDLE, W.DWORD, C.POINTER(W.HANDLE)], W.BOOL),
            "GetTokenInformation": (self.security, [W.HANDLE, C.c_int, C.c_void_p, W.DWORD, C.POINTER(W.DWORD)], W.BOOL),
            "CreateWellKnownSid": (self.security, [C.c_int, C.c_void_p, C.c_void_p, C.POINTER(W.DWORD)], W.BOOL),
            "ConvertSidToStringSidW": (self.security, [C.c_void_p, C.POINTER(C.c_void_p)], W.BOOL),
            "ConvertStringSecurityDescriptorToSecurityDescriptorW": (
                self.security, [W.LPCWSTR, W.DWORD, C.POINTER(C.c_void_p), C.c_void_p], W.BOOL),
            "GetSecurityInfo": (self.security, [W.HANDLE, C.c_int, W.DWORD, C.POINTER(C.c_void_p),
                C.c_void_p, C.POINTER(C.c_void_p), C.c_void_p, C.POINTER(C.c_void_p)], W.DWORD),
            "SetSecurityInfo": (self.security, [W.HANDLE, C.c_int, W.DWORD, C.c_void_p,
                                               C.c_void_p, C.c_void_p, C.c_void_p], W.DWORD),
            "GetSecurityDescriptorControl": (self.security, [C.c_void_p, C.POINTER(W.WORD), C.POINTER(W.DWORD)], W.BOOL),
            "GetSecurityDescriptorDacl": (self.security, [C.c_void_p, C.POINTER(W.BOOL),
                                                       C.POINTER(C.c_void_p), C.POINTER(W.BOOL)], W.BOOL),
            "GetAce": (self.security, [C.c_void_p, W.DWORD, C.POINTER(C.c_void_p)], W.BOOL),
            "EqualSid": (self.security, [C.c_void_p, C.c_void_p], W.BOOL),
        }
        for name, (library, arguments, result) in declarations.items():
            function = getattr(library, name)
            function.argtypes, function.restype = arguments, result

    @staticmethod
    def _ok(result):
        if not result:
            raise ValidationError(f"私有数据 Win32 操作失败，代码 {C.get_last_error()}")

    def _open(self, path, directory, access=0x20080, *, ancestor=False):
        path = Path(path).absolute() if ancestor else project_path(path, must_exist=True)
        handle = self.kernel.CreateFileW(str(path), access, 3, None, 3,
                                        0x200000 | (0x2000000 if directory else 0), None)
        if handle == C.c_void_p(-1).value:
            self._ok(False)
        try:
            # BY_HANDLE_FILE_INFORMATION 共 13 DWORD，链接数的索引为 10。
            information = (W.DWORD * 13)()
            self._ok(self.kernel.GetFileInformationByHandle(handle, information))
            if information[0] & 0x400 or bool(information[0] & 0x10) != directory or (
                    not directory and information[10] != 1):
                raise ValidationError("私有数据包含 reparse、错误类型或硬链接")
            final = C.create_unicode_buffer(32768)
            count = self.kernel.GetFinalPathNameByHandleW(handle, final, len(final), 0)
            if not count or count >= len(final) or final.value.casefold() != ("\\\\?\\" + str(path)).casefold():
                raise ValidationError("私有数据实际句柄路径与声明不符")
            return handle
        except BaseException:
            self.kernel.CloseHandle(handle)
            raise

    def _check(self, handle, directory, *, owner_only=False):
        if owner_only and directory:
            raise ValidationError("单用户权限核验只适用于正式导出的私钥文件")
        owner, acl, descriptor = C.c_void_p(), C.c_void_p(), C.c_void_p()
        if self.security.GetSecurityInfo(handle, 1, 5, C.byref(owner), None,
                                         C.byref(acl), None, C.byref(descriptor)):
            raise ValidationError("无法读取私有数据实际权限")
        try:
            control, revision = W.WORD(), W.DWORD()
            self._ok(self.security.GetSecurityDescriptorControl(descriptor, C.byref(control), C.byref(revision)))
            if not owner or not self.security.EqualSid(owner, self.user) or not acl or not control.value & 0x1000:
                raise ValidationError("私有数据 owner 或受保护 DACL 不符")
            expected = {"user"} if owner_only else {"user", "system"}
            if C.c_ushort.from_address(acl.value + 4).value != len(expected):
                raise ValidationError("私有数据 SID 许可数量不符")
            found = set()
            for index in range(len(expected)):
                ace = C.c_void_p()
                self._ok(self.security.GetAce(acl, index, C.byref(ace)))
                header = (C.c_ubyte * 4).from_address(ace.value)
                if header[0] or header[1] != (3 if directory else 0) or C.c_uint32.from_address(ace.value + 4).value != 0x1F01FF:
                    raise ValidationError("私有数据 ACE 类型、继承或权限不符")
                sid = ace.value + 8
                identity = "user" if self.security.EqualSid(sid, self.user) else (
                    "system" if self.security.EqualSid(sid, self.system) else "other")
                found.add(identity)
            if found != expected:
                raise ValidationError("私有数据允许了额外身份或重复许可")
        finally:
            self.kernel.LocalFree(descriptor)

    def directory(self, path, *, allow_existing=False):
        path = project_path(path)
        # 创建前固定每级祖先，不能在核验后把 .tmp 或源码父路径替换成 junction。
        for component in reversed(path.parents):
            if component == Path(component.anchor):
                continue
            self._pins.append(self._open(component, True, ancestor=True))
        attributes = _Attributes(C.sizeof(_Attributes), self._descriptors[1], False)
        if not self.kernel.CreateDirectoryW(str(path), C.byref(attributes)):
            if not allow_existing or C.get_last_error() != 183:
                self._ok(False)
        handle = self._open(path, True)
        try:
            self._check(handle, True)
        except BaseException:
            self.kernel.CloseHandle(handle)
            raise
        self._pins.append(handle)
        return path

    def write(self, path, content):
        path = project_path(path)
        attributes = _Attributes(C.sizeof(_Attributes), self._descriptors[0], False)
        handle = self.kernel.CreateFileW(str(path), 0x40020080, 0, C.byref(attributes), 1, 0x200080, None)
        if handle == C.c_void_p(-1).value:
            self._ok(False)
        try:
            self._check(handle, False)
            actual = W.DWORD()
            self._ok(self.kernel.WriteFile(handle, content, len(content), C.byref(actual), None))
            if actual.value != len(content):
                raise ValidationError("私有数据写入不完整")
            self._ok(self.kernel.FlushFileBuffers(handle))
        finally:
            self.kernel.CloseHandle(handle)
        return path

    def check_file(self, path, *, harden=False, owner_only=False):
        # 正式私钥必须直接核验产品导出权限，不能先改 ACL 掩盖缺陷。
        if harden and owner_only:
            raise ValidationError("正式私钥权限核验不能收紧或改写 ACL")
        handle = self._open(path, False, 0xE0080 if harden else 0x20080)
        try:
            if harden:
                acl, present, defaulted = C.c_void_p(), W.BOOL(), W.BOOL()
                self._ok(self.security.GetSecurityDescriptorDacl(self._descriptors[0], C.byref(present),
                    C.byref(acl), C.byref(defaulted)))
                if self.security.SetSecurityInfo(handle, 1, 0x80000005, self.user, None, acl, None):
                    raise ValidationError("无法收紧受保护数据文件权限")
            self._check(handle, False, owner_only=owner_only)
        finally:
            self.kernel.CloseHandle(handle)

    def close(self):
        failed = False
        for handle in reversed(self._pins):
            if not self.kernel.CloseHandle(handle):
                failed = True
        self._pins.clear()
        for descriptor in self._descriptors:
            self.kernel.LocalFree(descriptor)
        self._descriptors.clear()
        if failed:
            raise ValidationError("私有目录固定句柄清理失败")
