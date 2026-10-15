#!/usr/bin/env python3
"""执行编译随机命名空间的原生 Pageant IPC 宿主；拒绝生产程序。"""
import argparse
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import uuid

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "tools/suite/windows"))
from support import (ValidationError, file_digest, native_environment, project_path,
                     write_report)  # noqa: E402

CHECKS = {"startup-discovery", "dual-list", "pipe-add-wm-list", "dual-signatures",
          "malformed-copydata", "malformed-mapping", "cross-transport-remove",
          "deferred-cancel", "deferred-decrypt", "pending-close",
          "partial-pipe-close", "endpoint-cleanup",
          "stalled-pipe-client-close", "stalled-window-client-close", "fragmented-client-query"}


def audit_map(path):
    content = project_path(path, must_exist=True).read_text(encoding="utf-8", errors="replace")
    if re.search(r"(?<![\w])(?:sshrand|storage|noise)(?:\.c)?\.obj\b", content, re.I):
        raise ValidationError("Pageant 无存储宿主抽入了持久化/客户端 PRNG 对象")
    if re.search(r"(?<![\w])(?:aqsync|agent-client)(?:\.c)?\.obj\b", content, re.I):
        raise ValidationError("Pageant 宿主抽入了无超时或未隔离的原同步客户端")
    if re.search(r"\b_?(?:random_ref|random_reseed)\b", content):
        raise ValidationError("Pageant 无存储宿主抽入了客户端 PRNG 入口")
    for symbol in ("read_random_seed", "write_random_seed", "open_settings_r", "open_settings_w",
                   "pageant_init", "pa_windows_ipc_start", "random_read",
                   "agent_query_synchronous", "pa_windows_query_failed"):
        if not re.search(r"\b_?" + symbol + r"\b", content):
            raise ValidationError("Pageant 链接图缺少预期核心或持久化拒绝入口：" + symbol)
    # random_read 必须来自原版 pageant.c 的致命拒绝实现，不是 sshrand。
    return {"client_prng_objects_absent": True, "storage_tripwires_linked": True}


def decode(output):
    try:
        report = json.loads(output.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ValidationError("Pageant 宿主没有返回唯一合法 JSON") from error
    if (not isinstance(report, dict) or report.get("schema") != 1 or
            report.get("kind") != "pageant-ipc" or report.get("passed") is not True or
            report.get("storage") != "not-run"):
        raise ValidationError("Pageant 原生报告契约不符")
    checks = report.get("checks")
    if (not isinstance(checks, list) or any(not isinstance(check, str) for check in checks) or
            set(checks) != CHECKS or len(checks) != len(CHECKS)):
        raise ValidationError("Pageant 原生实际检查缺失或重复")
    return report


def run(binary, parent):
    if sys.platform != "win32":
        raise ValidationError("必须在原生 Windows 执行，不能用跳过冒充通过")
    binary = project_path(binary, must_exist=True)
    if binary.name.casefold() != "pageant-windows-ipc-tests.exe" or not binary.is_file():
        raise ValidationError("只允许专用 IPC 宿主，不允许运行生产 Pageant")
    link = audit_map(Path(str(binary) + ".map"))
    parent = project_path(parent)
    parent.mkdir(parents=True, exist_ok=True)
    root = project_path(parent / ("run-" + uuid.uuid4().hex))
    root.mkdir()
    try:
        result = subprocess.run([str(binary), "--scope", uuid.uuid4().hex], cwd=root, env=native_environment(root),
                                capture_output=True, timeout=70)
        if result.returncode:
            raise ValidationError(f"Pageant 原生宿主失败，退出码 {result.returncode}：" +
                                  result.stderr.decode("utf-8", errors="replace")[:4096])
        report = decode(result.stdout)
        if any(root.iterdir()):
            raise ValidationError("Pageant 无存储宿主意外创建运行产物")
        report.update({"binary_sha256": file_digest(binary), "link_audit": link,
                       "platform": sys.platform, "namespace": "compile-time-and-per-run-random",
                       "unverified": ["original-name-cross-version-startup", "cross-integrity-owner-acl",
                                      "windows-10-desktop", "windows-11-desktop", "openssh-authentication",
                                      "upstream-storage", "windows-gui"]})
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
    parser.add_argument("--parent", type=Path, required=True)
    parser.add_argument("--report-dir", type=Path, default=ROOT / ".tmp/windows-ci/reports")
    arguments = parser.parse_args()
    try:
        report = run(arguments.binary, arguments.parent)
        write_report(arguments.report_dir, "pageant-ipc.json", report)
        print(f"Windows Pageant IPC：{len(report['checks'])} 项原生检查通过；原名跨版本和桌面尚未运行")
        return 0
    except (ValidationError, OSError, subprocess.SubprocessError) as error:
        write_report(arguments.report_dir, "pageant-ipc.json", {
            "schema": 1, "kind": "pageant-ipc", "passed": False, "storage": "not-run", "error": str(error)})
        print(f"Windows Pageant IPC 夹具失败：{error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
