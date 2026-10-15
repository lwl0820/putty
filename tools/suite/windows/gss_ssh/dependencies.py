"""仅在独立 Paramiko 4 环境应用内容锁定的小补丁。"""
import hashlib
import importlib.metadata
import json
from pathlib import Path
import sys

from tools.suite.windows.gss_mit.common import read_regular
from tools.suite.windows.support import ValidationError, project_path

HERE = Path(__file__).resolve().parent
VERSIONS = {"bcrypt": "5.0.0", "cffi": "2.1.1", "cryptography": "50.0.2",
            "invoke": "3.0.3", "paramiko": "4.0.0", "pycparser": "3.11", "PyNaCl": "1.6.2"}
ORIGINAL = {
    "auth_handler.py": "90c634d31e6c524adc47db911c821a910c3813aeb8f685b5b4cb4840fac5305a",
    "transport.py": "06e3b7022d1a684eb5ad0e62fd667b63e658849aacc48665d1b5c3c22e692ca5",
    "ssh_gss.py": "04d8620ce37514e241d8fd954144072d827b4598536c38dcecd3cca923701fa6",
}
IMPORT_OLD = b"from paramiko.ssh_gss import GSSAuth, GSS_EXCEPTIONS"
IMPORT_NEW = b"from tools.suite.windows.gss_ssh.paramiko_adapter import GSSAuth, GSS_EXCEPTIONS"
TABLE_OLD = b"        return self.__handler_table\n"
TABLE_NEW = ("        # 测试夹具仅修复原函数的实例绑定，保留正式认证状态机。\n"
             "        return {kind: function.__get__(self, type(self))\n"
             "                for kind, function in self.__handler_table.items()}\n").encode("utf-8")


def patched_auth(content):
    if hashlib.sha256(content).hexdigest() != ORIGINAL["auth_handler.py"]:
        raise ValidationError("Paramiko 补丁输入与固定官方源码不符")
    if content.count(IMPORT_OLD) != 1 or content.count(TABLE_OLD) != 1:
        raise ValidationError("Paramiko 小补丁的两个唯一上下文不符")
    return content.replace(IMPORT_OLD, IMPORT_NEW).replace(TABLE_OLD, TABLE_NEW)


def source_directory():
    # 查询 metadata 不导入 Paramiko；在首次原生 GSS 加载之前核验依赖。
    if sys.prefix == sys.base_prefix:
        raise ValidationError("GSS SSH 夹具必须使用独立虚拟环境")
    prefix = project_path(sys.prefix, must_exist=True)
    for name, expected in VERSIONS.items():
        if importlib.metadata.version(name) != expected:
            raise ValidationError("GSS SSH 依赖版本与锁定版本不符")
    distribution = importlib.metadata.distribution("paramiko")
    directory = Path(distribution.locate_file("paramiko")).absolute()
    if not directory.is_relative_to(prefix):
        raise ValidationError("Paramiko 必须来自本次项目内独立环境")
    return directory


def verify_sources(*, apply=False):
    directory = source_directory()
    wheel = HERE / "paramiko-4.0.0-gss.patch"
    patch_sha = hashlib.sha256(wheel.read_bytes()).hexdigest()
    # 输出摘要的补丁哈希覆盖可审阅的 unified diff，而非隐藏运行时修改。
    state = json.loads((HERE / "source-lock.json").read_text(encoding="utf-8"))
    if patch_sha != state["patch_sha256"] or state["original"] != ORIGINAL:
        raise ValidationError("Paramiko 小补丁与源码锁不符")
    result = {}
    for name, original in ORIGINAL.items():
        path = directory / name
        content = read_regular(path, maximum=512 * 1024)
        actual = hashlib.sha256(content).hexdigest()
        expected = state["patched_auth_sha256"] if name == "auth_handler.py" else original
        if apply and name == "auth_handler.py" and actual == original:
            modified = patched_auth(content)
            if hashlib.sha256(modified).hexdigest() != expected:
                raise ValidationError("Paramiko 补丁输出与固定哈希不符")
            # 显式准备阶段修改独立 venv；认证进程只读核验，绝不猴补状态机。
            with path.open("wb") as output:
                output.write(modified)
                output.flush()
                import os
                os.fsync(output.fileno())
            actual = hashlib.sha256(read_regular(path, maximum=512 * 1024)).hexdigest()
        if actual != expected:
            raise ValidationError("Paramiko 正式认证源码与小补丁边界不符")
        result[name] = actual
    return result


def verify_windows_wheels(directory):
    directory = project_path(directory, must_exist=True)
    state = json.loads((HERE / "windows-wheel-lock.json").read_text(encoding="utf-8"))
    files = state["files"]
    if {p.name for p in directory.iterdir()} != {item["wheel"] for item in files}:
        raise ValidationError("Windows wheel 集合不符合七个锁定依赖")
    for item in files:
        content = read_regular(directory / item["wheel"], maximum=item["size"])
        if len(content) != item["size"] or hashlib.sha256(content).hexdigest() != item["sha256"]:
            raise ValidationError("Windows wheel 内容与固定锁不符")
    return len(files)


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--windows-wheels", type=Path)
    args = parser.parse_args()
    if args.windows_wheels:
        verify_windows_wheels(args.windows_wheels)
    verify_sources(apply=args.apply)
    print(json.dumps({"schema": 1, "kind": "paramiko-gss-dependencies", "passed": True,
                      "paramiko_version": "4.0.0", "source_patch_verified": True}))


if __name__ == "__main__":
    main()
