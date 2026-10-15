#!/usr/bin/env python3
"""只在项目 .tmp 生成测试 key、配置、socket 并运行真实 Qt 窗口。"""
import argparse
import os
from pathlib import Path
import subprocess
import sys
import uuid

PROJECT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(PROJECT))
from tools.suite.isolation import IsolationEnvironment, private_directory

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", required=True, type=Path)
    parser.add_argument("--puttygen", required=True, type=Path)
    parser.add_argument("--case", required=True)
    parser.add_argument("--cli", required=True, type=Path)
    parser.add_argument("--screenshot", action="store_true")
    args = parser.parse_args()
    binary, keygen = args.binary.resolve(strict=True), args.puttygen.resolve(strict=True)
    cli = args.cli.resolve(strict=True)
    with IsolationEnvironment() as env:
        env.probe()
        password = env.write_private("gui-passphrase", b"gui-passphrase\n")
        paths = {}
        for name in ("one", "two"):
            private = env.root / ("gui-" + name)
            env.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", name, "-f", private], check=True)
            plain = env.root / (name + ".ppk")
            encrypted = env.root / (name + "-encrypted.ppk")
            env.run([keygen, private, "-O", "private", "-o", plain], input=b"", check=True)
            env.run([keygen, private, "-O", "private", "-o", encrypted, "--new-passphrase", password,
                "--ppk-param", "version=3,kdf=argon2id,memory=1024,passes=2,parallelism=1"], input=b"", check=True)
            paths[name] = (plain, encrypted, str(private)+".pub")
        environment = env.environment()
        environment.update({"QT_QPA_PLATFORM": os.environ.get("PAGEANT_TEST_PLATFORM", "offscreen"),
            "PAGEANT_TEST_ROOT": str(env.root), "PAGEANT_TEST_KEY": str(paths["one"][0]),
            "PAGEANT_TEST_ENCRYPTED": str(paths["one"][1]),
            "PAGEANT_TEST_SECOND_ENCRYPTED": str(paths["two"][1]),
            "PAGEANT_TEST_SECOND_PUBLIC": paths["two"][2]})
        environment["PAGEANT_TEST_CLI"] = str(cli)
        # GUI 测试只调用有显式 .tmp socket 的新模块，不启动默认上游代理。
        result = subprocess.run([str(binary), args.case], env=environment,
            cwd=env.root, timeout=30, umask=0o077)
        if args.screenshot and (env.root / "window.png").exists():
            target = private_directory(PROJECT / ".tmp" / "pageant-ui-evidence") / (uuid.uuid4().hex + ".png")
            descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(descriptor, "wb") as output:
                output.write((env.root / "window.png").read_bytes())
            print("窗口截图：", target)
        env.validate()
        return result.returncode

if __name__ == "__main__":
    raise SystemExit(main())
