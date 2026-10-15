#!/usr/bin/env python3
"""为 Linux 套件测试创建可验证、仅位于项目 .tmp/ 的子进程环境。"""

import argparse
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import uuid


ROOT = Path(__file__).resolve().parents[2]
TEMPORARY_ROOT = ROOT / ".tmp"
DEFAULT_PARENT = TEMPORARY_ROOT / "suite-fixture"
AGENT_VARIABLES = (
    "SSH_AUTH_SOCK", "SSH_AGENT_PID", "SSH_AGENT_LAUNCHER", "GPG_AGENT_INFO",
    "GNOME_KEYRING_CONTROL", "GNOME_KEYRING_PID", "SSH_ASKPASS",
    "SSH_ASKPASS_REQUIRE", "SSH_ASKPASS_PROMPT", "SSH_SK_HELPER",
    "SSH_PKCS11_HELPER",
)
DIRECTORY_VARIABLES = {
    "PUTTYDIR": "putty", "PUTTYSESSIONS": "putty/sessions",
    "PUTTYSSHHOSTCAS": "putty/sshhostcas", "XDG_CONFIG_HOME": "config",
    "XDG_CONFIG_DIRS": "config-system", "XDG_DATA_HOME": "data",
    "XDG_DATA_DIRS": "data-system", "XDG_STATE_HOME": "state",
    "XDG_CACHE_HOME": "cache", "XDG_RUNTIME_DIR": "runtime",
    "TMPDIR": "temp", "TMP": "temp", "TEMP": "temp",
}
FILE_VARIABLES = {
    "PUTTYSSHHOSTKEYS": "putty/sshhostkeys",
    "PUTTYRANDOMSEED": "putty/randomseed",
}


class IsolationError(ValueError):
    """隔离前提未满足；调用方必须停止运行受测程序。"""


def require_linux(platform=None):
    if (sys.platform if platform is None else platform) != "linux":
        raise IsolationError("本夹具仅验证 Linux；Windows 需要逐进程 HKCU/随机种子探针")


def _relative_path(path):
    path = Path(path)
    if ".." in path.parts:
        raise IsolationError("隔离路径不能包含上级目录")
    path = path.absolute()
    if path == TEMPORARY_ROOT or not path.is_relative_to(TEMPORARY_ROOT):
        raise IsolationError("隔离路径必须位于项目 .tmp/ 内，且不能是 .tmp/ 本身")
    return path, path.relative_to(ROOT)


def _check_directory(descriptor, private):
    information = os.fstat(descriptor)
    if information.st_uid != os.geteuid():
        raise IsolationError("隔离目录的所有者不是当前进程用户")
    mode = stat.S_IMODE(information.st_mode)
    if mode & 0o022 or (private and mode != 0o700):
        raise IsolationError("隔离目录权限不安全，私有目录必须为 0700")


def _open_directory(path, create=False):
    """逐层用目录句柄和 O_NOFOLLOW 遍历，禁止解析符号链接。"""
    _, relative = _relative_path(path)
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    descriptor = os.open(ROOT, flags)
    try:
        _check_directory(descriptor, False)
        for index, part in enumerate(relative.parts):
            if create:
                try:
                    os.mkdir(part, mode=0o700, dir_fd=descriptor)
                except FileExistsError:
                    pass
            next_descriptor = os.open(part, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = next_descriptor
            # 项目共用 .tmp/ 可为 0755；本夹具的其余目录只能为 0700。
            _check_directory(descriptor, index != 0)
        return descriptor
    except (OSError, IsolationError) as error:
        os.close(descriptor)
        if isinstance(error, IsolationError):
            raise
        raise IsolationError("无法安全打开隔离目录，目录可能为链接或不可访问") from error


def private_directory(path):
    path, _ = _relative_path(path)
    descriptor = _open_directory(path, create=True)
    os.close(descriptor)
    return path


class IsolationEnvironment:
    """每次运行持有一个私有目录；不修改父进程环境或 HOME。"""

    def __init__(self, parent=DEFAULT_PARENT, *, base_environment=None):
        require_linux()
        self._base = dict(os.environ if base_environment is None else base_environment)
        self._closed = False
        parent = private_directory(parent)
        parent_descriptor = _open_directory(parent)
        try:
            self.root = parent / ("run-" + uuid.uuid4().hex[:12])
            os.mkdir(self.root.name, mode=0o700, dir_fd=parent_descriptor)
        finally:
            os.close(parent_descriptor)
        self._identity = self.root.lstat()
        try:
            for directory in set(DIRECTORY_VARIABLES.values()) | {"logs", "ssh", "transfer"}:
                private_directory(self.root / directory)
            for filename in FILE_VARIABLES.values():
                self.write_private(filename, b"")
        except BaseException:
            self.close()
            raise

    def __enter__(self):
        return self

    def __exit__(self, *unused):
        self.close()

    def validate(self):
        if self._closed:
            raise IsolationError("隔离环境已经关闭")
        descriptor = _open_directory(self.root)
        try:
            current = os.fstat(descriptor)
            if (current.st_dev, current.st_ino) != (self._identity.st_dev, self._identity.st_ino):
                raise IsolationError("隔离根目录已被替换")
        finally:
            os.close(descriptor)
        for directory in set(DIRECTORY_VARIABLES.values()) | {"logs", "ssh", "transfer"}:
            descriptor = _open_directory(self.root / directory)
            os.close(descriptor)
        for filename in FILE_VARIABLES.values():
            self._validate_file(self.root / filename)

    def _path(self, relative):
        relative = Path(relative)
        if relative.is_absolute() or ".." in relative.parts or not relative.parts:
            raise IsolationError("夹具内部路径必须是无上级目录的相对路径")
        return self.root / relative

    def _validate_file(self, path):
        descriptor = _open_directory(path.parent)
        try:
            information = os.stat(path.name, dir_fd=descriptor, follow_symlinks=False)
            if (not stat.S_ISREG(information.st_mode) or information.st_uid != os.geteuid()
                    or stat.S_IMODE(information.st_mode) != 0o600 or information.st_nlink != 1):
                raise IsolationError("隔离文件必须为当前用户独占的 0600 普通文件")
        finally:
            os.close(descriptor)

    def write_private(self, relative, content):
        if self._closed:
            raise IsolationError("隔离环境已经关闭")
        path = self._path(relative)
        descriptor = _open_directory(path.parent)
        try:
            # 仅创建新文件，不通过现有路径截断链接或个人文件。
            handle = os.open(path.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL
                             | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=descriptor)
            with os.fdopen(handle, "wb") as output:
                output.write(content)
        except OSError as error:
            raise IsolationError("无法独占创建隔离文件") from error
        finally:
            os.close(descriptor)
        self._validate_file(path)
        return path

    def environment(self, *, agent_socket=None):
        self.validate()
        result = self._base.copy()
        for variable in AGENT_VARIABLES:
            result.pop(variable, None)
        for variable, relative in {**DIRECTORY_VARIABLES, **FILE_VARIABLES}.items():
            result[variable] = str(self.root / relative)
        if agent_socket is not None:
            path = Path(agent_socket)
            if not path.is_absolute() or not path.is_relative_to(self.root):
                raise IsolationError("测试代理 socket 必须位于本次隔离根目录内")
            self._check_socket(path)
            result["SSH_AUTH_SOCK"] = str(path)
        return result

    def _check_socket(self, path):
        descriptor = _open_directory(path.parent)
        try:
            information = os.stat(path.name, dir_fd=descriptor, follow_symlinks=False)
            if (not stat.S_ISSOCK(information.st_mode) or information.st_uid != os.geteuid()
                    or stat.S_IMODE(information.st_mode) & 0o077):
                raise IsolationError("测试代理端点必须是当前用户的私有 Unix socket")
        finally:
            os.close(descriptor)

    def socket_path(self, name="agent.sock"):
        self.validate()
        if Path(name).name != name or name in ("", ".", ".."):
            raise IsolationError("socket 名称必须是单个文件名")
        path = self.root / "runtime" / name
        if len(os.fsencode(path)) > 107:
            raise IsolationError("测试 socket 路径超过 Linux Unix socket 长度上限")
        return path

    def guard_program(self, arguments):
        """未知 Pageant 构建没有可证明的目录前缀，始终拒绝自动启动。"""
        if not arguments:
            raise IsolationError("缺少受测程序")
        program = Path(arguments[0]).name.lower()
        if "pageant" in program:
            raise IsolationError("Pageant 默认 /tmp/pageant 未适配；TMPDIR 不能证明其 socket 已隔离")

    def run(self, arguments, *, timeout=30, input=None, check=False):
        self.guard_program(arguments)
        return subprocess.run(
            list(map(str, arguments)), cwd=self.root, env=self.environment(),
            capture_output=True, timeout=timeout, input=input, check=check, umask=0o077,
        )

    def probe(self):
        """真实子进程检查覆盖值和写入路径；不是上游内部存储调用的替代证据。"""
        result = self.run([sys.executable, "-B", str(Path(__file__)), "_probe-child", str(self.root)],
                          check=True)
        report = json.loads(result.stdout)
        if report.get("kind") != "linux-environment" or not report.get("passed"):
            raise IsolationError("子进程环境隔离探针失败")
        return report

    def close(self):
        if self._closed:
            return
        descriptor = _open_directory(self.root)
        try:
            current = os.fstat(descriptor)
            if (current.st_dev, current.st_ino) != (self._identity.st_dev, self._identity.st_ino):
                raise IsolationError("拒绝清理已替换的隔离目录")
        finally:
            os.close(descriptor)
        # rmtree 使用句柄防链接实现；不追随夹具中可能出现的链接。
        if not shutil.rmtree.avoids_symlink_attacks:
            raise IsolationError("本平台缺少安全的目录清理实现")
        shutil.rmtree(self.root)
        self._closed = True


def _probe_child(root):
    require_linux()
    root, _ = _relative_path(root)
    for variable, relative in {**DIRECTORY_VARIABLES, **FILE_VARIABLES}.items():
        if os.environ.get(variable) != str(root / relative):
            raise IsolationError("子进程收到的存储覆盖值不匹配")
    if any(variable in os.environ for variable in AGENT_VARIABLES):
        raise IsolationError("子进程仍继承个人代理或提示器环境")
    for relative in set(DIRECTORY_VARIABLES.values()) | {"logs"}:
        descriptor = _open_directory(root / relative)
        try:
            name = "probe-" + uuid.uuid4().hex
            handle = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                             0o600, dir_fd=descriptor)
            os.close(handle)
            os.unlink(name, dir_fd=descriptor)
        finally:
            os.close(descriptor)
    return {"kind": "linux-environment", "passed": True,
            "directory_overrides": len(DIRECTORY_VARIABLES),
            "file_overrides": len(FILE_VARIABLES), "upstream_storage_verified": False}


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("probe", "_probe-child"))
    parser.add_argument("root", nargs="?", type=Path)
    arguments = parser.parse_args()
    try:
        if arguments.command == "_probe-child":
            if arguments.root is None:
                parser.error("内部探针缺少隔离根目录")
            report = _probe_child(arguments.root)
        else:
            with IsolationEnvironment(arguments.root or DEFAULT_PARENT) as environment:
                report = environment.probe()
        print(json.dumps(report, ensure_ascii=False))
    except (IsolationError, OSError, subprocess.SubprocessError) as error:
        parser.exit(1, f"隔离检查失败：{error}\n")


if __name__ == "__main__":
    main()
