#!/usr/bin/env python3
"""准备固定官方SDK/包；所有执行与生成物均在项目私有.tmp根。"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import time
import urllib.request
import uuid

ROOT = Path(__file__).resolve().parents[4]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from tools.suite.windows.support import ValidationError, project_path
from tools.suite.windows.gss_kdc.archive import SafeArchive
from tools.suite.windows.gss_kdc.security import PrivateTree, closed_environment, digest, utf8_streams
from tools.suite.windows.gss_kdc.process import run_bounded

HERE = Path(__file__).resolve().parent
LOCK = json.loads((HERE / "dependencies-lock.json").read_text(encoding="utf-8"))


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *unused):
        raise ValidationError("固定依赖下载不允许重定向")


def fetch(tree, url, path, expected, *, algorithm="sha256", max_bytes=384 * 1024 * 1024):
    if not url.startswith(("https://builds.dotnet.microsoft.com/dotnet/", "https://api.nuget.org/v3-flatcontainer/")):
        raise ValidationError("依赖URL不是固定官方来源")
    path = tree.path(path)
    try:
        path.lstat()
    except FileNotFoundError:
        deadline = time.monotonic() + 90
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
        with opener.open(url, timeout=30) as source:
            if source.geturl() != url:
                raise ValidationError("依赖URL发生变化")
            # 单归档有硬体积上限，不落任意不完整归档或系统临时文件。
            content = bytearray()
            while True:
                if time.monotonic() >= deadline:
                    raise ValidationError("依赖下载超出总期限")
                data = source.read1(min(65536, max_bytes + 1 - len(content)))
                if not data:
                    break
                content.extend(data)
                if len(content) > max_bytes:
                    raise ValidationError("下载归档超过固定体积上限")
        if hashlib.new(algorithm, content).hexdigest() != expected:
            raise ValidationError("依赖下载归档哈希不符")
        tree.write(path, bytes(content))
    tree.check_file(path)
    value = hashlib.new(algorithm)
    with path.open("rb") as source:
        while data := source.read(1024 * 1024):
            value.update(data)
    if value.hexdigest() != expected:
        raise ValidationError("既有依赖归档已被改变")
    return path


def source_hash():
    value = hashlib.sha256()
    for name in ("app/KdcFixture.csproj", "app/Program.cs", "app/packages.lock.json", "dependencies-lock.json"):
        value.update(name.encode("ascii"))
        value.update((HERE / name).read_bytes())
    return value.hexdigest()


def prepare(destination):
    destination = project_path(destination)
    existing = destination.exists()
    tree = PrivateTree(destination, existing=existing)
    sdk_tree = build_tree = None
    try:
        # 本轮准备失败时不能留下上一轮成功标记供后续阶段误用。
        state_path = destination / "prepared.json"
        try:
            state_path.lstat()
        except FileNotFoundError:
            pass
        else:
            tree.check_file(state_path)
            state_path.unlink()
        rid = "win-x64" if os.name == "nt" else "linux-x64"
        sdk = next(value for value in LOCK["dotnet_sdk_files"] if value["rid"] == rid)
        archive = fetch(tree, sdk["url"], destination / "downloads" / sdk["url"].rsplit("/", 1)[1], sdk["hash"], algorithm="sha512")
        sdk_root = destination / "sdk"
        sdk_tree = PrivateTree(sdk_root, existing=sdk_root.exists())
        with SafeArchive(archive) as package:
            if not any(sdk_root.iterdir()):
                package.extract(sdk_tree)
            sdk_summary = package.verify(sdk_tree)
        dotnet = sdk_root / ("dotnet.exe" if os.name == "nt" else "dotnet")
        feed = destination / "feed"
        if not feed.exists():
            tree.directory(feed)
        for package in LOCK["nuget_packages"]:
            fetch(tree, package["url"], feed / package["url"].rsplit("/", 1)[1], package["sha256"], max_bytes=32 * 1024 * 1024)
        # 每次用空缓存还原，不能把已解包且可被改变的NuGet缓存视为哈希证据。
        build_root = destination / ("build-" + uuid.uuid4().hex)
        build_tree = PrivateTree(build_root)
        environment = closed_environment(build_tree)
        project = build_tree.directory(build_root / "project")
        for name in ("KdcFixture.csproj", "Program.cs", "packages.lock.json"):
            build_tree.write(project / name, (HERE / "app" / name).read_bytes())
        config = build_tree.write(build_root / "NuGet.Config", b'<?xml version="1.0"?><configuration><packageSources><clear/></packageSources></configuration>')
        # 直接托管MSBuild入口，不经过dotnet CLI首次体验/旧用户数据迁移。
        # Windows MigrationRunner 使用 KnownFolder 而非LOCALAPPDATA环境变量，不能只靠改env隔离CLI。
        msbuild = sdk_root / "sdk" / LOCK["dotnet_sdk_version"] / "MSBuild.dll"
        common = [dotnet, msbuild, project / "KdcFixture.csproj", "-m:1", "-nodeReuse:false",
                  "-p:UseSharedCompilation=false", "-p:NuGetAudit=false"]
        run_bounded([*common, "-t:Restore", "-p:RestoreLockedMode=true", "-p:RestoreSources=" + str(feed),
                     "-p:RestoreConfigFile=" + str(config)], cwd=build_root, environment=environment)
        run_bounded([*common, "-t:Build", "-p:Configuration=Release"], cwd=build_root, environment=environment)
        with SafeArchive(archive) as package:
            package.verify(sdk_tree)
        output = project / "bin/Release/net8.0"
        artifact = destination / ("artifact-" + uuid.uuid4().hex)
        tree.directory(artifact)
        files = {}
        for path in output.iterdir():
            if path.suffix in (".dll", ".json"):
                target = tree.write(artifact / path.name, path.read_bytes())
                files[target.name] = digest(target)
        if "KdcFixture.dll" not in files or "KdcFixture.runtimeconfig.json" not in files:
            raise ValidationError("KDC构建没有生成必需宿主")
        state = {"schema": 1, "sdk_version": LOCK["dotnet_sdk_version"], "rid": rid,
                 "source_sha256": source_hash(), "artifact": artifact.name, "files": files}
        # 只发布一次准备状态；重新prepare前显式删除旧状态也须先核验普通单链接。
        tree.write(state_path, (json.dumps(state, indent=2) + "\n").encode("utf-8"))
        return {"schema": 1, "kind": "gss-kdc-preparation", "passed": True,
                "sdk_version": LOCK["dotnet_sdk_version"], "package_count": len(LOCK["nuget_packages"]),
                "sdk_files_verified": sdk_summary["files"], "source_sha256": state["source_sha256"]}
    finally:
        if build_tree:
            build_tree.close()
        if sdk_tree:
            sdk_tree.close()
        tree.close()


def main():
    utf8_streams()
    parser = argparse.ArgumentParser(description="准备独占测试KDC")
    parser.add_argument("--root", type=Path, required=True)
    options = parser.parse_args()
    try:
        print(json.dumps(prepare(options.root), ensure_ascii=False))
    except (ValidationError, OSError, ValueError):
        print("固定KDC依赖准备失败", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
