"""真实 SSH 夹具自测；此处 Paramiko 客户端不属于 PuTTY 互操作证据。"""

from contextlib import contextmanager
import socket
import threading
import time
import unittest
from unittest import mock

import paramiko
from paramiko.common import cMSG_CHANNEL_DATA

from tools.suite.windows import ssh_peer
from tools.suite.windows.ssh_peer import (
    EXPECTED_ORDERED_INPUT, EXPECTED_WORKLOAD_INPUT, MAX_OUTPUT_BYTES,
    ORDERED_INPUT_COMMAND, RestrictedSSHPeer, WORKLOAD_COMMAND, WORKLOAD_SECONDS,
)


class SSHPeerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.key = paramiko.RSAKey.generate(2048)
        cls.bad_key = paramiko.RSAKey.generate(2048)

    @contextmanager
    def client(self, peer, *, username="suite", key=None, authenticate=True):
        sock = socket.create_connection(("127.0.0.1", peer.port), timeout=3)
        transport = paramiko.Transport(sock)
        try:
            transport.start_client(timeout=3)
            self.assertEqual(transport.get_remote_server_key().fingerprint, peer.host_key_fingerprint)
            if authenticate:
                transport.auth_publickey(username, key or self.key)
                self.assertTrue(transport.is_authenticated())
            yield transport
        finally:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            transport.close()
            sock.close()
            transport.join(3)
            self.assertFalse(transport.is_alive())
            deadline = time.monotonic() + 3
            while peer.statistics()["active_connections"] and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertEqual(peer.statistics()["active_connections"], 0)

    def read_output(self, channel, timeout=15):
        output = bytearray()
        deadline = time.monotonic() + timeout
        channel.settimeout(0.2)
        while time.monotonic() < deadline:
            if channel.recv_ready():
                output.extend(channel.recv(32768))
                self.assertLessEqual(len(output), MAX_OUTPUT_BYTES)
            elif channel.exit_status_ready():
                break
            else:
                time.sleep(0.005)
        self.assertTrue(channel.exit_status_ready(), "SSH 对端没有真实退出状态")
        while channel.recv_ready():
            output.extend(channel.recv(32768))
        self.assertFalse(channel.recv_stderr_ready())
        return bytes(output), channel.recv_exit_status()

    def test_reject_authentication(self):
        with RestrictedSSHPeer(self.key.asbytes()) as peer:
            for username, key in (("suite", self.bad_key), ("other", self.key)):
                with self.subTest(username=username), self.client(peer, authenticate=False) as transport:
                    with self.assertRaises(paramiko.AuthenticationException):
                        transport.auth_publickey(username, key)
                    self.assertFalse(transport.is_authenticated())
            with self.client(peer, authenticate=False) as transport:
                with self.assertRaises(paramiko.AuthenticationException):
                    transport.auth_none("suite")
                with self.assertRaises(paramiko.AuthenticationException):
                    transport.auth_password("suite", "fixture-only-rejected-password", fallback=False)
                self.assertFalse(transport.is_authenticated())
            # 公钥声明正确而签名来自另一私钥，仍须由真实 SSH 验签拒绝。
            forged = paramiko.RSAKey(data=self.key.asbytes())
            forged.sign_ssh_data = self.bad_key.sign_ssh_data
            with self.client(peer, authenticate=False) as transport:
                with self.assertRaises(paramiko.AuthenticationException):
                    transport.auth_publickey("suite", forged)
                self.assertFalse(transport.is_authenticated())
        self.assertEqual(peer.statistics()["authenticated_connections"], 0)
        self.assertGreaterEqual(peer.statistics()["rejected_authentication"], 4)
        self.assertEqual(peer.statistics()["live_threads"], 0)

    def test_reject_exec_and_services(self):
        with RestrictedSSHPeer(self.key.asbytes()) as peer:
            operations = (
                lambda channel: channel.exec_command(b"python -c arbitrary"),
                lambda channel: channel.get_pty(),
                lambda channel: channel.invoke_shell(),
                lambda channel: channel.invoke_subsystem("sftp"),
            )
            for operation in operations:
                with self.client(peer) as transport:
                    channel = transport.open_session(timeout=3)
                    with self.assertRaises(paramiko.SSHException):
                        operation(channel)
            with self.client(peer) as transport:
                with self.assertRaises(paramiko.ChannelException):
                    transport.open_channel("direct-tcpip", ("127.0.0.1", 1), ("127.0.0.1", 1), timeout=3)
                self.assertIsNone(transport.global_request("suite-unknown-global", wait=True))
                with self.assertRaises(paramiko.SSHException):
                    transport.request_port_forward("127.0.0.1", 0)
        self.assertGreaterEqual(peer.statistics()["rejected_requests"], 7)
        self.assertEqual(peer.statistics()["completed_commands"], 0)
        self.assertEqual(peer.statistics()["live_threads"], 0)

    def test_continuous_unicode_workload(self):
        started = time.monotonic()
        with RestrictedSSHPeer(self.key.asbytes()) as peer:
            with self.client(peer) as transport:
                channel = transport.open_session(timeout=3)
                channel.exec_command(WORKLOAD_COMMAND)
                channel.sendall(EXPECTED_WORKLOAD_INPUT)
                output, status = self.read_output(channel)
                self.assertEqual(status, 0)
                self.assertIn("FINAL 中文 e\u0301 😀\r\nINPUT_OK".encode("utf-8"), output)
                self.assertIn(b"\x1b[38;2;120;210;255m", output)
                self.assertGreater(output.count(b"\x1b[H\x1b[2KSSH"), 500)
        self.assertGreaterEqual(time.monotonic() - started, WORKLOAD_SECONDS)
        self.assertEqual(peer.statistics()["live_threads"], 0)
        self.assertEqual(peer.statistics()["authenticated_connections"], 1)
        result = peer.statistics()["command_results"][0]
        self.assertEqual(result["received_bytes"], len(EXPECTED_WORKLOAD_INPUT))
        self.assertGreaterEqual(result["duration_seconds"], WORKLOAD_SECONDS)
        self.assertEqual(result["output_bytes"], len(output))

    def test_ordered_input_and_bad_input(self):
        self.assertEqual(len(EXPECTED_ORDERED_INPUT), 642)
        with RestrictedSSHPeer(self.key.asbytes()) as peer:
            for content, expected_status, expected_output in (
                (EXPECTED_ORDERED_INPUT, 0, b"ORDER_OK"),
                (EXPECTED_ORDERED_INPUT[:-1] + b"x", 9, b"ORDER_BAD"),
                (EXPECTED_ORDERED_INPUT + b"x", 9, b"ORDER_BAD"),
                (b"x" * 5000, 9, b"ORDER_BAD"),
            ):
                with self.subTest(size=len(content)), self.client(peer) as transport:
                    channel = transport.open_session(timeout=3)
                    channel.exec_command(ORDERED_INPUT_COMMAND)
                    channel.sendall(content)
                    output, status = self.read_output(channel, timeout=4)
                    self.assertEqual((output, status), (expected_output, expected_status))
        self.assertEqual(peer.statistics()["completed_commands"], 4)
        self.assertEqual(peer.statistics()["authenticated_connections"], 4)
        self.assertEqual(peer.statistics()["live_threads"], 0)

    def test_close_in_flight_and_idempotent(self):
        peer = RestrictedSSHPeer(self.key.asbytes())
        with peer:
            with self.client(peer) as transport:
                channel = transport.open_session(timeout=3)
                channel.exec_command(WORKLOAD_COMMAND)
                deadline = time.monotonic() + 3
                while not channel.recv_ready() and time.monotonic() < deadline:
                    time.sleep(0.005)
                self.assertTrue(channel.recv_ready())
                started = time.monotonic()
                peer.close()
                peer.close()
                self.assertLess(time.monotonic() - started, 3)
                self.assertEqual(peer.statistics()["live_threads"], 0)
        with self.assertRaises(RuntimeError):
            peer.__enter__()

    def test_connection_capacity_and_stalled_handshake(self):
        with RestrictedSSHPeer(self.key.asbytes()) as peer:
            sockets = [socket.create_connection(("127.0.0.1", peer.port), timeout=3) for _ in range(3)]
            try:
                deadline = time.monotonic() + 3
                while peer.statistics()["accepted_connections"] < 3 and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertEqual(peer.statistics()["accepted_connections"], 3)
                self.assertEqual(peer.statistics()["active_high_water"], 2)
                self.assertEqual(peer.statistics()["capacity_refusals"], 1)
                sockets[0].sendall(b"x" * 4096)
                deadline = time.monotonic() + 3
                while peer.statistics()["active_connections"] > 1 and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertEqual(peer.statistics()["active_connections"], 1)
                peer.close()
                self.assertEqual(peer.statistics()["live_threads"], 0)
            finally:
                for sock in sockets:
                    sock.close()

    def test_connection_deadline_includes_command(self):
        # 缩短同一生产期限，证明开始执行命令不会重新获得一份连接预算。
        with mock.patch.object(ssh_peer, "CONNECTION_SECONDS", 1.2), \
                mock.patch.object(ssh_peer, "WORKLOAD_SECONDS", 3):
            with RestrictedSSHPeer(self.key.asbytes()) as peer:
                with self.client(peer) as transport:
                    channel = transport.open_session(timeout=3)
                    channel.exec_command(WORKLOAD_COMMAND)
                    channel.sendall(EXPECTED_WORKLOAD_INPUT)
                    deadline = time.monotonic() + 2.2
                    while transport.is_active() and time.monotonic() < deadline:
                        time.sleep(0.01)
                    self.assertFalse(transport.is_active(), "命令阶段没有遵守连接总期限")
        self.assertEqual(peer.statistics()["completed_commands"], 0)
        self.assertEqual(peer.statistics()["live_threads"], 0)

    def test_close_during_key_exchange_has_no_timer_thread(self):
        before = set(threading.enumerate())
        try:
            with RestrictedSSHPeer(self.key.asbytes()) as peer:
                with socket.create_connection(("127.0.0.1", peer.port), timeout=3) as sock:
                    sock.sendall(b"SSH-2.0-suite-stalled-key-exchange\r\n")
                    received = bytearray()
                    deadline = time.monotonic() + 3
                    while time.monotonic() < deadline:
                        received.extend(sock.recv(4096))
                        if b"\n" in received and received.split(b"\n", 1)[1]:
                            break
                    else:
                        self.fail("未进入真实 SSH 密钥交换")
                    peer.close()
                    self.assertEqual(peer.statistics()["live_threads"], 0)
                    self.assertFalse([thread for thread in threading.enumerate()
                                      if thread not in before and isinstance(thread, threading.Timer)],
                                     "未完成握手留下了统计之外的计时线程")
        finally:
            # 回归若失败，也不能让上游旧握手 Timer 留在其它测试中。
            for thread in set(threading.enumerate()) - before:
                if isinstance(thread, threading.Timer):
                    thread.cancel()
                    thread.join(3)

    def test_deadline_preempts_blocked_command(self):
        entered = threading.Event()
        release = threading.Event()

        def delayed_command(*unused):
            # 模拟命令线程停在下游发送等待；总期限仍须由其它现有线程执行。
            entered.set()
            release.wait(4)
            return 0, 0, 0

        with mock.patch.object(ssh_peer, "CONNECTION_SECONDS", 1.2):
            with RestrictedSSHPeer(self.key.asbytes()) as peer:
                with self.client(peer) as transport:
                    with mock.patch.object(peer, "_run_command", side_effect=delayed_command):
                        try:
                            channel = transport.open_session(timeout=3)
                            channel.exec_command(WORKLOAD_COMMAND)
                            self.assertTrue(entered.wait(1))
                            deadline = time.monotonic() + 2.2
                            while transport.is_active() and time.monotonic() < deadline:
                                time.sleep(0.01)
                            self.assertFalse(transport.is_active(), "阻塞命令绕过了连接总期限")
                        finally:
                            release.set()
        self.assertEqual(peer.statistics()["completed_commands"], 0)
        self.assertEqual(peer.statistics()["live_threads"], 0)

    def test_total_capacity_still_enforces_deadline(self):
        with mock.patch.object(ssh_peer, "MAX_CONNECTIONS", 3), \
                mock.patch.object(ssh_peer, "CONNECTION_SECONDS", 1.2):
            with RestrictedSSHPeer(self.key.asbytes()) as peer:
                sockets = [socket.create_connection(("127.0.0.1", peer.port), timeout=3)
                           for _ in range(3)]
                try:
                    deadline = time.monotonic() + 3
                    while peer.statistics()["accepted_connections"] < 3 and time.monotonic() < deadline:
                        time.sleep(0.01)
                    self.assertEqual(peer.statistics()["accepted_connections"], 3)
                    with self.assertRaises(OSError):
                        with socket.create_connection(("127.0.0.1", peer.port), timeout=1):
                            self.fail("达到总连接上限后仍在接受连接")
                    deadline = time.monotonic() + 2.2
                    while peer.statistics()["active_connections"] and time.monotonic() < deadline:
                        time.sleep(0.01)
                    self.assertEqual(peer.statistics()["active_connections"], 0,
                                     "关闭监听后没有继续执行既有连接期限")
                finally:
                    for sock in sockets:
                        sock.close()
        self.assertEqual(peer.statistics()["live_threads"], 0)

    def test_reject_unconsumed_or_excess_channel_data(self):
        with RestrictedSSHPeer(self.key.asbytes()) as peer:
            for kind in ("stderr", "packet", "window"):
                with self.subTest(kind=kind), self.client(peer) as transport:
                    channel = transport.open_session(timeout=3)
                    if kind == "stderr":
                        channel.send_stderr(b"unexpected-input")
                    else:
                        size = ssh_peer.PACKET_BYTES + (1 if kind == "packet" else 0)
                        count = 1 if kind == "packet" else 3
                        for _ in range(count):
                            # 故意越过客户端窗口节流，检查服务端的实际硬限额。
                            message = paramiko.Message()
                            message.add_byte(cMSG_CHANNEL_DATA)
                            message.add_int(channel.remote_chanid)
                            message.add_string(b"x" * size)
                            transport._send_user_message(message)
                    deadline = time.monotonic() + 1.5
                    while transport.is_active() and time.monotonic() < deadline:
                        time.sleep(0.01)
                    self.assertFalse(transport.is_active(), "未消费的通道数据缺少硬限额")
        self.assertEqual(peer.statistics()["completed_commands"], 0)
        self.assertEqual(peer.statistics()["live_threads"], 0)


if __name__ == "__main__":
    unittest.main()
