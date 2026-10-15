#!/usr/bin/env python3
"""在项目私有环境中验证真实终端离屏帧的尺寸边界。"""
import argparse
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from tools.suite.isolation import IsolationEnvironment, IsolationError


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--parent", type=Path, required=True)
    arguments = parser.parse_args()
    with IsolationEnvironment(arguments.parent) as environment:
        environment.probe()
        child = environment.environment()
        child.update(QT_QPA_PLATFORM="offscreen", PUTTY_TERMINAL_ISOLATED_ROOT=str(environment.root))
        return subprocess.run([str(arguments.binary.resolve()), "--mode", "render-bounds"],
                              env=child, cwd=environment.root, timeout=20, umask=0o077).returncode


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (IsolationError, OSError, subprocess.SubprocessError) as error:
        print(f"离屏帧检查失败：{error}", file=sys.stderr)
        raise SystemExit(1)
