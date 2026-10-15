"""内部子进程协议：每个认证场景首次加载前已由 CreateProcess 固定环境。"""
import argparse
import json
import logging
import os
from pathlib import Path
import socket
import sys
import threading
import time

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from tools.suite.windows.gss_ssh.dependencies import verify_sources
from tools.suite.windows.gss_ssh.native import NativeGss, GssError, REQUIRED_FLAGS, ssh_mic_payload
from tools.suite.windows.gss_ssh.paramiko_adapter import Provider
from tools.suite.windows.gss_kdc.process import run_bounded
from tools.suite.windows.support import ValidationError

SCENARIOS = ("token-mic", "ssh", "missing-cache", "wrong-user", "tampered-mic", "linux-plink")


class Deadline:
    """包括原生清理和最后 stdout 提交的硬期限，超时不发布成功。"""
    def __init__(self, seconds):
        self.deadline = time.monotonic() + seconds
        self.finished = threading.Event()
        self.worker = threading.Thread(target=self.watch, daemon=True, name="gss-ssh-deadline")
        self.worker.start()

    def watch(self):
        if not self.finished.wait(max(0, self.deadline - time.monotonic())):
            os._exit(124)

    def complete(self):
        if time.monotonic() >= self.deadline:
            os._exit(124)
        self.finished.set()


def token_mic(api):
    initiator = api.context(acceptor=False)
    acceptor = api.context(acceptor=True)
    try:
        token = initiator.step()
        exchanges = 0
        while exchanges < 8:
            exchanges += 1
            response = acceptor.step(token)
            if response:
                token = initiator.step(response)
            else:
                token = b""
            if initiator.complete and acceptor.complete:
                if token:
                    raise ValidationError("GSS 完成后仍有未消费 token")
                break
        if not initiator.complete or not acceptor.complete:
            raise ValidationError("GSS 相互认证未完成")
        message = ssh_mic_payload(bytes(range(32)))
        mic = initiator.mic(message)
        acceptor.verify(message, mic)
        bad = mic[:-1] + bytes([mic[-1] ^ 1])
        try:
            acceptor.verify(message, bad)
        except GssError as error:
            rejection = {"major": error.major, "minor": error.minor}
        else:
            raise ValidationError("真实 GSS 接受端错误接受篡改 MIC")
        return {"mutual_authentication_verified": True, "mic_verified": True,
                "tampered_mic_rejected": True, "delegation_not_requested_or_returned": True,
                "initiator_flags": initiator.flags, "acceptor_flags": acceptor.flags,
                "exchanges": exchanges, "mic_rejection": rejection}
    finally:
        try:
            acceptor.close()
        finally:
            initiator.close()


def ssh(api, scenario, plink=None):
    # 必须在源码核验和 MIT 首次加载后才导入固定补丁的 Paramiko。
    import paramiko
    from tools.suite.windows.gss_ssh.peer import GssSSHPeer, BoundedPacketizer, PeerShutdownError, COMMAND, OUTPUT
    negative = scenario in ("missing-cache", "wrong-user", "tampered-mic")
    provider = Provider(api, tamper_client_mic=scenario == "tampered-mic")
    peer, client, sock = GssSSHPeer(), None, None
    rejected = False
    try:
        with peer:
            if scenario == "linux-plink":
                if sys.platform == "win32" or plink is None:
                    raise ValidationError("Windows 原版 CLI 尚无 hive 隔离，不允许运行")
                output = run_bounded([plink, "-batch", "-v", "-noagent", "-noshare", "-P", str(peer.port),
                    "-l", "suite", "-hostkey", peer.fingerprint, "-T", "localhost", COMMAND.decode("ascii")],
                    cwd=api.root, environment=dict(os.environ), timeout=20, output_limit=16384)
                if output != OUTPUT:
                    raise ValidationError("Linux 正式 Plink 没有完成固定 GSS 命令")
            else:
                sock = socket.create_connection(("127.0.0.1", peer.port), timeout=5)
                client = paramiko.Transport(sock, packetizer_class=BoundedPacketizer)
                client.name = "gss-ssh-client-transport"
                client.banner_timeout, client.auth_timeout = 5, 8
                try:
                    client.start_client(timeout=5)
                    if client.get_remote_server_key().asbytes() != peer.key.asbytes():
                        raise ValidationError("GSS SSH 主机公钥不符")
                    try:
                        client.auth_gssapi_with_mic("wrong" if scenario == "wrong-user" else "suite", "localhost", False)
                    except (GssError, paramiko.AuthenticationException, paramiko.SSHException, EOFError):
                        if not negative:
                            raise
                        rejected = True
                    if negative:
                        if not rejected or client.is_authenticated():
                            raise ValidationError("GSS SSH 拒绝场景错误取得认证")
                    else:
                        channel = client.open_session(timeout=5)
                        channel.settimeout(5)
                        try:
                            channel.exec_command(COMMAND)
                            result = bytearray()
                            while data := channel.recv(1024):
                                result.extend(data)
                                if len(result) > len(OUTPUT):
                                    raise ValidationError("GSS SSH 固定输出超限")
                            if bytes(result) != OUTPUT or channel.recv_exit_status() != 0:
                                raise ValidationError("GSS SSH 固定命令结果不符")
                        finally:
                            channel.close()
                finally:
                    closing_deadline = peer.closing_deadline()
                    client.close()
                    sock.close()
                    client.join(max(0, closing_deadline - time.monotonic()))
                    if client.is_alive():
                        # 只能回收整个隔离进程，不能析构仍被原生调用使用的上下文。
                        os._exit(125)
        counts = provider.counts()
        peer_report = peer.report()
        if (not peer_report["closed"] or peer_report["active_connections"] or peer_report["live_threads"] or
                peer_report["worker_errors"] or peer_report["accepted"] != 1):
            raise ValidationError("GSS SSH 对端未形成唯一有界关闭结果")
        if negative:
            if peer_report["authenticated"] or peer_report["completed"]:
                raise ValidationError("GSS SSH 拒绝场景仍取得认证或命令")
            if scenario == "missing-cache" and not api.acquire_failures:
                raise ValidationError("缺失 cache 没有真实凭据申请失败证据")
            if scenario == "wrong-user" and counts["wrong_username_rejections"] != 1:
                raise ValidationError("错误 SSH 用户没有到达真实接受端拒绝分支")
            if scenario == "tampered-mic" and counts["mic_rejections"] != 1:
                raise ValidationError("篡改 MIC 没有到达真实 MIT 拒绝分支")
        elif (peer_report["authenticated"] != 1 or peer_report["completed"] != 1 or counts["acceptor_completed"] != 1 or
                scenario != "linux-plink" and counts["initiator_completed"] != 1):
            raise ValidationError("GSS SSH 接受端没有确认真实认证完成")
        return {"rejection_verified": negative, "fixed_command_completed": not negative,
                "formal_plink_verified": scenario == "linux-plink", "peer": peer_report,
                "provider": counts, "native_acquire_failures": len(api.acquire_failures)}
    finally:
        try:
            if client:
                client.close()
            if sock:
                sock.close()
            try:
                peer.close()
            except PeerShutdownError:
                os._exit(125)
        finally:
            provider.close()


def run(args):
    stage, api = "dependencies", None
    try:
        sources = verify_sources()
        stage = "native-load"
        api = NativeGss(root=args.root, directory=args.mit_directory,
                        linux_library=args.linux_library, missing_cache=args.scenario == "missing-cache")
        stage = "token-mic" if args.scenario == "token-mic" else "ssh-authentication"
        detail = token_mic(api) if args.scenario == "token-mic" else ssh(api, args.scenario, args.plink)
        stage = "native-cleanup"
        api.close()
        return {"schema": 1, "kind": "mit-gss-ssh-child", "passed": True, "scenario": args.scenario,
                "native_windows": sys.platform == "win32", "sspi_verified": False,
                "all_native_objects_released": True, "source_sha256": sources, "detail": detail}
    except Exception as error:
        result = {"schema": 1, "kind": "mit-gss-ssh-child", "passed": False,
                  "scenario": args.scenario, "stage": stage, "sspi_verified": False}
        if isinstance(error, GssError):
            result.update({"native_major": error.major, "native_minor": error.minor})
        return result
    finally:
        if api:
            api.close()


def main(*, deadline_seconds=45):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--scenario", choices=SCENARIOS, required=True)
    parser.add_argument("--mit-directory", type=Path)
    parser.add_argument("--linux-library", type=Path)
    parser.add_argument("--plink", type=Path)
    args = parser.parse_args()
    deadline = Deadline(deadline_seconds)
    logging.getLogger("paramiko").addHandler(logging.NullHandler())
    logging.getLogger("paramiko").setLevel(logging.CRITICAL)
    result = run(args)
    print(json.dumps(result, ensure_ascii=False), flush=True)
    deadline.complete()


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    main()
