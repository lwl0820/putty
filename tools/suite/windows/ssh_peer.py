#!/usr/bin/env python3
"""仅供测试的回环 SSH 对端；不执行系统命令，也不读取个人密钥。"""

import base64
from dataclasses import dataclass, field
import hashlib
import hmac
import socket
import struct
import threading
import time

import paramiko
from paramiko.common import MSG_CHANNEL_DATA, MSG_CHANNEL_EXTENDED_DATA
from paramiko.packet import Packetizer


PARAMIKO_VERSION = "5.0.0"
USERNAME = "suite"
WORKLOAD_COMMAND = b"suite-terminal-workload-v1"
ORDERED_INPUT_COMMAND = b"suite-terminal-ordered-input-v1"
EXPECTED_WORKLOAD_INPUT = "terminal-input-中文\n".encode("utf-8")
EXPECTED_ORDERED_INPUT = b"BEGIN" + (b"\x1b[Ax" * 64) + (b"\x1b[D" * 127)
WORKLOAD_SECONDS = 10.2
MAX_CONNECTIONS = 16
MAX_ACTIVE_CONNECTIONS = 2
MAX_INPUT_BYTES = 4096
MAX_OUTPUT_BYTES = 512 * 1024
WINDOW_BYTES = 64 * 1024
PACKET_BYTES = 32 * 1024
CONNECTION_SECONDS = 25
SHUTDOWN_SECONDS = 3


class PeerShutdownError(RuntimeError):
    """受控关闭未能在期限内回收所有线程，不能将该次测试视为通过。"""


class _BoundedPacketizer(Packetizer):
    def __init__(self, sock):
        super().__init__(sock)
        self._banner_socket = sock
        self._handshake_deadline = None
        self._received_channel_bytes = 0

    def start_handshake(self, timeout):
        # 上游默认创建 Timer；未完成密钥交换时 close 不会取消该线程。
        self._handshake_deadline = time.monotonic() + timeout

    def complete_handshake(self):
        self._handshake_deadline = None

    def handshake_timed_out(self):
        deadline = self._handshake_deadline
        return deadline is not None and time.monotonic() >= deadline

    def readline(self, timeout):
        # 协议 banner 尚未加密；逐字节读取，避免无换行输入无限增长。
        deadline = time.monotonic() + timeout
        line = bytearray()
        while time.monotonic() < deadline:
            try:
                byte = self._banner_socket.recv(1)
            except socket.timeout:
                continue
            if not byte:
                raise EOFError()
            if byte == b"\n":
                return line.rstrip(b"\r").decode("utf-8")
            line.extend(byte)
            if len(line) > 1024:
                raise paramiko.SSHException("测试对端拒绝超限 SSH banner")
        raise socket.timeout()

    def read_all(self, n, check_rekey=False):
        # 公开的 packetizer_class 接口在分配远端宣告的大包前拒绝它。
        if n > 256 * 1024:
            raise paramiko.SSHException("测试对端拒绝超限 SSH 包")
        return super().read_all(n, check_rekey)

    def read_message(self):
        kind, message = super().read_message()
        if kind == MSG_CHANNEL_EXTENDED_DATA:
            # 本夹具只消费标准输入，不允许未消费的 stderr 缓冲增长。
            raise paramiko.SSHException("测试对端拒绝输入扩展数据")
        if kind == MSG_CHANNEL_DATA:
            data = message.get_remainder()
            if len(data) < 8:
                raise paramiko.SSHException("测试对端拒绝不完整通道数据")
            size = struct.unpack(">I", data[4:8])[0]
            # 不能依赖客户端遵守通告的窗口；在进入 Channel 缓冲前检查。
            if (len(data) != 8 + size or size > PACKET_BYTES
                    or self._received_channel_bytes + size > WINDOW_BYTES):
                raise paramiko.SSHException("测试对端拒绝超限通道数据")
            self._received_channel_bytes += size
        return kind, message


@dataclass
class _Connection:
    sock: socket.socket
    deadline: float = field(default_factory=lambda: time.monotonic() + CONNECTION_SECONDS)
    worker: threading.Thread | None = None
    transport: paramiko.Transport | None = None
    channel: paramiko.Channel | None = None
    command_event: threading.Event = field(default_factory=threading.Event)
    command: bytes | None = None
    channel_id: int | None = None
    received_bytes: int = 0
    output_bytes: int = 0


class _Server(paramiko.ServerInterface):
    def __init__(self, peer, connection):
        self.peer = peer
        self.connection = connection

    def get_allowed_auths(self, username):
        return "publickey"

    def check_auth_none(self, username):
        self.peer._increment("rejected_authentication")
        return paramiko.AUTH_FAILED

    def check_auth_password(self, username, password):
        self.peer._increment("rejected_authentication")
        return paramiko.AUTH_FAILED

    def check_auth_publickey(self, username, key):
        self.peer._increment("offered_public_keys")
        if username == USERNAME and hmac.compare_digest(key.asbytes(), self.peer._allowed_blob):
            # 这里只准许公钥；真实签名仍由 Paramiko 验证。
            return paramiko.AUTH_SUCCESSFUL
        self.peer._increment("rejected_authentication")
        return paramiko.AUTH_FAILED

    def check_channel_request(self, kind, chanid):
        if kind == "session" and self.connection.channel_id is None:
            self.connection.channel_id = chanid
            return paramiko.OPEN_SUCCEEDED
        self.peer._increment("rejected_requests")
        return paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED

    def check_channel_direct_tcpip_request(self, chanid, origin, destination):
        self.peer._increment("rejected_requests")
        return paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED

    def check_channel_exec_request(self, channel, command):
        if (command in (WORKLOAD_COMMAND, ORDERED_INPUT_COMMAND)
                and channel.get_id() == self.connection.channel_id
                and self.connection.command is None):
            self.connection.command = command
            self.connection.command_event.set()
            return True
        self.peer._increment("rejected_requests")
        return False

    def _deny(self, *unused):
        self.peer._increment("rejected_requests")
        return False

    check_channel_pty_request = _deny
    check_channel_shell_request = _deny
    check_channel_subsystem_request = _deny
    check_channel_window_change_request = _deny
    check_channel_x11_request = _deny
    check_channel_forward_agent_request = _deny
    check_channel_env_request = _deny
    check_port_forward_request = _deny
    check_global_request = _deny


class RestrictedSSHPeer:
    """构造仅接受一个公开 SSH key blob，身份固定为 suite。

    使用 ``with RestrictedSSHPeer(public_blob) as peer`` 启动；端口通过
    ``peer.port`` 获取，``peer.host_key_fingerprint`` 可直接传给 Plink。
    ``statistics()`` 只返回计数、固定标记及耗时，不保存输入正文或秘密。
    """

    def __init__(self, allowed_public_blob):
        if paramiko.__version__ != PARAMIKO_VERSION:
            raise RuntimeError(f"测试依赖必须为 paramiko=={PARAMIKO_VERSION}")
        if not isinstance(allowed_public_blob, bytes) or not 16 <= len(allowed_public_blob) <= 8192:
            raise ValueError("必须提供单个有界的公开 SSH key blob")
        message = paramiko.Message(allowed_public_blob)
        key_type = message.get_text()
        key = paramiko.PKey.from_type_string(key_type, allowed_public_blob)
        if key.asbytes() != allowed_public_blob:
            raise ValueError("公开 SSH key blob 不规范或含尾随数据")
        self._allowed_blob = allowed_public_blob
        self._host_key = paramiko.RSAKey.generate(2048)
        digest = base64.b64encode(hashlib.sha256(self._host_key.asbytes()).digest()).decode("ascii")
        self.host_key_fingerprint = "SHA256:" + digest.rstrip("=")
        self.port = None
        self._listener = None
        self._listener_thread = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._connections = []
        self._results = []
        self._counts = dict.fromkeys((
            "accepted_connections", "authenticated_connections", "offered_public_keys",
            "rejected_authentication", "rejected_requests", "capacity_refusals",
            "connection_errors", "completed_commands", "active_high_water",
        ), 0)
        self._started = False

    def __enter__(self):
        if self._started or self._stop.is_set():
            raise RuntimeError("SSH 对端只能启动一次")
        self._started = True
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._listener = listener
        try:
            if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            listener.bind(("127.0.0.1", 0))
            listener.listen(MAX_ACTIVE_CONNECTIONS)
            listener.settimeout(0.1)
            self.port = listener.getsockname()[1]
            self._listener_thread = threading.Thread(target=self._accept, name="suite-ssh-listener")
            self._listener_thread.start()
            return self
        except BaseException:
            self.close()
            raise

    def __exit__(self, *unused):
        self.close()

    def _increment(self, name):
        with self._lock:
            self._counts[name] += 1

    def _accept(self):
        accepting = True
        while not self._stop.is_set():
            with self._lock:
                expired = [c for c in self._connections
                           if c.worker.is_alive() and time.monotonic() >= c.deadline]
            # 监听线程负责期限，即使命令线程正等待发送或密钥重协商也能断开。
            for connection in expired:
                self._release(connection)
            if not accepting:
                self._stop.wait(0.05)
                continue
            try:
                sock, address = self._listener.accept()
            except socket.timeout:
                continue
            except OSError:
                if not self._stop.is_set():
                    self._increment("connection_errors")
                break
            with self._lock:
                self._counts["accepted_connections"] += 1
                total = self._counts["accepted_connections"]
                active = sum(c.worker.is_alive() for c in self._connections)
                if self._stop.is_set() or address[0] != "127.0.0.1" or active >= MAX_ACTIVE_CONNECTIONS:
                    self._counts["capacity_refusals"] += 1
                    sock.close()
                else:
                    connection = _Connection(sock)
                    connection.worker = threading.Thread(
                        target=self._serve, args=(connection,), name=f"suite-ssh-worker-{total}")
                    self._connections.append(connection)
                    self._counts["active_high_water"] = max(self._counts["active_high_water"], active + 1)
                    connection.worker.start()
            if total >= MAX_CONNECTIONS:
                self._listener.close()
                accepting = False

    def _serve(self, connection):
        outcome = "closed"
        authenticated = False
        connection_deadline = connection.deadline
        try:
            with self._lock:
                if self._stop.is_set():
                    return
                transport = paramiko.Transport(
                    connection.sock, default_window_size=WINDOW_BYTES,
                    default_max_packet_size=PACKET_BYTES, packetizer_class=_BoundedPacketizer)
                connection.transport = transport
                transport.name = "suite-ssh-transport"
                transport.banner_timeout = 5
                transport.auth_timeout = 8
                transport.add_server_key(self._host_key)
                ready = threading.Event()
                transport.start_server(event=ready, server=_Server(self, connection))
            while not self._stop.is_set() and time.monotonic() < connection_deadline:
                if ready.wait(0.05):
                    break
            while not self._stop.is_set() and transport.is_active() and time.monotonic() < connection_deadline:
                if transport.is_authenticated() and not authenticated:
                    authenticated = True
                    self._increment("authenticated_connections")
                channel = transport.accept(0.05)
                if channel is None:
                    continue
                if not authenticated:
                    if not transport.is_authenticated():
                        raise paramiko.SSHException("未完成认证的连接不能取得会话通道")
                    authenticated = True
                    self._increment("authenticated_connections")
                with self._lock:
                    connection.channel = channel
                channel.settimeout(0.2)
                while not self._stop.is_set() and transport.is_active() and not channel.closed:
                    if time.monotonic() >= connection_deadline:
                        break
                    if connection.command_event.wait(0.02):
                        status, samples, duration = self._run_command(connection, connection_deadline)
                        self._check_deadline(connection_deadline)
                        channel.send_exit_status(status)
                        channel.shutdown_write()
                        # 允许客户端读完已发送的最终输出与 exit-status。
                        deadline = min(connection_deadline, time.monotonic() + 1)
                        while not self._stop.is_set() and transport.is_active() and not channel.closed:
                            if time.monotonic() >= deadline:
                                break
                            self._stop.wait(0.01)
                        with self._lock:
                            self._counts["completed_commands"] += 1
                            self._results.append({
                                "command": connection.command.decode("ascii"), "exit_status": status,
                                "received_bytes": connection.received_bytes, "output_bytes": connection.output_bytes,
                                "samples": samples, "duration_seconds": round(duration, 4),
                            })
                        outcome = "completed"
                        return
                break
        except (OSError, EOFError, paramiko.SSHException):
            if not self._stop.is_set() and authenticated:
                outcome = "connection-error"
                self._increment("connection_errors")
        except Exception:
            outcome = "worker-error"
            self._increment("connection_errors")
        finally:
            self._release(connection)
            if outcome != "completed" and connection.command is not None:
                with self._lock:
                    self._results.append({"command": connection.command.decode("ascii"), "outcome": outcome,
                                          "received_bytes": connection.received_bytes,
                                          "output_bytes": connection.output_bytes})

    def _read(self, connection, received):
        channel = connection.channel
        while channel.recv_ready():
            data = channel.recv(min(MAX_INPUT_BYTES + 1 - len(received), PACKET_BYTES))
            received.extend(data)
            connection.received_bytes += len(data)
            if len(received) > MAX_INPUT_BYTES:
                return False
        return True

    def _send(self, connection, data):
        if connection.output_bytes + len(data) > MAX_OUTPUT_BYTES:
            raise paramiko.SSHException("测试输出超过固定上限")
        connection.channel.sendall(data)
        connection.output_bytes += len(data)

    def _run_command(self, connection, connection_deadline):
        start = time.monotonic()
        received = bytearray()
        samples = 0
        valid = True
        if connection.command == WORKLOAD_COMMAND:
            self._send(connection, b"\x1b[2J\x1b[H")
            while not self._stop.is_set() and time.monotonic() - start < WORKLOAD_SECONDS:
                self._check_deadline(connection_deadline)
                line = (f"\x1b[H\x1b[2KSSH {samples:06d} 中文 e\u0301 Ω Ж 😀 漢字\r\n"
                        "\x1b[38;2;120;210;255m真彩色\x1b[0m").encode("utf-8")
                self._send(connection, line)
                samples += 1
                valid = self._read(connection, received)
                if not valid:
                    break
                self._stop.wait(0.005)
            self._check_deadline(connection_deadline)
            valid = valid and received == EXPECTED_WORKLOAD_INPUT
            self._send(connection, ("\x1b[4;1HFINAL 中文 e\u0301 😀\r\n"
                                   + ("INPUT_OK" if valid else "INPUT_BAD")).encode("utf-8"))
        else:
            complete_at = None
            while not self._stop.is_set() and time.monotonic() - start < 8:
                self._check_deadline(connection_deadline)
                valid = self._read(connection, received)
                if not valid or len(received) > len(EXPECTED_ORDERED_INPUT) or connection.channel.eof_received:
                    break
                if len(received) == len(EXPECTED_ORDERED_INPUT):
                    if complete_at is None:
                        complete_at = time.monotonic()
                    elif time.monotonic() - complete_at >= 0.1:
                        break
                self._stop.wait(0.005)
            self._check_deadline(connection_deadline)
            valid = valid and received == EXPECTED_ORDERED_INPUT
            self._send(connection, b"ORDER_OK" if valid else b"ORDER_BAD")
        return (0 if valid else 9), samples, time.monotonic() - start

    @staticmethod
    def _check_deadline(deadline):
        if time.monotonic() >= deadline:
            raise paramiko.SSHException("测试对端连接总期限已到")

    @staticmethod
    def _shutdown(sock):
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        sock.close()

    @classmethod
    def _release(cls, connection):
        # 先关闭底层 socket，令任何发送／重协商等待者返回，再停止传输。
        cls._shutdown(connection.sock)
        if connection.transport is not None:
            connection.transport.close()
        if connection.channel is not None:
            connection.channel.close()

    def close(self):
        self._stop.set()
        if self._listener is not None:
            self._shutdown(self._listener)
        with self._lock:
            connections = list(self._connections)
        for connection in connections:
            self._release(connection)
        deadline = time.monotonic() + SHUTDOWN_SECONDS
        threads = ([self._listener_thread] if self._listener_thread else [])
        threads += [c.worker for c in connections]
        threads += [c.transport for c in connections if c.transport is not None]
        for thread in threads:
            if thread is not threading.current_thread() and thread.ident is not None:
                thread.join(max(0, deadline - time.monotonic()))
        if any(thread.is_alive() for thread in threads):
            raise PeerShutdownError("SSH 对端关闭后仍有存活线程")

    def statistics(self):
        with self._lock:
            threads = ([self._listener_thread] if self._listener_thread else [])
            threads += [c.worker for c in self._connections]
            threads += [c.transport for c in self._connections if c.transport is not None]
            return {"paramiko": PARAMIKO_VERSION, "bind_host": "127.0.0.1", "port": self.port,
                    "host_key_algorithm": "ssh-rsa", **self._counts,
                    "command_results": [dict(result) for result in self._results],
                    "active_connections": sum(c.worker.is_alive() for c in self._connections),
                    "live_threads": sum(thread.is_alive() for thread in threads),
                    "closed": self._stop.is_set()}
