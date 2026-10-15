#!/usr/bin/env python3
"""执行 CTest 并核对必需原生宿主，区分受控存储与生产路径。"""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET

from support import TEMPORARY_ROOT, ValidationError, project_path, write_report

EXPECTED = {"runtime-windows-handles", "runtime-windows-events"}
HIVE_EXPECTED = {"runtime-windows-hive"}
STORAGE_EXPECTED = {"runtime-windows-storage"}
TERMINAL_EXPECTED = {"terminal-cpu-render", "terminal-windows-platform",
                     "terminal-gss-mic-failures", "terminal-sspi-mic-failures-shim"}
PAGEANT_EXPECTED = {"pageant-windows-ipc", "pageant-windows-runner-contract"}
TERMINAL_SSH_EXPECTED = {"terminal-windows-ssh-kdf-render", "terminal-windows-ssh-runner-contract"}
SSPI_NEGATIVE_EXPECTED = {"terminal-windows-sspi-negative"}


def validate_selection(selection, expected=EXPECTED):
    names = [test["name"] for test in selection.get("tests", [])]
    if len(names) != len(expected) or set(names) != expected:
        raise ValidationError("Windows 必需 CTest 注册集合不符：" + ", ".join(sorted(expected)))


def inspect_results(path, require_hive=False, require_storage=False, require_terminal=False,
                    require_pageant=False, require_terminal_ssh=False, require_sspi_negative=False):
    root = ET.parse(path).getroot()
    cases = root.findall(".//testcase")
    native = [case for case in cases if case.get("name") in EXPECTED]
    if len(native) != 2 or {case.get("name") for case in native} != EXPECTED:
        raise ValidationError("JUnit 中缺少两个实际 Windows 原生结果")
    for case in native:
        if (case.find("skipped") is not None or case.find("failure") is not None or case.find("error") is not None or
                case.get("status", "run").lower() in {"notrun", "disabled", "skipped"}):
            raise ValidationError("Windows 原生用例失败或被跳过")
    if require_hive:
        hive = [case for case in cases if case.get("name") in HIVE_EXPECTED]
        if (len(hive) != 1 or any(hive[0].find(tag) is not None for tag in ("skipped", "failure", "error")) or
                hive[0].get("status", "run").lower() in {"notrun", "disabled", "skipped"}):
            raise ValidationError("私有 hive 隔离用例缺失、失败或被跳过")
    if require_storage:
        if not require_hive:
            raise ValidationError("受控存储检查必须同时要求私有 hive 隔离")
        storage = [case for case in cases if case.get("name") in STORAGE_EXPECTED]
        if (len(storage) != 1 or any(storage[0].find(tag) is not None for tag in ("skipped", "failure", "error")) or
                storage[0].get("status", "run").lower() in {"notrun", "disabled", "skipped"}):
            raise ValidationError("受控上游存储用例缺失、失败或被跳过")
    if require_terminal:
        terminal = [case for case in cases if case.get("name") in TERMINAL_EXPECTED]
        if (len(terminal) != len(TERMINAL_EXPECTED) or
                {case.get("name") for case in terminal} != TERMINAL_EXPECTED or
                any(any(case.find(tag) is not None for tag in ("skipped", "failure", "error")) or
                    case.get("status", "run").lower() in {"notrun", "disabled", "skipped"}
                    for case in terminal)):
            raise ValidationError("四项终端原生用例缺失、重复、失败或被跳过")
    if require_pageant:
        pageant = [case for case in cases if case.get("name") in PAGEANT_EXPECTED]
        if (len(pageant) != len(PAGEANT_EXPECTED) or
                {case.get("name") for case in pageant} != PAGEANT_EXPECTED or
                any(any(case.find(tag) is not None for tag in ("skipped", "failure", "error")) or
                    case.get("status", "run").lower() in {"notrun", "disabled", "skipped"}
                    for case in pageant)):
            raise ValidationError("Pageant IPC 宿主或运行器契约缺失、重复、失败或被跳过")
    if require_terminal_ssh:
        if not require_hive:
            raise ValidationError("真实终端 SSH 检查必须同时要求私有 hive 隔离")
        terminal_ssh = [case for case in cases if case.get("name") in TERMINAL_SSH_EXPECTED]
        if (len(terminal_ssh) != len(TERMINAL_SSH_EXPECTED) or
                {case.get("name") for case in terminal_ssh} != TERMINAL_SSH_EXPECTED or
                any(any(case.find(tag) is not None for tag in ("skipped", "failure", "error")) or
                    case.get("status", "run").lower() in {"notrun", "disabled", "skipped"}
                    for case in terminal_ssh)):
            raise ValidationError("真实终端 SSH 宿主或运行器契约缺失、重复、失败或被跳过")
    if require_sspi_negative:
        negative = [case for case in cases if case.get("name") in SSPI_NEGATIVE_EXPECTED]
        if (len(negative) != 1 or
                any(negative[0].find(tag) is not None for tag in ("skipped", "failure", "error")) or
                negative[0].get("status", "run").lower() in {"notrun", "disabled", "skipped"}):
            raise ValidationError("原生 SSPI 负例缺失、重复、失败或被跳过")
    return {"native_passed": sorted(EXPECTED),
            "private_hive_probe": "passed" if require_hive else "not-run",
            "private_upstream_storage": "passed" if require_storage else "not-run",
            "terminal_native_passed": sorted(TERMINAL_EXPECTED) if require_terminal else [],
            "pageant_private_ipc": "passed" if require_pageant else "not-run",
            "terminal_private_ssh_ppk": "passed" if require_terminal_ssh else "not-run",
            "terminal_native_sspi_negative": "passed" if require_sspi_negative else "not-run",
            "ctest_total": len(cases),
            "ctest_skipped": [case.get("name") for case in cases if case.find("skipped") is not None],
            "unverified": (["hkcu-isolation"] if not require_hive else []) +
                          (["random-seed-storage"] if not require_storage else []) +
                          (["terminal-real-ssh-ppk"] if not require_terminal_ssh else []) +
                          ["production-seed-fallback", "upstream-cli-storage", "existing-runtime-storage",
                           "terminal-native-sspi",
                           "pageant-original-name-cross-version", "pageant-cross-integrity-owner-acl",
                           "pageant-openssh-authentication",
                           "windows-10-desktop", "windows-11-desktop"]}


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build", type=Path, required=True)
    parser.add_argument("--require-hive", action="store_true", help="同时强制私有 hive 探针实际通过")
    parser.add_argument("--require-storage", action="store_true", help="同时强制受控上游存储变体实际通过，须配合 --require-hive")
    parser.add_argument("--require-terminal", action="store_true", help="同时强制四项无存储终端原生用例实际通过")
    parser.add_argument("--require-pageant", action="store_true", help="同时强制私有 Pageant IPC 宿主和运行器契约实际通过")
    parser.add_argument("--require-terminal-ssh", action="store_true", help="同时强制真实终端 SSH／PPK 宿主及运行器契约通过，须配合 --require-hive")
    parser.add_argument("--require-sspi-negative", action="store_true", help="同时强制真实 SSPI 无效句柄负例通过；不表示 Kerberos 正向认证通过")
    arguments = parser.parse_args()
    try:
        if sys.platform != "win32":
            raise ValidationError("Windows CTest 入口只允许原生 Windows")
        if arguments.require_storage and not arguments.require_hive:
            raise ValidationError("--require-storage 必须配合 --require-hive")
        if arguments.require_terminal_ssh and not arguments.require_hive:
            raise ValidationError("--require-terminal-ssh 必须配合 --require-hive")
        build = project_path(arguments.build, must_exist=True)
        selected = subprocess.run(["ctest", "--test-dir", str(build), "--show-only=json-v1", "-L", "windows-native"],
                                  capture_output=True, check=True)
        validate_selection(json.loads(selected.stdout))
        if arguments.require_hive:
            hive_selection = subprocess.run(["ctest", "--test-dir", str(build), "--show-only=json-v1", "-L", "windows-hive"],
                                            capture_output=True, check=True)
            hive_names = [test["name"] for test in json.loads(hive_selection.stdout).get("tests", [])]
            if len(hive_names) != 1 or set(hive_names) != HIVE_EXPECTED:
                raise ValidationError("必须恰好注册一个私有 hive 隔离用例")
        if arguments.require_storage:
            storage_selection = subprocess.run(["ctest", "--test-dir", str(build), "--show-only=json-v1", "-L", "windows-storage"],
                                               capture_output=True, check=True)
            storage_names = [test["name"] for test in json.loads(storage_selection.stdout).get("tests", [])]
            if len(storage_names) != 1 or set(storage_names) != STORAGE_EXPECTED:
                raise ValidationError("必须恰好注册一个受控上游存储用例")
        if arguments.require_terminal:
            terminal_selection = subprocess.run(
                ["ctest", "--test-dir", str(build), "--show-only=json-v1", "-L", "^windows-terminal$"],
                capture_output=True, check=True)
            validate_selection(json.loads(terminal_selection.stdout), TERMINAL_EXPECTED)
        if arguments.require_pageant:
            pageant_selection = subprocess.run(
                ["ctest", "--test-dir", str(build), "--show-only=json-v1", "-L", "windows-pageant"],
                capture_output=True, check=True)
            validate_selection(json.loads(pageant_selection.stdout), PAGEANT_EXPECTED)
        if arguments.require_terminal_ssh:
            ssh_selection = subprocess.run(
                ["ctest", "--test-dir", str(build), "--show-only=json-v1", "-L", "^windows-terminal-ssh$"],
                capture_output=True, check=True)
            validate_selection(json.loads(ssh_selection.stdout), TERMINAL_SSH_EXPECTED)
        if arguments.require_sspi_negative:
            negative_selection = subprocess.run(
                ["ctest", "--test-dir", str(build), "--show-only=json-v1", "-L", "^windows-sspi-negative$"],
                capture_output=True, check=True)
            validate_selection(json.loads(negative_selection.stdout), SSPI_NEGATIVE_EXPECTED)
        report_dir = project_path(TEMPORARY_ROOT / "windows-ci/reports")
        report_dir.mkdir(parents=True, exist_ok=True)
        junit = project_path(report_dir / "ctest.xml")
        if junit.exists():
            junit.unlink()
        result = subprocess.run(["ctest", "--test-dir", str(build), "--no-tests=error",
                                 "--output-on-failure", "--output-junit", str(junit)])
        report = inspect_results(junit, arguments.require_hive, arguments.require_storage,
                                 arguments.require_terminal, arguments.require_pageant,
                                 arguments.require_terminal_ssh, arguments.require_sspi_negative)
        report.update({"schema": 1, "passed": result.returncode == 0,
                       "storage": "private-variant-passed" if arguments.require_storage else "not-run", "cli_executed": False})
        write_report(report_dir, "ctest.json", report)
        if result.returncode:
            return result.returncode
        print("Windows 必需原生宿主实际通过；生产种子回退、原版子进程与目标桌面仍未验")
        return 0
    except (ValidationError, OSError, ValueError, ET.ParseError, subprocess.SubprocessError) as error:
        write_report(TEMPORARY_ROOT / "windows-ci/reports", "ctest.json",
                     {"schema": 1, "passed": False, "error": str(error), "storage": "not-run"})
        print(f"Windows CTest 核查失败：{error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
