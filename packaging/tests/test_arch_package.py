"""校验 Arch 包安装树拒绝私有 Qt、便携布局和源码杂项。"""

import importlib.util
from pathlib import Path
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'arch/smoke_arch.py'
spec = importlib.util.spec_from_file_location('smoke_arch', SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class ArchPackageTest(unittest.TestCase):
    def setUp(self):
        self.files = ['.PKGINFO', '.BUILDINFO', '.MTREE', 'usr/', 'usr/bin/puttygen-gui',
                      'usr/share/applications/puttygen-gui.desktop',
                      'usr/share/icons/hicolor/scalable/apps/puttygen-gui.svg',
                      'usr/share/licenses/puttygen-gui/LICENSE',
                      'usr/share/licenses/puttygen-gui/PuTTY-LICENCE']

    def test_metadata_requires_architecture_and_runtime_dependencies(self):
        metadata = 'pkgname = puttygen-gui\narch = x86_64\n' + ''.join(
            f'depend = {name}\n' for name in ('glibc', 'libgcc', 'libstdc++', 'qt6-base>=6.8',
                                              'qt6-translations', 'hicolor-icon-theme', 'noto-fonts-cjk'))
        module.validate_metadata(metadata)
        for old, new in (('arch = x86_64', 'arch = any'), ('pkgname = puttygen-gui', 'pkgname = putty'),
                         ('depend = qt6-translations', '# 缺少翻译依赖')):
            with self.subTest(old=old), self.assertRaises(RuntimeError):
                module.validate_metadata(metadata.replace(old, new))

    def test_system_install_tree_is_accepted(self):
        module.validate_files(self.files)

    def test_missing_application_or_license_is_rejected(self):
        for missing in self.files[4:]:
            with self.subTest(missing=missing), self.assertRaises(RuntimeError):
                module.validate_files([name for name in self.files if name != missing])

    def test_portable_libraries_and_build_artifacts_are_rejected(self):
        for unexpected in ('opt/puttygen-gui/bin/puttygen-gui', 'usr/lib/libQt6Core.so',
                           'usr/bin/qt.conf', 'usr/share/.tmp/private-key', '.git/config', '../outside'):
            with self.subTest(unexpected=unexpected), self.assertRaises(RuntimeError):
                module.validate_files(self.files + [unexpected])


if __name__ == '__main__':
    unittest.main()
