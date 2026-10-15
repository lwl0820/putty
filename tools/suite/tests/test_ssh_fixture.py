"""使用真实 OpenSSH 验证认证、主机密钥、SFTP 传输和服务清理。"""

import hashlib
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time
import unittest
import uuid
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from tools.suite.isolation import DEFAULT_PARENT, IsolationEnvironment, IsolationError, private_directory  # noqa: E402
from tools.suite.ssh_fixture import FixtureUnavailable, SSHServer  # noqa: E402


def quote_path(path):
    return '"' + str(path).replace("\\", "\\\\").replace('"', '\\"') + '"'


@unittest.skipUnless(sys.platform == "linux", "本地 OpenSSH 服务夹具仅支持已验证的 Linux")
class SSHFixtureTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not all(shutil.which(program) for program in ("sshd", "ssh-keygen", "sftp")):
            raise unittest.SkipTest("缺少 OpenSSH 服务或客户端；真实 SSH/SFTP 互操作未执行")
        cls.parent = private_directory(DEFAULT_PARENT / ("ssh-unit-" + uuid.uuid4().hex[:8]))
        cls.addClassCleanup(shutil.rmtree, cls.parent)

    def setUp(self):
        self.environment = IsolationEnvironment(self.parent)
        self.addCleanup(self.environment.close)
        self.server = SSHServer(self.environment)
        self.addCleanup(self.server.close)
        # 端口或系统权限失败必须被看见，不把缺环境转换成互操作通过。
        self.server.start()

    def test_real_banner_and_effective_restrictions(self):
        with socket.create_connection((self.server.host, self.server.port), timeout=2) as connection:
            self.assertTrue(connection.recv(256).startswith(b"SSH-2.0-"))
        settings = self.environment.run([
            self.server.sshd, "-T", "-f", self.server.config,
        ], check=True).stdout.decode("utf-8").lower()
        self.assertIn(f"listenaddress {self.server.host}:{self.server.port}", settings)
        for value in ("usepam no", "passwordauthentication no", "permituserrc no",
                      "allowagentforwarding no", "allowtcpforwarding no",
                      "authenticationmethods publickey", "forcecommand internal-sftp"):
            self.assertIn(value, settings)
        self.assertTrue(self.server.host_key_fingerprint.startswith("SHA256:"))

    def test_binary_and_chinese_space_paths_round_trip(self):
        payload = bytes(range(256)) * 4096 + b"\x00\xff\x00"
        source = self.environment.write_private("temp/上传 中文.bin", payload)
        destination = self.environment.root / "temp" / "下载 中文.bin"
        result = self.server.run_sftp(
            f"put {quote_path(source)} {quote_path('远端 空格.bin')}\n"
            f"get {quote_path('远端 空格.bin')} {quote_path(destination)}\nquit\n")
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", errors="replace"))
        self.assertEqual(hashlib.sha256(destination.read_bytes()).digest(), hashlib.sha256(payload).digest())
        self.assertEqual((self.server.transfer_root / "远端 空格.bin").read_bytes(), payload)

    def test_empty_file_and_remote_operations(self):
        source = self.environment.write_private("temp/empty.bin", b"")
        result = self.server.run_sftp(
            f"mkdir {quote_path('测试目录')}\n"
            f"put {quote_path(source)} {quote_path('测试目录/空 文件')}\n"
            f"rename {quote_path('测试目录/空 文件')} {quote_path('测试目录/改名 文件')}\n"
            f"chmod 600 {quote_path('测试目录/改名 文件')}\nquit\n")
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", errors="replace"))
        remote = self.server.transfer_root / "测试目录" / "改名 文件"
        self.assertEqual(remote.read_bytes(), b"")
        self.assertEqual(remote.stat().st_mode & 0o777, 0o600)
        deleted = self.server.run_sftp(
            f"rm {quote_path('测试目录/改名 文件')}\nrmdir {quote_path('测试目录')}\nquit\n")
        self.assertEqual(deleted.returncode, 0, deleted.stderr.decode("utf-8", errors="replace"))
        self.assertFalse(remote.parent.exists())

    def test_failed_batch_stops_before_later_upload(self):
        source = self.environment.write_private("temp/source", b"must-not-upload")
        result = self.server.run_sftp(
            f"get absent-file {quote_path(self.environment.root / 'temp/absent')}\n"
            f"put {quote_path(source)} after-failure\n")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.server.transfer_root / "after-failure").exists())
        self.assertFalse((self.environment.root / "temp/absent").exists())

    def test_rejected_authentication_does_not_start_sftp(self):
        authorized_keys = self.environment.root / "ssh" / "authorized_keys"
        authorized_keys.write_bytes(b"")
        result = self.server.run_sftp("mkdir forbidden\nquit\n")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b"Permission denied", result.stderr)
        self.assertFalse((self.server.transfer_root / "forbidden").exists())

    def test_host_key_mismatch_is_rejected(self):
        # 使用另一个真实公钥构造错误预期，不能通过关闭主机密钥检查凑成功。
        wrong_key = Path(str(self.server.private_key) + ".pub").read_text(encoding="ascii").split()
        known_hosts = self.environment.root / "ssh" / "known_hosts"
        known_hosts.write_text(
            f"[{self.server.host}]:{self.server.port} {wrong_key[0]} {wrong_key[1]}\n", encoding="ascii")
        result = self.server.run_sftp("mkdir forbidden\nquit\n")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b"Host key verification failed", result.stderr)
        self.assertFalse((self.server.transfer_root / "forbidden").exists())

    def test_close_stops_listener_and_is_idempotent(self):
        process = self.server.process
        self.server.close()
        self.server.close()
        self.assertIsNotNone(process.poll())
        with self.assertRaises(OSError):
            socket.create_connection((self.server.host, self.server.port), timeout=0.5)
        with self.assertRaises(IsolationError):
            self.server.run_sftp("pwd\n")
        self.environment.close()
        self.assertFalse(self.environment.root.exists())

    def test_close_disconnects_active_session(self):
        client = subprocess.Popen(
            [self.server.sftp, "-F", str(self.server.client_config), "-P", str(self.server.port),
             "-b", "-", f"{self.server.user}@{self.server.host}"],
            cwd=self.environment.root, env=self.environment.environment(),
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        self.addCleanup(lambda: client.kill() if client.poll() is None else None)
        try:
            client.stdin.write(b"mkdir active-session\n")
            client.stdin.flush()
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline and not (self.server.transfer_root / "active-session").exists():
                if client.poll() is not None:
                    self.fail("SFTP 连接在关闭前已提前退出")
                time.sleep(0.02)
            self.assertTrue((self.server.transfer_root / "active-session").is_dir())
            self.server.close()
            # 批输入 EOF 本身可正常退出；继续发送真实请求才证明连接已撤销。
            client.communicate(input=b"mkdir survived-close\n", timeout=5)
            self.assertIsNotNone(client.poll())
            self.assertFalse((self.server.transfer_root / "survived-close").exists())
        finally:
            if client.poll() is None:
                client.kill()
            client.communicate(timeout=5)


@unittest.skipUnless(sys.platform == "linux", "Linux SSH 夹具参数检查")
class SSHFixtureFailureTest(unittest.TestCase):
    def test_missing_dependencies_do_not_create_service(self):
        with IsolationEnvironment() as environment:
            with patch("tools.suite.ssh_fixture.shutil.which", return_value=None), \
                    patch("pwd.getpwuid", side_effect=KeyError(os.geteuid())) as lookup:
                with self.assertRaisesRegex(FixtureUnavailable, "缺少 sshd"):
                    SSHServer(environment)
                lookup.assert_not_called()
            self.assertEqual(list((environment.root / "ssh").iterdir()), [])

    def test_missing_system_identity_does_not_create_service(self):
        with IsolationEnvironment() as environment:
            with patch("tools.suite.ssh_fixture.shutil.which", return_value="/usr/bin/fixture-tool"), \
                    patch("pwd.getpwuid", side_effect=KeyError(os.geteuid())):
                with self.assertRaisesRegex(FixtureUnavailable, "当前 UID 没有系统账户记录"):
                    SSHServer(environment)
            self.assertEqual(list((environment.root / "ssh").iterdir()), [])


if __name__ == "__main__":
    unittest.main()
