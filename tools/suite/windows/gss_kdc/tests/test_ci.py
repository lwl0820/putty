"""Windows CI 入口的真实只读观察和失败报告契约；Linux 仅验证逻辑。"""
import copy
from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import unittest
from unittest import mock
import uuid

ROOT = Path(__file__).resolve().parents[5]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from tools.suite.windows.gss_kdc import ci
from tools.suite.windows.gss_kdc.security import PrivateTree
from tools.suite.windows.support import ValidationError


def successful_protocol():
    closed = {"passed": True, "native_windows": True, "private_data_removed": True,
              "kdc": {"active_connections": 0, "active_workers": 0},
              "proxy": {"active_connections": 0, "live_workers": 0}}
    return {"schema": 1, "kind": "real-mit-kdc-crosscheck", "passed": True,
            **{name: True for name in ("native_windows", "only_tgt_after_as", "service_keytab_decryption_verified",
               "forwardable_as_rejected", "forwarding_rejected", "cross_realm_rejected", "unknown_service_rejected",
               "reset_removes_service_ticket", "oversize_zero_malformed_rejected")},
            "kvno": 1, "network_policy_probes": 8, "connection_budget": 64,
            "proxy_worker_budget": 8, "proxy_socket_budget": 16,
            "tgs_elapsed_seconds": 1.6, "inflight_close_seconds": 0.2,
            "normal": copy.deepcopy(closed), "inflight": copy.deepcopy(closed)}


class CIEntryTests(unittest.TestCase):
    def setUp(self):
        self.root = ROOT / ".tmp" / ("kdc-ci-contract-" + uuid.uuid4().hex)
        self.tree = PrivateTree(self.root)
        self.profile = self.tree.directory(self.root / "user-observation")
        self.data = self.tree.directory(self.profile / ".dotnet")
        self.file = self.tree.write(self.data / "existing.crl", b"private-content")
        self.mit = self.tree.directory(self.root / "mit")
        self.tools = self.root / "tools"
        self.parent = self.root / "runs"
        self.report = self.root / "report.json"

    def tearDown(self):
        self.tree.close()
        shutil.rmtree(self.root)

    def patches(self):
        stack = ExitStack()
        stack.enter_context(mock.patch.object(ci, "native_windows", return_value=True))
        stack.enter_context(mock.patch.object(ci, "actual_profile", return_value=self.profile))
        self.validate = stack.enter_context(mock.patch.object(ci, "validate_directory", return_value={"version": "4.1"}))
        self.prepare = stack.enter_context(mock.patch.object(ci, "prepare", return_value={
            "schema": 1, "kind": "gss-kdc-preparation", "passed": True, "sdk_version": ci.LOCK["dotnet_sdk_version"],
            "package_count": len(ci.LOCK["nuget_packages"]), "sdk_files_verified": 4704,
            "source_sha256": hashlib.sha256(b"public-source").hexdigest()}))
        self.verify = stack.enter_context(mock.patch.object(ci, "verify", return_value=successful_protocol()))
        return stack

    def run_entry(self):
        return ci.run(self.tools, self.mit, self.parent, self.report)

    def test_snapshot_is_readonly_and_detects_same_size_content_change(self):
        before = ci.snapshot_profile(self.profile)
        self.assertEqual(before, ci.snapshot_profile(self.profile))
        previous = self.file.stat()
        self.file.write_bytes(b"changed-content")
        os.utime(self.file, ns=(previous.st_atime_ns, previous.st_mtime_ns))
        after = ci.snapshot_profile(self.profile)
        self.assertNotEqual(before, after)
        self.assertEqual(before.total_bytes, after.total_bytes)
        self.assertEqual(before.files, 1)

    def test_snapshot_detects_new_missing_and_metadata_changes(self):
        before = ci.snapshot_profile(self.profile)
        created = self.tree.directory(self.profile / ".nuget")
        self.assertNotEqual(before, ci.snapshot_profile(self.profile))
        created.rmdir()
        previous = self.file.stat()
        before = ci.snapshot_profile(self.profile)
        os.utime(self.file, ns=(previous.st_atime_ns, previous.st_mtime_ns + 10000000))
        self.assertNotEqual(before, ci.snapshot_profile(self.profile))

    def test_snapshot_rejects_hardlinks_and_size_budget(self):
        os.link(self.file, self.data / "linked")
        with self.assertRaises(ValidationError):
            ci.snapshot_profile(self.profile)
        (self.data / "linked").unlink()
        with mock.patch.object(ci, "MAX_FILE", 1):
            with self.assertRaises(ValidationError):
                ci.snapshot_profile(self.profile)
        with mock.patch.object(ci, "MAX_ENTRIES", 1):
            with self.assertRaises(ValidationError):
                ci.snapshot_profile(self.profile)
        with mock.patch.object(ci, "SNAPSHOT_SECONDS", 0):
            with self.assertRaises(ValidationError):
                ci.snapshot_profile(self.profile)

    @unittest.skipIf(sys.platform == "win32", "Windows 无符号链接权限时不创建链接；原生 reparse 另测")
    def test_snapshot_rejects_symlink_and_linked_ancestor(self):
        linked = self.data / "linked"
        linked.symlink_to(self.file)
        with self.assertRaises(ValidationError):
            ci.snapshot_profile(self.profile)
        linked.unlink()
        linked.symlink_to(self.profile, target_is_directory=True)
        with self.assertRaises(ValidationError):
            ci.snapshot_profile(linked)
        linked.unlink()

    def test_success_publishes_only_white_list_after_cleanup_and_observation(self):
        self.report.write_text('{"passed": true, "old": "sensitive"}', encoding="utf-8")
        before_environment = dict(os.environ)
        with self.patches():
            self.verify.return_value["secret"] = str(self.profile)
            self.verify.return_value["normal"]["keytab"] = "private-ticket-material"
            def prepare_checked(_):
                pending = json.loads(self.report.read_text(encoding="utf-8"))
                self.assertFalse(pending["passed"])
                self.assertEqual(pending["stage"], "pending")
                return self.prepare.return_value
            self.prepare.side_effect = prepare_checked
            result = self.run_entry()
            self.validate.assert_called_once_with(self.mit)
            self.verify.assert_called_once_with(self.tools, self.parent,
                self.mit / "kinit.exe", self.mit / "kvno.exe", self.mit / "klist.exe")
        self.assertTrue(result["passed"])
        self.assertTrue(result["personal_metadata_unchanged"])
        self.assertTrue(result["private_parent_removed"])
        self.assertFalse(self.parent.exists())
        self.assertEqual(dict(os.environ), before_environment)
        content = self.report.read_text(encoding="utf-8")
        for private in (str(self.profile), "private-content", "private-ticket-material", "sensitive", "secret"):
            self.assertNotIn(private, content)
        self.assertFalse(result["sspi_verified"])
        self.assertFalse(result["formal_plink_verified"])
        self.assertFalse(result["gss_ssh_verified"])
        self.assertEqual(json.loads(content)["stage"], "pending")
        self.assertFalse(json.loads(content)["passed"])

    def cli_arguments(self):
        return ["ci", "--tools", str(self.tools), "--mit-directory", str(self.mit),
                "--parent", str(self.parent), "--report", str(self.report)]

    def test_cli_flushes_candidate_before_final_atomic_publication(self):
        original_publish = ci.publish_report
        events = []

        class CandidateStream:
            def write(_, value):
                events.append("write")
                return len(value)

            def flush(_):
                events.append("flush")

        def publish(path, result):
            if result["passed"]:
                self.assertIn("flush", events)
                self.assertFalse(json.loads(self.report.read_text(encoding="utf-8"))["passed"])
                events.append("commit")
            original_publish(path, result)

        with self.patches(), mock.patch.object(sys, "argv", self.cli_arguments()), \
                mock.patch.object(sys, "stdout", CandidateStream()), \
                mock.patch.object(ci, "publish_report", side_effect=publish):
            self.assertEqual(ci.main(), 0)
        self.assertEqual(events[-1], "commit")
        self.assertTrue(json.loads(self.report.read_text(encoding="utf-8"))["passed"])

    def test_cli_output_or_final_publication_failure_keeps_pending(self):
        original_publish = ci.publish_report

        class FailedStream:
            def write(_, value):
                return len(value)

            def flush(_):
                raise OSError("内部输出失败")

        for mode in ("stdout", "report"):
            with self.subTest(mode=mode):
                self.report.write_text('{"passed": true}', encoding="utf-8")
                def publish(path, result):
                    if mode == "report" and result["passed"]:
                        raise OSError("内部报告失败")
                    original_publish(path, result)
                stream = FailedStream() if mode == "stdout" else mock.Mock()
                with self.patches(), mock.patch.object(sys, "argv", self.cli_arguments()), \
                        mock.patch.object(sys, "stdout", stream), mock.patch.object(sys, "stderr"), \
                        mock.patch.object(ci, "publish_report", side_effect=publish):
                    self.assertEqual(ci.main(), 1)
                pending = json.loads(self.report.read_text(encoding="utf-8"))
                self.assertFalse(pending["passed"])
                self.assertEqual(pending["stage"], "pending")

    def test_deadline_start_failure_keeps_pending_and_does_not_execute(self):
        self.report.write_text('{"passed": true}', encoding="utf-8")
        with mock.patch.object(sys, "argv", self.cli_arguments()), \
                mock.patch.object(ci.threading.Thread, "start", side_effect=RuntimeError("内部启动失败")), \
                mock.patch.object(ci, "run") as execute, mock.patch.object(sys, "stderr"):
            self.assertEqual(ci.main(), 1)
            execute.assert_not_called()
        pending = json.loads(self.report.read_text(encoding="utf-8"))
        self.assertFalse(pending["passed"])
        self.assertEqual(pending["stage"], "pending")

    def test_real_cli_deadline_covers_prepare_cleanup_stdout_and_final_report(self):
        script = r'''
import hashlib
from pathlib import Path
import sys
import time
from tools.suite.windows.gss_kdc import ci
from tools.suite.windows.gss_kdc.tests.test_ci import successful_protocol
root, mode = Path(sys.argv[1]), sys.argv[2]
ci.native_windows = lambda: True
ci.actual_profile = lambda: root / "user-observation"
ci.validate_directory = lambda _: {"version": "4.1"}
ci.prepare = lambda _: {
    "schema": 1, "kind": "gss-kdc-preparation", "passed": True,
    "sdk_version": ci.LOCK["dotnet_sdk_version"], "package_count": len(ci.LOCK["nuget_packages"]),
    "sdk_files_verified": 4704, "source_sha256": hashlib.sha256(b"public-source").hexdigest()}
ci.verify = lambda *_: successful_protocol()
def hang(*_):
    (root / ("entered-" + mode)).write_text("true", encoding="ascii")
    while True:
        time.sleep(1)
if mode == "prepare":
    ci.prepare = hang
elif mode == "cleanup":
    ci.PrivateTree.remove = hang
elif mode == "stdout":
    class HungStream:
        def write(self, value):
            return len(value)
        def flush(self):
            hang()
    sys.stdout = HungStream()
elif mode == "report":
    publish = ci.publish_report
    def final_report(path, report):
        if report["passed"]:
            hang()
        publish(path, report)
    ci.publish_report = final_report
sys.argv = ["ci", "--tools", str(root / "tools"), "--mit-directory", str(root / "mit"),
            "--parent", str(root / ("runs-" + mode)), "--report", str(root / "report.json")]
raise SystemExit(ci.main(deadline_seconds=2))
'''
        for mode in ("prepare", "cleanup", "stdout", "report"):
            with self.subTest(mode=mode):
                self.report.write_text('{"passed": true}', encoding="utf-8")
                child = subprocess.run([sys.executable, "-B", "-c", script, str(self.root), mode],
                    cwd=ROOT, capture_output=True, timeout=15)
                self.assertEqual(child.returncode, 124, child.stderr.decode("utf-8", errors="replace"))
                self.assertEqual((self.root / ("entered-" + mode)).read_text(encoding="ascii"), "true")
                pending = json.loads(self.report.read_text(encoding="utf-8"))
                self.assertFalse(pending["passed"])
                self.assertEqual(pending["stage"], "pending")

    def test_prepare_failure_still_observes_and_replaces_old_success(self):
        self.report.write_text('{"passed": true}', encoding="utf-8")
        with self.patches():
            self.prepare.side_effect = ValidationError(str(self.profile))
            result = self.run_entry()
            self.verify.assert_not_called()
        self.assertFalse(result["passed"])
        self.assertEqual(result["stage"], "prepare-kdc")
        self.assertTrue(result["personal_metadata_after_observed"])
        self.assertTrue(result["personal_metadata_unchanged"])
        self.assertTrue(result["private_parent_removed"])
        self.assertNotIn(str(self.profile), self.report.read_text(encoding="utf-8"))

    def test_personal_change_overrides_protocol_success(self):
        with self.patches():
            def changed(*_):
                self.file.write_bytes(b"changed-content")
                return successful_protocol()
            self.verify.side_effect = changed
            result = self.run_entry()
        self.assertFalse(result["passed"])
        self.assertFalse(result["personal_metadata_unchanged"])
        self.assertEqual(result["stage"], "personal-metadata-changed")
        self.assertNotIn("protocol", result)

    def test_prepare_failure_with_personal_change_is_also_reported(self):
        with self.patches():
            def changed(_):
                self.file.write_bytes(b"changed-content")
                raise ValidationError("不公开内部路径")
            self.prepare.side_effect = changed
            result = self.run_entry()
            self.verify.assert_not_called()
        self.assertFalse(result["passed"])
        self.assertTrue(result["personal_metadata_after_observed"])
        self.assertFalse(result["personal_metadata_unchanged"])
        self.assertEqual(result["stage"], "personal-metadata-changed")

    def test_verify_failure_preserves_failure_and_observes_after(self):
        with self.patches():
            self.verify.side_effect = ValidationError(str(self.profile))
            result = self.run_entry()
        self.assertFalse(result["passed"])
        self.assertEqual(result["stage"], "real-as-tgs")
        self.assertTrue(result["personal_metadata_after_observed"])
        self.assertTrue(result["private_parent_removed"])

    def test_after_observation_failure_blocks_success(self):
        before = ci.snapshot_profile(self.profile)
        with self.patches(), mock.patch.object(ci, "snapshot_profile", side_effect=[before, ValidationError("拒绝")]):
            result = self.run_entry()
        self.assertFalse(result["passed"])
        self.assertFalse(result["personal_metadata_after_observed"])
        self.assertEqual(result["stage"], "personal-metadata-after")

    def test_before_observation_failure_still_attempts_after_and_blocks_preparation(self):
        after = ci.snapshot_profile(self.profile)
        with self.patches(), mock.patch.object(ci, "snapshot_profile", side_effect=[ValidationError("拒绝"), after]):
            result = self.run_entry()
            self.prepare.assert_not_called()
        self.assertFalse(result["passed"])
        self.assertFalse(result["personal_metadata_before_observed"])
        self.assertTrue(result["personal_metadata_after_observed"])
        self.assertFalse(result["personal_metadata_unchanged"])
        self.assertEqual(result["stage"], "personal-metadata-before")

    def test_cleanup_close_failure_cannot_skip_after_observation(self):
        original_close = PrivateTree.close
        def fail_after_close(tree):
            original_close(tree)
            raise ValidationError("拒绝释放")
        with self.patches(), mock.patch.object(PrivateTree, "remove", side_effect=ValidationError("拒绝清理")), \
                mock.patch.object(PrivateTree, "close", autospec=True, side_effect=fail_after_close):
            result = self.run_entry()
        self.assertFalse(result["passed"])
        self.assertTrue(result["personal_metadata_after_observed"])
        self.assertEqual(result["stage"], "private-parent-cleanup")

    def test_unknown_run_file_is_preserved_and_blocks_success(self):
        with self.patches():
            def dirty(*_):
                (self.parent / "unknown").write_bytes(b"keep")
                return successful_protocol()
            self.verify.side_effect = dirty
            result = self.run_entry()
        self.assertFalse(result["passed"])
        self.assertEqual(result["stage"], "private-parent-cleanup")
        self.assertTrue(result["personal_metadata_after_observed"])
        self.assertEqual((self.parent / "unknown").read_bytes(), b"keep")

    def test_existing_parent_rejected_before_prepare(self):
        self.tree.directory(self.parent)
        with self.patches():
            result = self.run_entry()
            self.prepare.assert_not_called()
        self.assertFalse(result["passed"])
        self.assertEqual(result["stage"], "private-parent")
        self.assertTrue(self.parent.exists())

    def test_no_native_windows_is_an_explicit_unsuccessful_skip(self):
        with mock.patch.object(ci, "native_windows", return_value=False), \
                mock.patch.object(ci, "actual_profile") as profile:
            result = self.run_entry()
        profile.assert_not_called()
        self.assertFalse(result["passed"])
        self.assertTrue(result["skipped"])
        self.assertEqual(result["stage"], "windows-required")

    def test_protocol_rejects_missing_cleanup_and_nonfinite_values(self):
        for mutate in (
            lambda result: result.update(schema=True),
            lambda result: result["normal"].update(private_data_removed=False),
            lambda result: result.update(normal=True),
            lambda result: result["inflight"].update(kdc=None),
            lambda result: result["inflight"]["proxy"].update(live_workers=1),
            lambda result: result.update(network_policy_probes=True),
            lambda result: result.update(tgs_elapsed_seconds=float("nan")),
        ):
            result = successful_protocol()
            mutate(result)
            with self.assertRaises(ValidationError):
                ci.protocol_summary(result)
        with self.assertRaises(ValidationError):
            ci.protocol_summary(None)


if __name__ == "__main__":
    unittest.main()
