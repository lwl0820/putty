"""验证重定向到旧 Windows 代码页时的中文日志与失败反馈。"""

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "tools" / "qt" / "build_qt.py"


class BuildQtEncodingTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        temporary_root = ROOT / ".tmp"
        temporary_root.mkdir(exist_ok=True)
        cls.temporary = tempfile.TemporaryDirectory(prefix="qt-encoding-", dir=temporary_root)
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.test_root = Path(cls.temporary.name)

    def environment(self):
        environment = os.environ.copy()
        # 明确覆盖 CI 的 UTF-8 配置，独立复现原来重定向标准流的编码。
        environment["PYTHONIOENCODING"] = "cp1252:strict"
        environment["PYTHONUTF8"] = "0"
        for variable in ("TMP", "TEMP", "TMPDIR"):
            environment[variable] = str(self.test_root)
        return environment

    def invoke(self, *arguments):
        return subprocess.run(
            [sys.executable, "-B", str(SCRIPT), *map(str, arguments)],
            cwd=ROOT, env=self.environment(), capture_output=True, check=False,
        )

    def test_help_is_utf8(self):
        result = self.invoke("--help")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("从已校验的官方源码构建发布用 Qt", result.stdout.decode("utf-8"))
        self.assertEqual(result.stderr, b"")

    def test_invalid_path_keeps_chinese_error(self):
        result = self.invoke("--prefix", ROOT / "不能写入的输出目录")
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("路径必须位于项目 .tmp/ 内", result.stderr.decode("utf-8"))
        self.assertNotIn(b"UnicodeEncodeError", result.stderr)

    def test_invalid_jobs_keeps_chinese_error(self):
        result = self.invoke("--prefix", self.test_root / "安装目录", "--jobs", "0")
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("--jobs 必须为正整数", result.stderr.decode("utf-8"))

    def run_logged_child(self, exit_code):
        # 真实子进程写出非 UTF-8 字节，验证构建输出仍直通、不被脚本捕获解码。
        child = self.test_root / "构建子进程.py"
        child.write_text(
            "import sys\n"
            "sys.stdout.buffer.write(b'child-output:\\xff\\n')\n"
            "sys.stderr.buffer.write(b'child-error:\\xfe\\n')\n"
            f"sys.exit({exit_code})\n",
            encoding="utf-8",
        )
        wrapper = (
            "import runpy, sys\n"
            "module = runpy.run_path(sys.argv[1])\n"
            "module['configure_output_encoding']()\n"
            "module['run']([sys.executable, '-B', sys.argv[2]])\n"
        )
        return subprocess.run(
            [sys.executable, "-B", "-c", wrapper, str(SCRIPT), str(child)],
            cwd=ROOT, env=self.environment(), capture_output=True, check=False,
        )

    def test_chinese_command_and_child_output(self):
        result = self.run_logged_child(0)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("执行：".encode("utf-8"), result.stdout)
        self.assertIn("构建子进程.py".encode("utf-8"), result.stdout)
        self.assertTrue(result.stdout.endswith(b"child-output:\xff\n"), result.stdout)
        self.assertEqual(result.stderr, b"child-error:\xfe\n")

    def test_child_failure_retains_diagnostic(self):
        result = self.run_logged_child(7)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("执行：".encode("utf-8"), result.stdout)
        self.assertIn(b"child-error:\xfe\n", result.stderr)
        diagnostic = result.stderr.split(b"child-error:\xfe\n", 1)[1].decode("utf-8")
        self.assertIn("CalledProcessError", diagnostic)
        self.assertIn("构建子进程.py", diagnostic)
        self.assertIn("exit status 7", diagnostic)
        self.assertNotIn("UnicodeEncodeError", diagnostic)


if __name__ == "__main__":
    unittest.main()
