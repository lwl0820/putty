#!/usr/bin/env python3
"""只运行白名单 Windows 无存储宿主；不因 CI 环境变量放行注册表测试。"""
import argparse
from pathlib import Path
import shutil
import subprocess
import sys
import uuid

from support import (BINARIES, TEMPORARY_ROOT, ValidationError, audit_link_map,
                     decode_native_report, file_digest, native_environment,
                     project_path, write_report)


def run(binary, kind, parent):
    if sys.platform != "win32":
        raise ValidationError("此入口必须在原生 Windows 执行；不能用跳过冒充通过")
    binary = project_path(binary, must_exist=True)
    if binary.name.casefold() != BINARIES[kind].casefold() or not binary.is_file():
        raise ValidationError("只允许运行指定的无存储原生测试宿主")
    parent = project_path(parent)
    parent.mkdir(parents=True, exist_ok=True)
    root = project_path(parent / ("run-" + uuid.uuid4().hex))
    root.mkdir()
    try:
        link = audit_link_map(Path(str(binary) + ".map")) if kind == "events" else {}
        result = subprocess.run([str(binary)], cwd=root, env=native_environment(root),
                                capture_output=True, timeout=45)
        if result.returncode:
            raise ValidationError(f"原生 {kind} 宿主失败，退出码 {result.returncode}："
                                  + result.stderr.decode("utf-8", errors="replace")[:4096])
        report = decode_native_report(result.stdout, kind)
        if any(root.iterdir()):
            raise ValidationError("无存储宿主意外创建运行产物")
        report.update({"binary_sha256": file_digest(binary), "link_audit": link,
                       "platform": sys.platform,
                       "unverified": ["hkcu-isolation", "random-seed-storage", "upstream-cli-storage",
                                      "windows-10-desktop", "windows-11-desktop"]})
        return report
    finally:
        project_path(root, must_exist=True)
        shutil.rmtree(root)


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--kind", choices=tuple(BINARIES), required=True)
    parser.add_argument("--parent", type=Path, required=True)
    parser.add_argument("--report-dir", type=Path, default=TEMPORARY_ROOT / "windows-ci/reports")
    arguments = parser.parse_args()
    try:
        report = run(arguments.binary, arguments.kind, arguments.parent)
        write_report(arguments.report_dir, "native-" + arguments.kind + ".json", report)
        print(f"Windows 原生 {arguments.kind}：{len(report['checks'])} 项实际检查通过；存储组未运行")
        return 0
    except (ValidationError, OSError, subprocess.SubprocessError) as error:
        write_report(arguments.report_dir, "native-" + arguments.kind + ".json",
                     {"schema": 1, "kind": arguments.kind, "passed": False, "error": str(error),
                      "storage": "not-run"})
        print(f"Windows 原生夹具失败：{error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
