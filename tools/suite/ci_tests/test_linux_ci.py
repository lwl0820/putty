"""确保 CI 不把未执行或残缺报告当成平台通过。"""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

SOURCE = Path(__file__).resolve().parents[1] / "run_linux_ci.py"
SPEC = importlib.util.spec_from_file_location("suite_linux_ci", SOURCE)
CI = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CI)


class LinuxCIContractTest(unittest.TestCase):
    def setUp(self):
        parent = CI.TEMPORARY_ROOT / "linux-ci-python-tests"
        parent.mkdir(parents=True, exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir=parent)
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def test_selection_requires_every_component_and_unique_names(self):
        good = {"tests": [{"name": name} for name in sorted(CI.REQUIRED)]}
        self.assertEqual(CI.selection_names(good), CI.REQUIRED)
        for tests in ([], good["tests"][:-1], good["tests"] * 2, [{"name": 12}], [None]):
            with self.subTest(tests=tests), self.assertRaises(ValueError):
                CI.selection_names({"tests": tests})

    def test_actual_results_require_matching_names_and_no_skips(self):
        path = self.root / "result.xml"
        variants = (
            ("", ""), ("<skipped/>", ""), ("<failure/>", ""), ("<error/>", ""),
            ("", ' status="notrun"'), ("", ' time="nan"'), ("", ' time="-1"'),
        )
        for content, attributes in variants:
            path.write_text(f'<testsuite><testcase name="first"{attributes}>{content}</testcase></testsuite>', encoding="utf-8")
            if not content and not attributes:
                self.assertEqual(len(CI.inspect_results(path, {"first"})), 1)
            else:
                with self.subTest(content=content, attributes=attributes), self.assertRaises(ValueError):
                    CI.inspect_results(path, {"first"})
        for body in ('', '<testcase name="other"/>', '<testcase name="first"/>' * 2):
            path.write_text('<testsuite>' + body + '</testsuite>', encoding="utf-8")
            with self.subTest(body=body), self.assertRaises(ValueError):
                CI.inspect_results(path, {"first"})

    def test_reports_contain_only_summary_fields(self):
        path = self.root / "result.xml"
        path.write_text('<testsuite><testcase name="first" time="1.5"><system-out>私有测试内容</system-out></testcase></testsuite>', encoding="utf-8")
        report = CI.inspect_results(path, {"first"})
        self.assertEqual(report, [{"name": "first", "seconds": 1.5, "passed": True}])
        self.assertNotIn("私有测试内容", json.dumps(report, ensure_ascii=False))

    def test_paths_reject_outside_navigation_and_symbolic_parents(self):
        self.assertEqual(CI.private_path(self.root / "report.json"), self.root / "report.json")
        for path in (CI.ROOT / "outside", CI.TEMPORARY_ROOT, self.root / ".." / "report.json"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                CI.private_path(path)
        (self.root / "link").symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(ValueError):
            CI.private_path(self.root / "link" / "report.json")


if __name__ == "__main__":
    unittest.main()
