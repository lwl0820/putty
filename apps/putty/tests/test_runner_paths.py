"""报告路径在启动服务和受测进程前必须拒绝逃出项目 .tmp/。"""
import importlib
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from tools.suite.isolation import IsolationEnvironment, IsolationError


class RunnerReportPaths(unittest.TestCase):
    def reject(self, path):
        for name in ("run_terminal_test", "run_terminal_gss_test"):
            with self.subTest(runner=name):
                runner = importlib.import_module(name)
                arguments = [name, "--binary", "unused", "--parent", str(ROOT / ".tmp/unused"),
                             "--report", str(path)]
                if name == "run_terminal_test":
                    arguments.extend(["--puttygen", "unused"])
                with patch.object(sys, "argv", arguments), patch.object(
                        runner, "IsolationEnvironment", side_effect=AssertionError("不应启动夹具")):
                    with self.assertRaises(IsolationError):
                        runner.main()

    def test_parent_segments_are_resolved_before_validation(self):
        self.reject(ROOT / ".tmp/../README.md")

    def test_symbolic_parent_cannot_escape_report_root(self):
        with IsolationEnvironment(ROOT / ".tmp/terminal-runner-paths") as environment:
            link = environment.root / "logs/project-link"
            link.symlink_to(ROOT, target_is_directory=True)
            self.reject(link / "README.md")


if __name__ == "__main__":
    unittest.main()
