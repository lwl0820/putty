#!/usr/bin/env python3
"""直接执行固定上游 CLI，验证标准流、隔离连接与文件传输结果。"""

import argparse
import hashlib
import os
from pathlib import Path
import select
import shlex
import shutil
import signal
import socket
import struct
import subprocess
import sys
import threading
import time
import unittest
import uuid

try:
    from .isolation import IsolationEnvironment, private_directory
    from .ssh_fixture import SSHServer
except ImportError:
    from isolation import IsolationEnvironment, private_directory
    from ssh_fixture import SSHServer


ARGUMENTS = None
PAYLOAD = bytes(range(256)) * 4097 + b"\x00\xff\r\n"
PSFTP_COMMANDS = (
    "!", "bye", "cd", "chmod", "close", "del", "delete", "dir", "exit", "get",
    "help", "lcd", "lpwd", "ls", "mget", "mkdir", "mput", "mv", "open", "put",
    "pwd", "quit", "reget", "ren", "rename", "reput", "rm", "rmdir",
)


def quote(value):
    """PSFTP 双引号通过重复表示；参数不经过宿主 shell。"""
    value = str(value)
    if "\n" in value or "\r" in value:
        raise ValueError("批处理路径不能包含换行")
    return '"' + value.replace('"', '""') + '"'


def stop_process(process):
    """断言失败时也先结束并回收受测进程，再允许夹具清理目录。"""
    if process.poll() is None:
        process.terminate()
    try:
        process.communicate(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.communicate(timeout=5)


def read_exact(connection, size):
    """流式握手可能分片；只有收到完整字段才允许继续。"""
    data = bytearray()
    while len(data) < size:
        chunk = connection.recv(size - len(data))
        if not chunk:
            raise EOFError("转发握手回复提前结束")
        data.extend(chunk)
    return bytes(data)


class IsolatedTest(unittest.TestCase):
    def setUp(self):
        self.environment = IsolationEnvironment()
        self.addCleanup(self.environment.close)
        self.assertTrue(self.environment.probe()["passed"])

    def run_cli(self, *arguments, input=b"", timeout=20):
        return self.environment.run([ARGUMENTS.executable, *arguments], input=input, timeout=timeout)

    def assert_ok(self, result):
        self.assertEqual(result.returncode, 0,
                         (result.stdout + result.stderr).decode("utf-8", errors="replace")[-3000:])

    def assert_same(self, path, content=PAYLOAD):
        self.assertEqual(hashlib.sha256(Path(path).read_bytes()).digest(),
                         hashlib.sha256(content).digest())


class LocalChecks(IsolatedTest):
    def test_version_aliases_preserve_stdout(self):
        for option in ("-V", "--version"):
            with self.subTest(option=option):
                result = self.run_cli(option)
                self.assert_ok(result)
                self.assertIn(b"Release 0.85", result.stdout)
                self.assertTrue(result.stdout.startswith((ARGUMENTS.component + ":").encode("ascii")))
                self.assertEqual(result.stderr, b"")

    def test_help_is_stdout(self):
        options = ("--help",) if ARGUMENTS.component == "plink" else ("--help", "-h", "-?")
        for option in options:
            with self.subTest(option=option):
                result = self.run_cli(option)
                self.assert_ok(result)
                self.assertIn(("Usage: " + ARGUMENTS.component).encode("ascii"), result.stdout)
                self.assertEqual(result.stderr, b"")

    def test_unknown_option_is_nonzero_stderr(self):
        result = self.run_cli("-suite-option-does-not-exist")
        self.assertEqual(result.returncode, 1)
        self.assertIn(b"unknown option", result.stderr)
        self.assertEqual(result.stdout, b"")

    def test_required_arguments_fail_without_connecting(self):
        for option in ("-P", "-l", "-i", "-load", "-hostkey", "-pwfile"):
            with self.subTest(option=option):
                result = self.run_cli(option)
                self.assertEqual(result.returncode, 1)
                self.assertIn(b"requires an argument", result.stderr)
                self.assertEqual(result.stdout, b"")

    def test_pgp_fingerprints_are_public_stdout(self):
        result = self.run_cli("-pgpfp")
        self.assert_ok(result)
        self.assertIn(b"fingerprint", result.stdout.lower())
        self.assertEqual(result.stderr, b"")


class PlinkLocal(LocalChecks):
    def test_fragmented_forwarding_reply_and_early_eof(self):
        first_read = threading.Event()
        sender, receiver = socket.socketpair()
        with sender, receiver:
            receiver.settimeout(5)
            sender.sendall(b"\x05")

            def send_tail():
                if first_read.wait(5):
                    sender.sendall(b"\x00")

            class FragmentedSocket:
                def recv(self, size):
                    chunk = receiver.recv(size)
                    first_read.set()
                    return chunk

            thread = threading.Thread(target=send_tail)
            thread.start()
            try:
                self.assertEqual(read_exact(FragmentedSocket(), 2), b"\x05\x00")
            finally:
                first_read.set()
                thread.join(timeout=5)
            self.assertFalse(thread.is_alive())
            sender.shutdown(socket.SHUT_WR)
            with self.assertRaises(EOFError):
                read_exact(receiver, 2)

    def test_cleanup_reaps_child_that_ignores_termination(self):
        process = subprocess.Popen(
            [sys.executable, "-B", "-c",
             "import os,signal; signal.signal(signal.SIGTERM, signal.SIG_IGN); "
             "os.write(1,b'R'); signal.pause()"],
            cwd=self.environment.root, env=self.environment.environment(),
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, umask=0o077)
        self.addCleanup(stop_process, process)
        ready, _, _ = select.select([process.stdout], [], [], 5)
        self.assertTrue(ready, "子进程尚未安装忽略终止信号的处理器")
        self.assertEqual(os.read(process.stdout.fileno(), 1), b"R")
        stop_process(process)
        self.assertEqual(process.returncode, -signal.SIGKILL)
        self.assertTrue(process.stdout.closed)
        self.assertTrue(process.stderr.closed)

    def test_missing_command_file_fails_before_connection(self):
        result = self.run_cli("-batch", "-m", str(self.environment.root / "temp/absent"), "127.0.0.1")
        self.assertEqual(result.returncode, 1)
        self.assertIn(b"unable to open command file", result.stderr)
        self.assertEqual(result.stdout, b"")

    def test_bad_forwarding_syntax_fails_before_connection(self):
        for option in ("-L", "-R", "-nc"):
            with self.subTest(option=option):
                result = self.run_cli("-batch", option, "invalid", "127.0.0.1")
                self.assertEqual(result.returncode, 1)
                self.assertIn(b"expects", result.stderr)
                self.assertEqual(result.stdout, b"")


class PSCPLocal(LocalChecks):
    def test_missing_transfer_operands(self):
        result = self.run_cli("-batch")
        self.assertEqual(result.returncode, 1)
        self.assertIn(b"expected at least two arguments", result.stderr)
        self.assertEqual(result.stdout, b"")


class PSFTPLocal(LocalChecks):
    def test_quit_aliases_exit_cleanly_without_connection(self):
        for command in ("bye", "exit", "quit"):
            with self.subTest(command=command):
                result = self.run_cli("-batch", input=(command + "\n").encode("ascii"))
                self.assert_ok(result)

    def test_all_command_help_and_aliases_are_resolved(self):
        batch = self.environment.write_private(
            "temp/help.batch", ("\n".join("help " + name for name in PSFTP_COMMANDS) + "\nquit\n").encode())
        result = self.run_cli("-batch", "-b", batch)
        self.assert_ok(result)
        self.assertNotIn(b"unknown command", result.stdout.lower())
        self.assertIn(b'resume interrupted "get -r"', result.stdout)
        self.assertIn(b'resume interrupted "put -r"', result.stdout)

    def test_batch_error_and_continue_have_upstream_exit_semantics(self):
        batch = self.environment.write_private("temp/invalid.batch", b"not-a-command\nlpwd\nquit\n")
        failed = self.run_cli("-batch", "-b", batch)
        self.assertEqual(failed.returncode, 2)
        self.assertIn(b"unknown command", failed.stdout)
        continued = self.run_cli("-batch", "-be", "-bc", "-b", batch)
        self.assert_ok(continued)
        self.assertIn(b"unknown command", continued.stdout)
        self.assertIn(b"lpwd", continued.stdout)
        self.assertIn(os.fsencode(self.environment.root), continued.stdout)


class ConnectedTest(IsolatedTest):
    def setUp(self):
        super().setUp()
        if ARGUMENTS.scenario == "container":
            try:
                from .container_ssh_fixture import ContainerSSHServer
            except ImportError:
                from container_ssh_fixture import ContainerSSHServer
            self.server = ContainerSSHServer(self.environment)
        else:
            self.server = SSHServer(self.environment)
        self.addCleanup(self.server.close)
        self.server.start()
        self.transfer_root = (self.server.remote_root if ARGUMENTS.scenario == "container"
                              else self.server.transfer_root)
        self.key = self.environment.root / "ssh/client.ppk"
        conversion = self.environment.run([
            ARGUMENTS.puttygen, self.server.private_key, "-O", "private", "-o", self.key,
        ], input=b"")
        self.assert_ok(conversion)
        self.environment._validate_file(self.key)
        self.assertTrue(self.key.read_bytes().startswith(b"PuTTY-User-Key-File-3:"))
        self.connection = ["-batch", "-noagent", "-noshare", "-2", "-4", "-no-trivial-auth",
                           "-P", str(self.server.port), "-l", self.server.user,
                           "-i", str(self.key), "-hostkey", self.server.host_key_fingerprint]

    def remote(self, name):
        return f"{self.server.user}@{self.server.host}:{self.transfer_root / name}"

    def psftp(self, commands, *options):
        path = self.environment.write_private("temp/" + uuid.uuid4().hex + ".batch", commands.encode("utf-8"))
        return self.run_cli(*self.connection, *options, "-b", path, self.server.host)

    def minimal_request(self, connection=None, input=b""):
        connection = self.connection if connection is None else connection
        if ARGUMENTS.component == "pscp":
            return self.run_cli(*connection, "-sftp", "-ls", self.remote("."), input=input)
        if ARGUMENTS.component == "psftp":
            path = self.environment.write_private("temp/" + uuid.uuid4().hex + ".batch", b"pwd\nquit\n")
            return self.run_cli(*connection, "-b", path, self.server.host, input=input)
        host_arguments = [] if "-load" in connection else [self.server.host]
        return self.run_cli(*connection, "-T", "-s", *host_arguments, "sftp",
                            input=input + struct.pack(">IBI", 5, 1, 3))

    def test_native_session_file_supplies_port_user_and_key(self):
        contents = (f"Present=1\nHostName={self.server.host}\nProtocol=ssh\n"
                    f"PortNumber={self.server.port}\nUserName={self.server.user}\n"
                    f"PublicKeyFile={self.key}\nTryAgent=0\nConnectionSharing=0\n"
                    "RemoteCommand=\n")
        self.environment.write_private("putty/sessions/suite-session", contents.encode("utf-8"))
        result = self.minimal_request(["-batch", "-noshare", "-load", "suite-session",
                                       "-hostkey", self.server.host_key_fingerprint])
        self.assert_ok(result)
        self.assertGreater((self.environment.root / "putty/randomseed").stat().st_size, 0)

    def check_host_key_cache(self):
        connection = [value for value in self.connection[:-2] if value != "-batch"]
        result = self.minimal_request(connection, input=b"y\n")
        self.assert_ok(result)
        content = (self.environment.root / "putty/sshhostkeys").read_bytes()
        self.assertIn(f"ssh-ed25519@{self.server.port}:{self.server.host} ".encode("ascii"), content)
        self.assert_ok(self.minimal_request(self.connection[:-2]))

    def test_wrong_host_key_is_rejected(self):
        connection = self.connection.copy()
        connection[-1] = "SHA256:" + "A" * 43
        result = self.minimal_request(connection)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b"host key", result.stderr.lower())
        self.assertNotIn(b"Access granted", result.stderr)

    def test_unknown_host_key_is_rejected_without_prompt(self):
        result = self.minimal_request(self.connection[:-2])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b"host key", result.stderr.lower())
        self.assertIn(b"batch", result.stderr.lower())
        self.assertEqual((self.environment.root / "putty/sshhostkeys").read_bytes(), b"")

    def test_rejected_public_key_is_not_success(self):
        name = "container_authorized_keys" if ARGUMENTS.scenario == "container" else "authorized_keys"
        (self.environment.root / "ssh" / name).write_bytes(b"")
        result = self.minimal_request()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b"server refused our key", result.stderr.lower())

    def test_closed_listener_is_not_success(self):
        self.server.close()
        result = self.minimal_request()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b"refused", result.stderr.lower())


class PlinkSFTP(ConnectedTest):
    def test_sftp_subsystem_preserves_binary_stdout(self):
        process = subprocess.Popen(
            [str(ARGUMENTS.executable), *self.connection, "-T", "-s", self.server.host, "sftp"],
            cwd=self.environment.root, env=self.environment.environment(),
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, umask=0o077)
        self.addCleanup(stop_process, process)
        process.stdin.write(struct.pack(">IBI", 5, 1, 3))
        process.stdin.flush()
        packet = b""
        deadline = time.monotonic() + 10
        expected = 4
        # internal-sftp 在 stdin EOF 后可直接退出；先读取真实回复，再发送 EOF。
        while len(packet) < expected:
            remaining = deadline - time.monotonic()
            self.assertGreater(remaining, 0, "等待 SFTP VERSION 回复超时")
            ready, _, _ = select.select([process.stdout], [], [], remaining)
            self.assertTrue(ready, "没有收到 SFTP VERSION 回复")
            chunk = os.read(process.stdout.fileno(), expected - len(packet))
            self.assertTrue(chunk, "SFTP VERSION 回复提前结束")
            packet += chunk
            if len(packet) == 4:
                expected = 4 + struct.unpack(">I", packet)[0]
                self.assertLess(expected, 65536)
        tail, errors = process.communicate(timeout=10)
        self.assertEqual(process.returncode, 0, errors.decode("utf-8", errors="replace"))
        self.assertEqual(tail, b"")
        length, message, version = struct.unpack(">IBI", packet[:9])
        self.assertEqual(length + 4, len(packet))
        self.assertEqual(message, 2)
        self.assertEqual(version, 3)
        self.assertIn(b"\x00", packet)
        self.assertNotIn(b"Access granted", packet)


class PSCPSFTP(ConnectedTest):
    def test_confirmed_host_key_cache_is_read_on_next_connection(self):
        self.check_host_key_cache()

    def test_default_and_forced_sftp_binary_and_empty_round_trip(self):
        for mode in ((), ("-sftp",)):
            for name, content in (("中文 空格.bin", PAYLOAD), ("空 文件", b"")):
                with self.subTest(mode=mode, name=name):
                    local = self.environment.write_private("temp/" + uuid.uuid4().hex, content)
                    uploaded = self.run_cli(*self.connection, *mode, "-q", local, self.remote(name))
                    self.assert_ok(uploaded)
                    self.assertEqual(uploaded.stdout, b"")
                    self.assert_same(self.transfer_root / name, content)
                    target = self.environment.root / "temp" / (uuid.uuid4().hex + " 下载")
                    self.assert_ok(self.run_cli(*self.connection, *mode, self.remote(name), target))
                    self.assert_same(target, content)

    def test_recursive_copy_and_preserved_metadata(self):
        source = private_directory(self.environment.root / "temp/目录 空格")
        private_directory(source / "empty")
        private_directory(source / "nested")
        file = self.environment.write_private("temp/目录 空格/nested/data.bin", PAYLOAD)
        os.utime(file, (1700000000, 1700000000))
        private_directory(self.transfer_root / "tree")
        self.assert_ok(self.run_cli(*self.connection, "-sftp", "-r", "-p", source, self.remote("tree")))
        self.assert_same(self.transfer_root / "tree/目录 空格/nested/data.bin")
        self.assertTrue((self.transfer_root / "tree/目录 空格/empty").is_dir())
        target = self.environment.root / "temp/download-tree"
        self.assert_ok(self.run_cli(*self.connection, "-sftp", "-r", "-p", self.remote("tree/目录 空格"), target))
        self.assert_same(target / "nested/data.bin")
        self.assertEqual(int((target / "nested/data.bin").stat().st_mtime), 1700000000)
        self.assertEqual((target / "nested/data.bin").stat().st_mode & 0o777, 0o600)

    def test_listing_wildcard_and_missing_remote_file(self):
        for name in ("one.bin", "two.bin"):
            self.environment.write_private("transfer/" + name, name.encode())
        listing = self.run_cli(*self.connection, "-sftp", "-ls", self.remote("."))
        self.assert_ok(listing)
        self.assertIn(b"one.bin", listing.stdout)
        self.assertIn(b"two.bin", listing.stdout)
        target = private_directory(self.environment.root / "temp/wildcard")
        self.assert_ok(self.run_cli(*self.connection, "-sftp", self.remote("*.bin"), target))
        self.assert_same(target / "one.bin", b"one.bin")
        self.assert_same(target / "two.bin", b"two.bin")
        destination = self.environment.root / "temp/absent"
        failed = self.run_cli(*self.connection, "-sftp", self.remote("does-not-exist"), destination)
        self.assertNotEqual(failed.returncode, 0)
        self.assertFalse(destination.exists())

    def test_unwritable_local_and_remote_destination(self):
        self.environment.write_private("transfer/source.bin", PAYLOAD)
        blocker = self.environment.write_private("temp/blocker", b"preserve")
        failed = self.run_cli(*self.connection, "-sftp", self.remote("source.bin"), blocker / "child")
        self.assertNotEqual(failed.returncode, 0)
        self.assertEqual(blocker.read_bytes(), b"preserve")
        remote_blocker = self.environment.write_private("transfer/blocker", b"preserve")
        failed = self.run_cli(*self.connection, "-sftp", blocker, self.remote("blocker/child"))
        self.assertNotEqual(failed.returncode, 0)
        self.assertEqual(remote_blocker.read_bytes(), b"preserve")


class PSFTPSFTP(ConnectedTest):
    def test_confirmed_host_key_cache_is_read_on_next_connection(self):
        self.check_host_key_cache()

    def test_binary_empty_quoted_paths_and_recursive_round_trip(self):
        source = self.environment.write_private("temp/上传 中文.bin", PAYLOAD)
        empty = self.environment.write_private("temp/空 文件", b"")
        destination = self.environment.root / "temp/下载 中文.bin"
        empty_destination = self.environment.root / "temp/空 下载"
        private_directory(self.environment.root / "temp/tree")
        private_directory(self.environment.root / "temp/tree/empty")
        self.environment.write_private("temp/tree/data.bin", PAYLOAD)
        tree_destination = self.environment.root / "temp/tree-down"
        result = self.psftp(
            f"put {quote(source)} {quote('远端 中文.bin')}\n"
            f"get {quote('远端 中文.bin')} {quote(destination)}\n"
            f"put {quote(empty)} {quote('空 文件')}\nget {quote('空 文件')} {quote(empty_destination)}\n"
            f"put -r {quote(source.parent / 'tree')} tree\nget -r tree {quote(tree_destination)}\nquit\n")
        self.assert_ok(result)
        self.assert_same(destination)
        self.assert_same(self.transfer_root / "远端 中文.bin")
        self.assert_same(empty_destination, b"")
        self.assert_same(tree_destination / "data.bin")
        self.assertTrue((tree_destination / "empty").is_dir())

    def test_resume_upload_and_download(self):
        source = self.environment.write_private("temp/full.bin", PAYLOAD)
        target = self.environment.write_private("temp/partial.bin", PAYLOAD[:32771])
        self.environment.write_private("transfer/resume.bin", PAYLOAD[:16387])
        result = self.psftp(f"reput {quote(source)} resume.bin\nreget resume.bin {quote(target)}\nquit\n")
        self.assert_ok(result)
        self.assert_same(target)
        self.assert_same(self.transfer_root / "resume.bin")

    def test_close_open_move_and_remove_aliases(self):
        self.environment.write_private("transfer/before.bin", b"aliases")
        result = self.psftp(
            "mv before.bin after.bin\nrm after.bin\nclose\n"
            f"open {self.server.user}@{self.server.host} {self.server.port}\npwd\nbye\n")
        self.assert_ok(result)
        self.assertFalse((self.transfer_root / "before.bin").exists())
        self.assertFalse((self.transfer_root / "after.bin").exists())

    def test_multiple_files_wildcards_and_directory_operations(self):
        source = private_directory(self.environment.root / "temp/multi")
        downloaded = private_directory(self.environment.root / "temp/multi-down")
        for name in ("one.dat", "two.dat"):
            self.environment.write_private("temp/multi/" + name, name.encode())
        result = self.psftp(
            f"mkdir multi\ncd multi\nlcd {quote(source)}\nlpwd\nmput *.dat\n"
            f"lcd {quote(downloaded)}\nmget *.dat\nls\ndir\npwd\n"
            "ren one.dat renamed.dat\nchmod 600 renamed.dat\n"
            "rename renamed.dat final.dat\ndel final.dat\ndelete two.dat\ncd ..\nrmdir multi\nquit\n")
        self.assert_ok(result)
        for name in ("one.dat", "two.dat"):
            self.assert_same(downloaded / name, name.encode())
        self.assertFalse((self.transfer_root / "multi").exists())

    def test_batch_failure_stops_but_be_continues(self):
        destination = self.environment.root / "temp/not-created"
        commands = f"get missing {quote(destination)}\nmkdir after-failure\nquit\n"
        failed = self.psftp(commands)
        self.assertEqual(failed.returncode, 2)
        self.assertFalse((self.transfer_root / "after-failure").exists())
        continued = self.psftp(commands, "-be", "-bc")
        self.assert_ok(continued)
        self.assertTrue((self.transfer_root / "after-failure").is_dir())
        self.assertFalse(destination.exists())

    def test_unwritable_local_destination_is_nonzero(self):
        self.environment.write_private("transfer/data.bin", PAYLOAD)
        blocker = self.environment.write_private("temp/blocker", b"preserve")
        result = self.psftp(f"get data.bin {quote(blocker / 'child')}\nquit\n")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(blocker.read_bytes(), b"preserve")

    def test_interactive_disconnect_cannot_execute_later_request(self):
        process = subprocess.Popen(
            [str(ARGUMENTS.executable), *self.connection, self.server.host],
            cwd=self.environment.root, env=self.environment.environment(),
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, umask=0o077)
        self.addCleanup(stop_process, process)
        process.stdin.write(b"mkdir connected\n")
        process.stdin.flush()
        deadline = time.monotonic() + 10
        while not (self.transfer_root / "connected").exists():
            self.assertIsNone(process.poll(), "PSFTP 在建立活动会话前退出")
            if time.monotonic() >= deadline:
                self.fail("没有收到真实 SFTP 创建目录请求")
            time.sleep(0.01)
        self.server.close()
        try:
            output, errors = process.communicate(b"mkdir after-disconnect\nquit\n", timeout=10)
        except BrokenPipeError:
            output, errors = process.communicate(timeout=10)
        self.assertFalse((self.transfer_root / "after-disconnect").exists())
        self.assertIn(b"closed", (output + errors).lower())


class PlinkContainer(ConnectedTest):
    def test_binary_stdin_stdout_stderr_and_remote_exit_status(self):
        code = ("import os,sys; data=sys.stdin.buffer.read(); os.write(1,data); "
                "os.write(2,b'separate\\x00\\xff'); sys.exit(37)")
        command = "/usr/bin/python3 -B -c " + shlex.quote(code)
        result = self.run_cli(*self.connection, "-T", "-no-sanitise-stdout", "-no-sanitise-stderr",
                              self.server.host, command, input=PAYLOAD)
        self.assertEqual(result.returncode, 37)
        self.assertEqual(result.stdout, PAYLOAD)
        self.assertEqual(result.stderr, b"separate\x00\xff")

    def test_command_file_and_verbose_stream_separation(self):
        script = self.environment.write_private(
            "temp/命令 空格.sh", b"printf command-file-output\nprintf remote-error >&2\nexit 19\n")
        result = self.run_cli(*self.connection, "-v", "-m", script, self.server.host)
        self.assertEqual(result.returncode, 19)
        self.assertEqual(result.stdout, b"command-file-output")
        self.assertIn(b"remote-error", result.stderr)
        self.assertIn(b"Access granted", result.stderr)

    def test_explicit_tty_and_no_tty(self):
        for option, expected in (("-t", 0), ("-T", 1)):
            with self.subTest(option=option):
                result = self.run_cli(*self.connection, option, self.server.host, "/usr/bin/test -t 0")
                self.assertEqual(result.returncode, expected)

    def test_nc_direct_channel_preserves_binary(self):
        port = self.server.start_echo_server()
        result = self.run_cli(*self.connection, "-nc", f"127.0.0.1:{port}", self.server.host, input=PAYLOAD)
        self.assert_ok(result)
        self.assertEqual(result.stdout, PAYLOAD)
        self.assertEqual(result.stderr, b"")

    def test_local_and_dynamic_forwarding(self):
        remote_port = self.server.start_echo_server()
        for option in ("-L", "-D"):
            with self.subTest(option=option):
                with socket.socket() as reserve:
                    reserve.bind(("127.0.0.1", 0))
                    port = reserve.getsockname()[1]
                destination = f"127.0.0.1:{port}"
                if option == "-L":
                    destination += f":127.0.0.1:{remote_port}"
                process = subprocess.Popen(
                    [str(ARGUMENTS.executable), *self.connection, "-N", option, destination, self.server.host],
                    cwd=self.environment.root, env=self.environment.environment(),
                    stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, umask=0o077)
                try:
                    deadline = time.monotonic() + 10
                    while True:
                        self.assertIsNone(process.poll(), "转发进程在监听前退出")
                        try:
                            connection = socket.create_connection(("127.0.0.1", port), timeout=0.2)
                            break
                        except OSError:
                            if time.monotonic() > deadline:
                                self.fail("Plink 转发监听未就绪")
                            time.sleep(0.02)
                    with connection:
                        connection.settimeout(10)
                        if option == "-D":
                            connection.sendall(b"\x05\x01\x00")
                            self.assertEqual(read_exact(connection, 2), b"\x05\x00")
                            connection.sendall(b"\x05\x01\x00\x01\x7f\x00\x00\x01" + struct.pack(">H", remote_port))
                            reply = read_exact(connection, 10)
                            self.assertEqual(reply[:2], b"\x05\x00")
                        data = PAYLOAD[:32771]
                        connection.sendall(data)
                        received = b""
                        while len(received) < len(data):
                            chunk = connection.recv(len(data) - len(received))
                            self.assertTrue(chunk)
                            received += chunk
                        self.assertEqual(received, data)
                finally:
                    stop_process(process)


class PSCPContainer(ConnectedTest):
    def remote_scp(self, name):
        # SCP 远端参数还要由远端 shell 解析；反斜杠引用也供原版文件名校验解码。
        path = str(self.transfer_root / name).replace("\\", "\\\\").replace(" ", "\\ ")
        return f"{self.server.user}@{self.server.host}:{path}"

    def test_scp_binary_empty_chinese_and_space_paths_round_trip(self):
        for name, content in (("SCP 中文 空格.bin", PAYLOAD), ("SCP 空 文件", b"")):
            with self.subTest(name=name):
                source = self.environment.write_private("temp/" + name, content)
                self.assert_ok(self.run_cli(*self.connection, "-scp", "-q", source, self.remote_scp(name)))
                self.assert_same(self.transfer_root / name, content)
                target = self.environment.root / "temp" / ("下载 " + name)
                self.assert_ok(self.run_cli(*self.connection, "-scp", self.remote_scp(name), target))
                self.assert_same(target, content)

    def test_scp_recursive_metadata_and_wildcards(self):
        source = private_directory(self.environment.root / "temp/目录 空格")
        private_directory(source / "empty")
        file = self.environment.write_private("temp/目录 空格/data.bin", PAYLOAD)
        os.utime(file, (1700000000, 1700000000))
        self.assert_ok(self.run_cli(*self.connection, "-scp", "-r", "-p", source, self.remote(".")))
        destination = self.environment.root / "temp/scp-tree"
        self.assert_ok(self.run_cli(*self.connection, "-scp", "-r", "-p", self.remote_scp(source.name), destination))
        self.assert_same(destination / "data.bin")
        self.assertTrue((destination / "empty").is_dir())
        self.assertEqual(int((destination / "data.bin").stat().st_mtime), 1700000000)
        for name in ("a.bin", "b.bin"):
            self.environment.write_private("transfer/" + name, name.encode())
        multiple = private_directory(self.environment.root / "temp/scp-multiple")
        restricted = self.run_cli(*self.connection, "-scp", self.remote("*.bin"), multiple)
        self.assert_ok(restricted)
        self.assertIn(b"Renaming of this file has been disallowed", restricted.stderr)
        self.assertTrue((multiple / "*.bin").is_file())
        self.assertFalse((multiple / "a.bin").exists())
        # 仅此受信任夹具显式验证原版 -unsafe 行为；默认路径仍验证上面的限制。
        multiple = private_directory(self.environment.root / "temp/scp-explicit-wildcard")
        self.assert_ok(self.run_cli(*self.connection, "-scp", "-unsafe", self.remote("*.bin"), multiple))
        for name in ("a.bin", "b.bin"):
            self.assert_same(multiple / name, name.encode())

    def test_scp_missing_file_and_unwritable_destination_are_nonzero(self):
        target = self.environment.root / "temp/not-created"
        result = self.run_cli(*self.connection, "-scp", self.remote("missing"), target)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(target.exists())
        source = self.environment.write_private("temp/source", PAYLOAD)
        blocker = self.environment.write_private("transfer/blocker", b"preserve")
        result = self.run_cli(*self.connection, "-scp", source, self.remote("blocker/child"))
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(blocker.read_bytes(), b"preserve")


def main():
    global ARGUMENTS
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--component", required=True, choices=("plink", "pscp", "psftp"))
    parser.add_argument("--executable", required=True, type=Path)
    parser.add_argument("--puttygen", required=True, type=Path)
    parser.add_argument("--scenario", required=True, choices=("local", "sftp", "container"))
    ARGUMENTS = parser.parse_args()
    ARGUMENTS.executable = ARGUMENTS.executable.resolve(strict=True)
    if sys.platform != "linux":
        parser.exit(77, "未执行：此自动检查仅支持已验证的 Linux 存储隔离\n")
    if ARGUMENTS.scenario in ("sftp", "container"):
        if not all(shutil.which(name) for name in ("sshd", "ssh-keygen", "sftp")):
            parser.exit(77, "未执行：缺少 OpenSSH 对端或客户端工具\n")
        ARGUMENTS.puttygen = ARGUMENTS.puttygen.resolve(strict=True)
    classes = {
        "local": {"plink": PlinkLocal, "pscp": PSCPLocal, "psftp": PSFTPLocal},
        "sftp": {"plink": PlinkSFTP, "pscp": PSCPSFTP, "psftp": PSFTPSFTP},
        "container": {"plink": PlinkContainer, "pscp": PSCPContainer},
    }
    if ARGUMENTS.component not in classes[ARGUMENTS.scenario]:
        parser.error("该组件没有此对端的专项测试")
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(classes[ARGUMENTS.scenario][ARGUMENTS.component])
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
