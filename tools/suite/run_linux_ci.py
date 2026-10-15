#!/usr/bin/env python3
"""核对目标 Ubuntu 的实际 CTest 结果，拒绝空集合、缺项和跳过。"""
import argparse
import json
import math
import os
from pathlib import Path
import platform
import subprocess
import sys
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
TEMPORARY_ROOT = ROOT / ".tmp"
REQUIRED = {
    "suite-fixture", "runtime-lifecycle", "runtime-handle-registration",
    "runtime-core-link", "runtime-agent-core-link",
    "terminal-gss-mic-failures", "terminal-sspi-mic-failures-shim",
    "terminal-render-bounds", "terminal-runner-report-paths",
    "terminal-ssh-kdf-render", "terminal-gss-render-close",
    "pageant-core-interop", "pageant-original-cli-entry",
}
for component in ("plink", "pscp", "psftp"):
    REQUIRED.update(f"cli-{component}-{scenario}" for scenario in ("local", "sftp"))
REQUIRED.update(("cli-plink-container", "cli-pscp-container"))
REQUIRED.update("pageant-gui-" + scenario for scenario in (
    "defaultLanguageAndSelection", "realAddDeleteAndClearActions",
    "serialPassphrasesAndReencrypt", "noTrayVisibleControlAndRecovery",
    "exitCancelsPromptAndSocket", "clearDuringPromptDismissesDialog",
    "originalCliControlsGuiAgent", "oversizedPassphraseKeepsPrompt"))


def private_path(value):
    path = Path(value).absolute()
    if ".." in path.parts or not path.is_relative_to(TEMPORARY_ROOT) or path == TEMPORARY_ROOT:
        raise ValueError("CI 路径只能位于项目 .tmp/ 内")
    current = ROOT
    for part in path.relative_to(ROOT).parts:
        current = current / part
        if current.is_symlink():
            raise ValueError("CI 路径不能包含符号链接")
    if not path.resolve().is_relative_to(TEMPORARY_ROOT.resolve()):
        raise ValueError("CI 路径解析后越界")
    return path


def selection_names(selection):
    tests = selection.get("tests")
    if not isinstance(tests, list):
        raise ValueError("CTest 未返回用例列表")
    names = [test.get("name") for test in tests if isinstance(test, dict)]
    if len(names) != len(tests) or not all(isinstance(name, str) and name for name in names):
        raise ValueError("CTest 用例名称非法")
    if len(set(names)) != len(names):
        raise ValueError("CTest 注册了重复用例")
    missing = REQUIRED - set(names)
    if missing:
        raise ValueError("CTest 缺少必需用例：" + ", ".join(sorted(missing)))
    return set(names)


def inspect_results(path, selected):
    cases = ET.parse(path).getroot().findall(".//testcase")
    names = [case.get("name") for case in cases]
    if len(names) != len(set(names)) or set(names) != selected:
        raise ValueError("实际 JUnit 与 CTest 注册列表不一致")
    records = []
    for case in cases:
        if any(case.find(tag) is not None for tag in ("failure", "error", "skipped")):
            raise ValueError("用例失败或被跳过：" + case.get("name", ""))
        if case.get("status", "run").lower() in {"notrun", "disabled", "skipped"}:
            raise ValueError("用例没有实际执行：" + case.get("name", ""))
        seconds = float(case.get("time", "0"))
        if not math.isfinite(seconds) or seconds < 0:
            raise ValueError("JUnit 用例耗时非法")
        records.append({"name": case.get("name"), "seconds": seconds, "passed": True})
    return records


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    arguments = parser.parse_args()
    os.umask(0o077)
    report_path = private_path(arguments.report)
    report_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    report = {"schema": 1, "passed": False, "desktop": "not-run"}
    try:
        release = platform.freedesktop_os_release()
        if sys.platform != "linux" or platform.machine() != "x86_64" or release.get("ID") != "ubuntu" or release.get("VERSION_ID") not in {"22.04", "24.04"}:
            raise ValueError("必须在目标 Ubuntu 22.04/24.04 x64 实际运行")
        build = private_path(arguments.build)
        if not build.is_dir():
            raise ValueError("CTest 构建目录不存在")
        report["system"] = {"id": release["ID"], "version": release["VERSION_ID"], "machine": platform.machine()}
        selected = subprocess.run(["ctest", "--test-dir", str(build), "--show-only=json-v1"],
                                  capture_output=True, check=True, timeout=30)
        names = selection_names(json.loads(selected.stdout))
        junit = private_path(report_path.with_suffix(".xml"))
        # 防止本轮未生成结果时误收上次的成功报告。
        junit.unlink(missing_ok=True)
        result = subprocess.run(["ctest", "--test-dir", str(build), "--no-tests=error",
                                 "--output-on-failure", "--output-junit", str(junit)], timeout=1800)
        report["ctest_returncode"] = result.returncode
        report["tests"] = inspect_results(junit, names)
        if result.returncode:
            raise ValueError("CTest 退出码非零")
        report["passed"] = True
        print(f"目标 Ubuntu 实际通过 {len(names)} 个 CTest，用例无缺失、无跳过")
        return 0
    except (OSError, ValueError, ET.ParseError, subprocess.SubprocessError) as error:
        report["error"] = str(error)
        print("Ubuntu CTest 核查失败：" + str(error), file=sys.stderr)
        return 1
    finally:
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
