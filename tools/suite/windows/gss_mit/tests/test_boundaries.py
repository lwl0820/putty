"""无网络回归：拒绝变形归档、越界 PE、身份继承和不安全产物。"""
import ast
import ctypes as C
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import threading
import time
import unittest
from unittest.mock import Mock, patch
import uuid

ROOT = Path(__file__).resolve().parents[5]
sys.path.insert(0, str(ROOT))
from tools.suite.windows.gss_mit.common import (
    HERE, ValidationError, closed_environment, project_path, publish_report,
    read_regular, regular_file, run_bounded,
)
from tools.suite.windows.gss_mit.mit_gss import Buffer, DLL_ORDER, MitGss, Oid, OidSet, verify_layout
from tools.suite.windows.gss_mit.pe import pe_summary
from tools.suite.windows.gss_mit.prepare_kfw import (
    NAMES, parse_listing, validate_binary, validate_directory,
)
from tools.suite.windows.gss_mit.probe import cleanup_run


def synthetic_pe():
    data = bytearray(2048)
    data[:2] = b"MZ"
    struct.pack_into("<I", data, 0x3c, 128)
    data[128:132] = b"PE\0\0"
    struct.pack_into("<HHIIIHH", data, 132, 0x8664, 1, 0, 0, 0, 240, 0)
    optional = 152
    struct.pack_into("<H", data, optional, 0x20b)
    struct.pack_into("<I", data, optional + 60, 512)
    struct.pack_into("<I", data, optional + 108, 16)
    struct.pack_into("<IIII", data, optional + 240 + 8, 1536, 0x1000, 1536, 512)
    struct.pack_into("<II", data, optional + 112 + 8, 0x1000, 40)
    struct.pack_into("<IIIII", data, 512, 0, 0, 0, 0x1040, 0)
    data[576:589] = b"kernel32.dll\0"
    struct.pack_into("<II", data, optional + 112 + 13 * 8, 0x1080, 64)
    struct.pack_into("<8I", data, 640, 1, 0x10d0, 0, 0, 0, 0, 0, 0)
    data[720:733] = b"advapi32.dll\0"
    return data


class Boundaries(unittest.TestCase):
    def setUp(self):
        self.root = ROOT / ".tmp" / ("mit-boundaries-" + uuid.uuid4().hex)
        self.root.mkdir(mode=0o700)

    def tearDown(self):
        shutil.rmtree(self.root)

    def test_real_parser_handles_normal_and_delay_imports(self):
        result = pe_summary(synthetic_pe())
        self.assertEqual(result["imports"], ["kernel32.dll"])
        self.assertEqual(result["delay_imports"], ["advapi32.dll"])
        for label, modify in (
            ("wrong-machine", lambda data: struct.pack_into("<H", data, 132, 0x14c)),
            ("rva-outside", lambda data: struct.pack_into("<I", data, 524, 0xffffffff)),
            ("missing-null", lambda data: struct.pack_into("<I", data, 152 + 124, 20)),
            ("delay-absolute", lambda data: struct.pack_into("<I", data, 640, 0)),
            ("section-overflow", lambda data: struct.pack_into("<I", data, 152 + 240 + 12, 0xfffffff0)),
        ):
            with self.subTest(label=label):
                data = synthetic_pe()
                modify(data)
                with self.assertRaises(ValidationError):
                    pe_summary(data)
        for size in (0, 63, 130, 512, 700):
            with self.subTest(truncated=size), self.assertRaises(ValidationError):
                pe_summary(synthetic_pe()[:size])

    def test_archive_catalog_rejects_duplicates_paths_links_and_expansion(self):
        good = b"Path = Disk1\nSize = 3\nModified = \n\nPath = !File\nSize = 1\nModified = \n"
        options = {"count": 2, "expanded_size": 4, "selected": {"Disk1": 3}}
        self.assertEqual(parse_listing(good, **options), 2)
        for bad in (
            good.replace(b"!File", b"disk1"), good.replace(b"!File", b"../evil"),
            good.replace(b"!File", b"C:evil"), good.replace(b"!File", b"a/b"),
            good.replace(b"Modified = ", b"Hard Link = evil", 1),
            good.replace(b"Modified = ", b"Attributes = D", 1),
            good.replace(b"Size = 3", b"Size = 4"),
            good.replace(b"Size = 3", b"Size = 99999999"),
            good.replace(b"Size = 3", b"Size = 3\nSize = 3"),
        ):
            with self.subTest(data=bad), self.assertRaises(ValidationError):
                parse_listing(bad, **options)

    def test_file_paths_reject_hardlinks_symlinks_and_parent_hops(self):
        source = self.root / "source"
        source.write_bytes(b"public")
        self.assertEqual(read_regular(source, maximum=6), b"public")
        with self.assertRaises(ValidationError):
            regular_file(source, maximum=5)
        second = self.root / "hardlink"
        os.link(source, second)
        with self.assertRaises(ValidationError):
            read_regular(source, maximum=6)
        second.unlink()
        if os.name != "nt":
            second.symlink_to(source)
            with self.assertRaises(ValidationError):
                read_regular(second, maximum=6)
        with self.assertRaises(ValidationError):
            project_path(self.root / ".." / "escape")
        with self.assertRaises(ValidationError):
            project_path(ROOT / "outside-project-temp")

    def test_portable_directory_rejects_extra_or_missing_files_before_load(self):
        for name in NAMES:
            (self.root / name).write_bytes(b"not-executed")
        (self.root / "unexpected.dll").write_bytes(b"not-executed")
        with self.assertRaises(ValidationError):
            validate_directory(self.root)
        (self.root / "unexpected.dll").unlink()
        (self.root / "gssapi64.dll").unlink()
        with self.assertRaises(ValidationError):
            validate_directory(self.root)

    def test_hash_and_dependency_closure_both_matter(self):
        from tools.suite.windows.gss_mit.common import digest
        content = bytes(synthetic_pe())
        record = {"name": "example.dll", "size": len(content), "sha256": digest(content),
                  "imports": ["kernel32.dll"], "delay_imports": ["advapi32.dll"]}
        validate_binary(content, record)
        with self.assertRaises(ValidationError):
            validate_binary(content + b"tamper", record)
        changed = dict(record, delay_imports=[])
        with self.assertRaises(ValidationError):
            validate_binary(content, changed)
        with patch("tools.suite.windows.gss_mit.prepare_kfw.pe_summary", return_value={
                "imports": ["evil.dll"], "delay_imports": [], "exports": []}):
            with self.assertRaises(ValidationError):
                validate_binary(content, dict(record, imports=["evil.dll"], delay_imports=[]))

    def test_closed_environment_and_native_layout(self):
        environment = closed_environment(self.root, {"SystemRoot": "C:/Windows", "PATH": "personal", "HOME": "personal",
            "USERPROFILE": "personal", "krb5ccname": "MSLSA:", "KRB5_CONFIG": "personal", "GSS_MECH_CONFIG": "personal",
            "KRB5_TRACE": "personal", "HTTP_PROXY": "personal", "TEMP": "personal"})
        self.assertEqual(set(environment) - {"SystemRoot"}, {"TMP", "TEMP", "TMPDIR", "HOME", "USERPROFILE", "APPDATA",
            "LOCALAPPDATA", "XDG_CONFIG_HOME", "XDG_CACHE_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME", "XDG_RUNTIME_DIR"})
        self.assertEqual(environment["TEMP"], str(self.root))
        self.assertTrue(all(value == str(self.root) for key, value in environment.items() if key != "SystemRoot"))
        if C.sizeof(C.c_void_p) == 8:
            verify_layout()
            self.assertEqual([C.sizeof(item) for item in (Buffer, Oid, OidSet)], [16, 16, 16])

    def test_extractor_output_limit_timeout_and_plain_failure(self):
        environment = closed_environment(self.root)
        run = lambda source, **kwargs: run_bounded([sys.executable, "-c", source], maximum=32,
                                                   cwd=self.root, environment=environment, **kwargs)
        self.assertEqual(run("print('bounded')"), b"bounded\n" if os.name != "nt" else b"bounded\r\n")
        for source, options in (("print('x'*65536)", {}), ("raise SystemExit(3)", {}),
                                ("import time;time.sleep(5)", {"timeout": 0.05})):
            begin = time.monotonic()
            with self.subTest(source=source), self.assertRaises(ValidationError):
                run(source, **options)
            self.assertLess(time.monotonic() - begin, 3)
            self.assertFalse([worker for worker in threading.enumerate() if worker.name.startswith("mit-extractor-")])

    def test_failure_atomically_replaces_stale_success_without_paths(self):
        output = self.root / "report.json"
        publish_report(output, {"passed": True})
        publish_report(output, {"passed": False, "error": "native-abi-failed"})
        self.assertFalse(json.loads(output.read_text(encoding="utf-8"))["passed"])
        self.assertFalse(list(self.root.glob(".mit-report-*")))
        # 真正非 Windows 环境必须失败；不把 ABI 类型静态检查算成原生成功。
        if sys.platform != "win32":
            publish_report(self.root / "failure.json", {"passed": True})
            result = subprocess.run([sys.executable, str(HERE / "probe.py"), "--directory", str(self.root),
                "--parent", str(self.root), "--report", str(self.root / "failure.json")], capture_output=True)
            self.assertEqual(result.returncode, 1)
            report = json.loads(result.stdout)
            self.assertFalse(report["passed"])
            self.assertFalse(json.loads((self.root / "failure.json").read_text(encoding="utf-8"))["passed"])
            self.assertNotIn(str(self.root), result.stdout.decode())
            self.assertFalse(result.stderr)

    def test_process_deadline_terminates_native_and_report_hangs(self):
        # 真正启动子进程并等待硬退出；不加载 DLL，也不绑定任何凭据入口。
        for stage in ("native-api", "final-report"):
            with self.subTest(stage=stage):
                output, marker = self.root / (stage + ".json"), self.root / (stage + ".entered")
                publish_report(output, {"passed": True, "private_run_removed": True})
                source = "\n".join((
                    "from pathlib import Path",
                    "import sys, time",
                    "from tools.suite.windows.gss_mit import probe as target",
                    "original_publish = target.publish_report",
                    "def hang():",
                    "    Path(" + repr(str(marker)) + ").write_bytes(b'entered')",
                    "    time.sleep(30)",
                    "def fake_probe(*args):",
                    "    if " + repr(stage) + " == 'native-api': hang()",
                    "    return {'passed': True, 'private_run_removed': True}",
                    "def fake_publish(path, report):",
                    "    if report.get('passed'): hang()",
                    "    return original_publish(path, report)",
                    "target.probe = fake_probe",
                    "target.publish_report = fake_publish",
                    "sys.argv = ['probe', '--directory', " + repr(str(self.root)) + ", '--parent', " +
                    repr(str(self.root)) + ", '--report', " + repr(str(output)) + "]",
                    "raise SystemExit(target.main(deadline_seconds=0.25))",
                ))
                begin = time.monotonic()
                result = subprocess.run([sys.executable, "-B", "-c", source], cwd=ROOT,
                                        capture_output=True, timeout=5)
                self.assertEqual(result.returncode, 124)
                self.assertLess(time.monotonic() - begin, 3)
                self.assertEqual(marker.read_bytes(), b"entered")
                self.assertFalse(result.stdout)
                self.assertFalse(result.stderr)
                report = json.loads(output.read_text(encoding="utf-8"))
                self.assertFalse(report["passed"])
                self.assertEqual(report["error"], "native-abi-incomplete")
                self.assertNotIn("private_run_removed", report)
                self.assertNotIn(str(self.root), json.dumps(report))

    def test_process_deadline_is_cancelled_after_normal_failure_report(self):
        output = self.root / "normal-failure.json"
        source = "\n".join((
            "import sys, time",
            "from tools.suite.windows.gss_mit import probe as target",
            "target.probe = lambda *args: {'passed': False, 'error': 'test-failure'}",
            "sys.argv = ['probe', '--directory', " + repr(str(self.root)) + ", '--parent', " +
            repr(str(self.root)) + ", '--report', " + repr(str(output)) + "]",
            "code = target.main(deadline_seconds=0.25)",
            "time.sleep(0.5)",
            "raise SystemExit(code)",
        ))
        result = subprocess.run([sys.executable, "-B", "-c", source], cwd=ROOT,
                                capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(json.loads(result.stdout), {"passed": False, "error": "test-failure"})
        self.assertEqual(json.loads(output.read_text(encoding="utf-8")), json.loads(result.stdout))
        self.assertFalse(result.stderr)

    def test_private_run_cleanup_only_removes_known_files(self):
        run = self.root / "abi-example"
        run.mkdir()
        (run / "krb5.ini").write_bytes(b"public-test")
        (run / "replay").mkdir()
        (run / "plugins").mkdir()
        unrelated = self.root / "bin"
        unrelated.mkdir()
        (unrelated / "public.dll").write_bytes(b"retained")
        cleanup_run(run)
        with self.assertRaises(FileNotFoundError):
            run.lstat()
        self.assertEqual((unrelated / "public.dll").read_bytes(), b"retained")
        run.mkdir()
        (run / "unexpected").write_bytes(b"retained")
        with self.assertRaises(ValidationError):
            cleanup_run(run)
        self.assertEqual((run / "unexpected").read_bytes(), b"retained")

    def test_first_stage_never_binds_credential_or_cache_open_functions(self):
        tree = ast.parse((HERE / "mit_gss.py").read_text(encoding="utf-8"))
        forbidden = {"gss_acquire_cred", "gss_acquire_cred_from", "gss_init_sec_context", "gss_accept_sec_context",
                     "krb5_cc_default", "krb5_cc_resolve", "krb5_cc_start_seq_get", "krb5_kt_resolve"}
        literals = {node.value for node in ast.walk(tree) if isinstance(node, ast.Constant) and isinstance(node.value, str)}
        self.assertFalse(literals & forbidden)

    def test_preloaded_target_dll_is_rejected_before_any_fixture_load(self):
        # 此处只测试失败分支的控制流，不冒充真实 Windows 加载或 CRT 隔离结果。
        for preloaded in DLL_ORDER:
            with self.subTest(module=preloaded):
                kernel = Mock()
                kernel.GetModuleHandleW.side_effect = lambda name: 1 if name == preloaded else 0
                with patch("tools.suite.windows.gss_mit.mit_gss.sys.platform", "win32"), \
                        patch.object(C, "WinDLL", create=True, return_value=kernel) as loader:
                    with self.assertRaises(ValidationError):
                        MitGss(self.root)
                loader.assert_called_once_with("kernel32", use_last_error=True)
                kernel.GetModuleFileNameW.assert_not_called()


if __name__ == "__main__":
    unittest.main()
