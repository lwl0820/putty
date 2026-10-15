"""显式启用的 Docker SSH 对端检查；不加入普通 runtime 用例发现。"""

import hashlib
import json
import os
from pathlib import Path
import shlex
import socket
import subprocess
import sys
import time
import unittest


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from tools.suite.isolation import IsolationEnvironment, IsolationError  # noqa: E402
from tools.suite.container_ssh_fixture import ContainerSSHServer, DEFAULT_CONTAINER_PARENT, SYSTEM_PATH  # noqa: E402


@unittest.skipUnless(sys.platform == "linux" and os.environ.get("PUTTY_SUITE_CONTAINER_TESTS") == "1",
                     "需显式设置 PUTTY_SUITE_CONTAINER_TESTS=1 并提供本地 Docker 权限")
class ContainerSSHFixtureTest(unittest.TestCase):
    def setUp(self):
        self.environment = IsolationEnvironment(DEFAULT_CONTAINER_PARENT)
        self.addCleanup(self.environment.close)
        self.server = ContainerSSHServer(self.environment)
        self.addCleanup(self.server.close)
        self.server.start()

    def test_actual_container_restrictions_and_scoped_mounts(self):
        report = self.server.isolation_report()
        self.assertTrue(report["readonly_root"])
        self.assertEqual(report["network_mode"], "none")
        self.assertEqual(report["cap_drop"], ["ALL"])
        self.assertFalse(report["privileged"])
        self.assertEqual(report["log_driver"], "none")
        self.assertEqual(report["user"], f"{os.geteuid()}:{os.getegid()}")
        self.assertIn("no-new-privileges", report["security_options"])
        system_sources = {Path(path).resolve() for path in ("/usr", "/bin", "/sbin", "/lib", "/lib64")
                          if Path(path).is_dir()}
        if Path("/etc/ld.so.cache").is_file():
            system_sources.add(Path("/etc/ld.so.cache"))
        mounted_system_sources = set()
        for mount in report["mounts"]:
            source = Path(mount["Source"])
            if source in system_sources:
                self.assertFalse(mount["RW"])
                mounted_system_sources.add(source)
            else:
                self.assertTrue(source.is_relative_to(self.environment.root), str(source))
        self.assertEqual(mounted_system_sources, system_sources)
        # 核对容器看到的真实程序和加载器字节，防止 Docker daemon 属于另一个宿主命名空间。
        paths = [self.server.sshd, self.server.python, "/lib64/ld-linux-x86-64.so.2"]
        expected = [hashlib.sha256(Path(path).read_bytes()).hexdigest() for path in paths]
        code = f"from pathlib import Path; import hashlib,json; print(json.dumps([hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in {paths!r}]))"
        actual = self.server.run_exec(shlex.quote(self.server.python) + " -B -c " + shlex.quote(code))
        self.assertEqual(actual.returncode, 0, actual.stderr)
        self.assertEqual(json.loads(actual.stdout), expected)
        failed = self.server.run_exec("/usr/bin/touch /must-not-write")
        self.assertNotEqual(failed.returncode, 0)

    def test_dedicated_account_and_binary_stream_exit_status(self):
        code = "import json,os,pwd; print(json.dumps([pwd.getpwuid(os.getuid()).pw_name,os.environ['HOME'],os.getcwd(),os.environ['TMPDIR'],os.environ['PATH']]))"
        result = self.server.run_exec("/usr/bin/python3 -B -c " + shlex.quote(code))
        self.assertEqual(result.returncode, 0, result.stderr)
        values = json.loads(result.stdout)
        self.assertEqual(values, ["suite", str(self.server.remote_home), str(self.server.remote_home),
                                  str(self.environment.root / "temp"), SYSTEM_PATH])
        code = "import os,sys; os.write(1,b'out\\x00\\xff'); os.write(2,b'err\\xfe'); sys.exit(7)"
        result = self.server.run_exec("/usr/bin/python3 -B -c " + shlex.quote(code))
        self.assertEqual(result.returncode, 7)
        self.assertEqual(result.stdout, b"out\x00\xff")
        self.assertEqual(result.stderr, b"err\xfe")

    def test_real_legacy_scp_binary_round_trip(self):
        payload = bytes(range(256)) * 4096 + b"\x00\xff\x00"
        source = self.environment.write_private("temp/scp-source.bin", payload)
        remote = self.server.remote_root / "scp-remote.bin"
        destination = self.environment.root / "temp" / "scp-result.bin"
        arguments = [self.server.scp, "-O", "-F", self.server.client_config, "-P", str(self.server.port)]
        uploaded = self.environment.run([
            *arguments, source, f"{self.server.user}@{self.server.host}:{remote}",
        ], timeout=30)
        self.assertEqual(uploaded.returncode, 0, uploaded.stderr)
        downloaded = self.environment.run([
            *arguments, f"{self.server.user}@{self.server.host}:{remote}", destination,
        ], timeout=30)
        self.assertEqual(downloaded.returncode, 0, downloaded.stderr)
        self.assertEqual(hashlib.sha256(destination.read_bytes()).digest(), hashlib.sha256(payload).digest())

    def test_real_sftp_subsystem(self):
        source = self.environment.write_private("temp/sftp-source", b"sftp-content\x00\xff")
        result = self.server.run_sftp(f'put "{source}" sftp-remote\nquit\n')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.server.remote_root / "sftp-remote").read_bytes(), b"sftp-content\x00\xff")

    def test_loopback_tcp_forward_transfers_binary(self):
        remote_port = self.server.start_echo_server()
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            local_port = reservation.getsockname()[1]
        arguments = self.server.ssh_arguments()
        command = [*arguments[:-1], "-N", "-o", "ExitOnForwardFailure=yes", "-L",
                   f"127.0.0.1:{local_port}:127.0.0.1:{remote_port}", arguments[-1]]
        log = self.environment.write_private("logs/forward.log", b"")
        with open(log, "ab") as output:
            process = subprocess.Popen(command, cwd=self.environment.root, env=self.environment.environment(),
                                       stdin=subprocess.DEVNULL, stdout=output, stderr=output, umask=0o077)
            try:
                deadline = time.monotonic() + 10
                connection = None
                while time.monotonic() < deadline:
                    if process.poll() is not None:
                        self.fail("SSH 转发提前退出：" + log.read_text(encoding="utf-8", errors="replace"))
                    try:
                        connection = socket.create_connection(("127.0.0.1", local_port), timeout=0.2)
                        break
                    except OSError:
                        time.sleep(0.03)
                self.assertIsNotNone(connection)
                payload = bytes(range(256)) * 1024
                with connection:
                    connection.settimeout(5)
                    connection.sendall(payload)
                    received = bytearray()
                    while len(received) < len(payload):
                        chunk = connection.recv(65536)
                        if not chunk:
                            break
                        received.extend(chunk)
                self.assertEqual(bytes(received), payload)
            finally:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)

    def test_close_kills_active_command_container_and_relays(self):
        marker = self.server.remote_root / "active-command"
        code = f"from pathlib import Path; import time; Path({str(marker)!r}).write_text('ready'); time.sleep(60)"
        client = subprocess.Popen(
            self.server.ssh_arguments("/usr/bin/python3 -B -c " + shlex.quote(code)),
            cwd=self.environment.root, env=self.environment.environment(), stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, umask=0o077,
        )
        try:
            deadline = time.monotonic() + 10
            while not marker.exists() and time.monotonic() < deadline:
                if client.poll() is not None:
                    self.fail("活动命令在关闭前提前退出")
                time.sleep(0.03)
            self.assertTrue(marker.exists())
            container_id = self.server.container_id
            self.server.close()
            self.server.close()
            client.communicate(timeout=5)
            self.assertNotEqual(client.returncode, 0)
            self.assertNotEqual(self.server._docker("inspect", container_id, check=False).returncode, 0)
            self.assertTrue(all(process.poll() is not None for process in self.server._relay_processes))
            with self.assertRaises(OSError):
                socket.create_connection((self.server.host, self.server.port), timeout=0.2)
            with self.assertRaises(IsolationError):
                self.server.run_exec("true")
        finally:
            if client.poll() is None:
                client.kill()
            client.communicate(timeout=5)


if __name__ == "__main__":
    unittest.main()
