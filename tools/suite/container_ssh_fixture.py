#!/usr/bin/env python3
"""以断网只读容器提供专用账户的 SSH 命令、SCP、SFTP 和回环转发对端。"""

import argparse
import json
import os
from pathlib import Path
import platform
import shlex
import shutil
import socket
import stat
import subprocess
import sys
import threading
import time
import uuid

try:
    from .isolation import IsolationEnvironment, IsolationError, TEMPORARY_ROOT, private_directory, require_linux
    from .ssh_fixture import FixtureUnavailable, _quote
except ImportError:
    from isolation import IsolationEnvironment, IsolationError, TEMPORARY_ROOT, private_directory, require_linux
    from ssh_fixture import FixtureUnavailable, _quote


DEFAULT_CONTAINER_PARENT = TEMPORARY_ROOT / "suite-container-fixture"
LABEL = "org.putty-suite.ssh-fixture"
SYSTEM_PATH = "/usr/bin:/usr/sbin:/bin:/sbin"
SYSTEM_DIRECTORIES = ("/usr", "/bin", "/sbin", "/lib", "/lib64")


def _default_image(release):
    if release.get("ID") == "arch":
        return "archlinux:base"
    if release.get("ID") == "ubuntu" and release.get("VERSION_ID") in ("22.04", "24.04"):
        return "ubuntu:" + release["VERSION_ID"]
    raise FixtureUnavailable("宿主系统没有默认匹配镜像；请显式传入 image= 或 --image，夹具不会拉取镜像")


def _system_mounts():
    """保留宿主程序和动态加载器布局；只接受系统目录及系统链接。"""
    mounts = []
    for name in SYSTEM_DIRECTORIES:
        path = Path(name)
        try:
            information = path.stat()
        except FileNotFoundError:
            if name == "/usr":
                raise FixtureUnavailable("宿主缺少 /usr 系统运行目录") from None
            continue
        source = path.resolve(strict=True)
        if not stat.S_ISDIR(information.st_mode) or not (source == path or source.is_relative_to("/usr")):
            raise FixtureUnavailable(f"不支持的系统目录布局：{path}；拒绝绑定其它宿主位置")
        mounts.append((source, path))
    cache = Path("/etc/ld.so.cache")
    try:
        information = cache.lstat()
    except FileNotFoundError:
        pass
    else:
        if not stat.S_ISREG(information.st_mode):
            raise FixtureUnavailable("系统动态库缓存不是普通文件")
        mounts.append((cache, cache))
    return mounts

# 仅在本次挂载根内创建此辅助脚本；relay 不经 shell，不向协议流写日志。
HELPER = r'''"""容器回环字节桥和转发测试回显对端。"""
import argparse
import os
import selectors
import socket
import socketserver

parser = argparse.ArgumentParser()
parser.add_argument("mode", choices=("relay", "echo"))
parser.add_argument("--port", type=int, default=22222)
parser.add_argument("--ready")
args = parser.parse_args()
if args.mode == "echo":
    class Echo(socketserver.BaseRequestHandler):
        def handle(self):
            while True:
                data = self.request.recv(65536)
                if not data:
                    return
                self.request.sendall(data)
    class Server(socketserver.ThreadingTCPServer):
        daemon_threads = True
    with Server(("127.0.0.1", 0), Echo) as server:
        descriptor = os.open(args.ready, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w") as output:
            output.write(str(server.server_address[1]))
        server.serve_forever()
else:
    with socket.create_connection(("127.0.0.1", args.port), timeout=5) as peer:
        peer.settimeout(None)
        with selectors.DefaultSelector() as selector:
            selector.register(0, selectors.EVENT_READ)
            selector.register(peer, selectors.EVENT_READ)
            while True:
                for key, _ in selector.select():
                    if key.fileobj == 0:
                        data = os.read(0, 65536)
                        if not data:
                            peer.shutdown(socket.SHUT_WR)
                            selector.unregister(0)
                        else:
                            peer.sendall(data)
                    else:
                        data = peer.recv(65536)
                        if not data:
                            raise SystemExit(0)
                        while data:
                            data = data[os.write(1, data):]
'''


class ContainerSSHServer:
    """只挂本次临时根和只读系统程序/库；不绑定宿主个人目录。"""

    host = "127.0.0.1"
    user = "suite"
    container_port = 22222

    def __init__(self, environment, *, image=None, docker=None):
        require_linux()
        if os.geteuid() == 0:
            raise FixtureUnavailable("此夹具要求普通用户 UID；不使用容器 root 账户认证")
        environment.validate()
        if any(character in str(environment.root) for character in (",", "\n", "\r")):
            raise IsolationError("Docker 绑定路径不能包含逗号或换行")
        self.environment = environment
        self.docker = docker or shutil.which("docker", path=SYSTEM_PATH)
        self.ssh = shutil.which("ssh", path=SYSTEM_PATH)
        self.scp = shutil.which("scp", path=SYSTEM_PATH)
        self.sftp = shutil.which("sftp", path=SYSTEM_PATH)
        self.ssh_keygen = shutil.which("ssh-keygen", path=SYSTEM_PATH)
        # 固定系统 PATH，不将个人 venv/自定义 bin 挂入容器；Ubuntu sshd 位于 sbin。
        self.sshd = shutil.which("sshd", path=SYSTEM_PATH)
        self.python = shutil.which("python3", path=SYSTEM_PATH)
        self.shell = shutil.which("bash", path=SYSTEM_PATH)
        self.nologin = shutil.which("nologin", path=SYSTEM_PATH)
        if not all((self.docker, self.ssh, self.scp, self.sftp, self.ssh_keygen,
                    self.sshd, self.python, self.shell, self.nologin)):
            raise FixtureUnavailable("缺少 Docker、OpenSSH 服务/客户端或系统 Python/bash/nologin")
        try:
            self.host_release = platform.freedesktop_os_release()
        except OSError:
            self.host_release = {}
        self.image = image if image is not None else _default_image(self.host_release)
        if not self.image:
            raise FixtureUnavailable("显式镜像名称不能为空")
        self.system_mounts = _system_mounts()
        self.image_id = None
        self.port = None
        self.private_key = environment.root / "ssh" / "container_client_key"
        self.host_key = environment.root / "ssh" / "container_host_key"
        self.client_config = environment.root / "ssh" / "container_client_config"
        self.remote_root = environment.root / "transfer"
        self.remote_home = environment.root / "container" / "home"
        self.host_key_fingerprint = None
        self._token = uuid.uuid4().hex
        self._name = "putty-suite-ssh-" + self._token[:12]
        self.container_id = None
        self._create_attempted = False
        self._prepared = False
        self._closed = False
        self._listener = None
        self._thread = None
        self._stop = threading.Event()
        self._relay_processes = []
        self._relay_lock = threading.Lock()
        self._proxy_log = None
        self._proxy_error = None
        self._echo_port = None

    def __enter__(self):
        return self.start()

    def __exit__(self, *unused):
        self.close()

    def _docker_environment(self):
        child = self.environment.environment()
        for name in list(child):
            if name.startswith("DOCKER_"):
                child.pop(name)
        child["PATH"] = SYSTEM_PATH
        return child

    def _docker_prefix(self):
        return [str(self.docker), "--host", "unix:///var/run/docker.sock", "--config",
                str(self.environment.root / "container" / "docker")]

    def _docker(self, *arguments, check=True, timeout=30):
        return subprocess.run(
            [*self._docker_prefix(), *map(str, arguments)], cwd=self.environment.root,
            env=self._docker_environment(), capture_output=True, check=check,
            timeout=timeout, umask=0o077,
        )

    def _generate_key(self, path):
        self.environment.run(
            [self.ssh_keygen, "-q", "-t", "ed25519", "-N", "", "-C", "suite-container",
             "-f", path], check=True)
        self.environment._validate_file(path)
        self.environment._validate_file(Path(str(path) + ".pub"))

    def _prepare(self):
        self.environment.probe()
        for relative in ("container", "container/docker", "container/etc", "container/home"):
            private_directory(self.environment.root / relative)
        self.environment.write_private("container/docker/config.json", b"{}\n")
        uid, gid = os.geteuid(), os.getegid()
        files = {
            "passwd": (f"root:x:0:0:root:/root:{self.nologin}\n"
                       f"suite:x:{uid}:{gid}:suite:{self.remote_home}:{self.shell}\n"
                       f"sshd:x:74:74:sshd:/var/empty:{self.nologin}\n"),
            "group": f"root:x:0:\nsuite:x:{gid}:\nsshd:x:74:\n",
            # 密码认证始终禁用；NP 仅使专用账户不会被 OpenSSH 当成锁定账户。
            "shadow": "root:!:20000:0:99999:7:::\nsuite:NP:20000:0:99999:7:::\nsshd:!:20000:0:99999:7:::\n",
            "nsswitch.conf": "passwd: files\ngroup: files\nshadow: files\nhosts: files\n",
        }
        for name, content in files.items():
            self.environment.write_private("container/etc/" + name, content.encode("utf-8"))
        self.environment.write_private("container/relay.py", HELPER.encode("utf-8"))
        self._generate_key(self.host_key)
        self._generate_key(self.private_key)
        authorized_keys = self.environment.write_private(
            "ssh/container_authorized_keys", Path(str(self.private_key) + ".pub").read_bytes())
        configuration = [
            f"Port {self.container_port}", "ListenAddress 127.0.0.1", "AddressFamily inet",
            f"HostKey {_quote(self.host_key)}", f"AuthorizedKeysFile {_quote(authorized_keys)}",
            f"PidFile {_quote(self.environment.root / 'runtime/container-sshd.pid')}",
            "AuthorizedKeysCommand none", "StrictModes yes", "PubkeyAuthentication yes",
            "AuthenticationMethods publickey", "PasswordAuthentication no",
            "KbdInteractiveAuthentication no", "UsePAM no", "PermitRootLogin no", "AllowUsers suite",
            "PermitUserEnvironment no", "PermitUserRC no", "HostbasedAuthentication no",
            "IgnoreRhosts yes", "X11Forwarding no", "AllowAgentForwarding no",
            "AllowTcpForwarding yes", "AllowStreamLocalForwarding no", "PermitTunnel no",
            "GatewayPorts no", "PermitOpen 127.0.0.1:*", "PermitListen 127.0.0.1:*",
            "PrintMotd no", "PrintLastLog no", "UseDNS no", "LogLevel ERROR", "MaxSessions 4",
            f"Subsystem sftp internal-sftp -d {_quote(self.remote_root)}",
            # sshd 对此选项取首次定义，所有变量必须放在同一条 SetEnv 中。
            f"SetEnv TMPDIR={_quote(self.environment.root / 'temp')} PATH={_quote(SYSTEM_PATH)}",
        ]
        self.environment.write_private("ssh/container_sshd_config", ("\n".join(configuration) + "\n").encode("utf-8"))
        self.environment.write_private("logs/container-sshd.log", b"")
        self.environment.write_private("logs/container-relay.log", b"")

    def _create(self):
        information = self._docker("image", "inspect", self.image)
        image = json.loads(information.stdout)[0]
        if image.get("Os") != "linux" or image.get("Architecture") != "amd64":
            raise FixtureUnavailable("该开发夹具只验证本机 Linux x64 镜像")
        self.image_id = image["Id"]
        root = self.environment.root
        arguments = [
            "create", "--name", self._name, "--label", LABEL + "=" + self._token,
            "--pull=never", "--read-only", "--network", "none", "--ipc", "none",
            "--cap-drop=ALL", "--security-opt=no-new-privileges",
            "--user", f"{os.geteuid()}:{os.getegid()}", "--log-driver=none",
            "--pids-limit", "64", "--memory", "256m", "--cpus", "1",
            "--env", "PATH=" + SYSTEM_PATH, "--env", f"TMPDIR={root / 'temp'}",
            "--mount", f"type=bind,src={root},dst={root}",
            "--mount", f"type=bind,src={root / 'temp'},dst=/tmp",
            "--mount", f"type=bind,src={root / 'temp'},dst=/var/tmp",
            "--mount", f"type=bind,src={root / 'runtime'},dst=/run",
        ]
        for source, destination in self.system_mounts:
            arguments.extend(["--mount", f"type=bind,src={source},dst={destination},readonly"])
        for name in ("passwd", "group", "shadow", "nsswitch.conf"):
            arguments.extend(["--mount", f"type=bind,src={root / 'container/etc' / name},dst=/etc/{name},readonly"])
        arguments.extend([
            "--entrypoint", self.sshd, self.image_id, "-D", "-e", "-f",
            str(root / "ssh/container_sshd_config"), "-E", str(root / "logs/container-sshd.log"),
        ])
        self._create_attempted = True
        result = self._docker(*arguments)
        self.container_id = result.stdout.decode("ascii").strip()
        self._docker("start", self.container_id)

    def _client_configuration(self):
        public = Path(str(self.host_key) + ".pub").read_text(encoding="ascii").split()
        known_hosts = self.environment.write_private(
            "ssh/container_known_hosts",
            f"[{self.host}]:{self.port} {public[0]} {public[1]}\n".encode("ascii"))
        empty = self.environment.write_private("ssh/container_empty_known_hosts", b"")
        values = [
            "Host *", "BatchMode yes", "IdentitiesOnly yes", "IdentityAgent none",
            f"IdentityFile {_quote(self.private_key)}", "PreferredAuthentications publickey",
            "PasswordAuthentication no", "KbdInteractiveAuthentication no", "StrictHostKeyChecking yes",
            f"UserKnownHostsFile {_quote(known_hosts)}", f"GlobalKnownHostsFile {_quote(empty)}",
            "UpdateHostKeys no", "ControlMaster no", "ControlPath none", "ConnectionAttempts 1",
            "ConnectTimeout 8", "LogLevel ERROR",
        ]
        self.environment.write_private("ssh/container_client_config", ("\n".join(values) + "\n").encode("utf-8"))
        result = self.environment.run(
            [self.ssh_keygen, "-l", "-f", str(self.host_key) + ".pub", "-E", "sha256"], check=True)
        self.host_key_fingerprint = result.stdout.decode("ascii").split()[1]

    def _accept(self):
        try:
            while not self._stop.is_set():
                try:
                    connection, _ = self._listener.accept()
                except TimeoutError:
                    continue
                except OSError:
                    if self._stop.is_set():
                        return
                    raise
                with connection:
                    with self._relay_lock:
                        active = sum(process.poll() is None for process in self._relay_processes)
                    if active >= 16:
                        continue
                    process = subprocess.Popen(
                        [*self._docker_prefix(), "exec", "-i", self.container_id, self.python, "-B",
                         str(self.environment.root / "container/relay.py"), "relay", "--port", str(self.container_port)],
                        cwd=self.environment.root, env=self._docker_environment(), stdin=connection,
                        stdout=connection, stderr=self._proxy_log, start_new_session=True, umask=0o077,
                    )
                    with self._relay_lock:
                        self._relay_processes.append(process)
        except BaseException as error:
            self._proxy_error = error

    def start(self):
        if self._closed or self._prepared:
            raise IsolationError("容器 SSH 夹具不能重复启动")
        self._prepared = True
        try:
            self._prepare()
            self._create()
            self._listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._listener.bind((self.host, 0))
            self._listener.listen(16)
            self._listener.settimeout(0.2)
            self.port = self._listener.getsockname()[1]
            self._client_configuration()
            self._proxy_log = open(self.environment.root / "logs/container-relay.log", "ab", buffering=0)
            self._thread = threading.Thread(target=self._accept, daemon=True)
            self._thread.start()
            result = self.run_exec("/usr/bin/printf suite-ready", timeout=15)
            if result.returncode or result.stdout != b"suite-ready" or result.stderr:
                raise RuntimeError("容器专用账户的真实 SSH 启动探针失败")
            return self
        except BaseException:
            self.close()
            raise

    def ssh_arguments(self, command=None):
        if self._closed or self.container_id is None or self.port is None:
            raise IsolationError("容器 SSH 夹具没有运行")
        if self._proxy_error is not None:
            raise RuntimeError("本地 SSH 字节代理失败") from self._proxy_error
        arguments = [self.ssh, "-F", str(self.client_config), "-p", str(self.port), f"{self.user}@{self.host}"]
        if command is not None:
            arguments.append(command)
        return arguments

    def run_exec(self, command, *, timeout=30, input=None):
        if not isinstance(command, str):
            raise IsolationError("远端命令必须为显式字符串")
        return self.environment.run(self.ssh_arguments(command), timeout=timeout, input=input)

    def run_sftp(self, batch, *, timeout=30):
        self.ssh_arguments()
        if isinstance(batch, str):
            batch = batch.encode("utf-8")
        return self.environment.run(
            [self.sftp, "-F", self.client_config, "-P", str(self.port), "-b", "-", f"{self.user}@{self.host}"],
            timeout=timeout, input=batch)

    def start_echo_server(self):
        self.ssh_arguments()
        if self._echo_port is not None:
            return self._echo_port
        ready = self.environment.root / "runtime/container-echo-ready"
        self._docker("exec", "--detach", self.container_id, self.python, "-B",
                     self.environment.root / "container/relay.py", "echo", "--ready", ready)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            try:
                value = ready.read_text(encoding="ascii")
                if value:
                    self._echo_port = int(value)
                    return self._echo_port
            except FileNotFoundError:
                pass
            time.sleep(0.02)
        raise RuntimeError("容器回环回显对端未就绪")

    def isolation_report(self):
        self.ssh_arguments()
        value = json.loads(self._docker("inspect", self.container_id).stdout)[0]
        configuration = value["HostConfig"]
        return {"readonly_root": configuration["ReadonlyRootfs"],
                "network_mode": configuration["NetworkMode"], "cap_drop": configuration["CapDrop"],
                "privileged": configuration["Privileged"], "user": value["Config"]["User"],
                "log_driver": configuration["LogConfig"]["Type"],
                "security_options": configuration["SecurityOpt"], "mounts": value["Mounts"],
                "image_id": self.image_id, "image": self.image, "sshd": self.sshd,
                "system_mounts": [{"source": str(source), "destination": str(destination)}
                                  for source, destination in self.system_mounts],
                "host_system": {key: self.host_release[key] for key in ("ID", "VERSION_ID")
                                if key in self.host_release}}

    def _recover_created_container(self):
        if self.container_id is None and self._create_attempted:
            result = self._docker("inspect", self._name, check=False)
            if result.returncode == 0:
                value = json.loads(result.stdout)[0]
                if value["Config"].get("Labels", {}).get(LABEL) != self._token:
                    raise RuntimeError("同名容器不属于本次夹具，拒绝清理")
                self.container_id = value["Id"]

    def close(self):
        if self._closed:
            return
        self._stop.set()
        try:
            if self._listener is not None:
                self._listener.close()
            if self._thread is not None:
                self._thread.join(timeout=5)
                if self._thread.is_alive():
                    raise RuntimeError("本地 SSH 代理线程未结束")
        finally:
            try:
                self._recover_created_container()
                if self.container_id is not None:
                    self._docker("rm", "--force", self.container_id)
            finally:
                with self._relay_lock:
                    processes = list(self._relay_processes)
                for process in processes:
                    try:
                        process.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        process.terminate()
                        try:
                            process.wait(timeout=3)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.wait(timeout=3)
                if self._proxy_log is not None:
                    self._proxy_log.close()
                self._closed = True


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", help="显式指定本机已有镜像；默认匹配 Arch/Ubuntu 22.04/24.04，从不拉取")
    args = parser.parse_args()
    try:
        with IsolationEnvironment(DEFAULT_CONTAINER_PARENT) as environment:
            with ContainerSSHServer(environment, image=args.image) as server:
                code = "import os,sys; os.write(1,b'out\\x00\\xff'); os.write(2,b'err\\xfe'); sys.exit(7)"
                result = server.run_exec("/usr/bin/python3 -B -c " + shlex.quote(code))
                if result.returncode != 7 or result.stdout != b"out\x00\xff" or result.stderr != b"err\xfe":
                    raise RuntimeError("SSH 二进制标准流或退出码不匹配")
                report = server.isolation_report()
                print(json.dumps({"kind": "container-openssh", "passed": True,
                                  "exec_verified": True, "network_mode": report["network_mode"],
                                  "readonly_root": report["readonly_root"]}, ensure_ascii=False))
    except (IsolationError, FixtureUnavailable, OSError, subprocess.SubprocessError, RuntimeError) as error:
        parser.exit(1, f"容器 SSH 夹具失败：{error}\n")


if __name__ == "__main__":
    main()
