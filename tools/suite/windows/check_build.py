#!/usr/bin/env python3
"""只读核对 Windows 构建断言及 CLI 安装清单，不运行任何 CLI 入口。"""
import argparse
import json
from pathlib import Path
import re
import subprocess
import sys

from support import TEMPORARY_ROOT, ValidationError, file_digest, project_path, write_report


def assertions_enabled(command):
    enabled = True
    for match in re.finditer(r"(?:^|\s)[/-]([DU])\s*\"?NDEBUG(?:=[^\s\"]+)?\"?(?=\s|$)", command, re.IGNORECASE):
        enabled = match.group(1).upper() == "U"
    return enabled


def read_cache(build):
    values = {}
    for line in (build / "CMakeCache.txt").read_text(encoding="utf-8").splitlines():
        match = re.match(r"([^/#][^:]*):[^=]+=(.*)$", line)
        if match:
            values[match.group(1)] = match.group(2)
    return values


def installation(build, prefix, components, configuration):
    build = project_path(build, must_exist=True)
    prefix = project_path(prefix, must_exist=True)
    cache = read_cache(build)
    output = build / "suite-cli"
    if cache.get("CMAKE_CONFIGURATION_TYPES"):
        output /= configuration
    expected = {"bin/" + component + ".exe" for component in components}
    actual = set()
    for path in prefix.rglob("*"):
        project_path(path, must_exist=True)
        if path.is_file():
            actual.add(path.relative_to(prefix).as_posix())
    if actual != expected:
        raise ValidationError(f"安装文件集合不符：预期 {sorted(expected)}，实际 {sorted(actual)}")
    hashes = {}
    for component in components:
        source = project_path(output / (component + ".exe"), must_exist=True)
        installed = prefix / "bin" / source.name
        digest = file_digest(source)
        if file_digest(installed) != digest:
            raise ValidationError("安装摘要与对应配置构建产物不一致：" + component)
        hashes[component] = digest
    return {"configuration": configuration, "files": sorted(actual), "sha256": hashes}


def assertions(build, components, configuration):
    build = project_path(build, must_exist=True)
    cache = read_cache(build)
    commands = json.loads((build / "compile_commands.json").read_text(encoding="utf-8"))
    shared = [item.get("command", "") for item in commands
              if "suite-putty.dir" in item.get("command", "")]
    if not shared or any(not assertions_enabled(command) for command in shared):
        raise ValidationError("完整核心没有编译证据或 Release 断言被关闭")
    cli_dir = project_path(build / "suite-cli", must_exist=True)
    cli_cache = read_cache(cli_dir)
    if not assertions_enabled(cli_cache.get("CMAKE_C_FLAGS_RELEASE", "")):
        raise ValidationError("CLI 独立构建仍以 NDEBUG 关闭 Release 断言")
    command = ["ninja", "-C", str(cli_dir)]
    if cache.get("CMAKE_CONFIGURATION_TYPES"):
        command.extend(["-f", f"build-{configuration}.ninja"])
    # compdb -x 展开 rsp 内容，避免长 MSVC 命令把有效断言标志藏在响应文件内。
    command.extend(["-t", "compdb", "-x"])
    result = subprocess.run(command, capture_output=True, check=True)
    actual = [item.get("command", "") for item in json.loads(result.stdout)]
    compiled = [line for line in actual if re.search(r"(?:^|[\\/])cl(?:\.exe)?(?:\"|\s)", line, re.IGNORECASE)
                and re.search(r"(?:^|\s)[/-]c(?:\s|$)", line, re.IGNORECASE)]
    if not compiled or any(not assertions_enabled(line) for line in compiled):
        raise ValidationError("CLI 实际生成编译命令缺失或关闭了 Release 断言")
    return {"shared_compile_commands": len(shared), "cli_compile_commands": len(compiled),
            "release_assertions_enabled": True}


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build", type=Path, required=True)
    parser.add_argument("--prefix", type=Path, required=True)
    parser.add_argument("--components", nargs="+", choices=("plink", "pscp", "psftp"), required=True)
    parser.add_argument("--configuration", choices=("Debug", "Release"), default="Release")
    parser.add_argument("--assertions", action="store_true")
    parser.add_argument("--report", default="build-release.json")
    arguments = parser.parse_args()
    try:
        if Path(arguments.report).name != arguments.report or not arguments.report.endswith(".json"):
            raise ValidationError("报告名必须是单个 JSON 文件名")
        report = installation(arguments.build, arguments.prefix, arguments.components, arguments.configuration)
        if arguments.assertions:
            report.update(assertions(arguments.build, arguments.components, arguments.configuration))
        report.update({"schema": 1, "passed": True, "cli_executed": False,
                       "cli_storage": "not-run"})
        write_report(TEMPORARY_ROOT / "windows-ci/reports", arguments.report, report)
        print("构建/安装清单及对应配置摘要通过；未运行 CLI，存储兼容未验")
        return 0
    except (ValidationError, OSError, ValueError, subprocess.SubprocessError) as error:
        write_report(TEMPORARY_ROOT / "windows-ci/reports", "build-failure.json",
                     {"schema": 1, "passed": False, "error": str(error), "cli_executed": False})
        print(f"Windows 构建核查失败：{error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
