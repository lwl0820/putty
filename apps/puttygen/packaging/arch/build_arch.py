#!/usr/bin/env python3
"""以普通用户从当前已提交源码生成并检查 Arch 原生包，所有中间文件留在 .tmp。"""

import hashlib
import json
import os
from pathlib import Path
import re
import platform
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[4]
SOURCE_PATHS = ('CMakeLists.txt', 'LICENSE', 'THIRD_PARTY_NOTICES.md',
                'README.md', 'apps/puttygen', 'cmake', 'vendor', 'docs')


def run(command, **kwargs):
    return subprocess.run(command, check=True, **kwargs)


def main():
    if os.geteuid() == 0:
        raise SystemExit('makepkg 必须以普通用户运行；请先在容器中创建构建用户')
    if not Path('/etc/arch-release').exists() or platform.machine() != 'x86_64':
        raise SystemExit('Arch 原生包必须在 Arch Linux x86_64 中构建')
    work = ROOT / '.tmp/arch-package'
    work.mkdir(parents=True, exist_ok=True)
    output = ROOT / '.tmp/packages/arch'
    output.mkdir(parents=True, exist_ok=True)
    commit = run(['git', 'rev-parse', 'HEAD'], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    cmake = run(['git', 'show', f'{commit}:CMakeLists.txt'], cwd=ROOT,
                capture_output=True, text=True).stdout
    version = re.search(r'project\(putty VERSION ([0-9.]+)', cmake).group(1)
    archive = work / f'puttygen-gui-{version}.tar.gz'
    run(['git', 'archive', '--format=tar.gz', f'--prefix=puttygen-gui-{version}/',
         f'--output={archive}', commit, '--', *SOURCE_PATHS], cwd=ROOT)
    checksum = hashlib.sha256(archive.read_bytes()).hexdigest()
    # 配方也来自同一提交，避免工作区改动与已归档源码版本不一致。
    recipe = run(['git', 'show', f'{commit}:apps/puttygen/packaging/arch/PKGBUILD'],
                 cwd=ROOT, capture_output=True, text=True).stdout
    (work / 'PKGBUILD').write_text(recipe.replace('@VERSION@', version)
                                .replace('@SOURCE_SHA256@', checksum), encoding='utf-8')
    temporary = work / 'temp'
    temporary.mkdir(exist_ok=True)
    environment = os.environ.copy()
    environment.update(TMPDIR=str(temporary), TMP=str(temporary), TEMP=str(temporary),
                       PKGDEST=str(output), SRCDEST=str(work), PKGEXT='.pkg.tar.zst', LC_ALL='C.UTF-8')
    packages = [Path(name) for name in run(['makepkg', '--packagelist'], cwd=work,
                env=environment, capture_output=True, text=True).stdout.splitlines()]
    if len(packages) != 1 or packages[0].parent != output or not packages[0].name.endswith('.pkg.tar.zst'):
        raise RuntimeError('配方必须仅输出一个原生应用包')
    # 即使测试失败，也保留 CTest 诊断到独立的普通用户报告目录。
    try:
        run(['makepkg', '--cleanbuild', '--force'], cwd=work, env=environment)
    finally:
        log = work / f'src/puttygen-gui-{version}/.tmp/build/arch/Testing/Temporary/LastTest.log'
        if log.is_file():
            shutil.copyfile(log, output / 'LastTest.log')
        (output / '构建信息.json').write_text(json.dumps(
            {'提交': commit, '源码SHA256': checksum, '版本': version,
             '包文件': packages[0].name,
             '环境版本': run(['pacman', '-Q', 'pacman', 'gcc', 'glibc', 'libgcc', 'libstdc++',
                            'qt6-base', 'qt6-translations', 'cmake', 'ninja', 'python', 'openssh'],
                           capture_output=True, text=True).stdout},
            ensure_ascii=False, indent=2), encoding='utf-8')
    for package in packages:
        (package.with_name(package.name + '.sha256')).write_text(
            f'{hashlib.sha256(package.read_bytes()).hexdigest()}  {package.name}\n', encoding='utf-8')
    # 发布配方与校验过的对应源码，便于审计及重建；不含 .git/.tmp/测试私钥。
    shutil.copyfile(archive, output / archive.name)
    shutil.copyfile(work / 'PKGBUILD', output / 'PKGBUILD')


if __name__ == '__main__':
    main()
