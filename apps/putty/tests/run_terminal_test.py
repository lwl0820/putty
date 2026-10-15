#!/usr/bin/env python3
"""在项目私有环境和断网专用 SSH 容器中运行终端纵切。"""
import argparse
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from tools.suite.isolation import IsolationEnvironment, IsolationError
from tools.suite.container_ssh_fixture import ContainerSSHServer
from tools.suite.ssh_fixture import FixtureUnavailable

# 远端实际持续 SSH 输出并读取真实输入；select 用于吞吐控制，不模拟认证耗时。
PROGRAM = r'''
import os, select, time
start=time.monotonic(); count=0; received=b''
os.write(1,b'\x1b[2J\x1b[H')
while time.monotonic()-start < 10.2:
    line='\x1b[H\x1b[2KSSH %06d 中文 e\u0301 Ω Ж 😀 漢字\r\n\x1b[38;2;120;210;255m真彩色\x1b[0m' % count
    os.write(1,line.encode('utf-8')); count+=1
    if select.select([0],[],[],0.005)[0]:
        data=os.read(0,65536)
        received+=data
expected='terminal-input-中文\n'.encode('utf-8')
os.write(1,('\x1b[4;1HFINAL 中文 e\u0301 😀\r\n'+('INPUT_OK' if received==expected else 'INPUT_BAD')).encode('utf-8'))
raise SystemExit(0 if received==expected else 9)
'''

def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"): stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--puttygen", type=Path, required=True)
    parser.add_argument("--parent", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--debugger", action="store_true", help="在同一隔离环境中记录 gdb 退出栈")
    args = parser.parse_args()
    if sys.platform != "linux":
        print("Windows 原生终端测试需要逐进程注册表与种子隔离")
        return 77
    report = args.report.resolve()
    if not report.is_relative_to(ROOT / ".tmp"):
        raise IsolationError("报告只能写入项目 .tmp/")
    with IsolationEnvironment(args.parent) as environment:
        probe = environment.probe()
        with ContainerSSHServer(environment) as server:
            passphrase = environment.write_private("ssh/terminal-passphrase", b"suite-terminal-private-passphrase\n")
            key = environment.root / "ssh/terminal-encrypted.ppk"
            converted = environment.run([
                args.puttygen.resolve(), server.private_key, "-O", "private", "-o", key,
                "--new-passphrase", passphrase,
                "--ppk-param", "version=3,kdf=argon2id,memory=8192,time=1500,parallelism=1",
            ], input=b"", timeout=30)
            if converted.returncode:
                raise RuntimeError(converted.stderr.decode("utf-8", errors="replace"))
            environment._validate_file(key)
            public_header = key.read_text(encoding="ascii").split("Private-Lines:", 1)[0]
            if "Encryption: aes256-cbc" not in public_header or "Key-Derivation: Argon2id" not in public_header:
                raise RuntimeError("生成的 PPK 没有实际加密或未使用 Argon2id")
            command = "/usr/bin/python3 -B -c " + shlex.quote(PROGRAM)
            child = environment.environment()
            child.update(QT_QPA_PLATFORM="offscreen", PUTTY_TERMINAL_ISOLATED_ROOT=str(environment.root))
            invocation = [
                str(args.binary.resolve()), "--port", str(server.port), "--key", str(key),
                "--remote-command", command, "--passphrase-file", str(passphrase),
            ]
            if args.debugger:
                invocation = ["gdb", "--batch", "--return-child-result", "-ex", "run", "-ex", "thread apply all backtrace", "--args"] + invocation
            result = subprocess.run(invocation, env=child, cwd=environment.root,
                                    capture_output=True, timeout=60, umask=0o077)
            sys.stdout.buffer.write(result.stdout); sys.stderr.buffer.write(result.stderr)
            report.parent.mkdir(parents=True, exist_ok=True)
            report.with_suffix(".stdout.log").write_bytes(result.stdout)
            report.with_suffix(".stderr.log").write_bytes(result.stderr)
            details = {"passed": result.returncode == 0, "returncode": result.returncode,
                       "probe": probe, "server_image": server.image_id,
                       "ppk_parameters": [line for line in public_header.splitlines()
                           if line.startswith(("Encryption:", "Key-Derivation:", "Argon2-"))],
                       "gss_verified": False, "windows_native_verified": False}
            produced = environment.root / "logs/terminal-result.json"
            if produced.exists(): details["terminal"] = json.loads(produced.read_text(encoding="utf-8"))
            screenshot = environment.root / "logs/terminal-final.png"
            if screenshot.exists():
                report.with_suffix(".png").write_bytes(screenshot.read_bytes())
            report.write_text(json.dumps(details, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            if result.returncode: return 1
            if not produced.exists(): raise RuntimeError("受测进程没有生成最终结果")
            if not (environment.root / "putty/randomseed").stat().st_size:
                raise RuntimeError("真实客户端没有在私有目录写入种子")
            if not (environment.root / "putty/sshhostkeys").read_bytes():
                raise RuntimeError("接受的真实 SSH 主机密钥没有写入私有缓存")
    return 0

if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except FixtureUnavailable as error:
        print(f"终端互操作依赖未满足：{error}")
        raise SystemExit(77)
    except (IsolationError, OSError, subprocess.SubprocessError, RuntimeError, ValueError) as error:
        print(f"终端互操作失败：{error}", file=sys.stderr)
        raise SystemExit(1)
