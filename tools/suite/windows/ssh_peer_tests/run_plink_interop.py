#!/usr/bin/env python3
"""仅在 Linux 私有环境中以原版 Plink 验证测试对端；不代替 Windows 验收。"""

import argparse
import base64
import json
import os
from pathlib import Path
import shutil
import sys
import time

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))

from tools.suite.isolation import IsolationEnvironment, IsolationError, private_directory
from tools.suite.windows.ssh_peer import (
    EXPECTED_ORDERED_INPUT, EXPECTED_WORKLOAD_INPUT, MAX_OUTPUT_BYTES,
    ORDERED_INPUT_COMMAND, RestrictedSSHPeer, WORKLOAD_COMMAND, WORKLOAD_SECONDS,
)


def run(plink, puttygen, parent):
    if sys.platform != "linux":
        raise IsolationError("此原版 CLI 对照要求 Linux 逐进程隔离，不可直接运行 Windows Plink")
    keygen = shutil.which("ssh-keygen")
    if keygen is None:
        raise RuntimeError("缺少 ssh-keygen；互操作未执行")
    with IsolationEnvironment(parent) as environment:
        isolation = environment.probe()
        key = environment.root / "ssh/client-key"
        environment.run([keygen, "-q", "-t", "ed25519", "-N", "", "-C", "suite-peer",
                         "-f", key], timeout=15, check=True)
        environment._validate_file(key)
        environment._validate_file(Path(str(key) + ".pub"))
        public = Path(str(key) + ".pub").read_bytes().split()
        blob = base64.b64decode(public[1], validate=True)
        empty = environment.write_private("ssh/empty-passphrase", b"")
        ppk = environment.root / "ssh/client.ppk"
        environment.run([puttygen, key, "-O", "private", "-o", ppk,
                         "--new-passphrase", empty], timeout=15, check=True)
        environment._validate_file(ppk)
        checks = []
        with RestrictedSSHPeer(blob) as peer:
            for command, data in ((ORDERED_INPUT_COMMAND, EXPECTED_ORDERED_INPUT),
                                  (WORKLOAD_COMMAND, EXPECTED_WORKLOAD_INPUT)):
                started = time.monotonic()
                result = environment.run([
                    plink, "-ssh", "-batch", "-noagent", "-noshare", "-T", "-no-antispoof",
                    "-hostkey", peer.host_key_fingerprint, "-P", str(peer.port),
                    "-l", "suite", "-i", ppk, "127.0.0.1", command.decode("ascii"),
                ], timeout=20, input=data)
                duration = time.monotonic() - started
                if result.returncode != 0 or result.stderr or len(result.stdout) > MAX_OUTPUT_BYTES:
                    raise RuntimeError("原版 Plink 公钥认证、退出码或标准流检查失败")
                if command == ORDERED_INPUT_COMMAND:
                    if result.stdout != b"ORDER_OK":
                        raise RuntimeError("原版 Plink 642 字节有序输入检查失败")
                elif (duration < WORKLOAD_SECONDS or result.stdout.count(b"\x1b[H\x1b[2KSSH") < 500
                      or not result.stdout.endswith("FINAL 中文 e\u0301 😀\r\nINPUT_OK".encode("utf-8"))):
                    raise RuntimeError("原版 Plink 持续 Unicode 输出或精确输入检查失败")
                checks.append({"command": command.decode("ascii"), "exit_status": result.returncode,
                               "input_bytes": len(data), "output_bytes": len(result.stdout),
                               "duration_seconds": round(duration, 4)})
        statistics = peer.statistics()
        if (statistics["live_threads"] != 0 or statistics["completed_commands"] != 2
                or statistics["authenticated_connections"] != 2 or statistics["connection_errors"] != 0):
            raise RuntimeError("SSH 对端完成计数或资源回收检查失败")
        return {"passed": True, "platform": sys.platform, "client": "upstream-plink",
                "windows_native_verified": False, "isolation": isolation,
                "checks": checks, "peer": statistics}


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plink", type=Path, required=True)
    parser.add_argument("--puttygen", type=Path, required=True)
    parser.add_argument("--parent", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    report = args.report.absolute()
    if ".." in args.report.parts or not report.is_relative_to(ROOT / ".tmp"):
        parser.error("报告必须位于项目 .tmp/ 且不能包含上级目录")
    private_directory(report.parent)
    result = {"passed": False, "windows_native_verified": False}
    try:
        result = run(args.plink.resolve(strict=True), args.puttygen.resolve(strict=True), args.parent)
    finally:
        descriptor = os.open(report, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            json.dump(result, output, ensure_ascii=False, indent=2)
            output.write("\n")
    print("原版 Plink：固定命令、公钥认证、642 字节输入和十秒输出通过")


if __name__ == "__main__":
    main()
