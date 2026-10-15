#!/usr/bin/env python3
"""在隔离目录内运行只接受测试公钥的本地 OpenSSH/SFTP 对端。"""

import argparse
import json
import os
from pathlib import Path
import re
import select
import shutil
import signal
import socket
import subprocess
import sys
import time

try:
    from .isolation import IsolationEnvironment, IsolationError, require_linux
except ImportError:
    from isolation import IsolationEnvironment, IsolationError, require_linux


class FixtureUnavailable(RuntimeError):
    """当前系统没有运行夹具所需的工具或权限；不能记为互操作通过。"""


def _quote(value):
    value = str(value)
    if "\n" in value or "\r" in value:
        raise IsolationError("OpenSSH 配置值不能包含换行")
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


class SSHServer:
    """普通用户 SFTP 服务；不进入登录 shell，不改系统服务或账户。"""

    host = "127.0.0.1"

    def __init__(self, environment, *, sshd=None, ssh_keygen=None, sftp=None):
        require_linux()
        import pwd
        if not hasattr(os, "pidfd_open") or not hasattr(signal, "pidfd_send_signal"):
            raise FixtureUnavailable("当前 Python 缺少 Linux pidfd，无法可靠清理独立 SSH 会话")
        environment.validate()
        self.environment = environment
        self.sshd = sshd or shutil.which("sshd")
        self.ssh_keygen = ssh_keygen or shutil.which("ssh-keygen")
        self.sftp = sftp or shutil.which("sftp")
        if not all((self.sshd, self.ssh_keygen, self.sftp)):
            raise FixtureUnavailable("缺少 sshd、ssh-keygen 或 sftp；未运行 SSH 互操作")
        try:
            self.user = pwd.getpwuid(os.geteuid()).pw_name
        except KeyError as error:
            raise FixtureUnavailable("当前 UID 没有系统账户记录；普通用户 SSH 夹具不能运行") from error
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", self.user):
            raise FixtureUnavailable("当前用户名无法安全写入 OpenSSH AllowUsers")
        self.process = None
        self._log_handle = None
        self._process_handle = None
        self._closed = False
        self._prepared = False
        self.port = None
        self.private_key = environment.root / "ssh" / "client_key"
        self.host_key = environment.root / "ssh" / "host_key"
        self.config = environment.root / "ssh" / "sshd_config"
        self.client_config = environment.root / "ssh" / "client_config"
        self.log_path = environment.root / "logs" / "sshd.log"
        self.transfer_root = environment.root / "transfer"
        self.host_key_fingerprint = None

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *unused):
        self.close()

    def _generate_key(self, path):
        result = subprocess.run(
            [str(self.ssh_keygen), "-q", "-t", "ed25519", "-N", "", "-C", "suite-fixture",
             "-f", str(path)], cwd=self.environment.root,
            env=self.environment.environment(), capture_output=True, timeout=15, umask=0o077,
        )
        if result.returncode:
            raise RuntimeError("测试密钥生成失败：" + result.stderr.decode("utf-8", errors="replace"))
        self.environment._validate_file(path)
        self.environment._validate_file(Path(str(path) + ".pub"))

    def _prepare(self):
        self.environment.probe()
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            try:
                listener.bind((self.host, 0))
            except PermissionError as error:
                raise FixtureUnavailable("当前执行环境禁止本地 TCP 监听；未运行 SSH 互操作") from error
            self.port = listener.getsockname()[1]
        self._generate_key(self.host_key)
        self._generate_key(self.private_key)
        authorized_keys = self.environment.write_private(
            "ssh/authorized_keys", Path(str(self.private_key) + ".pub").read_bytes())
        host_public = Path(str(self.host_key) + ".pub").read_text(encoding="ascii").split()
        known_hosts = self.environment.write_private(
            "ssh/known_hosts", f"[{self.host}]:{self.port} {host_public[0]} {host_public[1]}\n".encode("ascii"))
        empty_known_hosts = self.environment.write_private("ssh/empty_known_hosts", b"")
        fingerprint = self.environment.run(
            [self.ssh_keygen, "-l", "-f", str(self.host_key) + ".pub", "-E", "sha256"], check=True)
        self.host_key_fingerprint = fingerprint.stdout.decode("ascii").split()[1]
        # internal-sftp 在进程内处理请求；不用当前账户的登录 shell 或个人 RC。
        configuration = [
            f"Port {self.port}", f"ListenAddress {self.host}", "AddressFamily inet",
            f"HostKey {_quote(self.host_key)}", f"PidFile {_quote(self.environment.root / 'ssh/sshd.pid')}",
            f"AuthorizedKeysFile {_quote(authorized_keys)}", "AuthorizedKeysCommand none",
            "StrictModes yes", "PubkeyAuthentication yes", "AuthenticationMethods publickey",
            "PasswordAuthentication no", "KbdInteractiveAuthentication no", "UsePAM no",
            "PermitRootLogin no", f"AllowUsers {self.user}", "PermitUserEnvironment no",
            "PermitUserRC no", "HostbasedAuthentication no", "IgnoreRhosts yes",
            "X11Forwarding no", "AllowAgentForwarding no", "AllowTcpForwarding no",
            "AllowStreamLocalForwarding no", "PermitTunnel no", "PrintMotd no", "PrintLastLog no",
            "UseDNS no", "LogLevel ERROR", "LoginGraceTime 10", "MaxSessions 4",
            "Subsystem sftp internal-sftp",
            f"ForceCommand internal-sftp -d {_quote(self.transfer_root)}",
            f"SetEnv TMPDIR={_quote(self.environment.root / 'temp')}",
        ]
        self.environment.write_private("ssh/sshd_config", ("\n".join(configuration) + "\n").encode("utf-8"))
        client_configuration = [
            "Host *", "BatchMode yes", "IdentitiesOnly yes", "IdentityAgent none",
            f"IdentityFile {_quote(self.private_key)}", "PreferredAuthentications publickey",
            "PasswordAuthentication no", "KbdInteractiveAuthentication no", "StrictHostKeyChecking yes",
            f"UserKnownHostsFile {_quote(known_hosts)}", f"GlobalKnownHostsFile {_quote(empty_known_hosts)}",
            "UpdateHostKeys no", "ControlMaster no", "ControlPath none", "ConnectionAttempts 1",
            "ConnectTimeout 5", "LogLevel ERROR",
        ]
        self.environment.write_private("ssh/client_config", ("\n".join(client_configuration) + "\n").encode("utf-8"))
        self.environment.write_private("logs/sshd.log", b"")
        self._prepared = True

    def start(self):
        if self._closed or self._prepared:
            raise IsolationError("SSH 夹具实例不能重新启动")
        try:
            self._prepare()
            validation = self.environment.run([self.sshd, "-t", "-f", self.config])
            if validation.returncode:
                self.environment.write_private("logs/sshd-config-error.log", validation.stderr)
                raise FixtureUnavailable("当前 sshd 不能使用普通用户配置："
                                         + validation.stderr.decode("utf-8", errors="replace").strip())
            self._log_handle = open(self.log_path, "ab", buffering=0)
            self.process = subprocess.Popen(
                [str(self.sshd), "-D", "-e", "-f", str(self.config)],
                cwd=self.environment.root, env=self.environment.environment(), stdin=subprocess.DEVNULL,
                stdout=self._log_handle, stderr=subprocess.STDOUT, start_new_session=True, umask=0o077,
            )
            self._process_handle = os.pidfd_open(self.process.pid)
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline:
                if self.process.poll() is not None:
                    raise FixtureUnavailable("本地 sshd 提前退出："
                                             + self.log_path.read_text(encoding="utf-8", errors="replace").strip())
                try:
                    with socket.create_connection((self.host, self.port), timeout=0.2) as connection:
                        connection.settimeout(0.5)
                        banner = connection.recv(256)
                        if banner.startswith(b"SSH-2.0-"):
                            return self
                except OSError:
                    pass
                time.sleep(0.04)
            raise FixtureUnavailable("本地 sshd 未在时限内就绪；诊断位于夹具 logs/")
        except BaseException:
            self.close()
            raise

    def run_sftp(self, batch, *, timeout=20):
        if self._closed or self.process is None or self.process.poll() is not None:
            raise IsolationError("SSH 夹具没有运行")
        if isinstance(batch, str):
            batch = batch.encode("utf-8")
        return self.environment.run(
            [self.sftp, "-F", self.client_config, "-P", str(self.port), "-b", "-",
             f"{self.user}@{self.host}"], timeout=timeout, input=batch)

    @staticmethod
    def _process_state(pid):
        # stat 的 comm 可含空格或括号，从最后一个右括号之后解析固定字段。
        fields = Path(f"/proc/{pid}/stat").read_text(encoding="ascii").rsplit(")", 1)[1].split()
        return fields[0], int(fields[1])

    def _terminate_tree(self):
        handles = []
        owned = set()
        pending = [(self.process.pid, self._process_handle, None)]
        try:
            while pending:
                pid, descriptor, expected_parent = pending.pop()
                if select.select([descriptor], [], [], 0)[0]:
                    continue
                try:
                    _, parent = self._process_state(pid)
                    if expected_parent is not None and parent != expected_parent:
                        continue
                    # 先停止父进程，再枚举并停止子进程，防止清理过程中继续接受连接/派生。
                    signal.pidfd_send_signal(descriptor, signal.SIGSTOP)
                    handles.append(descriptor)
                    deadline = time.monotonic() + 1
                    while not select.select([descriptor], [], [], 0)[0]:
                        state, _ = self._process_state(pid)
                        if state in ("T", "t", "Z"):
                            break
                        if time.monotonic() >= deadline:
                            raise RuntimeError("无法停止 SSH 夹具子进程")
                        time.sleep(0.005)
                    children_path = Path(f"/proc/{pid}/task/{pid}/children")
                    for child in children_path.read_text(encoding="ascii").split():
                        try:
                            child_pid = int(child)
                            child_descriptor = os.pidfd_open(child_pid)
                            owned.add(child_descriptor)
                            pending.append((child_pid, child_descriptor, pid))
                        except ProcessLookupError:
                            pass
                except (ProcessLookupError, FileNotFoundError):
                    pass
        finally:
            try:
                # 即使枚举失败，也必须唤醒并结束已经停止的进程。
                # 认证后的会话可脱离进程组；pidfd 只指向已确认的本夹具子树，避免 PID 复用误杀。
                for descriptor in reversed(handles):
                    try:
                        signal.pidfd_send_signal(descriptor, signal.SIGTERM)
                        signal.pidfd_send_signal(descriptor, signal.SIGCONT)
                    except ProcessLookupError:
                        pass
                deadline = time.monotonic() + 5
                remaining = set(handles)
                while remaining and time.monotonic() < deadline:
                    completed, _, _ = select.select(list(remaining), [], [], max(0, deadline - time.monotonic()))
                    remaining.difference_update(completed)
                for descriptor in remaining:
                    try:
                        signal.pidfd_send_signal(descriptor, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                deadline = time.monotonic() + 2
                while remaining and time.monotonic() < deadline:
                    completed, _, _ = select.select(list(remaining), [], [], max(0, deadline - time.monotonic()))
                    remaining.difference_update(completed)
                if remaining:
                    raise RuntimeError("SSH 夹具子进程未在清理时限内结束")
            finally:
                for descriptor in owned:
                    os.close(descriptor)

    def close(self):
        if self._closed:
            return
        try:
            if self.process is not None:
                if self._process_handle is not None:
                    self._terminate_tree()
                elif self.process.poll() is None:
                    self.process.terminate()
                self.process.wait(timeout=5)
        finally:
            if self._process_handle is not None:
                os.close(self._process_handle)
            if self._log_handle is not None:
                self._log_handle.close()
            self._closed = True


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    try:
        with IsolationEnvironment() as environment:
            with SSHServer(environment) as server:
                result = server.run_sftp("pwd\nquit\n")
                if result.returncode:
                    raise RuntimeError("真实 SFTP 认证或请求失败")
                print(json.dumps({"kind": "openssh-sftp", "passed": True,
                                  "loopback_only": True, "shell_commands_verified": False}, ensure_ascii=False))
    except (IsolationError, FixtureUnavailable, OSError, subprocess.SubprocessError, RuntimeError) as error:
        parser.exit(1, f"SSH 夹具检查失败：{error}\n")


if __name__ == "__main__":
    main()
