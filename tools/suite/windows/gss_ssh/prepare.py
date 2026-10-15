"""准备独立 Python 3.11 Windows x64 环境，只安装七个内容锁定 wheel。"""
import argparse
import json
import os
from pathlib import Path
import sys

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[4]))
from tools.suite.windows.gss_kdc.process import run_bounded
from tools.suite.windows.gss_kdc.security import PrivateTree, closed_environment, utf8_streams
from tools.suite.windows.gss_ssh.dependencies import HERE, verify_windows_wheels
from tools.suite.windows.support import ValidationError, project_path


def prepare(parent, *, wheel_directory=None):
    if sys.platform != "win32" or sys.version_info[:2] != (3, 11):
        raise ValidationError("依赖准备器只接受 Windows x64 Python 3.11")
    import struct
    if struct.calcsize("P") != 8:
        raise ValidationError("依赖准备器只接受 Windows x64 Python 3.11")
    parent = project_path(parent)
    try:
        parent.lstat()
    except FileNotFoundError:
        tree = PrivateTree(parent)
    else:
        tree = PrivateTree(parent, existing=True)
    try:
        # 准备失败不复用旧状态。依赖为公开产物，保留已知准备目录供重查。
        environment = closed_environment(tree)
        venv = parent / "venv"
        try:
            venv.lstat()
        except FileNotFoundError:
            run_bounded([sys.executable, "-I", "-B", "-m", "venv", venv], cwd=parent,
                        environment=environment, timeout=90, output_limit=65536)
        else:
            raise ValidationError("准备器必须使用新的独立 venv 目录")
        python = venv / "Scripts" / "python.exe"
        if wheel_directory is None:
            wheels = tree.directory(parent / "wheels")
            run_bounded([python, "-I", "-B", "-m", "pip", "--isolated", "--disable-pip-version-check",
                "download", "--no-cache-dir", "--require-hashes", "--only-binary=:all:",
                "--dest", wheels, "-r", HERE / "requirements-windows.txt"],
                cwd=parent, environment=environment, timeout=120, output_limit=262144)
        else:
            wheels = project_path(wheel_directory, must_exist=True)
        verify_windows_wheels(wheels)
        run_bounded([python, "-I", "-B", "-m", "pip", "--isolated", "--disable-pip-version-check",
            "install", "--no-index", "--no-cache-dir", "--require-hashes", "--only-binary=:all:",
            "--find-links", wheels, "-r", HERE / "requirements-windows.txt"],
            cwd=parent, environment=environment, timeout=90, output_limit=262144)
        # -I 不搜索 cwd；固定脚本只从自己的已知仓库根导入本模块。
        run_bounded([python, "-I", "-B", HERE / "apply_patch.py"], cwd=parent,
                    environment=environment, timeout=15, output_limit=4096)
        return {"schema": 1, "kind": "gss-ssh-preparation", "passed": True,
                "wheel_count": 7, "paramiko_version": "4.0.0", "source_patch_verified": True,
                "authentication_verified": False}
    finally:
        tree.close()


def main():
    utf8_streams()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--parent", type=Path, required=True)
    parser.add_argument("--wheel-directory", type=Path)
    args = parser.parse_args()
    try:
        report = prepare(args.parent, wheel_directory=args.wheel_directory)
    except Exception:
        report = {"schema": 1, "kind": "gss-ssh-preparation", "passed": False,
                  "error": "dependency-preparation-failed", "authentication_verified": False}
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
