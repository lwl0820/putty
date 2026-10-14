"""验证 Qt Tools 配置会避开残缺的系统 Clang 导出包。"""

from pathlib import Path
import runpy
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "tools" / "qt" / "build_qt.py"
CONFIGURE_COMMAND = runpy.run_path(str(SCRIPT))["module_configure_command"]


class BuildQtDependencyTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        temporary_root = ROOT / ".tmp"
        temporary_root.mkdir(exist_ok=True)
        cls.temporary = tempfile.TemporaryDirectory(prefix="qt-dependencies-", dir=temporary_root)
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.test_root = Path(cls.temporary.name)
        cls.source = cls.test_root / "source"
        cls.source.mkdir()
        # 与 Qt 6.8.3 相同：先发现 WrapLibClang，再由其载入 Clang CONFIG。
        (cls.source / "CMakeLists.txt").write_text(
            "cmake_minimum_required(VERSION 3.16)\n"
            "project(QtToolsDependencyProbe LANGUAGES NONE)\n"
            'list(PREPEND CMAKE_MODULE_PATH "${CMAKE_CURRENT_SOURCE_DIR}")\n'
            "find_package(WrapLibClang 8)\n",
            encoding="utf-8",
        )
        (cls.source / "FindWrapLibClang.cmake").write_text(
            "find_package(Clang CONFIG)\n", encoding="utf-8",
        )
        cls.clang = cls.test_root / "broken-clang"
        cls.clang.mkdir()
        (cls.clang / "ClangConfig.cmake").write_text(
            'file(WRITE "${CMAKE_BINARY_DIR}/clang-probe.txt" "ClangConfig entered")\n'
            'set(missing "${CMAKE_CURRENT_LIST_DIR}/libclangBasic.a")\n'
            'if(NOT EXISTS "${missing}")\n'
            '  message(FATAL_ERROR "clangBasic references missing ${missing}")\n'
            "endif()\n",
            encoding="utf-8",
        )

    def configure(self, name, remove_options=()):
        build = self.test_root / name
        command = CONFIGURE_COMMAND("qttools", self.source, build, self.test_root / "prefix")
        command = [str(part) for part in command if part not in remove_options]
        command.append(f"-DClang_DIR={self.clang}")
        result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, check=False)
        return result, build, command

    def test_qdoc_off_alone_still_loads_broken_clang(self):
        result, build, _ = self.configure("old", (
            "-DFEATURE_clang=OFF", "-DFEATURE_clangcpp=OFF",
            "-DCMAKE_DISABLE_FIND_PACKAGE_WrapLibClang=TRUE",
        ))
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue((build / "clang-probe.txt").is_file())
        self.assertIn("clangBasic references missing", result.stderr)

    def test_clang_features_off_still_load_broken_clang(self):
        result, build, _ = self.configure("features-only", (
            "-DCMAKE_DISABLE_FIND_PACKAGE_WrapLibClang=TRUE",
        ))
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue((build / "clang-probe.txt").is_file())
        self.assertIn("clangBasic references missing", result.stderr)

    def test_qttools_does_not_enter_broken_clang_package(self):
        result, build, command = self.configure("fixed")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse((build / "clang-probe.txt").exists())
        self.assertIn("-DFEATURE_clang=OFF", command)
        self.assertIn("-DFEATURE_clangcpp=OFF", command)

    def test_unrelated_modules_keep_their_configuration(self):
        for module in ("qtwayland", "qttranslations"):
            with self.subTest(module=module):
                command = CONFIGURE_COMMAND(module, self.source, self.test_root / module,
                                            self.test_root / "prefix")
                self.assertFalse(any("Clang" in str(part) or "FEATURE_clang" in str(part)
                                     for part in command))
                self.assertEqual("-DFEATURE_wayland_server=OFF" in command,
                                 module == "qtwayland")


if __name__ == "__main__":
    unittest.main()
