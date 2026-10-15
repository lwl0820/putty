"""hive 报告与启动拒绝门的本机回归，不构成 Win32 隔离通过。"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

HIVE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HIVE))
from run_hive_test import CHECKS, TEMPORARY_ROOT, ValidationError, child_environment, decode_report, run  # noqa: E402


class HiveRunnerTest(unittest.TestCase):
    def test_report_requires_isolation_checks_and_preserves_unverified_groups(self):
        report = {"schema": 1, "kind": "private-hive", "passed": True, "checks": sorted(CHECKS),
                  "upstream_storage": "not-run", "upstream_seed": "not-run", "prng": "not-run"}
        self.assertEqual(decode_report(json.dumps(report).encode()), report)
        for fields in ({"checks": []}, {"checks": report["checks"] * 2}, {"passed": False},
                       {"upstream_seed": "passed"}, {"upstream_storage": "passed"}, {"prng": "passed"}):
            with self.subTest(fields=fields), self.assertRaises(ValidationError):
                decode_report(json.dumps({**report, **fields}).encode())

    def test_ci_marker_never_bypasses_native_platform(self):
        with patch("run_hive_test.sys.platform", "linux"), patch.dict(os.environ, {"GITHUB_ACTIONS": "true"}), \
                patch("run_hive_test.subprocess.run") as child:
            with self.assertRaises(ValidationError):
                run(TEMPORARY_ROOT / "suite-windows-hive-tests.exe", TEMPORARY_ROOT / "windows-hive")
            child.assert_not_called()

    def test_upstream_cli_is_never_executed(self):
        parent = TEMPORARY_ROOT / "windows-hive-python-tests"
        parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=parent) as root:
            binary = Path(root) / "plink.exe"
            binary.write_bytes(b"not-executed")
            with patch("run_hive_test.sys.platform", "win32"), patch("run_hive_test.subprocess.run") as child:
                with self.assertRaises(ValidationError):
                    run(binary, TEMPORARY_ROOT / "windows-hive")
                child.assert_not_called()

    def test_child_environment_preserves_personal_profile_and_parent(self):
        with patch.dict(os.environ, {"HOME": "unchanged", "USERPROFILE": "unchanged-profile",
                                     "APPDATA": "unchanged-appdata", "SSH_AUTH_SOCK": "personal",
                                     "PUTTY_SUITE_FAKE_HIVE": "untrusted"}):
            environment = child_environment()
            for key in ("HOME", "USERPROFILE", "APPDATA"):
                self.assertEqual(environment[key], os.environ[key])
            self.assertNotIn("SSH_AUTH_SOCK", environment)
            self.assertNotIn("PUTTY_SUITE_FAKE_HIVE", environment)
            self.assertEqual(os.environ["SSH_AUTH_SOCK"], "personal")
            self.assertEqual(environment["TMP"], str(TEMPORARY_ROOT))

    def test_help_is_utf8_under_cp1252_redirection(self):
        result = subprocess.run([sys.executable, "-B", str(HIVE / "run_hive_test.py"), "--help"],
                                capture_output=True, timeout=10,
                                env=dict(os.environ, PYTHONIOENCODING="cp1252"))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("私有", result.stdout.decode("utf-8"))


if __name__ == "__main__":
    unittest.main()
