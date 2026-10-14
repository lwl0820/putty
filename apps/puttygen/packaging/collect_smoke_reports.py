#!/usr/bin/env python3
"""将冒烟检查的报告单独暂存，避免上传器扫描私有运行目录。"""

import argparse
from pathlib import Path
import shutil


ROOT = Path(__file__).resolve().parents[3]
TEMPORARY_ROOT = ROOT / ".tmp"
PLATFORMS = ("offscreen", "xcb", "wayland", "native")
REPORTS = ("结果.json", "启动输出.txt", "动态库.txt")


def collect(source, destination):
    if not source.exists():
        return 0
    count = 0

    def copy_report(path):
        nonlocal count
        if path.is_symlink():
            raise ValueError(f"报告不能是符号链接：{path}")
        if not path.is_file():
            return
        target = destination / path.relative_to(source)
        target.parent.mkdir(parents=True, exist_ok=True)
        # 不复制容器文件的所有者或权限；暂存文件由上传器所属用户创建。
        shutil.copyfile(path, target)
        count += 1

    copy_report(source / "weston.log")
    # 仅枚举包这一层，定点读取报告；不进入 runtime、配置或解压目录。
    for package in sorted(source.iterdir()):
        if package.is_symlink() or not package.is_dir():
            continue
        for platform in PLATFORMS:
            work = package / platform
            if work.is_symlink():
                raise ValueError(f"报告目录不能是符号链接：{work}")
            for report in REPORTS:
                copy_report(work / report)
    return count


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=TEMPORARY_ROOT / "package-smoke")
    parser.add_argument("--destination", type=Path,
                        default=TEMPORARY_ROOT / "package-smoke-reports")
    args = parser.parse_args()
    source, destination = args.source.resolve(), args.destination.resolve()
    for path in (source, destination):
        if not path.is_relative_to(TEMPORARY_ROOT.resolve()):
            parser.error(f"路径必须位于项目 .tmp/ 内：{path}")
    if source.is_relative_to(destination) or destination.is_relative_to(source):
        parser.error("报告源目录与暂存目录不能重叠")
    print(f"已收集 {collect(source, destination)} 个冒烟检查报告：{destination}")


if __name__ == "__main__":
    main()
