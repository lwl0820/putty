#!/usr/bin/env python3
"""在隔离验证目录检查包内容和启动；DEB 安装验证仅在专用机器运行。"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import zipfile


ROOT = Path(__file__).resolve().parents[3]


def run(command, **kwargs):
    return subprocess.run(command, check=True, text=True, **kwargs)


def launch(executable, work, platform):
    environment = os.environ.copy()
    # 不从构建机 Qt SDK 借用插件或运行库；验证包的查找路径。
    for name in ("QT_PLUGIN_PATH", "QT_QPA_PLATFORM_PLUGIN_PATH", "QTDIR", "LD_LIBRARY_PATH"):
        environment.pop(name, None)
    for name in ("TMP", "TEMP", "TMPDIR", "XDG_CONFIG_HOME", "XDG_CACHE_HOME", "XDG_DATA_HOME"):
        directory = work / name.lower()
        directory.mkdir(exist_ok=True)
        environment[name] = str(directory)
    if sys.platform == "linux" and not environment.get("XDG_RUNTIME_DIR"):
        # Wayland 已提供的运行时目录含合成器 socket，必须保留。
        runtime = work / "runtime"
        runtime.mkdir(exist_ok=True)
        runtime.chmod(0o700)
        environment["XDG_RUNTIME_DIR"] = str(runtime)
    if platform != "native":
        environment["QT_QPA_PLATFORM"] = platform
    else:
        environment.pop("QT_QPA_PLATFORM", None)
    environment["PATH"] = os.pathsep.join(
        part for part in environment.get("PATH", "").split(os.pathsep)
        if "qt" not in part.lower()
    )
    with (work / "启动输出.txt").open("w", encoding="utf-8") as log:
        process = subprocess.Popen([str(executable)], cwd=work, env=environment,
                                   stdout=log, stderr=log)
        try:
            time.sleep(3)
            if process.poll() is not None:
                raise RuntimeError(f"GUI 提前退出：{process.returncode}，检查 {log.name}")
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("package", type=Path)
    parser.add_argument("--platform", choices=("offscreen", "xcb", "wayland", "native"), default="offscreen")
    parser.add_argument("--install-deb", action="store_true",
                        help="在未安装本应用的专用验证机器上安装、启动并卸载 DEB")
    args = parser.parse_args()
    package = args.package.resolve()
    work = ROOT / ".tmp" / "package-smoke" / package.stem / args.platform
    work.mkdir(parents=True, exist_ok=True)
    result = {"包": package.name, "SHA256": hashlib.sha256(package.read_bytes()).hexdigest(),
              "平台插件": args.platform, "结果": "未完成"}
    if package.suffix.lower() == ".zip":
        if sys.platform != "win32":
            parser.error("Windows ZIP 的启动验证必须在 Windows 运行")
        destination = work / "解压包"
        destination.mkdir(exist_ok=True)
        with zipfile.ZipFile(package) as archive:
            for name in archive.namelist():
                if not (destination / name).resolve().is_relative_to(destination.resolve()):
                    raise RuntimeError("ZIP 含越界路径")
            archive.extractall(destination)
        executables = list(destination.rglob("puttygen-gui.exe"))
        if len(executables) != 1:
            raise RuntimeError("ZIP 应包含且只包含一个 GUI 主程序")
        launch(executables[0], work, args.platform)
    elif package.suffix == ".deb":
        if not args.install_deb:
            parser.error("DEB 验证必须显式指定 --install-deb，并使用专用验证环境")
        if sys.platform != "linux" or os.geteuid() != 0:
            parser.error("DEB 安装验证须在专用 Linux 环境以 root 运行")
        installed = subprocess.run(["dpkg-query", "-W", "-f=${Status}", "puttygen-gui"],
                                   capture_output=True, text=True)
        if "install ok installed" in installed.stdout:
            raise RuntimeError("验证环境已安装 puttygen-gui，拒绝覆盖现有安装")
        run(["dpkg-deb", "--info", str(package)], stdout=subprocess.PIPE)
        run(["apt-get", "install", "-y", str(package)])
        try:
            binary = Path("/opt/puttygen-gui/bin/puttygen-gui")
            dependencies = run(["ldd", str(binary)], stdout=subprocess.PIPE).stdout
            if "not found" in dependencies:
                raise RuntimeError("已安装的 GUI 存在缺失动态库")
            (work / "动态库.txt").write_text(dependencies, encoding="utf-8")
            run(["desktop-file-validate", "/usr/share/applications/puttygen-gui.desktop"])
            launch(binary, work, args.platform)
        finally:
            run(["apt-get", "remove", "--purge", "-y", "puttygen-gui"])
        for owned in ("/opt/puttygen-gui/bin/puttygen-gui", "/usr/bin/puttygen-gui",
                      "/usr/share/applications/puttygen-gui.desktop"):
            if Path(owned).exists():
                raise RuntimeError(f"卸载后仍残留包文件：{owned}")
    else:
        parser.error("仅接受 Windows ZIP 或 Ubuntu DEB")
    result["结果"] = "启动通过；不代表人工功能验收"
    (work / "结果.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
