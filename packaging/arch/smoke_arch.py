#!/usr/bin/env python3
"""在专用干净 Arch 容器安装、启动并卸载 pacman 包。"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from smoke_package import ROOT, launch, run


def validate_files(files):
    required = {'usr/bin/puttygen-gui', 'usr/share/applications/puttygen-gui.desktop',
                'usr/share/icons/hicolor/scalable/apps/puttygen-gui.svg',
                'usr/share/licenses/puttygen-gui/LICENSE',
                'usr/share/licenses/puttygen-gui/PuTTY-LICENCE'}
    paths = {name.removeprefix('./').rstrip('/') for name in files}
    if not required.issubset(paths):
        raise RuntimeError(f'Arch 包缺少安装文件：{sorted(required - paths)}')
    for name in paths:
        if name.startswith(('/', '../', 'opt/', 'usr/lib/')) or '/.tmp/' in name or name.endswith('/qt.conf'):
            raise RuntimeError(f'Arch 包含非系统安装文件：{name}')
        if name not in ('.PKGINFO', '.BUILDINFO', '.MTREE') and not (name == 'usr' or name.startswith('usr/')):
            raise RuntimeError(f'Arch 包含意外顶层文件：{name}')


def validate_metadata(text):
    fields = {}
    for line in text.splitlines():
        if ' = ' in line:
            key, value = line.split(' = ', 1)
            fields.setdefault(key, []).append(value)
    if fields.get('pkgname') != ['puttygen-gui'] or fields.get('arch') != ['x86_64']:
        raise RuntimeError('Arch 包名或架构不符')
    dependencies = set(fields.get('depend', []))
    if not {'glibc', 'libgcc', 'libstdc++', 'qt6-base>=6.8', 'qt6-translations',
            'hicolor-icon-theme', 'noto-fonts-cjk'}.issubset(dependencies):
        raise RuntimeError('Arch 包缺少声明的运行依赖')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('package', type=Path)
    parser.add_argument('--install-arch', action='store_true')
    parser.add_argument('--platform', choices=('offscreen', 'xcb', 'wayland'), required=True)
    args = parser.parse_args()
    if not args.install_arch or not Path('/etc/arch-release').exists() or os.geteuid() != 0:
        parser.error('必须显式 --install-arch，在专用 Arch 容器以 root 运行')
    package = args.package.resolve()
    if not package.name.endswith('.pkg.tar.zst'):
        parser.error('仅接受 Arch .pkg.tar.zst 包')
    if subprocess.run(['pacman', '-Q', 'puttygen-gui'], capture_output=True).returncode == 0:
        raise RuntimeError('验证环境已安装应用，拒绝覆盖')
    validate_files(run(['bsdtar', '-tf', str(package)], stdout=subprocess.PIPE).stdout.splitlines())
    validate_metadata(run(['bsdtar', '-xOf', str(package), '.PKGINFO'], stdout=subprocess.PIPE).stdout)
    identity = run(['pacman', '-Qp', str(package)], stdout=subprocess.PIPE).stdout.split()
    if len(identity) != 2 or identity[0] != 'puttygen-gui':
        raise RuntimeError('包元数据与预期应用不符')
    work = ROOT / '.tmp/package-smoke' / package.name.removesuffix('.pkg.tar.zst') / args.platform
    work.mkdir(parents=True, exist_ok=True)
    run(['pacman', '-U', '--noconfirm', str(package)])
    try:
        owned = run(['pacman', '-Qlq', 'puttygen-gui'], stdout=subprocess.PIPE).stdout.splitlines()
        binary = Path('/usr/bin/puttygen-gui')
        if binary.read_bytes()[:4] != b'\x7fELF':
            raise RuntimeError('/usr/bin 必须是实际 ELF 程序')
        dependencies = run(['ldd', str(binary)], stdout=subprocess.PIPE).stdout
        (work / '动态库.txt').write_text(dependencies, encoding='utf-8')
        if 'not found' in dependencies or '/opt/' in dependencies:
            raise RuntimeError('系统依赖解析失败')
        dynamic = run(['readelf', '-d', str(binary)], stdout=subprocess.PIPE).stdout
        if '(RPATH)' in dynamic or '(RUNPATH)' in dynamic:
            raise RuntimeError('Arch ELF 不应包含私有运行库查找路径')
        run(['desktop-file-validate', '/usr/share/applications/puttygen-gui.desktop'])
        launch(binary, work, args.platform)
    finally:
        run(['pacman', '-R', '--noconfirm', 'puttygen-gui'])
    for name in owned:
        if not name.endswith('/') and Path(name).exists():
            raise RuntimeError(f'卸载后残留文件：{name}')
    result = {'包': package.name, 'SHA256': hashlib.sha256(package.read_bytes()).hexdigest(),
              '平台插件': args.platform, '结果': '安装、启动、卸载通过；不代表人工功能验收'}
    (work / '结果.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
