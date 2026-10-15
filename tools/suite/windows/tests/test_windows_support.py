"""在项目 .tmp 内验证拒绝门；这些用例不是 Win32 API 执行证据。"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

SUITE_WINDOWS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SUITE_WINDOWS))
from support import (CHECKS, TEMPORARY_ROOT, ValidationError, audit_link_map,
                     decode_native_report, native_environment, project_path)  # noqa: E402
from check_build import assertions, assertions_enabled, installation  # noqa: E402
from run_ctest import inspect_results, validate_selection  # noqa: E402
from run_native_test import run  # noqa: E402


class WindowsContractTest(unittest.TestCase):
    def setUp(self):
        parent = TEMPORARY_ROOT / "windows-python-tests"
        parent.mkdir(parents=True, exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir=parent)
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def test_paths_reject_escape_and_parent_navigation(self):
        self.assertEqual(project_path(self.root), self.root)
        for value in (TEMPORARY_ROOT, TEMPORARY_ROOT.parent / "outside", self.root / ".." / "escape"):
            with self.subTest(value=str(value)), self.assertRaises(ValidationError):
                project_path(value)

    def test_reparse_paths_are_rejected(self):
        target = self.root / "target"
        target.mkdir()
        link = self.root / "link"
        try:
            link.symlink_to(target, target_is_directory=True)
        except OSError:
            self.skipTest("当前身份不能创建链接；原生 reparse 防护仍需 Windows CI 验证")
        with self.assertRaises(ValidationError):
            project_path(link / "file")

    def test_child_environment_preserves_profile_and_removes_agents(self):
        base = {"HOME": "kept-home", "USERPROFILE": "kept-profile", "APPDATA": "kept-appdata",
                "SSH_AUTH_SOCK": "personal", "SSH_ASKPASS": "personal", "PUTTY_SUITE_FAKE_HIVE": "unsafe"}
        environment = native_environment(self.root, base)
        for key in ("HOME", "USERPROFILE", "APPDATA"):
            self.assertEqual(environment[key], base[key])
        self.assertNotIn("SSH_AUTH_SOCK", environment)
        self.assertNotIn("SSH_ASKPASS", environment)
        self.assertNotIn("PUTTY_SUITE_FAKE_HIVE", environment)
        self.assertEqual(environment["TMP"], str(self.root))
        self.assertEqual(base["SSH_AUTH_SOCK"], "personal")

    def test_report_requires_all_checks_and_no_storage_claim(self):
        report = {"schema": 1, "kind": "events", "passed": True,
                  "checks": sorted(CHECKS["events"]), "storage": "not-run"}
        self.assertEqual(decode_native_report(json.dumps(report).encode(), "events"), report)
        for changed in ({"checks": []}, {"checks": report["checks"] * 2}, {"passed": False},
                        {"storage": "isolated"}, {"kind": "handles"}):
            with self.subTest(changed=changed), self.assertRaises(ValidationError):
                decode_native_report(json.dumps({**report, **changed}).encode(), "events")

    def test_ordinary_ci_marker_never_enables_execution_on_other_platform(self):
        with patch("run_native_test.sys.platform", "linux"), patch.dict(os.environ, {"GITHUB_ACTIONS": "true"}), \
                patch("run_native_test.subprocess.run") as child:
            with self.assertRaises(ValidationError):
                run(self.root / "suite-windows-runtime-tests.exe", "events", self.root / "runs")
            child.assert_not_called()

    def test_only_known_no_storage_binary_can_run(self):
        binary = self.root / "plink.exe"
        binary.write_bytes(b"not-executed")
        with patch("run_native_test.sys.platform", "win32"), patch("run_native_test.subprocess.run") as child:
            with self.assertRaises(ValidationError):
                run(binary, "events", self.root / "runs")
            child.assert_not_called()

    def test_link_map_rejects_rng_storage_and_missing_tripwires(self):
        path = self.root / "test.map"
        safe = "no_storage.c.obj read_random_seed write_random_seed open_settings_r open_settings_w"
        path.write_text(safe, encoding="utf-8")
        self.assertTrue(audit_link_map(path)["prng_objects_absent"])
        for bad in (safe + " suite-putty.lib:storage.c.obj", safe + " sshrand.obj", safe + " random_ref",
                    safe + " noise.c.obj", "empty"):
            path.write_text(bad, encoding="utf-8")
            with self.subTest(bad=bad), self.assertRaises(ValidationError):
                audit_link_map(path)

    def test_effective_assertion_flag_order(self):
        self.assertTrue(assertions_enabled('cl /DNDEBUG /UNDEBUG /c source.c'))
        self.assertTrue(assertions_enabled('cl /D NDEBUG=1 /U NDEBUG /c source.c'))
        self.assertFalse(assertions_enabled('cl /UNDEBUG /DNDEBUG /c source.c'))
        self.assertFalse(assertions_enabled('cl /D"NDEBUG" /c source.c'))

    @unittest.skipUnless(shutil.which("ninja"), "缺少 Ninja，响应文件静态生成命令未验")
    def test_ninja_response_file_assertions_are_expanded_without_execution(self):
        build = self.root / "build"
        cli = build / "suite-cli"
        cli.mkdir(parents=True)
        (build / "CMakeCache.txt").write_text("CMAKE_BUILD_TYPE:STRING=Release\n", encoding="utf-8")
        (cli / "CMakeCache.txt").write_text("CMAKE_C_FLAGS_RELEASE:STRING=/O2\n", encoding="utf-8")
        (build / "compile_commands.json").write_text(json.dumps([
            {"command": "cl /DNDEBUG /UNDEBUG -c suite-putty.dir/source.c"}]), encoding="utf-8")
        rule = ('rule CC\n  command = "C:\\tool\\cl.exe" @$out.rsp\n'
                '  rspfile = $out.rsp\n  rspfile_content = /DNDEBUG /UNDEBUG -c $in /Fo$out\n'
                'build test.obj: CC source.c\n')
        (cli / "build.ninja").write_text(rule, encoding="utf-8")
        self.assertEqual(assertions(build, ["psftp"], "Release")["cli_compile_commands"], 1)
        self.assertFalse((cli / "test.obj").exists())
        (cli / "build.ninja").write_text(rule.replace("/DNDEBUG /UNDEBUG", "/UNDEBUG /DNDEBUG"), encoding="utf-8")
        with self.assertRaises(ValidationError):
            assertions(build, ["psftp"], "Release")

    def test_install_inventory_and_configuration_digest(self):
        build, prefix = self.root / "build", self.root / "install"
        (build / "suite-cli/Release").mkdir(parents=True)
        (build / "suite-cli/Debug").mkdir()
        (prefix / "bin").mkdir(parents=True)
        (build / "CMakeCache.txt").write_text("CMAKE_CONFIGURATION_TYPES:STRING=Debug;Release\n", encoding="utf-8")
        source = build / "suite-cli/Release/psftp.exe"
        source.write_bytes(b"Release")
        (build / "suite-cli/Debug/psftp.exe").write_bytes(b"Debug")
        installed = prefix / "bin/psftp.exe"
        shutil.copyfile(source, installed)
        self.assertEqual(installation(build, prefix, ["psftp"], "Release")["files"], ["bin/psftp.exe"])
        with self.assertRaises(ValidationError):
            installation(build, prefix, ["psftp"], "Debug")
        (prefix / "bin/extra.exe").write_bytes(b"extra")
        with self.assertRaises(ValidationError):
            installation(build, prefix, ["psftp"], "Release")

    def test_ctest_zero_missing_and_duplicate_are_failures(self):
        good = {"tests": [{"name": "runtime-windows-handles"}, {"name": "runtime-windows-events"}]}
        validate_selection(good)
        for tests in ([], good["tests"][:1], good["tests"] * 2):
            with self.subTest(tests=tests), self.assertRaises(ValidationError):
                validate_selection({"tests": tests})

    def test_native_skips_and_failures_never_count_as_passed(self):
        path = self.root / "results.xml"
        for tag in ("", "<skipped/>", "<failure/>", "<error/>"):
            path.write_text('<testsuite><testcase name="runtime-windows-handles">' + tag +
                            '</testcase><testcase name="runtime-windows-events"/>'
                            '<testcase name="runtime-lifecycle"><skipped/></testcase></testsuite>', encoding="utf-8")
            if tag:
                with self.assertRaises(ValidationError):
                    inspect_results(path)
            else:
                self.assertEqual(inspect_results(path)["ctest_skipped"], ["runtime-lifecycle"])

    def test_entry_help_survives_cp1252_redirect(self):
        for script in ("run_native_test.py", "run_ctest.py", "check_build.py"):
            environment = dict(os.environ, PYTHONIOENCODING="cp1252")
            result = subprocess.run([sys.executable, "-B", str(SUITE_WINDOWS / script), "--help"],
                                    env=environment, cwd=self.root, capture_output=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(any("\u4e00" <= character <= "\u9fff"
                                for character in result.stdout.decode("utf-8")))

    def test_hive_requirement_rejects_missing_duplicate_skipped_and_failed(self):
        path = self.root / "hive-results.xml"
        native = '<testcase name="runtime-windows-handles"/><testcase name="runtime-windows-events"/>'
        good = '<testcase name="runtime-windows-hive"/>'
        path.write_text('<testsuite>' + native + good + '</testsuite>', encoding="utf-8")
        self.assertEqual(inspect_results(path, True)["private_hive_probe"], "passed")
        for hive in ('', good * 2, '<testcase name="runtime-windows-hive"><skipped/></testcase>',
                     '<testcase name="runtime-windows-hive"><failure/></testcase>',
                     '<testcase name="runtime-windows-hive"><error/></testcase>',
                     '<testcase name="runtime-windows-hive" status="notrun"/>',
                     '<testcase name="runtime-windows-hive" status="disabled"/>',
                     '<testcase name="runtime-windows-hive" status="skipped"/>'):
            path.write_text('<testsuite>' + native + hive + '</testsuite>', encoding="utf-8")
            with self.subTest(hive=hive), self.assertRaises(ValidationError):
                inspect_results(path, True)

        for status in ("notrun", "disabled", "skipped"):
            path.write_text('<testsuite><testcase name="runtime-windows-handles" status="' + status +
                            '"/><testcase name="runtime-windows-events"/></testsuite>', encoding="utf-8")
            with self.subTest(status=status), self.assertRaises(ValidationError):
                inspect_results(path)


if __name__ == "__main__":
    unittest.main()
