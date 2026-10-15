"""Windows GSS CI 的协议与失败边界；非 Windows 只执行逻辑模拟。"""
import copy
from contextlib import ExitStack
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import unittest
from unittest import mock
import uuid

ROOT = Path(__file__).resolve().parents[4]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from tools.suite.windows import gss_ci as ci
from tools.suite.windows.gss_kdc.security import PrivateTree
from tools.suite.windows.support import ValidationError


def preparation():
    return {"schema": 1, "kind": "gss-kdc-preparation", "passed": True,
            "sdk_version": ci.LOCK["dotnet_sdk_version"], "package_count": len(ci.LOCK["nuget_packages"]),
            "sdk_files_verified": 4704, "source_sha256": ci.source_hash()}


def python_preparation():
    return {"schema": 1, "kind": "gss-ssh-preparation", "passed": True, "wheel_count": 7,
            "paramiko_version": "4.0.0", "source_patch_verified": True, "authentication_verified": False}


def successful_inner():
    checks = {"token-mic": {"mutual_authentication_verified": True, "mic_verified": True,
        "tampered_mic_rejected": True, "delegation_not_requested_or_returned": True,
        "initiator_flags": 34, "acceptor_flags": 34, "exchanges": 2,
        "mic_rejection": {"major": 393216, "minor": 0}}}
    for case in ci.SCENARIOS[1:]:
        positive = case == "ssh"
        checks[case] = {"rejection_verified": not positive, "fixed_command_completed": positive,
            "formal_plink_verified": False, "native_acquire_failures": int(case == "missing-cache"),
            "peer": {"accepted": 1, "authenticated": int(positive), "completed": int(positive),
                     "rejected_auth": 0, "capacity_refused": 0, "worker_errors": 0,
                     "active_high_water": 1, "closed": True, "active_connections": 0, "live_threads": 0},
            "provider": {"wrong_username_rejections": int(case == "wrong-user"),
                         "mic_rejections": int(case == "tampered-mic"),
                         "initiator_completed": int(case in ("ssh", "tampered-mic")),
                         "acceptor_completed": int(case in ("ssh", "tampered-mic"))}}
    return {"schema": 1, "kind": "mit-gss-ssh", "passed": True, "paramiko_version": "4.0.0",
            "native_windows": True, "sspi_verified": False, "credentials_acquired": True,
            "kerberos_authentication_verified": True, "ssh_login_verified": True,
            "formal_plink_verified": False, "private_data_removed": True,
            "all_native_objects_released": True, "all_threads_closed": True,
            "source_sha256": ci.expected_sources(), "checks": checks,
            "kdc": {"schema": 1, "kind": "isolated-real-kdc", "passed": True,
                "realm": "SUITE.TEST", "encryption": "aes256-cts-hmac-sha1-96",
                "private_data_removed": True, "native_windows": True,
                "kdc": {"schema": 1, "stopped": True, "accepted": 14, "completed": 14,
                        "rejected": 0, "malformed": 0, "as_requests": 10, "tgs_requests": 4,
                        "active_connections": 0, "active_workers": 0},
                "proxy": {"requests": {"as": 0, "tgs": 1},
                          "responses": {"as": 0, "tgs": 1, "error": 0},
                          "configured_delay_seconds": 1.6, "observed_delay_seconds": [1.6001],
                          "active_connections": 0, "accepted_connections": 4, "live_workers": 0}}}


class GSSCIEntryTests(unittest.TestCase):
    def setUp(self):
        self.root = ROOT / ".tmp" / ("gss-ci-contract-" + uuid.uuid4().hex)
        self.tree = PrivateTree(self.root)
        self.profile = self.tree.directory(self.root / "observed-user")
        self.data = self.tree.directory(self.profile / ".dotnet")
        self.existing = self.tree.write(self.data / "existing.crl", b"private-content")
        self.mit = self.tree.directory(self.root / "mit")
        self.tools = self.root / "tools"
        self.python = self.root / "python"
        self.parent = self.root / "runs"
        self.invocation = self.root / "invocation"
        self.report = self.root / "report.json"

    def tearDown(self):
        self.tree.close()
        shutil.rmtree(self.root)

    def patches(self):
        stack = ExitStack()
        stack.enter_context(mock.patch.object(ci, "native_windows", return_value=True))
        stack.enter_context(mock.patch.object(ci, "actual_profile", return_value=self.profile))
        self.validate = stack.enter_context(mock.patch.object(ci, "validate_directory", return_value={"version": "4.1"}))
        self.kdc = stack.enter_context(mock.patch.object(ci, "prepare_kdc", return_value=preparation()))
        self.venv = stack.enter_context(mock.patch.object(ci, "prepare_python", return_value=python_preparation()))
        self.invoke = stack.enter_context(mock.patch.object(ci, "invoke", return_value=ci.protocol_summary(successful_inner(), ci.expected_sources())))
        return stack

    def execute(self):
        return ci.run(self.tools, self.python, self.mit, self.parent, self.invocation, self.report)

    def arguments(self):
        return ["gss-ci", "--tools", str(self.tools), "--python-tools", str(self.python),
                "--mit-directory", str(self.mit), "--parent", str(self.parent),
                "--invocation", str(self.invocation), "--report", str(self.report)]

    def test_success_observes_prepare_and_cleanup_without_mutating_environment(self):
        self.report.write_text('{"passed":true,"old":"private-ticket"}', encoding="utf-8")
        environment = dict(os.environ)
        events = []
        snapshot = ci.snapshot_profile
        def observed(profile):
            events.append("observe")
            return snapshot(profile)
        with self.patches(), mock.patch.object(ci, "snapshot_profile", side_effect=observed):
            def prepared(_):
                events.append("prepare")
                self.assertEqual(json.loads(self.report.read_text(encoding="utf-8"))["stage"], "pending")
                self.assertEqual(events[0], "observe")
                return preparation()
            self.kdc.side_effect = prepared
            result = self.execute()
            self.validate.assert_called_once_with(self.mit)
        self.assertTrue(result["passed"])
        self.assertTrue(result["gss_ssh_verified"])
        self.assertTrue(result["personal_metadata_unchanged"])
        self.assertTrue(result["private_parent_removed"])
        self.assertTrue(result["private_invocation_removed"])
        self.assertFalse(result["formal_plink_verified"])
        self.assertFalse(result["sspi_verified"])
        self.assertEqual(events, ["observe", "prepare", "observe"])
        self.assertEqual(os.environ, environment)
        self.assertFalse(self.parent.exists())
        self.assertFalse(self.invocation.exists())
        self.assertFalse(json.loads(self.report.read_text(encoding="utf-8"))["passed"])
        public = json.dumps(result)
        for secret in (str(self.profile), str(self.parent), "private-content", "private-ticket"):
            self.assertNotIn(secret, public)

    def test_prepare_failure_still_observes_after_and_removes_owned_empty_roots(self):
        for name in ("kdc", "venv"):
            with self.subTest(name=name), self.patches():
                getattr(self, name).side_effect = ValidationError(str(self.profile))
                result = self.execute()
                self.invoke.assert_not_called()
                self.assertFalse(result["passed"])
                self.assertEqual(result["stage"], "prepare-kdc" if name == "kdc" else "prepare-python")
                self.assertTrue(result["personal_metadata_after_observed"])
                self.assertTrue(result["personal_metadata_unchanged"])
                self.assertTrue(result["private_parent_removed"])
                self.assertTrue(result["private_invocation_removed"])

    def test_personal_change_overrides_both_success_and_preparation_failure(self):
        for preparation_fails in (False, True):
            with self.subTest(preparation_fails=preparation_fails), self.patches():
                def changed(*_):
                    self.existing.write_bytes(b"changed-content" + bytes([preparation_fails]))
                    if preparation_fails:
                        raise ValidationError("私有失败")
                    return self.invoke.return_value
                (self.kdc if preparation_fails else self.invoke).side_effect = changed
                result = self.execute()
                self.assertFalse(result["passed"])
                self.assertFalse(result["personal_metadata_unchanged"])
                self.assertEqual(result["stage"], "personal-metadata-changed")
                self.assertNotIn("protocol", result)

    def test_before_or_after_observation_failure_blocks_success(self):
        snapshot = ci.snapshot_profile(self.profile)
        for before_fails in (True, False):
            outcomes = [ValidationError("拒绝"), snapshot] if before_fails else [snapshot, ValidationError("拒绝")]
            with self.subTest(before_fails=before_fails), self.patches(), mock.patch.object(ci, "snapshot_profile", side_effect=outcomes):
                result = self.execute()
                self.assertFalse(result["passed"])
                self.assertFalse(result["personal_metadata_unchanged"])
                self.assertEqual(result["stage"], "personal-metadata-before" if before_fails else "personal-metadata-after")
                if before_fails:
                    self.kdc.assert_not_called()
                    self.assertTrue(result["personal_metadata_after_observed"])

    def test_unknown_runtime_files_are_preserved_and_after_is_observed(self):
        for root_name in ("parent", "invocation"):
            with self.subTest(root_name=root_name), self.patches():
                target = getattr(self, root_name)
                def dirty(*_):
                    (target / "unknown").write_bytes(b"keep")
                    return self.invoke.return_value
                self.invoke.side_effect = dirty
                result = self.execute()
                self.assertFalse(result["passed"])
                self.assertTrue(result["personal_metadata_after_observed"])
                self.assertEqual((target / "unknown").read_bytes(), b"keep")
                (target / "unknown").unlink()
                target.rmdir()

    def test_existing_roots_and_ancestor_overlap_refuse_before_prepare(self):
        self.tree.directory(self.parent)
        with self.patches():
            result = self.execute()
            self.kdc.assert_not_called()
        self.assertFalse(result["passed"])
        self.assertEqual(result["stage"], "private-parent")
        self.parent.rmdir()
        for nested in (self.parent / "tools", self.invocation, self.report):
            with self.subTest(nested=nested), self.patches():
                result = ci.run(nested, self.python, self.mit, self.parent, self.invocation, self.report)
                self.kdc.assert_not_called()
                self.assertFalse(result["passed"])
                self.assertEqual(result["stage"], "validate-input")

    def test_non_windows_refuses_without_observing_or_preparing(self):
        with mock.patch.object(ci, "native_windows", return_value=False), mock.patch.object(ci, "actual_profile") as profile:
            result = self.execute()
        profile.assert_not_called()
        self.assertFalse(result["passed"])
        self.assertTrue(result["skipped"])
        self.assertEqual(result["stage"], "windows-required")

    def test_preparation_rejects_unknown_fields_hashes_and_type_confusion(self):
        for mutate in (lambda a, b: a.update(schema=True), lambda a, b: a.update(source_sha256="0" * 64),
                       lambda a, b: a.update(secret="private"), lambda a, b: b.update(wheel_count=True),
                       lambda a, b: b.update(authentication_verified=True), lambda a, b: b.update(extra="private")):
            a, b = preparation(), python_preparation()
            mutate(a, b)
            with self.assertRaises(ValidationError):
                ci.preparation_summary(a, b)

    def test_success_protocol_rejects_wrong_scenarios_native_cleanup_or_extra_fields(self):
        mutations = (
            lambda r: r.update(schema=True), lambda r: r.update(native_windows=False),
            lambda r: r.update(formal_plink_verified=True), lambda r: r.update(sspi_verified=True),
            lambda r: r.update(private_data_removed=False), lambda r: r.update(secret="private"),
            lambda r: r["checks"].update({"linux-plink": {}}),
            lambda r: r["checks"].pop("missing-cache"),
            lambda r: r["checks"]["wrong-user"]["provider"].update(wrong_username_rejections=0),
            lambda r: r["checks"]["ssh"]["peer"].update(live_threads=1),
            lambda r: r["checks"]["token-mic"].update(initiator_flags=35),
            lambda r: r["checks"]["token-mic"]["mic_rejection"].update(major=True),
            lambda r: r["kdc"].update(native_windows=False),
            lambda r: r["kdc"]["kdc"].update(schema=True),
            lambda r: r["kdc"]["kdc"].update(active_workers=1),
            lambda r: r["kdc"]["kdc"].update(completed=13),
            lambda r: r["kdc"]["proxy"].update(live_workers=True),
            lambda r: r["kdc"]["proxy"].update(observed_delay_seconds=[float("nan")]),
            lambda r: r["source_sha256"].update({"auth_handler.py": "0" * 64}),
        )
        for mutate in mutations:
            result = successful_inner()
            mutate(result)
            with self.subTest(mutate=mutate), self.assertRaises(ValidationError):
                ci.protocol_summary(result, ci.expected_sources())

    def test_strict_json_rejects_duplicates_nonfinite_overflow_and_type_mismatch(self):
        for content in (b'{"a":1,"a":2}', b'{"a":{"b":1,"b":2}}', b'{"x":NaN}', b'{"x":1e9999}',
                        b'{"x":' + b"9" * 5000 + b'}', b"[]", b"{}{}", b" " * (ci.MAX_REPORT + 1)):
            with self.subTest(content=content[:30]), self.assertRaises(ValidationError):
                ci.decode_report(content)

    def test_failure_native_codes_have_fixed_stage_and_u32_bounds(self):
        base = {"schema": 1, "kind": "mit-gss-ssh", "passed": False,
                "stage": "child-ssh-native-load", "kerberos_authentication_verified": False,
                "ssh_login_verified": False, "formal_plink_verified": False, "sspi_verified": False,
                "native_major": 393216, "native_minor": 0}
        with self.assertRaises(ci._InnerFailure) as failure:
            ci.protocol_summary(base, ci.expected_sources())
        self.assertEqual(failure.exception.major, 393216)
        for update in ({"native_major": True}, {"native_minor": 2**32}, {"native_major": 0},
                       {"stage": "dependencies"}, {"stage": str(self.profile)}, {"extra": "private"}):
            with self.subTest(update=update), self.assertRaises(ValidationError) as failure:
                ci.protocol_summary({**base, **update}, ci.expected_sources())
            self.assertNotIsInstance(failure.exception, ci._InnerFailure)

    def test_invoke_uses_isolated_venv_closed_environment_and_matching_final_file(self):
        python_root = self.tree.directory(self.python)
        venv = self.tree.directory(python_root / "venv")
        scripts = self.tree.directory(venv / "Scripts")
        executable = self.tree.write(scripts / "python.exe", b"public-test-placeholder")
        inner = successful_inner()
        inv = PrivateTree(self.invocation)
        record = {}
        class Child:
            def __init__(_, command, **options):
                record.update(command=command, **options)
                _.stdout = json.dumps(inner).encode("utf-8")
                _.process = mock.Mock(returncode=0)
                _.aborted = False
                ci.publish_report(inv.root / "inner-report.json", inner)
            def finish(_):
                pass
            def abort(_):
                _.aborted = True
                record["aborted"] = True
        try:
            with mock.patch.object(ci, "BoundedProcess", Child), mock.patch.object(inv, "check_file", wraps=inv.check_file) as check:
                result = ci.invoke(self.tools, self.python, self.mit, self.parent, inv)
                check.assert_called_once_with(inv.root / "inner-report.json", harden=True)
            self.assertTrue(result["credentials_acquired"])
            self.assertEqual(record["command"][:4], [executable, "-I", "-B", ci.GSS_DIRECTORY / "run.py"])
            self.assertNotIn("--plink", record["command"])
            self.assertEqual(record["cwd"], inv.root)
            self.assertEqual(record["timeout"], 190)
            self.assertEqual(record["output_limit"], 65536)
            self.assertTrue(record["aborted"])
            for name in ("HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "TMP", "TEMP"):
                self.assertTrue(Path(record["environment"][name]).is_relative_to(inv.root))
            for name in ("SSH_AUTH_SOCK", "PYTHONPATH", "KRB5CCNAME", "GSS_MECH_CONFIG"):
                self.assertNotIn(name, record["environment"])
        finally:
            inv.remove()
            inv.close()

    def test_invoke_rejects_nonzero_exit_missing_report_and_candidate_mismatch(self):
        python_root = self.tree.directory(self.python)
        venv = self.tree.directory(python_root / "venv")
        scripts = self.tree.directory(venv / "Scripts")
        self.tree.write(scripts / "python.exe", b"public-test-placeholder")
        for mode in ("exit", "missing", "mismatch", "bool-int"):
            with self.subTest(mode=mode):
                inv = PrivateTree(self.invocation)
                inner = successful_inner()
                class Child:
                    def __init__(_, *_args, **_kwargs):
                        _.stdout = json.dumps(inner).encode("utf-8")
                        _.process = mock.Mock(returncode=1 if mode == "exit" else 0)
                        final = copy.deepcopy(inner)
                        if mode == "mismatch":
                            final["checks"]["token-mic"]["mic_rejection"]["minor"] = 1
                        elif mode == "bool-int":
                            final["schema"] = True
                        if mode != "missing":
                            ci.publish_report(inv.root / "inner-report.json", final)
                    def finish(_):
                        if mode == "exit":
                            raise ValidationError("内部非零")
                    def abort(_):
                        pass
                try:
                    with mock.patch.object(ci, "BoundedProcess", Child), self.assertRaises((ValidationError, FileNotFoundError)):
                        ci.invoke(self.tools, self.python, self.mit, self.parent, inv)
                finally:
                    inv.remove()
                    inv.close()

    def test_main_flushes_candidate_before_atomic_publication(self):
        events = []
        original_publish = ci.publish_report
        class Stream:
            def write(_, text):
                events.append("write")
                return len(text)
            def flush(_):
                events.append("flush")
        def publish(path, result):
            if result["passed"]:
                self.assertIn("flush", events)
                self.assertFalse(json.loads(self.report.read_text(encoding="utf-8"))["passed"])
                events.append("commit")
            original_publish(path, result)
        with self.patches(), mock.patch.object(sys, "argv", self.arguments()), mock.patch.object(sys, "stdout", Stream()), \
                mock.patch.object(ci, "publish_report", side_effect=publish):
            self.assertEqual(ci.main(), 0)
        self.assertEqual(events[-1], "commit")
        self.assertTrue(json.loads(self.report.read_text(encoding="utf-8"))["passed"])

    def test_main_output_or_report_error_preserves_pending(self):
        original_publish = ci.publish_report
        class Stream(io.StringIO):
            def flush(_):
                raise OSError("私有标准流失败")
        for mode in ("stdout", "report"):
            with self.subTest(mode=mode):
                self.report.write_text('{"passed":true}', encoding="utf-8")
                def publish(path, result):
                    if mode == "report" and result["passed"]:
                        raise OSError("私有报告失败")
                    original_publish(path, result)
                with self.patches(), mock.patch.object(sys, "argv", self.arguments()), \
                        mock.patch.object(sys, "stdout", Stream() if mode == "stdout" else io.StringIO()), \
                        mock.patch.object(sys, "stderr", io.StringIO()), mock.patch.object(ci, "publish_report", side_effect=publish):
                    self.assertEqual(ci.main(), 1)
                self.assertEqual(json.loads(self.report.read_text(encoding="utf-8"))["stage"], "pending")

    def test_deadline_start_failure_never_executes(self):
        with mock.patch.object(sys, "argv", self.arguments()), mock.patch.object(ci.threading.Thread, "start", side_effect=RuntimeError("拒绝")), \
                mock.patch.object(ci, "run") as run, mock.patch.object(sys, "stderr", io.StringIO()):
            self.assertEqual(ci.main(), 1)
            run.assert_not_called()
        self.assertFalse(json.loads(self.report.read_text(encoding="utf-8"))["passed"])

    def test_real_hard_deadline_covers_prepare_cleanup_stdout_and_report(self):
        script = r'''
from pathlib import Path
import sys
import time
from tools.suite.windows import gss_ci as ci
from tools.suite.windows.tests.test_gss_ci import preparation, python_preparation, successful_inner
root, mode = Path(sys.argv[1]), sys.argv[2]
ci.native_windows = lambda: True
ci.actual_profile = lambda: root / "observed-user"
ci.validate_directory = lambda _: {"version": "4.1"}
ci.prepare_kdc = lambda _: preparation()
ci.prepare_python = lambda _: python_preparation()
ci.invoke = lambda *_: ci.protocol_summary(successful_inner(), ci.expected_sources())
def hang(*_):
    (root / ("entered-" + mode)).write_text("true", encoding="ascii")
    while True:
        time.sleep(1)
if mode == "prepare":
    ci.prepare_kdc = hang
elif mode == "cleanup":
    ci.PrivateTree.remove = hang
elif mode == "stdout":
    class Stream:
        def write(self, value):
            return len(value)
        def flush(self):
            hang()
    sys.stdout = Stream()
elif mode == "report":
    publish = ci.publish_report
    def blocked(path, report):
        if report["passed"]:
            hang()
        publish(path, report)
    ci.publish_report = blocked
sys.argv = ["gss-ci", "--tools", str(root / "tools"), "--python-tools", str(root / "python"),
    "--mit-directory", str(root / "mit"), "--parent", str(root / ("runs-" + mode)),
    "--invocation", str(root / ("invocation-" + mode)), "--report", str(root / "report.json")]
raise SystemExit(ci.main(deadline_seconds=2))
'''
        for mode in ("prepare", "cleanup", "stdout", "report"):
            with self.subTest(mode=mode):
                self.report.write_text('{"passed":true}', encoding="utf-8")
                child = subprocess.run([sys.executable, "-B", "-c", script, str(self.root), mode],
                    cwd=ROOT, capture_output=True, timeout=15)
                self.assertEqual(child.returncode, 124, child.stderr.decode("utf-8", errors="replace"))
                self.assertEqual((self.root / ("entered-" + mode)).read_text(encoding="ascii"), "true")
                pending = json.loads(self.report.read_text(encoding="utf-8"))
                self.assertFalse(pending["passed"])
                self.assertEqual(pending["stage"], "pending")

    def test_workflow_has_one_final_report_and_same_sha_kdc_dependency(self):
        workflow = (ROOT / ".github/workflows/putty-suite.yml").read_text(encoding="utf-8")
        job = workflow.split("  windows-mit-gss-ssh:\n", 1)[1].split("\n  windows:\n", 1)[0]
        for required in ("needs: windows-mit-kdc", "runs-on: windows-2022", "timeout-minutes: 20", "python-version: '3.11'",
                         "if: always()", "path: .tmp/windows-ci/reports/gss-mit-ssh.json", "gss_ci.py", "Get-Command 7z.exe"):
            self.assertIn(required, job)
        self.assertEqual(job.count("uses: actions/upload-artifact@v4"), 1)
        self.assertNotIn("download-artifact", job)
        self.assertNotIn("--plink", job)
        self.assertNotIn("path: |", job)


if __name__ == "__main__":
    unittest.main()
