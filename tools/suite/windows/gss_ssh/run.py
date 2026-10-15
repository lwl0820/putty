"""隔离的真实 MIT token/MIC 与固定 GSS SSH 认证验收。"""
import argparse
import json
import os
from pathlib import Path
import sys
import threading
import time

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from tools.suite.windows.gss_kdc import PortableKDC, PreparedKDC
from tools.suite.windows.gss_kdc.process import BoundedProcess
from tools.suite.windows.gss_kdc.security import PrivateTree, publish_report, utf8_streams
from tools.suite.windows.gss_mit.prepare_kfw import validate_directory
from tools.suite.windows.support import ValidationError, project_path
from tools.suite.windows.gss_ssh.dependencies import verify_sources

HERE = Path(__file__).resolve().parent
SCENARIOS = ("token-mic", "ssh", "missing-cache", "wrong-user", "tampered-mic")
DETAIL_FIELDS = {
    "token-mic": {"mutual_authentication_verified", "mic_verified", "tampered_mic_rejected",
                  "delegation_not_requested_or_returned", "initiator_flags", "acceptor_flags", "exchanges", "mic_rejection"},
    "ssh": {"rejection_verified", "fixed_command_completed", "formal_plink_verified", "peer", "provider", "native_acquire_failures"},
}


class RunFailure(ValidationError):
    def __init__(self, stage, native_major=None, native_minor=None):
        self.stage, self.native_major, self.native_minor = stage, native_major, native_minor
        super().__init__("GSS SSH 正式验收未通过")


class Deadline:
    def __init__(self, seconds):
        self.limit = time.monotonic() + seconds
        self.done = threading.Event()
        self.worker = threading.Thread(target=self._watch, daemon=True, name="gss-ssh-run-deadline")
        self.worker.start()

    def _watch(self):
        if not self.done.wait(max(0, self.limit - time.monotonic())):
            os._exit(124)

    def complete(self):
        if time.monotonic() >= self.limit:
            os._exit(124)
        self.done.set()


def validate_child(content, scenario, sources):
    def unique_fields(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("重复的协议字段")
            result[key] = value
        return result

    def invalid_constant(value):
        raise ValueError("协议不接受非有限数值")

    try:
        if not isinstance(content, bytes) or len(content) > 16384:
            raise ValueError("协议体积或类型无效")
        result = json.loads(content.decode("utf-8"), object_pairs_hook=unique_fields,
                            parse_constant=invalid_constant)
    except (ValueError, UnicodeDecodeError, RecursionError):
        raise RunFailure("child-protocol") from None
    stages = {"dependencies", "native-load", "native-cleanup",
              "token-mic" if scenario == "token-mic" else "ssh-authentication"}
    if scenario not in (*SCENARIOS, "linux-plink"):
        raise RunFailure("child-protocol")
    failed_fields = {"schema", "kind", "passed", "scenario", "stage", "sspi_verified"}
    if (isinstance(result, dict) and result.get("passed") is False and
            set(result) in (failed_fields, failed_fields | {"native_major", "native_minor"}) and
            type(result["schema"]) is int and result["schema"] == 1 and result["kind"] == "mit-gss-ssh-child" and
            result["scenario"] == scenario and result["sspi_verified"] is False and
            isinstance(result["stage"], str) and result["stage"] in stages):
        codes = (result.get("native_major"), result.get("native_minor"))
        no_codes = set(result) == failed_fields
        valid_codes = (result["stage"] != "dependencies" and
                       all(type(code) is int and 0 <= code < 2**32 for code in codes) and codes[0] != 0)
        if no_codes or valid_codes:
            raise RunFailure("child-" + scenario + "-" + result["stage"], *codes)
    fields = {"schema", "kind", "passed", "scenario", "native_windows", "sspi_verified",
              "all_native_objects_released", "source_sha256", "detail"}
    if (not isinstance(result, dict) or set(result) != fields or type(result["schema"]) is not int or result["schema"] != 1 or
            result["kind"] != "mit-gss-ssh-child" or result["passed"] is not True or
            result["scenario"] != scenario or result["native_windows"] is not (os.name == "nt") or
            result["sspi_verified"] is not False or result["all_native_objects_released"] is not True or
            result["source_sha256"] != sources):
        raise RunFailure("child-" + scenario)
    detail = result["detail"]
    expected = DETAIL_FIELDS["token-mic" if scenario == "token-mic" else "ssh"]
    if not isinstance(detail, dict) or set(detail) != expected:
        raise RunFailure("child-protocol")
    if scenario == "token-mic":
        rejection = detail["mic_rejection"]
        if (any(detail[k] is not True for k in ("mutual_authentication_verified", "mic_verified", "tampered_mic_rejected", "delegation_not_requested_or_returned")) or
                any(type(detail[k]) is not int or not 0 <= detail[k] < 2**32 or detail[k] & 34 != 34 or detail[k] & 1 for k in ("initiator_flags", "acceptor_flags")) or
                type(detail["exchanges"]) is not int or not 1 <= detail["exchanges"] <= 8 or
                not isinstance(rejection, dict) or set(rejection) != {"major", "minor"} or
                any(type(rejection[k]) is not int or not 0 <= rejection[k] < 2**32 for k in ("major", "minor")) or
                rejection["major"] == 0):
            raise RunFailure("child-protocol")
    else:
        negative = scenario in ("missing-cache", "wrong-user", "tampered-mic")
        peer, provider = detail["peer"], detail["provider"]
        peer_fields = {"accepted", "authenticated", "completed", "rejected_auth", "capacity_refused", "worker_errors", "active_high_water",
                       "closed", "active_connections", "live_threads"}
        provider_fields = {"wrong_username_rejections", "mic_rejections", "initiator_completed", "acceptor_completed"}
        if (not isinstance(peer, dict) or set(peer) != peer_fields or not isinstance(provider, dict) or set(provider) != provider_fields or
                any(type(peer[k]) is not int or not 0 <= peer[k] <= 16 for k in peer_fields - {"closed"}) or
                any(type(provider[k]) is not int or not 0 <= provider[k] <= 2 for k in provider_fields) or
                peer["closed"] is not True or peer["accepted"] != 1 or peer["active_high_water"] != 1 or
                any(peer[k] for k in ("active_connections", "live_threads", "worker_errors", "capacity_refused")) or
                detail["formal_plink_verified"] is not (scenario == "linux-plink") or
                detail["rejection_verified"] is not negative or detail["fixed_command_completed"] is not (not negative) or
                type(detail["native_acquire_failures"]) is not int or
                detail["native_acquire_failures"] != (1 if scenario == "missing-cache" else 0)):
            raise RunFailure("child-protocol")
        expected_provider = {
            "wrong_username_rejections": int(scenario == "wrong-user"),
            "mic_rejections": int(scenario == "tampered-mic"),
            "initiator_completed": int(scenario in ("ssh", "tampered-mic")),
            "acceptor_completed": int(scenario in ("ssh", "tampered-mic", "linux-plink")),
        }
        if provider != expected_provider or peer["rejected_auth"] != int(scenario == "linux-plink"):
            raise RunFailure("child-protocol")
        if negative:
            if peer["authenticated"] or peer["completed"]:
                raise RunFailure("child-protocol")
            evidence = {"wrong-user": provider["wrong_username_rejections"], "tampered-mic": provider["mic_rejections"],
                        "missing-cache": detail["native_acquire_failures"]}[scenario]
            if evidence != 1:
                raise RunFailure("child-protocol")
        elif peer["authenticated"] != 1 or peer["completed"] != 1 or provider["acceptor_completed"] != 1:
            raise RunFailure("child-protocol")
    return detail


def register_replay(realm, *, required):
    # 固定 KfW 4.1 的显式 dfl residual；Linux 1.18+ 的 dfl 固定按 uid 命名。
    path = realm.root / ("suite-gss-replay" if os.name == "nt" else f"krb5_{os.geteuid()}.rcache2")
    try:
        path.lstat()
    except FileNotFoundError:
        if required:
            raise RunFailure("replay-cache-not-created") from None
    else:
        realm.register_artifact(path)


def invoke(args, realm, scenario, sources):
    environment = dict(realm.environment)
    if scenario == "missing-cache":
        environment["KRB5CCNAME"] = "FILE:" + str(realm.root / "absent.ccache")
    command = [sys.executable, "-I", "-B", HERE / "child.py", "--root", realm.root, "--scenario", scenario]
    if args.mit_directory:
        command.extend(("--mit-directory", args.mit_directory))
    if args.linux_library:
        command.extend(("--linux-library", args.linux_library))
    if scenario == "linux-plink":
        if os.name == "nt":
            raise RunFailure("windows-cli-isolation-missing")
        command.extend(("--plink", args.plink))
        environment["PUTTYDIR"] = str(realm.root / "putty")
        environment["PUTTYRANDOMSEED"] = str(realm.root / "putty" / "randomseed")
    child = BoundedProcess(command, cwd=realm.root, environment=environment,
                           timeout=50, output_limit=16384, line_protocol=False)
    try:
        child.finish()
        detail = validate_child(bytes(child.stdout), scenario, sources)
        register_replay(realm, required=scenario in ("token-mic", "ssh", "tampered-mic", "linux-plink"))
        if scenario == "linux-plink":
            realm.register_artifact(realm.root / "putty" / "randomseed")
        return detail
    finally:
        child.abort()


def run(args):
    stage = "validate-input"
    try:
        parent = project_path(args.parent, must_exist=True)
        guard = PrivateTree(parent, existing=True)
        guard.close()
        if os.name == "nt":
            if args.allow_linux or args.linux_library or args.plink or not args.mit_directory:
                raise ValidationError("Windows GSS 验收只允许便携 MIT 和 Paramiko 客户端")
            if not str(parent).isascii() or not str(args.mit_directory).isascii():
                raise ValidationError("当前 KfW 窄字符夹具只接受已经证明的 ASCII 运行路径")
            args.mit_directory = project_path(args.mit_directory, must_exist=True)
            validate_directory(args.mit_directory)
            args.kinit = project_path(args.mit_directory / "kinit.exe", must_exist=True)
        elif not args.allow_linux or not args.linux_library or args.mit_directory or not args.kinit:
            raise ValidationError("Linux 真实对照须显式允许并指定 MIT 工具及共享库")
        if args.plink:
            args.plink = project_path(args.plink, must_exist=True)
        stage = "dependencies"
        sources = verify_sources()
        stage = "prepared-kdc"
        prepared = PreparedKDC.from_directory(args.prepared_kdc)
        results = {}
        stage = "kdc-start"
        with PortableKDC(prepared, parent=parent) as realm:
            if args.plink:
                realm.tree.directory(realm.root / "putty")
            for scenario in SCENARIOS + (("linux-plink",) if args.plink else ()):
                stage = "reset-" + scenario
                realm.reset_client(args.kinit)
                stage = "case-" + scenario
                results[scenario] = invoke(args, realm, scenario, sources)
                if scenario != "missing-cache" and not realm.delay_started.is_set():
                    raise RunFailure("real-tgs-not-observed")
            stage = "kdc-cleanup"
        kdc = realm.report()
        return {"schema": 1, "kind": "mit-gss-ssh", "passed": True, "paramiko_version": "4.0.0",
                "native_windows": os.name == "nt", "sspi_verified": False, "credentials_acquired": True,
                "kerberos_authentication_verified": True, "ssh_login_verified": True,
                "formal_plink_verified": bool(args.plink), "private_data_removed": True,
                "all_native_objects_released": True, "all_threads_closed": True,
                "source_sha256": sources, "checks": results, "kdc": kdc}
    except Exception as error:
        if isinstance(error, RunFailure):
            raise
        raise RunFailure(stage) from None


def main(*, deadline_seconds=180):
    utf8_streams()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepared-kdc", type=Path, required=True)
    parser.add_argument("--parent", type=Path, required=True)
    parser.add_argument("--mit-directory", type=Path)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--linux-library", type=Path)
    parser.add_argument("--kinit", type=Path)
    parser.add_argument("--plink", type=Path)
    parser.add_argument("--allow-linux", action="store_true")
    args = parser.parse_args()
    failed = {"schema": 1, "kind": "mit-gss-ssh", "passed": False, "stage": "pending",
              "kerberos_authentication_verified": False, "ssh_login_verified": False,
              "formal_plink_verified": False, "sspi_verified": False}
    publish_report(args.report, failed)
    deadline = Deadline(deadline_seconds)
    try:
        report = run(args)
    except RunFailure as error:
        report = {**failed, "stage": error.stage}
        if error.native_major is not None:
            report.update({"native_major": error.native_major, "native_minor": error.native_minor})
    # stdout 只是候选；刷新失败或挂起时保留执行前的失败占位。
    print(json.dumps(report, ensure_ascii=False), flush=True)
    publish_report(args.report, report)
    deadline.complete()
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
