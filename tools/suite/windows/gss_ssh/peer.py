"""固定 GSS 用户和命令的有界回环 SSH 对端，不执行系统命令。"""
import base64
from dataclasses import dataclass, field
import hashlib
import socket
import struct
import threading
import time

import paramiko
from paramiko.common import MSG_CHANNEL_DATA, MSG_CHANNEL_EXTENDED_DATA
from paramiko.packet import Packetizer

from tools.suite.windows.support import ValidationError

USERNAME = "suite"
COMMAND = b"suite-gss-probe-v1"
OUTPUT = b"GSS_SSH_OK\n"
# 与终端公钥纵切相同的后续负载标识；当前短认证检查不执行此命令。
WORKLOAD_COMMAND = b"suite-terminal-workload-v1"
WORKLOAD_SECONDS = 10.2
EXPECTED_WORKLOAD_INPUT = "terminal-input-中文\n".encode("utf-8")
MAX_CONNECTIONS, MAX_ACTIVE_CONNECTIONS = 16, 2
MAX_INPUT_BYTES, MAX_OUTPUT_BYTES = 4096, 512 * 1024
WINDOW_BYTES, PACKET_BYTES = 64 * 1024, 32 * 1024
CONNECTION_SECONDS, SHUTDOWN_SECONDS = 25, 3


class PeerShutdownError(ValidationError):
    """线程仍在原生调用中，不能提前释放它使用的 GSS 对象。"""


def unicode_line(sample):
    if type(sample) is not int or not 0 <= sample < 1000000:
        raise ValidationError("Unicode 负载编号无效")
    return (f"\x1b[H\x1b[2KSSH {sample:06d} 中文 e\u0301 Ω Ж 😀 漢字\r\n"
            "\x1b[38;2;120;210;255m真彩色\x1b[0m").encode("utf-8")


class BoundedPacketizer(Packetizer):
    """公开扩展接口在分配、banner 和 Channel 缓冲之前执行上限。"""
    def __init__(self, sock):
        super().__init__(sock)
        self._socket = sock
        self._handshake_deadline = None
        self._received_channel_bytes = 0

    def start_handshake(self, timeout):
        self._handshake_deadline = time.monotonic() + timeout

    def complete_handshake(self):
        self._handshake_deadline = None

    def handshake_timed_out(self):
        return self._handshake_deadline is not None and time.monotonic() >= self._handshake_deadline

    def readline(self, timeout):
        deadline, result = time.monotonic() + timeout, bytearray()
        while time.monotonic() < deadline:
            try:
                data = self._socket.recv(1)
            except socket.timeout:
                continue
            if not data:
                raise EOFError()
            if data == b"\n":
                return result.rstrip(b"\r").decode("utf-8")
            result.extend(data)
            if len(result) > 1024:
                raise paramiko.SSHException("GSS SSH 对端拒绝超限 banner")
        raise socket.timeout()

    def read_all(self, n, check_rekey=False):
        if n > 256 * 1024:
            raise paramiko.SSHException("GSS SSH 对端拒绝超限包")
        return super().read_all(n, check_rekey)

    def read_message(self):
        kind, message = super().read_message()
        if kind == MSG_CHANNEL_EXTENDED_DATA:
            raise paramiko.SSHException("GSS SSH 对端拒绝输入扩展数据")
        if kind == MSG_CHANNEL_DATA:
            data = message.get_remainder()
            size = struct.unpack(">I", data[4:8])[0] if len(data) >= 8 else -1
            if (size < 0 or len(data) != size + 8 or size > PACKET_BYTES or
                    self._received_channel_bytes + size > min(MAX_INPUT_BYTES, WINDOW_BYTES)):
                raise paramiko.SSHException("GSS SSH 对端拒绝超限输入")
            self._received_channel_bytes += size
        return kind, message


@dataclass
class Connection:
    sock: socket.socket
    deadline: float = field(default_factory=lambda: time.monotonic() + CONNECTION_SECONDS)
    worker: threading.Thread | None = None
    transport: paramiko.Transport | None = None
    channel: paramiko.Channel | None = None
    command_event: threading.Event = field(default_factory=threading.Event)
    channel_id: int | None = None
    command: bytes | None = None


class Server(paramiko.ServerInterface):
    def __init__(self, peer, connection):
        self.peer, self.connection = peer, connection
        self.authenticated = False

    def get_allowed_auths(self, username):
        return "gssapi-with-mic"

    def enable_auth_gssapi(self):
        return True

    def check_auth_gssapi_with_mic(self, username, gss_authenticated=paramiko.AUTH_FAILED, cc_file=None):
        if username == USERNAME and gss_authenticated == paramiko.AUTH_SUCCESSFUL and cc_file is None:
            self.authenticated = True
            self.peer.increment("authenticated")
            return paramiko.AUTH_SUCCESSFUL
        self.peer.increment("rejected_auth")
        return paramiko.AUTH_FAILED

    def check_auth_none(self, username):
        self.peer.increment("rejected_auth")
        return paramiko.AUTH_FAILED

    def check_auth_password(self, username, password):
        return self.check_auth_none(username)

    def check_auth_publickey(self, username, key):
        return self.check_auth_none(username)

    def check_channel_request(self, kind, chanid):
        if self.authenticated and kind == "session" and self.connection.channel_id is None:
            self.connection.channel_id = chanid
            return paramiko.OPEN_SUCCEEDED
        return paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED

    def check_channel_exec_request(self, channel, command):
        if self.authenticated and channel.get_id() == self.connection.channel_id and command == COMMAND and self.connection.command is None:
            self.connection.command = command
            self.connection.command_event.set()
            return True
        return False

    def _deny(self, *unused):
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


class GssSSHPeer:
    def __init__(self):
        if paramiko.__version__ != "4.0.0":
            raise ValidationError("GSS SSH 对端必须使用独立 Paramiko 4.0.0")
        self.key = paramiko.RSAKey.generate(2048)
        self.fingerprint = "SHA256:" + base64.b64encode(hashlib.sha256(self.key.asbytes()).digest()).decode("ascii").rstrip("=")
        self._stop, self._lock = threading.Event(), threading.Lock()
        self._connections = []
        self._listener = self._listener_thread = None
        self.port = None
        self._closing_deadline = None
        self.counts = dict.fromkeys(("accepted", "authenticated", "completed", "rejected_auth", "capacity_refused", "worker_errors", "active_high_water"), 0)

    def increment(self, key):
        with self._lock:
            self.counts[key] += 1

    def start(self):
        if self._listener is not None or self._stop.is_set():
            raise ValidationError("GSS SSH 对端只能启动一次")
        self._listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self._listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        self._listener.bind(("127.0.0.1", 0))
        self.port = self._listener.getsockname()[1]
        self._listener.listen(MAX_ACTIVE_CONNECTIONS)
        self._listener.settimeout(0.05)
        self._listener_thread = threading.Thread(target=self._accept, name="gss-ssh-listener")
        self._listener_thread.start()
        return self

    def _accept(self):
        accepting = True
        while not self._stop.is_set():
            with self._lock:
                connections = list(self._connections)
            for connection in connections:
                if connection.worker.is_alive() and time.monotonic() >= connection.deadline:
                    self.release(connection)
            if not accepting:
                self._stop.wait(0.02)
                continue
            try:
                sock, address = self._listener.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            with self._lock:
                self.counts["accepted"] += 1
                total = self.counts["accepted"]
                active = sum(c.worker.is_alive() for c in self._connections)
                if self._stop.is_set() or address[0] != "127.0.0.1" or active >= MAX_ACTIVE_CONNECTIONS:
                    self.counts["capacity_refused"] += 1
                    sock.close()
                else:
                    connection = Connection(sock)
                    connection.worker = threading.Thread(target=self._serve, args=(connection,), name="gss-ssh-worker")
                    self._connections.append(connection)
                    self.counts["active_high_water"] = max(self.counts["active_high_water"], active + 1)
                    connection.worker.start()
            if total >= MAX_CONNECTIONS:
                self._listener.close()
                accepting = False

    def _serve(self, connection):
        try:
            with self._lock:
                if self._stop.is_set():
                    return
                transport = paramiko.Transport(connection.sock, default_window_size=WINDOW_BYTES,
                    default_max_packet_size=PACKET_BYTES, packetizer_class=BoundedPacketizer)
                connection.transport = transport
                transport.name = "gss-ssh-server-transport"
                transport.banner_timeout, transport.auth_timeout = 5, 8
                transport.add_server_key(self.key)
                ready = threading.Event()
                transport.start_server(event=ready, server=Server(self, connection))
            while not self._stop.is_set() and time.monotonic() < connection.deadline:
                if ready.wait(0.02):
                    break
            while not self._stop.is_set() and transport.is_active() and time.monotonic() < connection.deadline:
                channel = transport.accept(0.05)
                if channel is None:
                    continue
                connection.channel = channel
                channel.settimeout(0.2)
                while not self._stop.is_set() and transport.is_active() and not channel.closed and time.monotonic() < connection.deadline:
                    if connection.command_event.wait(0.02):
                        if len(OUTPUT) > MAX_OUTPUT_BYTES:
                            raise ValidationError("GSS SSH 固定输出超限")
                        channel.sendall(OUTPUT)
                        channel.send_exit_status(0)
                        channel.shutdown_write()
                        # 发送通道 CLOSE 后再结束整条传输；正式 Plink 以此确认正常退出。
                        channel.close()
                        self.increment("completed")
                        limit = min(connection.deadline, time.monotonic() + 1)
                        while transport.is_active() and time.monotonic() < limit:
                            self._stop.wait(0.01)
                        return
                break
        except (OSError, EOFError, paramiko.SSHException, ValidationError):
            # 具体拒绝证据由原生 GSS 提供方计数验证，不靠泛化网络错误判成功。
            pass
        except Exception:
            self.increment("worker_errors")
        finally:
            self.release(connection)

    @staticmethod
    def shutdown(sock):
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        sock.close()

    @classmethod
    def release(cls, connection):
        cls.shutdown(connection.sock)
        if connection.transport:
            connection.transport.close()
        if connection.channel:
            connection.channel.close()

    def closing_deadline(self):
        if self._closing_deadline is None:
            self._closing_deadline = time.monotonic() + SHUTDOWN_SECONDS
        return self._closing_deadline

    def close(self):
        deadline = self.closing_deadline()
        self._stop.set()
        if self._listener:
            self.shutdown(self._listener)
        with self._lock:
            connections = list(self._connections)
        for connection in connections:
            self.release(connection)
        threads = ([self._listener_thread] if self._listener_thread else [])
        threads += [c.worker for c in connections]
        threads += [c.transport for c in connections if c.transport]
        for thread in threads:
            if thread is not threading.current_thread() and thread.ident is not None:
                thread.join(max(0, deadline - time.monotonic()))
        if any(t.is_alive() for t in threads):
            raise PeerShutdownError("GSS SSH 对端关闭后仍有存活线程")

    def report(self):
        with self._lock:
            transports = [c.transport for c in self._connections if c.transport]
            return {**self.counts, "closed": self._stop.is_set(),
                    "active_connections": sum(c.worker.is_alive() for c in self._connections),
                    "live_threads": sum(t.is_alive() for t in [self._listener_thread, *transports] if t)}

    def __enter__(self):
        try:
            return self.start()
        except BaseException:
            self.close()
            raise

    def __exit__(self, *unused):
        self.close()
