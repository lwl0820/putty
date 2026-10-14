"""验证报告收集避开不可访问运行目录，并保留失败诊断。"""

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[4]
SCRIPT = ROOT / "apps/puttygen/packaging/collect_smoke_reports.py"


class CollectSmokeReportsTest(unittest.TestCase):
    def setUp(self):
        temporary_root = ROOT / ".tmp"
        temporary_root.mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(prefix="smoke-reports-", dir=temporary_root)
        self.addCleanup(self.temporary.cleanup)
        self.work = Path(self.temporary.name)
        self.source = self.work / "package-smoke"
        self.destination = self.work / "package-smoke-reports"
        self.source.mkdir()

    def invoke(self, source=None, destination=None):
        environment = os.environ.copy()
        environment["PYTHONIOENCODING"] = "utf-8"
        for name in ("TMP", "TEMP", "TMPDIR"):
            environment[name] = str(self.work)
        return subprocess.run(
            [sys.executable, "-B", str(SCRIPT), "--source", str(source or self.source),
             "--destination", str(destination or self.destination)],
            capture_output=True, env=environment, check=False,
        )

    def report(self, path, content):
        target = self.source / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    def scan_reports(self):
        def fail(error):
            raise error

        files = {}
        # 实际递归扫描暂存目录并读取所有文件，模拟普通上传器。
        for directory, _, names in os.walk(self.destination, onerror=fail):
            for name in names:
                path = Path(directory) / name
                files[path.relative_to(self.destination).as_posix()] = path.read_text(encoding="utf-8")
        return files

    def test_reports_keep_package_and_platform_names(self):
        expected = {}
        for package in ("包 22.04", "包 24.04"):
            for platform in ("offscreen", "xcb", "wayland", "native"):
                for name in ("结果.json", "启动输出.txt", "动态库.txt"):
                    path = f"{package}/{platform}/{name}"
                    expected[path] = f"{package} {platform} {name}"
                    self.report(path, expected[path])
        result = self.invoke()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.scan_reports(), expected)

    def test_failure_keeps_logs_without_success_result(self):
        expected = {"包/wayland/启动输出.txt": "GUI 提前退出", "weston.log": "合成器失败"}
        for path, content in expected.items():
            self.report(path, content)
        self.report("包/wayland/xdg_config_home/偏好.json", "不上传配置")
        self.report("包/wayland/解压包/测试私钥", "不上传运行文件")
        result = self.invoke()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.scan_reports(), expected)

    @unittest.skipUnless(os.name == "posix" and os.geteuid() != 0,
                         "需要普通 POSIX 用户实际触发目录权限拒绝")
    def test_unreadable_runtime_is_not_scanned_or_changed(self):
        self.report("包/offscreen/结果.json", "通过")
        runtime = self.source / "包/offscreen/runtime"
        runtime.mkdir(mode=0o700)
        (runtime / "敏感文件").write_text("不能上传", encoding="utf-8")
        runtime.chmod(0)
        self.addCleanup(runtime.chmod, 0o700)
        with self.assertRaises(PermissionError):
            list(runtime.iterdir())
        result = self.invoke()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.scan_reports(), {"包/offscreen/结果.json": "通过"})
        self.assertEqual(runtime.stat().st_mode & 0o777, 0)

    def test_top_level_helpers_are_not_package_reports(self):
        self.report("包/offscreen/结果.json", "真实包报告")
        # 即使辅助目录可读且碰巧包含相同文件结构，也不能把它当作包。
        for helper in ("temp", "wayland-runtime"):
            self.report(f"{helper}/offscreen/结果.json", "不应上传的辅助文件")
        result = self.invoke()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.scan_reports(), {"包/offscreen/结果.json": "真实包报告"})

    @unittest.skipUnless(os.name == "posix" and os.geteuid() != 0,
                         "需要普通 POSIX 用户实际触发辅助目录权限拒绝")
    def test_unreadable_top_level_helpers_are_not_probed_or_changed(self):
        self.report("包/offscreen/结果.json", "通过")
        helpers = []
        for name in ("temp", "wayland-runtime"):
            helper = self.source / name
            helper.mkdir(mode=0o700)
            helper.chmod(0)
            self.addCleanup(helper.chmod, 0o700)
            helpers.append(helper)
            with self.assertRaises(PermissionError):
                list(helper.iterdir())
        result = self.invoke()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.scan_reports(), {"包/offscreen/结果.json": "通过"})
        for helper in helpers:
            self.assertEqual(helper.stat().st_mode & 0o777, 0)

    @unittest.skipUnless(os.name == "posix" and os.geteuid() != 0,
                         "需要普通 POSIX 用户实际触发报告目录权限拒绝")
    def test_real_report_directory_permission_failure_is_not_silently_dropped(self):
        self.report("包/offscreen/结果.json", "通过")
        directory = self.source / "包/offscreen"
        directory.chmod(0)
        self.addCleanup(directory.chmod, 0o755)
        result = self.invoke()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b"PermissionError", result.stderr)

    def test_missing_source_is_allowed_after_earlier_build_failure(self):
        self.source.rmdir()
        result = self.invoke()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.destination.exists())

    def test_paths_outside_project_temporary_root_are_rejected(self):
        self.report("包/offscreen/结果.json", "通过")
        for paths in ({"source": ROOT}, {"destination": ROOT}):
            with self.subTest(paths=paths):
                result = self.invoke(**paths)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("路径必须位于项目 .tmp/ 内".encode("utf-8"), result.stderr)
                self.assertFalse(self.destination.exists())

    def test_overlapping_source_and_destination_are_rejected(self):
        self.report("包/offscreen/结果.json", "通过")
        for destination in (self.source, self.source / "报告", self.work):
            with self.subTest(destination=destination):
                result = self.invoke(destination=destination)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("报告源目录与暂存目录不能重叠".encode("utf-8"), result.stderr)
                self.assertFalse(self.destination.exists())

    def test_report_write_failure_is_not_silently_dropped(self):
        # 用报告路径上的目录制造实际复制错误，不依赖 root 可绕过的 chmod。
        self.report("包/offscreen/结果.json", "通过")
        target = self.destination / "包/offscreen/结果.json"
        target.mkdir(parents=True)
        result = self.invoke()
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(result.stderr)

    @unittest.skipUnless(os.name == "posix" and os.geteuid() != 0,
                         "需要普通 POSIX 用户实际触发文件读取拒绝")
    def test_report_read_failure_is_not_silently_dropped(self):
        self.report("包/offscreen/结果.json", "通过")
        report = self.source / "包/offscreen/结果.json"
        report.chmod(0)
        self.addCleanup(report.chmod, 0o600)
        result = self.invoke()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b"PermissionError", result.stderr)


if __name__ == "__main__":
    unittest.main()
