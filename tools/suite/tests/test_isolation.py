"""验证真实子进程的存储覆盖和私有目录边界。"""

import json
import os
from pathlib import Path
import shutil
import socket
import stat
import subprocess
import sys
import unittest
import uuid


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from tools.suite.isolation import (  # noqa: E402
    AGENT_VARIABLES, DEFAULT_PARENT, DIRECTORY_VARIABLES, FILE_VARIABLES,
    IsolationEnvironment, IsolationError, private_directory, require_linux,
)


@unittest.skipUnless(sys.platform == "linux", "Linux 隔离夹具；Windows 尚需独立 HKCU 探针")
class IsolationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.parent = private_directory(DEFAULT_PARENT / ("unit-" + uuid.uuid4().hex[:10]))
        cls.addClassCleanup(shutil.rmtree, cls.parent)

    def setUp(self):
        self.environment = IsolationEnvironment(self.parent)
        self.addCleanup(self.environment.close)

    def test_real_child_overrides_and_preserved_home(self):
        inherited = os.environ.copy()
        for variable in AGENT_VARIABLES:
            inherited[variable] = "不应继承的个人代理"
        with IsolationEnvironment(self.parent, base_environment=inherited) as environment:
            result = environment.run([
                sys.executable, "-B", "-c",
                "import json,os; print(json.dumps(dict(os.environ)))",
            ], check=True)
            child = json.loads(result.stdout)
            self.assertEqual(child.get("HOME"), inherited.get("HOME"))
            self.assertFalse(any(variable in child for variable in AGENT_VARIABLES))
            for variable, relative in {**DIRECTORY_VARIABLES, **FILE_VARIABLES}.items():
                self.assertEqual(child[variable], str(environment.root / relative))
            self.assertEqual(inherited["SSH_AUTH_SOCK"], "不应继承的个人代理")
            self.assertNotEqual(os.environ.get("SSH_AUTH_SOCK"), "不应继承的个人代理")

    def test_probe_writes_in_child_and_does_not_claim_upstream_storage(self):
        report = self.environment.probe()
        self.assertTrue(report["passed"])
        self.assertEqual(report["kind"], "linux-environment")
        self.assertFalse(report["upstream_storage_verified"])
        self.assertFalse(list(self.environment.root.rglob("probe-*")))

    def test_real_child_failure_and_binary_streams_are_preserved(self):
        result = self.environment.run([
            sys.executable, "-B", "-c",
            "import sys; sys.stdout.buffer.write(b'out\\x00\\xff'); "
            "sys.stderr.buffer.write(b'err\\xfe'); sys.exit(7)",
        ])
        self.assertEqual(result.returncode, 7)
        self.assertEqual(result.stdout, b"out\x00\xff")
        self.assertEqual(result.stderr, b"err\xfe")
        with self.assertRaises(subprocess.CalledProcessError):
            self.environment.run([sys.executable, "-B", "-c", "raise SystemExit(8)"], check=True)

    def test_real_child_timeout_is_reported(self):
        with self.assertRaises(subprocess.TimeoutExpired):
            self.environment.run([sys.executable, "-B", "-c", "import time; time.sleep(5)"], timeout=0.1)

    def test_child_created_files_use_private_umask(self):
        self.environment.run([
            sys.executable, "-B", "-c",
            "from pathlib import Path; Path('created-file').write_bytes(b'private'); "
            "Path('created-directory').mkdir()",
        ], check=True)
        self.assertEqual((self.environment.root / "created-file").stat().st_mode & 0o777, 0o600)
        self.assertEqual((self.environment.root / "created-directory").stat().st_mode & 0o777, 0o700)

    def test_directories_and_private_files_have_exact_modes(self):
        for path in self.environment.root.rglob("*"):
            mode = stat.S_IMODE(path.lstat().st_mode)
            self.assertEqual(mode, 0o700 if path.is_dir() else 0o600, str(path))

    def test_parent_outside_project_temporary_root_is_rejected(self):
        with self.assertRaises(IsolationError):
            IsolationEnvironment(ROOT / "fixture-invalid")
        self.assertFalse((ROOT / "fixture-invalid").exists())

    def test_temporary_root_itself_and_parent_navigation_are_rejected(self):
        with self.assertRaises(IsolationError):
            IsolationEnvironment(ROOT / ".tmp")
        with self.assertRaises(IsolationError):
            IsolationEnvironment(self.parent / ".." / "invalid")

    def test_symlinked_parent_is_rejected(self):
        target = private_directory(self.parent / "target")
        link = self.parent / "link"
        link.symlink_to(target, target_is_directory=True)
        self.addCleanup(link.unlink)
        with self.assertRaises(IsolationError):
            IsolationEnvironment(link)
        self.assertEqual(list(target.iterdir()), [])

    def test_unsafe_parent_permissions_are_rejected_without_repair(self):
        unsafe = self.parent / "unsafe"
        unsafe.mkdir(mode=0o755)
        # mkdir 的 mode 受宿主 umask 影响；显式构造不安全权限才能验证拒绝分支。
        unsafe.chmod(0o755)
        self.assertEqual(stat.S_IMODE(unsafe.stat().st_mode), 0o755)
        with self.assertRaises(IsolationError):
            IsolationEnvironment(unsafe)
        self.assertEqual(stat.S_IMODE(unsafe.stat().st_mode), 0o755)

    def test_runtime_permissions_are_revalidated_before_child_runs(self):
        runtime = self.environment.root / "runtime"
        runtime.chmod(0o755)
        try:
            with self.assertRaises(IsolationError):
                self.environment.run([sys.executable, "-B", "-c", "raise SystemExit(0)"])
        finally:
            runtime.chmod(0o700)

    def test_storage_symlink_and_hardlink_are_rejected(self):
        seed = self.environment.root / FILE_VARIABLES["PUTTYRANDOMSEED"]
        saved = self.environment.root / "seed-backup"
        seed.rename(saved)
        seed.symlink_to(saved)
        try:
            with self.assertRaises(IsolationError):
                self.environment.environment()
        finally:
            seed.unlink()
        os.link(saved, seed)
        try:
            with self.assertRaises(IsolationError):
                self.environment.environment()
        finally:
            saved.unlink()

    def test_existing_file_is_never_truncated(self):
        path = self.environment.write_private("logs/keep.txt", b"keep")
        with self.assertRaises(IsolationError):
            self.environment.write_private("logs/keep.txt", b"replace")
        self.assertEqual(path.read_bytes(), b"keep")

    def test_internal_path_escape_is_rejected(self):
        for relative in ("../escape", "/absolute", "logs/../../escape", "."):
            with self.subTest(relative=relative), self.assertRaises(IsolationError):
                self.environment.write_private(relative, b"invalid")

    def test_default_pageant_cannot_run_even_with_tmpdir(self):
        with self.assertRaisesRegex(IsolationError, "TMPDIR"):
            self.environment.run([ROOT / ".tmp" / "pageant", "--debug"])

    def test_close_removes_private_material_and_is_idempotent(self):
        root = self.environment.root
        self.environment.write_private("ssh/private-material", "仅测试内容".encode("utf-8"))
        self.environment.close()
        self.environment.close()
        self.assertFalse(root.exists())
        with self.assertRaises(IsolationError):
            self.environment.environment()

    def test_close_does_not_follow_symlink(self):
        external = self.parent / ("external-" + uuid.uuid4().hex[:8])
        external.mkdir(mode=0o700)
        marker = external / "marker"
        marker.write_bytes(b"keep")
        (self.environment.root / "external-link").symlink_to(external, target_is_directory=True)
        self.environment.close()
        self.assertEqual(marker.read_bytes(), b"keep")

    def test_socket_path_rejects_escape_and_excessive_length(self):
        with self.assertRaises(IsolationError):
            self.environment.socket_path("../outside")
        with self.assertRaises(IsolationError):
            self.environment.socket_path("a" * 108)

    def test_agent_socket_must_be_private_and_never_a_link(self):
        path = self.environment.socket_path()
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as agent:
            agent.bind(str(path))
            path.chmod(0o666)
            with self.assertRaises(IsolationError):
                self.environment.environment(agent_socket=path)
            path.chmod(0o600)
            child_environment = self.environment.environment(agent_socket=path)
            self.assertEqual(child_environment["SSH_AUTH_SOCK"], str(path))
            link = self.environment.root / "runtime" / "link.sock"
            link.symlink_to(path)
            with self.assertRaises(IsolationError):
                self.environment.environment(agent_socket=link)

    def test_regular_file_cannot_be_used_as_agent_socket(self):
        path = self.environment.write_private("runtime/ordinary-file", b"")
        with self.assertRaises(IsolationError):
            self.environment.environment(agent_socket=path)


class PlatformGuardTest(unittest.TestCase):
    def test_unverified_windows_is_explicitly_rejected(self):
        with self.assertRaisesRegex(IsolationError, "HKCU"):
            require_linux("win32")


if __name__ == "__main__":
    unittest.main()
