#!/usr/bin/env python3
"""真实 Kerberos 服务票据网络等待期间的终端心跳和关闭。"""
import argparse
import json
from pathlib import Path
import shlex
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from tools.suite.gss_fixture import GSSSSHServer, KerberosRealm
from tools.suite.isolation import IsolationEnvironment, IsolationError
from tools.suite.ssh_fixture import FixtureUnavailable
from run_terminal_test import PROGRAM


def invoke(binary, environment, realm, server, mode, report):
    marker = environment.root / "logs/gss-delay-started"
    if marker.exists(): marker.unlink()
    realm.reset_client(delay_seconds=1.5)
    child = realm.client_environment()
    child.update(QT_QPA_PLATFORM="offscreen", PUTTY_TERMINAL_ISOLATED_ROOT=str(environment.root))
    arguments = [str(binary.resolve()), "--port", str(server.port), "--mode", mode,
                 "--kdc-marker", str(marker), "--remote-command",
                 "/usr/bin/python3 -B -c " + shlex.quote(PROGRAM)]
    client = subprocess.Popen(arguments, env=child, cwd=environment.root,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, umask=0o077)
    try:
        deadline = time.monotonic() + 15
        while client.poll() is None and not realm.delay_started.wait(0.02):
            if time.monotonic() >= deadline:
                raise RuntimeError("终端未请求真实 KDC 服务票据")
        if realm.delay_started.is_set():
            environment.write_private("logs/gss-delay-started", b"actual-kdc-request\n")
        stdout, stderr = client.communicate(timeout=40)
        report.with_suffix(f".{mode}.stdout.log").write_bytes(stdout)
        report.with_suffix(f".{mode}.stderr.log").write_bytes(stderr)
        sys.stdout.buffer.write(stdout); sys.stderr.buffer.write(stderr)
        if client.returncode:
            raise RuntimeError(f"{mode} 退出码 {client.returncode}")
        delay = realm.delay_report()
        if (delay["delayed_requests"] < 1 or delay["tgs_requests"] < 1 or
                max(delay["observed_delays"], default=0) < 1.5):
            raise RuntimeError("没有实际 TGS 请求和受控网络等待证据")
        result = json.loads((environment.root / "logs/terminal-result.json").read_text(encoding="utf-8"))
        if not result["gss_verified"]: raise RuntimeError("终端没有验证真实 GSS 路径")
        screenshot = environment.root / "logs/terminal-final.png"
        if mode == "gss-workload" and screenshot.exists():
            report.with_suffix(".png").write_bytes(screenshot.read_bytes())
        return {"terminal": result, "kdc": delay}
    finally:
        if client.poll() is None:
            client.terminate()
        try: client.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            client.kill(); client.communicate()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--parent", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    arguments = parser.parse_args()
    if sys.platform != "linux": return 77
    report = arguments.report.resolve()
    if not report.is_relative_to(ROOT / ".tmp"): raise IsolationError("报告只能写入项目 .tmp/")
    report.parent.mkdir(parents=True, exist_ok=True)
    details = {"passed": False, "windows_native_verified": False}
    try:
        with IsolationEnvironment(arguments.parent) as environment:
            details["isolation"] = environment.probe()
            with KerberosRealm(environment) as realm:
                with GSSSSHServer(environment, realm=realm) as server:
                    details["server_image"] = server.image_id
                    details["workload"] = invoke(arguments.binary, environment, realm, server, "gss-workload", report)
                    details["close"] = invoke(arguments.binary, environment, realm, server, "gss-close", report)
            details["passed"] = True
    finally:
        report.write_text(json.dumps(details, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    try: raise SystemExit(main())
    except FixtureUnavailable as error:
        print(f"真实 GSS 测试依赖未满足：{error}"); raise SystemExit(77)
    except (IsolationError, OSError, RuntimeError, ValueError, subprocess.SubprocessError) as error:
        print(f"终端 GSS 测试失败：{error}", file=sys.stderr); raise SystemExit(1)
