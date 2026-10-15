"""KDC 夹具的私有文件与封闭子进程环境。"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import stat
import sys
import uuid

from tools.suite.windows.private_data import PrivateData
from tools.suite.windows.support import ValidationError, project_path


def utf8_streams():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")


class PrivateTree:
    """Windows 复用已验的两 SID ACL，Unix 使用 owner 独占根。"""

    def __init__(self, root, *, existing=False):
        self.root = project_path(root, must_exist=existing)
        self.native = PrivateData() if sys.platform == "win32" else None
        self._closed = False
        self._identity = None
        self._directories = set()
        self._files = set()
        try:
            if existing:
                self.directory(self.root, existing=True)
            else:
                self.directory(self.root)
            info = self.root.lstat()
            self._identity = (info.st_dev, info.st_ino)
        except BaseException:
            self.close()
            raise

    def path(self, path):
        result = project_path(path)
        if result != self.root and not result.is_relative_to(self.root):
            raise ValidationError("KDC 文件越出独占根")
        return result

    def directory(self, path, *, existing=False):
        path = self.path(path)
        if self.native:
            if path not in self._directories:
                self.native.directory(path, allow_existing=existing)
                self._directories.add(path)
            # 已核验目录句柄始终不共享DELETE；同一对象不会被路径替换。
            return path
        if not existing:
            path.mkdir(mode=0o700)
        info = path.lstat()
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or
                stat.S_IMODE(info.st_mode) != 0o700):
            raise ValidationError("KDC 目录不是当前身份独占的真实目录")
        self._directories.add(path)
        return path

    def parents(self, path):
        path = self.path(path)
        for directory in reversed(path.parents):
            if directory == self.root or directory.is_relative_to(self.root):
                try:
                    directory.lstat()
                except FileNotFoundError:
                    self.directory(directory)
                else:
                    self.directory(directory, existing=True)
        return path

    def write(self, path, content, *, executable=False):
        path = self.parents(path)
        if self.native:
            self.native.write(path, content)
            self._files.add(path)
            return path
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                             0o700 if executable else 0o600)
        with os.fdopen(descriptor, "wb") as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        self.check_file(path)
        self._files.add(path)
        return path

    def expect_file(self, path):
        """运行前登记外部工具将创建的确切文件，不接受运行后的任意目录扫描。"""
        path = self.path(path)
        if path == self.root or path.parent not in self._directories:
            raise ValidationError("KDC预期文件必须位于已经登记的私有目录")
        self._files.add(path)
        return path

    def register_artifact(self, path, *, directory=False):
        """可信调用方按已知生产规则登记确切产物，不能递归接纳目录内容。"""
        if self._closed:
            raise ValidationError("不能登记已经关闭的KDC资料")
        path = self.path(path)
        if path == self.root or path.parent not in self._directories:
            raise ValidationError("KDC产物父目录尚未登记")
        if directory:
            return self.directory(path, existing=True)
        self.check_file(path, harden=True)
        self._files.add(path)
        return path

    def check_file(self, path, *, harden=False):
        path = self.path(path)
        info = path.lstat()
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or
                getattr(info, "st_file_attributes", 0) & 0x400):
            raise ValidationError("KDC 文件不是普通单链接文件")
        if self.native:
            self.native.check_file(path, harden=harden)
        elif info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) not in (0o600, 0o700):
            raise ValidationError("KDC 文件不是当前身份独占")
        return path

    def close(self):
        if not self._closed:
            self._closed = True
            if self.native:
                self.native.close()

    def remove(self):
        """仅清理明确登记且仍为同一身份的普通私有树。"""
        if self._closed:
            raise ValidationError("不能清理已经释放身份保护的KDC目录")
        pending = [self.root]
        count = 0
        while pending:
            directory = pending.pop()
            if directory not in self._directories:
                raise ValidationError("KDC运行目录出现未知子目录")
            self.directory(directory, existing=True)
            for path in directory.iterdir():
                count += 1
                if count > 4096:
                    raise ValidationError("KDC运行目录清理项数越界")
                info = path.lstat()
                if stat.S_ISDIR(info.st_mode) and not getattr(info, "st_file_attributes", 0) & 0x400:
                    if path not in self._directories:
                        raise ValidationError("KDC运行目录出现未知子目录")
                    pending.append(path)
                else:
                    if path not in self._files:
                        raise ValidationError("KDC运行目录出现未知文件")
                    self.check_file(path, harden=True)
        info = self.root.lstat()
        if (info.st_dev, info.st_ino) != self._identity:
            raise ValidationError("KDC运行目录身份已变化")
        self.close()
        if sys.platform != "win32":
            # 按目录fd逐项删除已登记路径；重扫期间新增的未知内容仍保留并失败。
            flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
            def erase_fd(descriptor, path):
                for name in os.listdir(descriptor):
                    child = path / name
                    info = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
                    if stat.S_ISDIR(info.st_mode):
                        if child not in self._directories:
                            raise ValidationError("KDC清理期间出现未知子目录")
                        nested = os.open(name, flags, dir_fd=descriptor)
                        try:
                            actual = os.fstat(nested)
                            if (actual.st_dev, actual.st_ino) != (info.st_dev, info.st_ino):
                                raise ValidationError("KDC清理期间子目录身份已变化")
                            erase_fd(nested, child)
                        finally:
                            os.close(nested)
                        os.rmdir(name, dir_fd=descriptor)
                    else:
                        if (child not in self._files or not stat.S_ISREG(info.st_mode) or
                                info.st_uid != os.geteuid() or info.st_nlink != 1 or
                                stat.S_IMODE(info.st_mode) not in (0o600, 0o700)):
                            raise ValidationError("KDC清理期间出现未知或非私有文件")
                        os.unlink(name, dir_fd=descriptor)
            parent = os.open(self.root.parent, flags)
            descriptor = None
            try:
                descriptor = os.open(self.root.name, flags, dir_fd=parent)
                info = os.fstat(descriptor)
                if (info.st_dev, info.st_ino) != self._identity:
                    raise ValidationError("释放后KDC运行目录身份已变化")
                erase_fd(descriptor, self.root)
                info = os.stat(self.root.name, dir_fd=parent, follow_symlinks=False)
                if (info.st_dev, info.st_ino) != self._identity:
                    raise ValidationError("KDC清理期间根目录身份已变化")
                os.rmdir(self.root.name, dir_fd=parent)
            finally:
                if descriptor is not None:
                    os.close(descriptor)
                os.close(parent)
            return
        import ctypes as C
        from ctypes import wintypes as W
        native = PrivateData()
        handles = []
        dispose = native.kernel.SetFileInformationByHandle
        dispose.argtypes = [W.HANDLE, C.c_int, C.c_void_p, W.DWORD]
        dispose.restype = W.BOOL
        try:
            # 旧目录句柄不共享DELETE，先释放，再逐祖先固定并核同一根身份。
            for ancestor in reversed(self.root.parents):
                if ancestor != Path(ancestor.anchor):
                    handles.append(native._open(ancestor, True, ancestor=True))
            def erase(path, directory):
                if path not in (self._directories if directory else self._files):
                    raise ValidationError("KDC清理期间出现未知内容")
                handle = native._open(path, directory, 0x30080)  # DELETE/READ_CONTROL/READ_ATTRIBUTES
                try:
                    native._check(handle, directory)
                    if path == self.root:
                        info = path.lstat()
                        if (info.st_dev, info.st_ino) != self._identity:
                            raise ValidationError("释放后KDC运行目录身份已变化")
                    if directory:
                        for child in path.iterdir():
                            info = child.lstat()
                            erase(child, stat.S_ISDIR(info.st_mode))
                    # 使用已核验且不共享DELETE的同一对象句柄，不能按可能被替换的路径删除。
                    flag = W.BOOL(True)
                    native._ok(dispose(handle, 4, C.byref(flag), C.sizeof(flag)))
                finally:
                    native._ok(native.kernel.CloseHandle(handle))
            erase(self.root, True)
        finally:
            failed = False
            for handle in reversed(handles):
                if not native.kernel.CloseHandle(handle):
                    failed = True
            native.close()
            if failed:
                raise ValidationError("KDC清理祖先句柄释放失败")


def closed_environment(tree: PrivateTree, *, base=None):
    """只保留 OS 必需变量；不继承加载器、profiler、SSL 或凭据配置。"""
    inherited = os.environ if base is None else base
    allowed = {"SYSTEMROOT", "WINDIR", "SYSTEMDRIVE", "COMSPEC", "NUMBER_OF_PROCESSORS",
               "PROCESSOR_ARCHITECTURE", "PROCESSOR_LEVEL", "PROCESSOR_REVISION"}
    result = {key.upper(): value for key, value in inherited.items() if key.upper() in allowed}
    # 不继承 PATH：调用方只执行已校验的绝对路径，工具仅可找到系统工具。
    result["PATH"] = str(Path(result.get("SYSTEMROOT", "C:/Windows")) / "System32") if sys.platform == "win32" else "/usr/bin:/bin"
    for key, relative in {
        "HOME": "user", "USERPROFILE": "user", "APPDATA": "user/appdata",
        "LOCALAPPDATA": "user/localappdata", "XDG_DATA_HOME": "user/data",
        "XDG_CONFIG_HOME": "user/config", "XDG_CACHE_HOME": "user/cache",
        "XDG_RUNTIME_DIR": "runtime", "NUGET_SCRATCH": "nuget-scratch",
        "TMP": "temp", "TEMP": "temp", "TMPDIR": "temp", "DOTNET_CLI_HOME": "cli",
        "NUGET_PACKAGES": "packages", "NUGET_HTTP_CACHE_PATH": "nuget-http",
        "DOTNET_BUNDLE_EXTRACT_BASE_DIR": "bundle",
    }.items():
        directory = tree.parents(tree.root / relative)
        try:
            directory.lstat()
        except FileNotFoundError:
            tree.directory(directory)
        else:
            tree.directory(directory, existing=True)
        result[key] = str(directory)
    result.update({"DOTNET_GENERATE_ASPNET_CERTIFICATE": "false", "DOTNET_ADD_GLOBAL_TOOLS_TO_PATH": "false",
                   "DOTNET_CLI_WORKLOAD_UPDATE_NOTIFY_DISABLE": "true", "DOTNET_CLI_TELEMETRY_OPTOUT": "1",
                   "DOTNET_NOLOGO": "1", "DOTNET_MULTILEVEL_LOOKUP": "0", "DOTNET_EnableDiagnostics": "0",
                   "NUGET_CERT_REVOCATION_MODE": "offline", "LC_ALL": "C.UTF-8", "LANG": "C.UTF-8",
                   "PYTHONIOENCODING": "utf-8", "MSBUILDDISABLENODEREUSE": "1",
                   "DOTNET_CLI_USE_MSBUILD_SERVER": "0", "MSBuildEnableWorkloadResolver": "false",
                   "DOTNET_SKIP_WORKLOAD_INTEGRITY_CHECK": "true"})
    for key in ("HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "XDG_DATA_HOME", "XDG_CONFIG_HOME",
                "XDG_CACHE_HOME", "TMP", "TEMP", "TMPDIR", "DOTNET_CLI_HOME", "NUGET_PACKAGES",
                "NUGET_HTTP_CACHE_PATH", "DOTNET_BUNDLE_EXTRACT_BASE_DIR", "XDG_RUNTIME_DIR", "NUGET_SCRATCH"):
        tree.path(result[key])
    return result


def digest(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as source:
        while data := source.read(1024 * 1024):
            value.update(data)
    return value.hexdigest()


def publish_report(path, report):
    """先持久化同目录新文件，再原子替换，失败不会发布半份JSON。"""
    path = project_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    project_path(path.parent, must_exist=True)
    try:
        info = path.lstat()
    except FileNotFoundError:
        pass
    else:
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > 65536:
            raise ValidationError("KDC报告目标必须为有界普通单链接文件")
    content = (json.dumps(report, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    if len(content) > 65536:
        raise ValidationError("KDC公开报告超出体积上限")
    pending = path.parent / (".kdc-report-" + uuid.uuid4().hex)
    descriptor = os.open(pending, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        project_path(path)
        os.replace(pending, path)
    finally:
        try:
            pending.unlink()
        except FileNotFoundError:
            pass
