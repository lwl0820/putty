#!/usr/bin/env python3
"""执行 CTest 并强制两个原生用例实际通过，单独记录存储跳过边界。"""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET

from support import TEMPORARY_ROOT, ValidationError, project_path, write_report

EXPECTED = {"runtime-windows-handles", "runtime-windows-events"}


def validate_selection(selection):
    names = [test["name"] for test in selection.get("tests", [])]
    if len(names) != 2 or set(names) != EXPECTED:
        raise ValidationError("Windows 原生 CTest 必须恰好注册两个指定宿主")


def inspect_results(path):
    root = ET.parse(path).getroot()
    cases = root.findall(".//testcase")
    native = [case for case in cases if case.get("name") in EXPECTED]
    if len(native) != 2 or {case.get("name") for case in native} != EXPECTED:
        raise ValidationError("JUnit 中缺少两个实际 Windows 原生结果")
    for case in native:
        if case.find("skipped") is not None or case.find("failure") is not None or case.find("error") is not None:
            raise ValidationError("Windows 原生用例失败或被跳过")
    return {"native_passed": sorted(EXPECTED),
            "ctest_total": len(cases),
            "ctest_skipped": [case.get("name") for case in cases if case.find("skipped") is not None],
            "unverified": ["hkcu-isolation", "random-seed-storage", "upstream-cli-storage",
                           "windows-10-desktop", "windows-11-desktop"]}


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build", type=Path, required=True)
    arguments = parser.parse_args()
    try:
        if sys.platform != "win32":
            raise ValidationError("Windows CTest 入口只允许原生 Windows")
        build = project_path(arguments.build, must_exist=True)
        selected = subprocess.run(["ctest", "--test-dir", str(build), "--show-only=json-v1", "-L", "windows-native"],
                                  capture_output=True, check=True)
        validate_selection(json.loads(selected.stdout))
        report_dir = project_path(TEMPORARY_ROOT / "windows-ci/reports")
        report_dir.mkdir(parents=True, exist_ok=True)
        junit = project_path(report_dir / "ctest.xml")
        result = subprocess.run(["ctest", "--test-dir", str(build), "--no-tests=error",
                                 "--output-on-failure", "--output-junit", str(junit)])
        report = inspect_results(junit)
        report.update({"schema": 1, "passed": result.returncode == 0,
                       "storage": "not-run", "cli_executed": False})
        write_report(report_dir, "ctest.json", report)
        if result.returncode:
            return result.returncode
        print("两个 Windows 原生宿主实际通过；存储/原版子进程与目标桌面仍未验")
        return 0
    except (ValidationError, OSError, ValueError, ET.ParseError, subprocess.SubprocessError) as error:
        write_report(TEMPORARY_ROOT / "windows-ci/reports", "ctest.json",
                     {"schema": 1, "passed": False, "error": str(error), "storage": "not-run"})
        print(f"Windows CTest 核查失败：{error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
