"""SSPI 负例的执行拒绝门；这些测试不提供 Windows API 运行证据。"""
import json
import os
from pathlib import Path, PureWindowsPath
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
import run_sspi_test as runner  # noqa: E402


def valid_report():
    return {"schema": 1, "kind": "sspi-negative", "passed": True,
            "storage": "not-run", "prng": "not-run", "checks": sorted(runner.CHECKS),
            "secur32_path": r"C:\Windows\System32\secur32.dll", "native_api": True,
            "kerberos_authentication_verified": False, "pointer_bits": 64, "processor_architecture": 9,
            "warmup_iterations": 8, "measured_iterations": 128, "handles_before": 50, "handles_after": 50,
            "statuses": {name: 0 if name in {"package", "package_free"} else runner.INVALID_HANDLE
                         for name in runner.STATUS_FIELDS},
            "calls": {name: 136 if name in runner.REPEATED_CALLS else 0
                      for name in runner.REPEATED_CALLS | runner.ZERO_CALLS}}


def valid_map():
    symbols = ("main", "safemalloc", "safefree", "p_InitializeSecurityContextA",
               "p_QueryContextAttributesA", "p_MakeSignature", "p_VerifySignature")
    content = "\n".join(f" 0001:{number:08x} {name} 0000000140000000 f windows_sspi_negative_test.c.obj"
                        for number, name in enumerate(symbols))
    return content + "\n" + "\n".join(
        " 0002:00000000 __imp_" + name + " 0000000140000000 kernel32.dll"
        for name in ("GetProcAddress", "LoadLibraryExW", "GetSystemDirectoryW", "GetModuleFileNameW",
                     "GetProcessHandleCount", "FreeLibrary"))


class SSPIRunnerTest(unittest.TestCase):
    def setUp(self):
        parent = runner.TEMPORARY_ROOT / "sspi-runner-tests"
        parent.mkdir(parents=True, exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir=parent)
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def binary(self, name=runner.BINARY_NAME):
        path = self.root / name
        path.write_bytes(b"never-executed")
        Path(str(path) + ".map").write_text(valid_map(), encoding="utf-8")
        return path

    def test_native_platform_cannot_be_overridden_by_ci_marker(self):
        with patch.object(runner.sys, "platform", "linux"), patch.dict(os.environ, {"GITHUB_ACTIONS": "true"}), \
                patch.object(runner.subprocess, "run") as child:
            with self.assertRaises(runner.ValidationError):
                runner.run(self.root / runner.BINARY_NAME)
            child.assert_not_called()

    def test_only_named_native_negative_host_can_run(self):
        for name in ("plink.exe", "pscp.exe", "psftp.exe", "putty-qt.exe", "putty-terminal-tests.exe"):
            binary = self.binary(name)
            with self.subTest(name=name), patch.object(runner.sys, "platform", "win32"), \
                    patch.object(runner.subprocess, "run") as child:
                with self.assertRaises(runner.ValidationError):
                    runner.run(binary)
                child.assert_not_called()

    def test_binary_and_map_must_be_ordinary_unlinked_project_files(self):
        binary = self.binary()
        for name in (runner.TEMPORARY_ROOT.parent / runner.BINARY_NAME,
                     self.root / ".." / runner.BINARY_NAME):
            with self.subTest(name=name), self.assertRaises(runner.ValidationError):
                runner.ordinary_file(name)
        alias = self.root / "hard-link.exe"
        os.link(binary, alias)
        with self.assertRaises(runner.ValidationError):
            runner.ordinary_file(binary)
        alias.unlink()
        with patch.object(Path, "stat", return_value=type("Info", (), {"st_mode": stat.S_IFDIR, "st_nlink": 1})()):
            with self.assertRaises(runner.ValidationError):
                runner.ordinary_file(binary)
        with patch.object(Path, "lstat", return_value=type("Info", (), {"st_mode": stat.S_IFDIR, "st_file_attributes": 0x400})()):
            with self.assertRaises(runner.ValidationError):
                runner.ordinary_file(binary)

    def test_map_requires_host_real_transparent_calls_and_system_entries(self):
        path = Path(str(self.binary()) + ".map")
        self.assertTrue(runner.audit_map(path)["native_host_only"])
        for symbol in ("windows_sspi_negative_test", "p_InitializeSecurityContextA", "p_QueryContextAttributesA",
                       "p_MakeSignature", "p_VerifySignature", "GetProcessHandleCount", "LoadLibraryExW"):
            path.write_text(valid_map().replace(symbol, "missing"), encoding="utf-8")
            with self.subTest(symbol=symbol), self.assertRaises(runner.ValidationError):
                runner.audit_map(path)

    def test_map_rejects_full_core_prng_storage_qt_and_default_credential_imports(self):
        path = Path(str(self.binary()) + ".map")
        for extra in ("suite-putty.lib:harmless.obj", "sshrand.c.obj", "storage.obj", "noise.c.obj", "prng.obj",
                      "Qt6Core.lib", "Qt6Widgets.dll", "random_ref", "read_random_seed", "open_settings_w",
                      " 0002:00000000 __imp_AcquireCredentialsHandleA 0000000140000000 secur32.dll",
                      " 0002:00000000 AcquireCredentialsHandleW 0000000140000000 secur32.dll",
                      " 0002:00000000 _AcquireCredentialsHandleA@36 0000000140000000 secur32.dll"):
            path.write_text(valid_map() + "\n" + extra, encoding="utf-8")
            with self.subTest(extra=extra), self.assertRaises(runner.ValidationError):
                runner.audit_map(path)
        # GetProcAddress 的导出名字符串可以存在，不等于凭据获取函数静态导入。
        path.write_text(valid_map() + '\nGetProcAddress(module, "AcquireCredentialsHandleA")', encoding="utf-8")
        runner.audit_map(path)

    def test_report_requires_exact_unique_checks_and_no_positive_claim(self):
        report = valid_report()
        self.assertEqual(runner.decode(json.dumps(report).encode()), report)
        for changed in ({"checks": []}, {"checks": report["checks"][:-1]}, {"checks": report["checks"] * 2},
                        {"checks": [1] + report["checks"][1:]}, {"passed": False}, {"schema": True},
                        {"kind": "sspi-positive"}, {"native_api": False}, {"storage": "passed"},
                        {"prng": "passed"}, {"kerberos_authentication_verified": True}, {"gss_verified": True}):
            with self.subTest(changed=changed), self.assertRaises(runner.ValidationError):
                runner.decode(json.dumps({**report, **changed}).encode())
        for missing in runner.REPORT_FIELDS:
            changed = {key: value for key, value in report.items() if key != missing}
            with self.subTest(missing=missing), self.assertRaises(runner.ValidationError):
                runner.decode(json.dumps(changed).encode())

    def test_report_rejects_missing_duplicate_and_non_json_evidence(self):
        output = json.dumps(valid_report()).encode()
        for changed in (b"", b"[]", output + output, b"\xff", b"x" * 65537,
                        output.replace(b'"schema": 1', b'"schema": 1, "schema": 1'),
                        output.replace(b'"acquire": 0', b'"acquire": 0, "acquire": 0')):
            with self.subTest(changed=changed[:80]), self.assertRaises(runner.ValidationError):
                runner.decode(changed)

    def test_report_requires_actual_sdk_failures_iterations_and_zero_sensitive_calls(self):
        for field, value in (("pointer_bits", 32), ("processor_architecture", 12), ("warmup_iterations", 0),
                             ("measured_iterations", 127), ("handles_before", 0), ("handles_after", 51),
                             ("measured_iterations", True), ("handles_after", 50.0)):
            report = valid_report()
            report[field] = value
            with self.subTest(field=field, value=value), self.assertRaises(runner.ValidationError):
                runner.decode(json.dumps(report).encode())
        for group in ("statuses", "calls"):
            for field in valid_report()[group]:
                report = valid_report()
                report[group][field] = 1
                with self.subTest(group=group, field=field), self.assertRaises(runner.ValidationError):
                    runner.decode(json.dumps(report).encode())
                del report[group][field]
                with self.subTest(group=group, missing=field), self.assertRaises(runner.ValidationError):
                    runner.decode(json.dumps(report).encode())

    def test_report_library_path_must_be_system32_secur32(self):
        for value in ("secur32.dll", r"C:\temp\secur32.dll", r"C:\Windows\System32\other.dll",
                      "C:\\Windows\\System32\\secur32.dll\n", r"C:\Windows\System32\..\secur32.dll", None):
            with self.subTest(value=value), self.assertRaises(runner.ValidationError):
                runner.decode(json.dumps({**valid_report(), "secur32_path": value}).encode())

    def test_only_fixed_diagnostic_expressions_are_published(self):
        known = "原生 SSPI 负例失败，第 124 行：statuses.mic == SEC_E_INVALID_HANDLE && mic_empty"
        secret = "原生 SSPI 宿主失败，第 123 行：private-ticket-or-secret"
        result = runner.safe_diagnostics((known + "\n" + secret + "\nsecret\n").encode())
        self.assertEqual(result, known)
        self.assertLessEqual(len(runner.safe_diagnostics(((known + "\n") * 100).encode())), 1536)
        self.assertEqual(runner.safe_diagnostics(b"\xffsecret"), "")

    def test_native_run_sanitizes_environment_returns_summary_and_cleans_root(self):
        binary = self.binary()
        output = subprocess.CompletedProcess([], 0, json.dumps(valid_report()).encode(), b"")
        with patch.object(runner.sys, "platform", "win32"), patch.object(runner, "system_secur32",
                return_value=PureWindowsPath(r"C:\Windows\System32\secur32.dll")), \
                patch.dict(os.environ, {"KRB5CCNAME": "personal", "SSH_AUTH_SOCK": "personal"}), \
                patch.object(runner.subprocess, "run", return_value=output) as child:
            report = runner.run(binary)
        self.assertFalse(report["kerberos_authentication_verified"])
        self.assertEqual(report["negative_api_calls"], 816)
        self.assertIn("terminal-long-sspi-call", report["unverified"])
        options = child.call_args.kwargs
        self.assertEqual(child.call_args.args[0], [str(binary)])
        self.assertNotIn("KRB5CCNAME", options["env"])
        self.assertNotIn("SSH_AUTH_SOCK", options["env"])
        self.assertEqual(options["env"]["TMP"], str(options["cwd"]))
        self.assertEqual(options["timeout"], 20)
        self.assertFalse(options["cwd"].exists())

    def test_native_run_rejects_failure_path_mismatch_and_unexpected_files(self):
        binary = self.binary()
        known = "原生 SSPI 宿主失败，第 219 行：run_iteration()"
        outputs = [subprocess.CompletedProcess([], 1, b"", (known + "\nsecret-ticket").encode()),
                   subprocess.CompletedProcess([], 0, json.dumps({**valid_report(), "secur32_path":
                       r"D:\Windows\System32\secur32.dll"}).encode(), b"")]
        for output in outputs:
            with self.subTest(returncode=output.returncode), patch.object(runner.sys, "platform", "win32"), \
                    patch.object(runner, "system_secur32", return_value=PureWindowsPath(r"C:\Windows\System32\secur32.dll")), \
                    patch.object(runner.subprocess, "run", return_value=output) as child:
                with self.assertRaises(runner.ValidationError) as raised:
                    runner.run(binary)
                self.assertNotIn("secret-ticket", str(raised.exception))
                self.assertFalse(child.call_args.kwargs["cwd"].exists())

        def write_file(*arguments, **options):
            (options["cwd"] / "unexpected").write_text("unexpected", encoding="utf-8")
            return subprocess.CompletedProcess([], 0, json.dumps(valid_report()).encode(), b"")

        with patch.object(runner.sys, "platform", "win32"), \
                patch.object(runner, "system_secur32", return_value=PureWindowsPath(r"C:\Windows\System32\secur32.dll")), \
                patch.object(runner.subprocess, "run", side_effect=write_file):
            with self.assertRaises(runner.ValidationError):
                runner.run(binary)

    def test_main_writes_only_fixed_report_and_replaces_stale_success_with_failure(self):
        destination = self.root / "reports"
        destination.mkdir()
        (destination / "sspi-negative.json").write_text('{"passed":true}', encoding="utf-8")
        with patch.object(runner, "REPORT_DIRECTORY", destination), \
                patch.object(runner, "run", side_effect=runner.ValidationError("固定错误")), \
                patch.object(sys, "argv", ["run_sspi_test.py", "--binary", str(self.root / runner.BINARY_NAME)]):
            self.assertEqual(runner.main(), 1)
        report = json.loads((destination / "sspi-negative.json").read_text(encoding="utf-8"))
        self.assertFalse(report["passed"])
        self.assertFalse(report["kerberos_authentication_verified"])
        self.assertEqual(report["storage"], "not-run")
        self.assertEqual(set(destination.iterdir()), {destination / "sspi-negative.json"})
        self.assertEqual(runner.REPORT_DIRECTORY, runner.TEMPORARY_ROOT / "windows-ci/reports")

    def test_help_remains_utf8_under_cp1252_redirection(self):
        result = subprocess.run([sys.executable, "-B", str(HERE / "run_sspi_test.py"), "--help"],
                                capture_output=True, timeout=10,
                                env=dict(os.environ, PYTHONIOENCODING="cp1252"))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("负例", result.stdout.decode("utf-8"))


if __name__ == "__main__":
    unittest.main()
