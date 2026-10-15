"""验证报告、链接和入口拒绝，不能替代真实 Windows 存储执行。"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

STORAGE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(STORAGE))
from run_storage_test import (CHECKS, STORAGE_SHA256, TEMPORARY_ROOT, ValidationError,
                              audit_link_map, audit_storage_source, decode_report, run)  # noqa: E402


class StorageRunnerTest(unittest.TestCase):
    def setUp(self):
        parent = TEMPORARY_ROOT / "windows-storage-python-tests"
        parent.mkdir(parents=True, exist_ok=True)
        self.directory = tempfile.TemporaryDirectory(dir=parent)
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def test_report_requires_all_actual_checks_and_unverified_production(self):
        report = {"schema": 1, "kind": "private-storage", "passed": True, "checks": sorted(CHECKS),
                  "storage_variant": "guarded-win32", "production_fallback": "not-run",
                  "upstream_cli": "not-run", "existing_tests_enabled": False}
        self.assertEqual(decode_report(json.dumps(report).encode()), report)
        for fields in ({"checks": []}, {"checks": report["checks"] * 2}, {"passed": False},
                       {"production_fallback": "passed"}, {"upstream_cli": "passed"}, {"existing_tests_enabled": True}):
            with self.subTest(fields=fields), self.assertRaises(ValidationError):
                decode_report(json.dumps({**report, **fields}).encode())

    def test_map_requires_real_sources_and_excludes_production_storage(self):
        path = self.root / "storage.map"
        objects = ["storage_variant", "seed_guard", "settings", "sshrand", "noise"]
        symbols = ["save_settings", "load_settings", "store_host_key", "check_stored_host_key", "host_ca_save",
                   "host_ca_load", "host_ca_delete", "read_random_seed", "write_random_seed", "random_ref", "random_read", "random_clear",
                   "prng_new", "prng_free", "noise_get_heavy", "win_read_random", "schedule_timer", "suite_seed_CreateFileA"]
        valid = "\n".join([*("suite.lib:" + obj + ".c.obj" for obj in objects), *symbols])
        path.write_text(valid, encoding="utf-8")
        self.assertTrue(audit_link_map(path)["guarded_variant_only"])
        for invalid in (valid + "\nsuite-putty.lib:storage.c.obj", valid.replace("sshrand.c.obj", "fake.obj"),
                        valid.replace("prng_new", "fuzzing_fake"), valid.replace("suite_seed_CreateFileA", "CreateFileA")):
            with self.subTest(invalid=invalid):
                path.write_text(invalid, encoding="utf-8")
                with self.assertRaises(ValidationError):
                    audit_link_map(path)

    def test_source_change_requires_review_before_execution(self):
        self.assertEqual(audit_storage_source(), STORAGE_SHA256)
        from support import ROOT
        same = self.root / "crlf-storage.c"
        same.write_bytes((ROOT / "vendor/putty/windows/storage.c").read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))
        self.assertEqual(audit_storage_source(same), STORAGE_SHA256)
        path = self.root / "changed-storage.c"
        path.write_text("CreateFileW(unreviewed_candidate);", encoding="utf-8")
        with self.assertRaises(ValidationError):
            audit_storage_source(path)

    def test_fake_ci_marker_never_bypasses_native_gate(self):
        with patch("run_storage_test.sys.platform", "linux"), patch.dict(os.environ, {"GITHUB_ACTIONS": "true"}), \
                patch("run_storage_test.subprocess.run") as child:
            with self.assertRaises(ValidationError):
                run(self.root / "suite-windows-storage-tests.exe", TEMPORARY_ROOT / "windows-storage")
            child.assert_not_called()

    def test_cli_name_never_launches_or_creates_private_parent(self):
        binary = self.root / "plink.exe"
        binary.write_bytes(b"not-executed")
        parent = TEMPORARY_ROOT / "windows-storage-forbidden-parent"
        with patch("run_storage_test.sys.platform", "win32"), patch("run_storage_test.subprocess.run") as child:
            with self.assertRaises(ValidationError):
                run(binary, parent)
            child.assert_not_called()
        self.assertFalse(parent.exists())

    def test_help_is_utf8_under_cp1252_redirection(self):
        result = subprocess.run([sys.executable, "-B", str(STORAGE / "run_storage_test.py"), "--help"],
                                capture_output=True, timeout=10,
                                env=dict(os.environ, PYTHONIOENCODING="cp1252"))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("存储", result.stdout.decode("utf-8"))


if __name__ == "__main__":
    unittest.main()
