#!/usr/bin/env python3
"""只运行受控 Windows 存储变体宿主，保留生产回退和原版 CLI 的未验边界。"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

WINDOWS_TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WINDOWS_TOOLS / "hive"))
from run_hive_test import child_environment  # noqa: E402
from support import ROOT, TEMPORARY_ROOT, ValidationError, file_digest, project_path, write_report  # noqa: E402

CHECKS = {"session-settings", "session-enumeration", "font-filename", "hostkey-cache", "host-ca", "host-ca-delete",
          "hive-handle-readback", "child-explicit-init", "seed-binary-read-write", "seed-private-handle",
          "unbound-rejected", "missing-config-rejected", "corrupt-config-rejected", "outside-seed-rejected",
          "read-open-failure-rejected", "write-open-failure-rejected", "real-prng", "prng-destroy-timers", "cleanup"}
# 固定上游源码变更时先重新审查所有文件候选和宏覆盖，再更新摘要。
STORAGE_SHA256 = "e2c323be831900ac99ac8075409f0e9c1516dd51c9a39de8a2305d5b469e928a"


def audit_storage_source(path=ROOT / "vendor/putty/windows/storage.c"):
    # Git 的 Windows checkout 可能转换 CRLF；只归一换行，不忽略其它源代码差异。
    digest = hashlib.sha256(Path(path).read_bytes().replace(b"\r\n", b"\n")).hexdigest()
    if digest != STORAGE_SHA256:
        raise ValidationError("上游 Windows storage 源码已变更，须重新审查种子回退边界")
    return STORAGE_SHA256


def audit_link_map(path):
    path = project_path(path, must_exist=True)
    source = path.read_text(encoding="utf-8", errors="replace")
    if re.search(r"(?<![\w])storage(?:\.c)?\.obj\b", source, re.IGNORECASE):
        raise ValidationError("存储宿主链接了生产 storage 对象，不能绕过测试变体")
    for required in ("storage_variant", "seed_guard", "settings", "sshrand", "noise"):
        if not re.search(r"\b" + required + r"(?:\.c)?\.obj\b", source, re.IGNORECASE):
            raise ValidationError("链接图缺少真实上游或受控变体对象：" + required)
    for symbol in ("save_settings", "load_settings", "store_host_key", "check_stored_host_key",
                   "host_ca_save", "host_ca_load", "host_ca_delete", "read_random_seed", "write_random_seed",
                   "random_ref", "random_read", "random_clear", "prng_new", "prng_free",
                   "noise_get_heavy", "win_read_random", "schedule_timer", "suite_seed_CreateFileA"):
        if not re.search(r"\b_?" + symbol + r"\b", source):
            raise ValidationError("链接图缺少必需的真实调用或失败闭合入口：" + symbol)
    return {"guarded_variant_only": True, "real_settings_noise_prng_linked": True}


def decode_report(output):
    try:
        report = json.loads(output.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValidationError("存储宿主未返回唯一 UTF-8 JSON 摘要") from error
    if not isinstance(report, dict) or report.get("schema") != 1 or report.get("kind") != "private-storage" or report.get("passed") is not True:
        raise ValidationError("存储宿主报告契约不符")
    checks = report.get("checks")
    if not isinstance(checks, list) or any(not isinstance(check, str) for check in checks) or set(checks) != CHECKS or len(checks) != len(CHECKS):
        raise ValidationError("实际存储检查缺失或重复")
    if (report.get("storage_variant") != "guarded-win32" or report.get("production_fallback") != "not-run" or
            report.get("upstream_cli") != "not-run" or report.get("existing_tests_enabled") is not False):
        raise ValidationError("测试变体不能开放生产回退、旧运行时或原版 CLI")
    return report


def run(binary, parent):
    if sys.platform != "win32":
        raise ValidationError("真实 Windows 存储宿主只能在原生 Windows 执行，不能跳过冒充通过")
    binary = project_path(binary, must_exist=True)
    if binary.name.casefold() != "suite-windows-storage-tests.exe" or not binary.is_file():
        raise ValidationError("只允许独立受控存储宿主，禁止原版 CLI 和旧运行时")
    parent = project_path(parent)
    if parent.parent != TEMPORARY_ROOT:
        raise ValidationError("存储父目录必须是项目 .tmp 的直接子目录，由 C 层创建私有权限")
    source_sha256 = audit_storage_source()
    link = audit_link_map(Path(str(binary) + ".map"))
    before = {path.name for path in parent.iterdir()} if parent.exists() else set()
    result = subprocess.run([str(binary), "--parent", str(parent)], cwd=TEMPORARY_ROOT,
                            env=child_environment(), capture_output=True, timeout=105)
    if result.returncode:
        raise ValidationError(f"受控存储宿主失败，退出码 {result.returncode}："
                              + result.stderr.decode("utf-8", errors="replace")[:4096])
    report = decode_report(result.stdout)
    if {path.name for path in parent.iterdir()} != before:
        raise ValidationError("存储宿主成功后仍有新的私有运行根残留")
    report.update({"binary_sha256": file_digest(binary), "platform": sys.platform,
                   "storage_source_sha256": source_sha256, "link_audit": link,
                   "unverified": ["production-seed-fallback", "upstream-cli-storage",
                                  "existing-runtime-storage", "windows-10-desktop", "windows-11-desktop"]})
    return report


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--parent", type=Path, default=TEMPORARY_ROOT / "windows-storage")
    parser.add_argument("--report-dir", type=Path, default=TEMPORARY_ROOT / "windows-storage-reports")
    arguments = parser.parse_args()
    try:
        report = run(arguments.binary, arguments.parent)
        write_report(arguments.report_dir, "private-storage.json", report)
        print(f"Windows 受控存储：{len(CHECKS)} 项检查通过；生产回退、原版 CLI 与旧运行时未开放")
        return 0
    except (ValidationError, OSError, subprocess.SubprocessError) as error:
        write_report(arguments.report_dir, "private-storage.json",
                     {"schema": 1, "kind": "private-storage", "passed": False, "error": str(error),
                      "production_fallback": "not-run", "upstream_cli": "not-run", "existing_tests_enabled": False})
        print(f"受控存储测试失败：{error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
