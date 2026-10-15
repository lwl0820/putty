"""MIT 夹具的有界文件、环境和脱敏报告边界。"""
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import threading
import time
import uuid

ROOT = Path(__file__).resolve().parents[4]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from tools.suite.windows.support import ValidationError, project_path

HERE = Path(__file__).resolve().parent
LOCK_PATH = HERE / "kfw-lock.json"


def digest(content):
    return hashlib.sha256(content).hexdigest()


def regular_file(path, *, maximum, project=True):
    path = project_path(path, must_exist=True) if project else Path(path).absolute()
    for component in (*reversed(path.parents), path):
        info = component.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ValidationError("文件路径不能经过链接或 reparse point")
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > maximum:
        raise ValidationError("文件类型、链接数或体积不符合边界")
    return path


def read_regular(path, *, maximum):
    path = regular_file(path, maximum=maximum)
    # 同时核验打开前后身份，避免把路径检查当成实际读取句柄检查。
    before = path.lstat()
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0))
    try:
        current = os.fstat(descriptor)
        if (current.st_dev, current.st_ino) != (before.st_dev, before.st_ino) or current.st_nlink != 1:
            raise ValidationError("文件在打开边界发生替换或出现硬链接")
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            content = stream.read(maximum + 1)
        if len(content) > maximum or len(content) != current.st_size:
            raise ValidationError("文件读取超限或长度变化")
        return content
    finally:
        os.close(descriptor)


class WorkspaceFiles:
    """Windows 使用共同两 SID ACL；Linux 只用于解包结构验证。"""

    def __init__(self):
        self.private = None
        if sys.platform == "win32":
            from tools.suite.windows.private_data import PrivateData
            self.private = PrivateData()

    def directory(self, path, *, existing=False):
        path = project_path(path)
        if self.private:
            return self.private.directory(path, allow_existing=existing)
        try:
            path.mkdir(mode=0o700)
        except FileExistsError:
            if not existing:
                raise ValidationError("准备目录必须是新建目录") from None
        info = path.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise ValidationError("解包目录必须由当前用户独占")
        return path

    def write(self, path, content):
        path = project_path(path)
        if self.private:
            self.private.write(path, content)
            self.private.check_file(path)
            return path
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        regular_file(path, maximum=len(content))
        return path

    def close(self):
        if self.private:
            self.private.close()


def closed_environment(root, base=None):
    # 只传启动程序所需的公开系统位置；不继承个人 Kerberos、代理或插件配置。
    inherited = os.environ if base is None else base
    allowed = {"SYSTEMROOT", "WINDIR", "COMSPEC", "SYSTEMDRIVE", "NUMBER_OF_PROCESSORS", "PROCESSOR_ARCHITECTURE"}
    environment = {key: value for key, value in inherited.items() if key.upper() in allowed}
    environment.update({"TMP": str(root), "TEMP": str(root), "TMPDIR": str(root)})
    for name in ("HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "XDG_CONFIG_HOME",
                 "XDG_CACHE_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME", "XDG_RUNTIME_DIR"):
        environment[name] = str(root)
    return environment


def run_bounded(command, *, maximum, cwd, environment, timeout=60):
    """两个管道独立限额，超限立即杀死解包器；错误不回显路径或完整日志。"""
    process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, cwd=cwd, env=environment)
    buffers, failures = [bytearray(), bytearray()], []

    def drain(index, stream, limit):
        try:
            while True:
                part = stream.read(65536)
                if not part:
                    break
                if len(buffers[index]) + len(part) > limit:
                    failures.append("limit")
                    process.kill()
                    break
                buffers[index].extend(part)
        except OSError:
            failures.append("pipe")
        finally:
            stream.close()

    workers = [threading.Thread(target=drain, args=(0, process.stdout, maximum), daemon=True,
                                name="mit-extractor-stdout"),
               threading.Thread(target=drain, args=(1, process.stderr, 65536), daemon=True,
                                name="mit-extractor-stderr")]
    for worker in workers:
        worker.start()
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        failures.append("timeout")
        process.kill()
        process.wait(timeout=5)
    finally:
        # 两个线程共享五秒收尾预算，不能每个线程各消耗一个超时。
        cleanup_deadline = time.monotonic() + 5
        for worker in workers:
            worker.join(timeout=max(0, cleanup_deadline - time.monotonic()))
    if failures or process.returncode or any(worker.is_alive() for worker in workers):
        raise ValidationError("有界解包器执行失败、超时或输出超限")
    return bytes(buffers[0])


def publish_report(path, report):
    path = project_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    project_path(path.parent, must_exist=True)
    content = (json.dumps(report, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    try:
        path.lstat()
    except FileNotFoundError:
        pass
    else:
        regular_file(path, maximum=65536)
    pending = path.parent / (".mit-report-" + uuid.uuid4().hex)
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


def utf8_streams():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
