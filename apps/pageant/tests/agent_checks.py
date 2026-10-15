#!/usr/bin/env python3
"""在项目私有目录对真实代理核心执行 OpenSSH 与标准客户端互操作。"""
import argparse
import base64
import json
import os
from pathlib import Path
import select
import socket
import stat
import struct
import subprocess
import sys
import time
import unittest

PROJECT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(PROJECT))
from tools.suite.isolation import IsolationEnvironment
from tools.suite.ssh_fixture import SSHServer

ARGUMENTS = None

def string(data):
    return struct.pack(">I", len(data)) + data

class Host:
    def __init__(self, env, path=None):
        self.env = env
        self.path = path or env.socket_path("agent")
        # 此驱动只创建显式 socket，先逐层验证项目 .tmp；不调用上游默认前缀。
        env.validate()
        if not Path(self.path).is_relative_to(PROJECT / ".tmp"):
            raise RuntimeError("测试代理必须位于项目 .tmp")
        self.stderr = env.write_private("logs/agent-stderr.log", b"")
        self.log = self.stderr.open("ab")
        self.process = subprocess.Popen([ARGUMENTS.host, str(self.path)],
            cwd=env.root, env=env.environment(), stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=self.log, umask=0o077)
        self.buffer = b""
        self.events = []

    def send(self, **value):
        self.process.stdin.write(json.dumps(value).encode() + b"\n")
        self.process.stdin.flush()

    def wait(self, predicate, timeout=8):
        deadline = time.monotonic() + timeout
        while True:
            for index, event in enumerate(self.events):
                if predicate(event):
                    return self.events.pop(index)
            if time.monotonic() >= deadline:
                raise AssertionError(f"代理事件超时：{self.events[-4:]}，{self.stderr.read_text()[-2000:]}")
            if self.process.poll() is not None:
                raise AssertionError(f"代理退出 {self.process.returncode}：{self.stderr.read_text()[-3000:]}")
            if not select.select([self.process.stdout], [], [], max(0, deadline-time.monotonic()))[0]:
                continue
            data = os.read(self.process.stdout.fileno(), 65536)
            if not data:
                continue
            self.buffer += data
            while b"\n" in self.buffer:
                line, self.buffer = self.buffer.split(b"\n", 1)
                self.events.append(json.loads(line))

    def ready(self):
        return self.wait(lambda e: e.get("kind") == 1)

    def prompt(self):
        return self.wait(lambda e: e.get("kind") == 3)

    def result(self, action):
        return self.wait(lambda e: e.get("kind") == 4 and e.get("action") == action)

    def snapshot(self, count):
        self.events = [e for e in self.events if e.get("kind") != 2]
        self.send(action=1)
        return self.wait(lambda e: e.get("kind") == 2 and len(e.get("keys", [])) == count)

    def close(self):
        if self.process.poll() is None:
            self.send(close=True)
            self.process.wait(timeout=8)
        self.log.close()
        self.process.stdin.close()
        self.process.stdout.close()
        if self.process.returncode:
            raise AssertionError(self.stderr.read_text()[-24000:])
        if Path(self.path).exists():
            raise AssertionError("退出后代理 socket 仍存在")

class Checks(unittest.TestCase):
    def setUp(self):
        self.env = IsolationEnvironment()
        self.env.__enter__()
        self.env.probe()
        self.host = Host(self.env)
        self.host.ready()
        self.children = []

    def tearDown(self):
        try:
            self.host.close()
            for process in self.children:
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=5)
        finally:
            self.env.__exit__(None, None, None)

    def run_tool(self, argv, **options):
        result = subprocess.run([str(v) for v in argv], cwd=self.env.root,
            env=self.env.environment(agent_socket=self.host.path), capture_output=True,
            timeout=15, umask=0o077, **options)
        return result

    def ok(self, result):
        self.assertEqual(result.returncode, 0, (result.stdout+result.stderr).decode(errors="replace"))

    def key(self, name="key", algorithm="ed25519", encrypted=False):
        path = self.env.root / name
        self.ok(self.run_tool(["ssh-keygen", "-q", "-t", algorithm, "-N", "", "-C", name, "-f", path]))
        ppk = self.env.root / (name + ".ppk")
        argv = [ARGUMENTS.puttygen, path, "-O", "private", "-o", ppk]
        if encrypted:
            password = self.env.write_private(name + ".pass", b"test-passphrase\n")
            argv += ["--new-passphrase", password, "--ppk-param",
                     "version=3,kdf=argon2id,memory=1024,passes=2,parallelism=1"]
        self.ok(self.run_tool(argv, input=b""))
        return path, ppk

    def add(self, ppk, encrypted=False):
        self.host.send(action=2, path=str(ppk), flags=1 if encrypted else 0)
        self.assertEqual(self.host.result(2)["result"], 0)

    def sign_client(self, public):
        process = subprocess.Popen(["ssh-keygen", "-Y", "sign", "-f", str(public), "-n", "suite"],
            cwd=self.env.root, env=self.env.environment(agent_socket=self.host.path),
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, umask=0o077)
        process.stdin.write(b"suite-payload\x00\xff")
        process.stdin.close()
        process.stdin = None
        self.children.append(process)
        return process

    def verify(self, process, public):
        output, error = process.communicate(timeout=8)
        self.assertEqual(process.returncode, 0, error.decode())
        signature = self.env.write_private(f"sig-{len(self.children)}", output)
        allowed = self.env.write_private(f"allowed-{len(self.children)}", b"suite " + Path(public).read_bytes())
        self.ok(self.run_tool(["ssh-keygen", "-Y", "verify", "-f", allowed,
            "-I", "suite", "-n", "suite", "-s", signature], input=b"suite-payload\x00\xff"))

    def raw_client(self, blob, data=b"payload", flags=0):
        client = socket.socket(socket.AF_UNIX)
        client.settimeout(5)
        client.connect(str(self.host.path))
        packet = b"\r" + string(blob) + string(data) + struct.pack(">I", flags)
        client.sendall(string(packet))
        self.addCleanup(client.close)
        return client

    def receive(self, client):
        def take(n):
            output = b""
            while len(output) < n:
                chunk = client.recv(n-len(output))
                if not chunk:
                    raise AssertionError("代理在回复前断开")
                output += chunk
            return output
        return take(struct.unpack(">I", take(4))[0])

    def blob(self, public):
        return base64.b64decode(Path(public).read_text().split()[1])

    def test_socket_permissions_and_empty_list(self):
        self.assertEqual(stat.S_IMODE(Path(self.host.path).stat().st_mode), 0o600)
        self.assertEqual(self.host.snapshot(0)["result"], 0)
        listed = self.run_tool(["ssh-add", "-l"])
        self.assertEqual(listed.returncode, 1)
        self.assertIn(b"no identities", listed.stdout)

    def test_openssh_add_list_sign_delete(self):
        key, _ = self.key()
        self.ok(self.run_tool(["ssh-add", key]))
        self.assertEqual(len(self.host.snapshot(1)["keys"]), 1)
        self.verify(self.sign_client(Path(str(key)+".pub")), Path(str(key)+".pub"))
        self.ok(self.run_tool(["ssh-add", "-d", str(key)+".pub"]))
        self.host.snapshot(0)

    def test_ppk_algorithms_and_public_identity_delete(self):
        for index, algorithm in enumerate(("ed25519", "ecdsa", "rsa")):
            with self.subTest(algorithm=algorithm):
                key, ppk = self.key(f"key-{index}", algorithm)
                self.add(ppk)
                row = self.host.snapshot(1)["keys"][0]
                self.assertEqual(base64.b64decode(row["blob"]), self.blob(str(key)+".pub"))
                self.verify(self.sign_client(Path(str(key)+".pub")), Path(str(key)+".pub"))
                self.host.send(action=3, version=row["version"], blob=row["blob"])
                self.assertEqual(self.host.result(3)["result"], 0)
                self.host.snapshot(0)

    def test_deferred_wrong_passphrase_retry_and_reencrypt(self):
        key, ppk = self.key(encrypted=True)
        self.add(ppk, encrypted=True)
        self.assertEqual(self.host.snapshot(1)["keys"][0]["flags"], 3)
        process = self.sign_client(Path(str(key)+".pub"))
        first = self.host.prompt()
        self.host.send(action=7, request=first["request"], passphrase="wrong")
        second = self.host.prompt()
        self.assertNotEqual(first["request"], second["request"])
        self.host.send(action=7, request=second["request"], passphrase="test-passphrase")
        self.verify(process, Path(str(key)+".pub"))
        row = self.host.snapshot(1)["keys"][0]
        self.assertEqual(row["flags"], 1)
        self.host.send(action=5, version=2, blob=row["blob"])
        self.assertEqual(self.host.result(5)["result"], 0)
        self.assertEqual(self.host.snapshot(1)["keys"][0]["flags"], 3)
        process = self.sign_client(Path(str(key)+".pub"))
        pending = self.host.prompt()
        self.host.send(action=7, request=pending["request"], flags=2)
        self.assertNotEqual(process.communicate(timeout=8) and process.returncode, 0)

    def test_add_prompt_cancel_wrong_and_retry(self):
        _, ppk = self.key(encrypted=True)
        self.host.send(action=2, path=str(ppk))
        first = self.host.prompt()
        self.host.send(action=7, request=first["request"], passphrase="wrong")
        second = self.host.prompt()
        self.host.send(action=7, request=second["request"], flags=2)
        self.assertEqual(self.host.result(2)["result"], 3)
        self.host.snapshot(0)
        self.host.send(action=2, path=str(ppk))
        third = self.host.prompt()
        self.host.send(action=7, request=third["request"], passphrase="test-passphrase")
        self.assertEqual(self.host.result(2)["result"], 0)
        self.host.snapshot(1)

    def two_deferred(self):
        first, ppk = self.key("first", encrypted=True)
        second, ppk2 = self.key("second", encrypted=True)
        self.add(ppk, True)
        self.add(ppk2, True)
        a = self.raw_client(self.blob(str(first)+".pub"))
        prompt = self.host.prompt()
        b = self.raw_client(self.blob(str(second)+".pub"))
        # 往返枚举确保第二个连接的请求已进入核心事件循环。
        self.ok(self.run_tool(["ssh-add", "-L"]))
        return first, a, b, prompt

    def test_external_delete_during_prompt_cancel_unblocks_second_key(self):
        first, a, b, prompt = self.two_deferred()
        self.ok(self.run_tool(["ssh-add", "-d", str(first)+".pub"]))
        self.host.send(action=7, request=prompt["request"], flags=2)
        self.assertEqual(self.receive(a), b"\x05")
        next_prompt = self.host.prompt()
        self.host.send(action=7, request=next_prompt["request"], passphrase="test-passphrase")
        self.assertEqual(self.receive(b)[0], 14)
        self.host.snapshot(1)

    def test_external_delete_during_prompt_success_unblocks_second_key(self):
        first, a, b, prompt = self.two_deferred()
        self.ok(self.run_tool(["ssh-add", "-d", str(first)+".pub"]))
        self.host.send(action=7, request=prompt["request"], passphrase="test-passphrase")
        self.assertEqual(self.receive(a), b"\x05")
        next_prompt = self.host.prompt()
        self.host.send(action=7, request=next_prompt["request"], passphrase="test-passphrase")
        self.assertEqual(self.receive(b)[0], 14)
        self.host.snapshot(1)

    def test_disconnected_client_wrong_reply_unblocks_second_key(self):
        _, a, b, prompt = self.two_deferred()
        a.close()
        self.ok(self.run_tool(["ssh-add", "-L"]))
        self.host.send(action=7, request=prompt["request"], passphrase="wrong")
        next_prompt = self.host.prompt()
        self.host.send(action=7, request=next_prompt["request"], passphrase="test-passphrase")
        self.assertEqual(self.receive(b)[0], 14)

    def test_delete_all_cancels_prompt_without_resurrection(self):
        _, a, b, prompt = self.two_deferred()
        self.host.send(action=4)
        self.assertEqual(self.host.result(4)["result"], 0)
        self.assertEqual(self.receive(a), b"\x05")
        self.assertEqual(self.receive(b), b"\x05")
        self.host.send(action=7, request=prompt["request"], passphrase="test-passphrase")
        self.host.wait(lambda e: e.get("type") == 4 and e.get("status") == 4)
        self.host.snapshot(0)

    def test_close_active_half_packet_and_prompt_connections(self):
        _, a, b, _ = self.two_deferred()
        half = socket.socket(socket.AF_UNIX)
        half.connect(str(self.host.path))
        half.sendall(b"\x00\x00")
        self.addCleanup(half.close)
        self.host.close()
        for client in (a, b, half):
            content = b""
            while True:
                try:
                    chunk = client.recv(1024)
                except ConnectionResetError:
                    break
                if not chunk: break
                content += chunk
            # 在关闭前已完成的拒绝可以被读到；之后必须 EOF，不能挂起。
            self.assertIn(content, (b"", string(b"\x05")))

    def test_invalid_deferred_private_data_unblocks_second_key(self):
        first, ppk = self.key("bad", encrypted=True)
        second, ppk2 = self.key("good", encrypted=True)
        data = ppk.read_text()
        prefix, remainder = data.split("Private-Lines: ", 1)
        _, remainder = remainder.split("\n", 1)
        private, mac = remainder.split("Private-MAC: ", 1)
        ppk.write_text(prefix + "Private-Lines: 1\n!invalid!\nPrivate-MAC: " + mac)
        self.add(ppk, True)
        self.add(ppk2, True)
        a = self.raw_client(self.blob(str(first)+".pub"))
        pending = self.host.prompt()
        b = self.raw_client(self.blob(str(second)+".pub"))
        self.ok(self.run_tool(["ssh-add", "-L"]))
        self.host.send(action=7, request=pending["request"], passphrase="test-passphrase")
        self.assertEqual(self.receive(a), b"\x05")
        next_prompt = self.host.prompt()
        self.host.send(action=7, request=next_prompt["request"], passphrase="test-passphrase")
        self.assertEqual(self.receive(b)[0], 14)

    def test_same_private_key_multiple_certificates_remain_distinct(self):
        key, ppk = self.key()
        ca, _ = self.key("ca")
        self.add(ppk)
        for name in ("first", "second"):
            self.ok(self.run_tool(["ssh-keygen", "-q", "-s", ca, "-I", name,
                                  "-n", "suite", str(key)+".pub"]))
            cert_ppk = self.env.root / (name + ".ppk")
            self.ok(self.run_tool([ARGUMENTS.puttygen, ppk, "--certificate", str(key)+"-cert.pub",
                                  "-O", "private", "-o", cert_ppk], input=b""))
            self.add(cert_ppk)
        rows = self.host.snapshot(3)["keys"]
        self.assertEqual(len({row["blob"] for row in rows}), 3)
        self.host.send(action=3, version=2, blob=rows[1]["blob"])
        self.assertEqual(self.host.result(3)["result"], 0)
        self.host.snapshot(2)

    def test_real_openssh_authentication_uses_agent_only(self):
        with SSHServer(self.env) as server:
            ppk = self.env.root / "server.ppk"
            self.ok(self.run_tool([ARGUMENTS.puttygen, server.private_key, "-O", "private", "-o", ppk], input=b""))
            argv = ["sftp", "-F", server.client_config, "-o", "IdentityFile=none",
                    "-o", "IdentitiesOnly=no", "-o", "IdentityAgent="+str(self.host.path),
                    "-P", str(server.port), "-b", "-", f"{server.user}@{server.host}"]
            # 配置中的私钥仍会累加；另写只保留公钥、主机信任项的配置。
            config = self.env.write_private("ssh/agent_client_config", b"\n".join(
                line for line in server.client_config.read_bytes().splitlines()
                if not line.strip().startswith((b"IdentityFile ", b"IdentityAgent ", b"IdentitiesOnly "))) + b"\n")
            argv[2] = config
            self.assertNotEqual(self.run_tool(argv, input=b"pwd\nquit\n").returncode, 0)
            self.add(ppk)
            self.ok(self.run_tool(argv, input=b"pwd\nquit\n"))

    def test_reject_invalid_key_and_reencrypt_plain_key(self):
        key, ppk = self.key()
        self.host.send(action=2, path=str(key))
        self.assertEqual(self.host.result(2)["result"], 1)
        self.host.snapshot(0)
        self.add(ppk)
        self.host.send(action=6)
        self.assertEqual(self.host.result(6)["result"], 1)

    def test_prompt_creation_failure_unblocks_second_key(self):
        if not ARGUMENTS.branches:
            self.skipTest("未指定真实上游客户端提示失败分支驱动")
        _, first = self.key("branch-a", encrypted=True)
        _, second = self.key("branch-b", encrypted=True)
        self.ok(self.run_tool([ARGUMENTS.branches, first, second]))

    def test_invalid_one_time_reply_cancels_prompt_and_unblocks_next(self):
        first, first_ppk = self.key("invalid-reply-one", encrypted=True)
        second, second_ppk = self.key("invalid-reply-two", encrypted=True)
        self.add(first_ppk, True)
        self.add(second_ppk, True)
        one = self.sign_client(str(first) + ".pub")
        prompt = self.host.prompt()
        two = self.sign_client(str(second) + ".pub")
        self.host.send(action=7, request=prompt["request"], corrupt=True)
        self.assertEqual(self.host.wait(lambda e: e.get("kind") == 5)["request"], prompt["request"])
        one.communicate(timeout=5)
        self.assertNotEqual(one.returncode, 0)
        following = self.host.prompt()
        self.host.send(action=7, request=following["request"], passphrase="test-passphrase")
        self.verify(two, str(second) + ".pub")

    def test_plain_ppk_extension_and_ssh1_temporary_ownership(self):
        _, ppk = self.key()
        self.add(ppk, encrypted=True)
        self.host.snapshot(1)
        empty = self.env.write_private("empty-passphrase", b"\n")
        rsa1 = self.env.root / "rsa1.ppk"
        self.ok(self.run_tool([ARGUMENTS.puttygen, "-t", "rsa1", "-b", "1024",
            "--new-passphrase", empty, "-o", rsa1], input=b""))
        self.add(rsa1)
        rows = self.host.snapshot(2)["keys"]
        self.assertEqual({row["version"] for row in rows}, {1, 2})
        self.host.send(action=4)
        self.assertEqual(self.host.result(4)["result"], 0)
        self.host.snapshot(0)

def main():
    global ARGUMENTS
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", required=True)
    parser.add_argument("--puttygen", required=True)
    parser.add_argument("--branches")
    ARGUMENTS, remaining = parser.parse_known_args()
    ARGUMENTS.host = str(Path(ARGUMENTS.host).resolve())
    ARGUMENTS.puttygen = str(Path(ARGUMENTS.puttygen).resolve())
    if ARGUMENTS.branches:
        ARGUMENTS.branches = str(Path(ARGUMENTS.branches).resolve())
    unittest.main(argv=[sys.argv[0], *remaining], verbosity=2)

if __name__ == "__main__":
    main()
