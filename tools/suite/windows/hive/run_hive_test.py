#!/usr/bin/env python3
"""单独执行私有 app hive 自测；不开放现有运行时或原版 CLI。"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

WINDOWS_TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WINDOWS_TOOLS))
from support import TEMPORARY_ROOT, ValidationError, project_path, write_report  # noqa: E402

CHECKS = {"private-acl", "app-hive-create", "hkcu-handle-marker", "reload-persistence",
          "child-not-inherited", "child-explicit-init", "private-seed-file",
          "permissions-closed", "single-mapping-and-path-guard", "owner-handoff", "continuous-mapping", "cleanup"}


def decode_report(output):
    try:
        report = json.loads(output.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValidationError("hive 宿主未返回唯一合法 JSON") from error
    if not isinstance(report, dict) or report.get("schema") != 1 or report.get("kind") != "private-hive" or report.get("passed") is not True:
        raise ValidationError("hive 宿主报告契约不符")
    checks = report.get("checks")
    if not isinstance(checks, list) or any(not isinstance(check, str) for check in checks) or set(checks) != CHECKS or len(checks) != len(CHECKS):
        raise ValidationError("hive 原生实际检查缺失或重复")
    if any(report.get(key) != "not-run" for key in ("upstream_storage", "upstream_seed", "prng")):
        raise ValidationError("隔离探针不能把上游存储或 PRNG 记为已验")
    return report


def child_environment():
    environment = dict(os.environ)
    for key in tuple(environment):
        if key.upper().startswith("PUTTY_SUITE_") or key.upper() in {
            "SSH_AUTH_SOCK", "SSH_AGENT_PID", "SSH_ASKPASS", "SSH_ASKPASS_REQUIRE",
            "GPG_AGENT_INFO", "GPG_TTY", "PUTTY_RUNTIME_ISOLATED_ROOT",
        }:
            environment.pop(key)
    # 原生宿主创建私有根后，再为其显式启动的两个子进程收紧到该根。
    for key in ("TMP", "TEMP", "TMPDIR"):
        environment[key] = str(TEMPORARY_ROOT)
    return environment


def run(binary, parent):
    if sys.platform != "win32":
        raise ValidationError("私有 hive 探针必须在原生 Windows 执行，不能跳过冒充通过")
    binary = project_path(binary, must_exist=True)
    if binary.name.casefold() != "suite-windows-hive-tests.exe" or not binary.is_file():
        raise ValidationError("仅允许独立 hive 自测宿主；不允许原版 CLI 或旧运行时")
    parent = project_path(parent)
    if parent.parent != TEMPORARY_ROOT:
        raise ValidationError("hive 父目录必须是项目 .tmp 的直接子目录")
    before = {path.name for path in parent.iterdir()} if parent.exists() else set()
    result = subprocess.run([str(binary), "--parent", str(parent)], cwd=TEMPORARY_ROOT,
                            env=child_environment(), capture_output=True, timeout=75)
    if result.returncode:
        raise ValidationError(f"hive 自测失败，退出码 {result.returncode}："
                              + result.stderr.decode("utf-8", errors="replace")[:4096])
    report = decode_report(result.stdout)
    if {path.name for path in parent.iterdir()} != before:
        raise ValidationError("hive 自测结束后存在新运行根残留")
    report.update({"binary_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
                   "platform": sys.platform, "existing_tests_enabled": False})
    return report


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--parent", type=Path, default=TEMPORARY_ROOT / "windows-hive")
    arguments = parser.parse_args()
    directory = TEMPORARY_ROOT / "windows-hive-reports"
    try:
        report = run(arguments.binary, arguments.parent)
        write_report(directory, "private-hive.json", report)
        print(f"私有 app hive：{len(CHECKS)} 项隔离检查通过；上游存储、种子与 PRNG 未运行")
        return 0
    except (ValidationError, OSError, subprocess.SubprocessError) as error:
        write_report(directory, "private-hive.json",
                     {"schema": 1, "kind": "private-hive", "passed": False, "error": str(error),
                      "upstream_storage": "not-run", "upstream_seed": "not-run", "prng": "not-run",
                      "existing_tests_enabled": False})
        print(f"私有 hive 隔离失败：{error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
