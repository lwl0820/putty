#!/usr/bin/env python3
"""从已校验的官方源码构建发布用 Qt，全部缓存位于项目 .tmp。"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import urllib.request


SCRIPT_DIRECTORY = Path(__file__).resolve().parent
ROOT = SCRIPT_DIRECTORY.parents[1] if SCRIPT_DIRECTORY.name == "qt" and SCRIPT_DIRECTORY.parent.name == "tools" else Path.cwd().resolve()
LOCK = SCRIPT_DIRECTORY / "qt-sources.json"


def project_temp(value):
    """防止构建、缓存或输出被误放到系统临时目录。"""
    path = Path(value).resolve()
    if not path.is_relative_to(ROOT / ".tmp"):
        raise argparse.ArgumentTypeError("路径必须位于项目 .tmp/ 内")
    return path


def run(command, cwd=None):
    print("执行：", " ".join(str(part) for part in command), flush=True)
    subprocess.run([str(part) for part in command], cwd=cwd, check=True)


def source_archive(module, lock, cache):
    filename = f"{module}-everywhere-src-{lock['version']}.tar.xz"
    archive = cache / filename
    expected = lock["modules"][module]
    if not archive.exists():
        pending = archive.with_suffix(".download")
        urllib.request.urlretrieve(lock["base_url"] + filename, pending)
        pending.replace(archive)
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    if digest != expected:
        raise RuntimeError(f"源码校验失败：{archive.name}，实际 SHA256={digest}")
    return archive


def unpack(archive, sources):
    destination = sources / archive.name.removesuffix(".tar.xz")
    if not destination.exists():
        with tarfile.open(archive) as package:
            # Qt 归档含源码相对符号链接，先校验所有路径和链接目标再解包。
            for member in package.getmembers():
                member_path = (sources / member.name).resolve()
                if not member_path.is_relative_to(sources.resolve()):
                    raise RuntimeError("源码归档含越界路径")
                if member.issym() or member.islnk():
                    base = member_path.parent if member.issym() else sources
                    if not (base / member.linkname).resolve().is_relative_to(sources.resolve()):
                        raise RuntimeError("源码归档含越界链接")
                elif not (member.isfile() or member.isdir()):
                    raise RuntimeError("源码归档含非常规文件")
            package.extractall(sources)
    return destination


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prefix", type=project_temp, required=True)
    parser.add_argument("--work", type=project_temp, default=ROOT / ".tmp" / "qt-build")
    parser.add_argument("--cache", type=project_temp, default=ROOT / ".tmp" / "qt-archives")
    parser.add_argument("--jobs", type=int, default=4)
    parser.add_argument("--download-only", action="store_true")
    args = parser.parse_args()
    if args.jobs < 1:
        parser.error("--jobs 必须为正整数")
    lock = json.loads(LOCK.read_text(encoding="utf-8"))
    modules = ["qtbase", "qttools", "qttranslations"]
    if sys.platform == "linux":
        modules.append("qtwayland")
    elif sys.platform != "win32":
        parser.error("发布构建仅支持 Windows 和 Linux x64")
    sources = args.work / "sources"
    for directory in (args.cache, sources, args.prefix, args.work / "temp"):
        directory.mkdir(parents=True, exist_ok=True)
    # CMake、编译器和下载器的间接临时文件也限制到项目目录。
    for variable in ("TMP", "TEMP", "TMPDIR"):
        os.environ[variable] = str(args.work / "temp")
    archives = {module: source_archive(module, lock, args.cache) for module in modules}
    if args.download_only:
        print("全部官方源码 SHA256 校验通过")
        return
    for module in modules:
        source = unpack(archives[module], sources)
        build = args.work / module
        build.mkdir(exist_ok=True)
        if module == "qtbase":
            configure = source / ("configure.bat" if sys.platform == "win32" else "configure")
            command = [configure, "-prefix", args.prefix, "-opensource", "-confirm-license",
                       "-release", "-shared", "-nomake", "examples", "-nomake", "tests",
                       "--", "-G", "Ninja", "-DCMAKE_INSTALL_LIBDIR=lib"]
            if sys.platform == "win32":
                command = ["cmd.exe", "/c", *command]
            run(command, build)
        else:
            command = ["cmake", "-S", source, "-B", build, "-G", "Ninja",
                       f"-DCMAKE_PREFIX_PATH={args.prefix}", f"-DCMAKE_INSTALL_PREFIX={args.prefix}",
                       "-DCMAKE_BUILD_TYPE=Release", "-DQT_BUILD_EXAMPLES=OFF",
                       "-DQT_BUILD_TESTS=OFF", "-DCMAKE_INSTALL_LIBDIR=lib"]
            if module == "qttools":
                command += ["-DFEATURE_assistant=OFF", "-DFEATURE_designer=OFF", "-DFEATURE_qdoc=OFF"]
            if module == "qtwayland":
                command += ["-DFEATURE_wayland_server=OFF"]
            run(command)
        run(["cmake", "--build", build, "--parallel", args.jobs])
        run(["cmake", "--install", build])
    material = args.prefix / "share" / "puttygen-gui" / "qt-sources"
    material.mkdir(parents=True, exist_ok=True)
    shutil.copy2(LOCK, material)
    shutil.copy2(__file__, material)
    for module, archive in archives.items():
        shutil.copy2(archive, material)
        licence_dir = sources / archive.name.removesuffix(".tar.xz") / "LICENSES"
        if not licence_dir.is_dir():
            raise RuntimeError(f"Qt 许可材料缺失：{licence_dir}")
        shutil.copytree(licence_dir, material / "licenses" / module, dirs_exist_ok=True)
    compiler_command = ["cl"] if sys.platform == "win32" else [os.environ.get("CXX", "c++"), "--version"]
    compiler = subprocess.run(compiler_command, text=True, capture_output=True, check=False)
    (material / "构建信息.json").write_text(json.dumps({
        "Qt": lock["version"], "系统": sys.platform,
        "模块": modules, "动态链接": True,
        "编译器": compiler.stdout + compiler.stderr,
        "CMake": subprocess.check_output(["cmake", "--version"], text=True).strip(),
        "Ninja": subprocess.check_output(["ninja", "--version"], text=True).strip(),
        "配置": "Release / shared / examples=off / tests=off",
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Qt {lock['version']} 已安装到 {args.prefix}")


if __name__ == "__main__":
    main()
