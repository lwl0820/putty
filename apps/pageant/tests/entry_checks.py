#!/usr/bin/env python3
"""只启动已编译项目 .tmp 前缀的原 CLI，验证无图形分流及真实服务。"""
import argparse
import base64
import json
import os
from pathlib import Path
import select
import subprocess
import sys
import time

PROJECT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(PROJECT))
from tools.suite.isolation import IsolationEnvironment, private_directory


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--binary", required=True, type=Path)
    parser.add_argument("--puttygen", required=True, type=Path)
    parser.add_argument("--prefix-parent", required=True, type=Path)
    args = parser.parse_args()
    binary, keygen = args.binary.resolve(strict=True), args.puttygen.resolve(strict=True)
    parent = private_directory(args.prefix_parent)
    with IsolationEnvironment() as env:
        env.probe()
        environment = env.environment()
        environment.pop("DISPLAY", None)
        environment.pop("WAYLAND_DISPLAY", None)
        environment["QT_QPA_PLATFORM"] = "pageant-deliberately-unavailable"

        def run(arguments, expected=0, data=None):
            result = subprocess.run([str(v) for v in arguments], env=environment,
                cwd=env.root, input=data, capture_output=True, timeout=8, umask=0o077)
            if result.returncode != expected:
                raise AssertionError((arguments, result.returncode, result.stderr.decode(errors="replace")))
            return result.stdout

        assert b"0.85" in run([binary, "--version"])
        assert b"--exec" in run([binary, "--help"])
        run([binary], expected=1)
        run([binary, "--exec"], expected=1)
        run([binary, "--foreground", "-l"], expected=1)
        run([binary, "--gui", "extra"], expected=2)
        private = env.root / "entry-key"
        env.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", "entry", "-f", private], check=True)
        ppk = env.root / "entry.ppk"
        env.run([keygen, private, "-O", "private", "-o", ppk], input=b"", check=True)
        # 真实桌面入口的启动探针。正常退出/提示取消由 Qt 场景验证，此处只终止独立探针进程。
        graphical = dict(environment, QT_QPA_PLATFORM="offscreen")
        desktop = subprocess.Popen([str(binary), "--gui"], env=graphical, cwd=env.root,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, umask=0o077)
        try:
            deadline = time.monotonic() + 5
            sockets = []
            while not sockets and time.monotonic() < deadline and desktop.poll() is None:
                sockets = list((env.root / "runtime").glob("pageant-*/agent"))
                time.sleep(0.01)
            if not sockets or desktop.poll() is not None:
                raise AssertionError("真实 --gui 入口未创建 XDG 私有代理端点")
            gui_environment = dict(environment, SSH_AUTH_SOCK=str(sockets[0]))
            listed = subprocess.run(["ssh-add", "-L"], env=gui_environment,
                cwd=env.root, capture_output=True, timeout=5, umask=0o077)
            assert listed.returncode == 1, listed.stderr
        finally:
            desktop.terminate()
            desktop.communicate(timeout=5)
        # 原 --exec 的子进程持有 stdin；关闭管道即触发原 SIGCHLD 生命周期。
        child = "import os,json,sys; print(json.dumps(os.environ['SSH_AUTH_SOCK']),flush=True); sys.stdin.buffer.read()"
        process = subprocess.Popen([str(binary), "--exec", sys.executable, "-c", child],
            env=environment, cwd=env.root, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, umask=0o077)
        endpoint = None
        try:
            if not select.select([process.stdout], [], [], 8)[0]:
                raise AssertionError("原 CLI --exec 未报告受控 socket")
            line = process.stdout.readline()
            if not line:
                raise AssertionError(process.stderr.read().decode(errors="replace"))
            endpoint = Path(json.loads(line))
            if not endpoint.is_relative_to(parent) or not endpoint.is_socket():
                raise AssertionError("原 CLI 端点不在已注入的项目 .tmp 前缀")
            environment["SSH_AUTH_SOCK"] = str(endpoint)
            run([binary, "-a", ppk])
            assert b"entry" in run([binary, "-l"])
            public = Path(str(private) + ".pub").read_bytes()
            listed = run(["ssh-add", "-L"])
            assert public.split()[1] == listed.split()[1]
            signature = run(["ssh-keygen", "-Y", "sign", "-f", str(private) + ".pub", "-n", "suite"], data=b"entry\x00\xff")
            signature_file = env.write_private("entry.sig", signature)
            allowed = env.write_private("entry.allowed", b"suite " + public)
            run(["ssh-keygen", "-Y", "verify", "-f", allowed, "-I", "suite", "-n", "suite", "-s", signature_file], data=b"entry\x00\xff")
            run([binary, "-d", ppk])
            run(["ssh-add", "-L"], expected=1)
            run(["ssh-add", private])
            assert base64.b64decode(public.split()[1]) == base64.b64decode(run([binary, "-L", ppk]).split()[1])
            run([binary, "-D"])
            run(["ssh-add", "-L"], expected=1)
        finally:
            process.stdin.close()
            process.stdin = None
            try:
                output, error = process.communicate(timeout=8)
            except subprocess.TimeoutExpired:
                process.kill()
                output, error = process.communicate(timeout=5)
                raise AssertionError("原 CLI --exec 未随子进程退出")
            if process.returncode:
                raise AssertionError((process.returncode, output, error))
        deadline = time.monotonic() + 3
        while endpoint.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert not endpoint.exists(), "原 CLI 退出后遗留 socket"
        env.validate()
    print("原 CLI：6 个入口分流检查及真实 --exec 双向身份、签名、删除、退出通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
