"""固定SDK归档的保守解包与复核；不信任既有目录或自签清单。"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path, PurePosixPath
import stat
import struct
import tarfile
import zipfile

from tools.suite.windows.support import ValidationError
from .security import digest

MAX_FILES = 12000
MAX_TOTAL = 1024 * 1024 * 1024
MAX_FILE = 64 * 1024 * 1024


@dataclass
class Entry:
    name: str
    directory: bool
    size: int
    executable: bool
    member: object


def normalized(name, *, directory):
    if name in (".", "./") and directory:
        return ""
    if name.startswith("./"):
        name = name[2:]
    if directory:
        name = name.rstrip("/")
    if (not name or len(name) > 240 or any(ord(char) < 32 or char in '\\:<>"|?*' for char in name) or name.startswith("/") or
            any(part in ("", ".", "..") or part.endswith((".", " ")) or
                part.split(".")[0].upper() in {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
                                               *(f"LPT{i}" for i in range(1, 10))}
                for part in name.split("/"))):
        raise ValidationError("SDK归档包含非法或跨平台歧义路径")
    return str(PurePosixPath(name))


class SafeArchive:
    def __init__(self, path):
        self.path = Path(path)
        self.zip = self.path.suffix == ".zip"
        if self.zip:
            # 先核中央目录数量/大小，避免ZipFile在应用上限检查前分配任意成员表。
            with self.path.open("rb") as source:
                length = source.seek(0, 2)
                source.seek(max(0, length - 65557))
                tail = source.read(65557)
            position = tail.rfind(b"PK\x05\x06")
            if position < 0 or len(tail) - position < 22:
                raise ValidationError("SDK ZIP缺少完整末尾目录")
            _, disk, start_disk, disk_count, count, size, offset, comment = struct.unpack_from("<4s4H2IH", tail, position)
            if (disk or start_disk or disk_count != count or count > MAX_FILES or size > 8 * 1024 * 1024 or
                    offset + size > length or position + 22 + comment != len(tail)):
                raise ValidationError("SDK ZIP中央目录数量、体积或分卷越界")
        self.archive = zipfile.ZipFile(path) if self.zip else tarfile.open(path, "r:gz")
        self.entries = []
        seen = set()
        total = 0
        try:
            members = self.archive.infolist() if self.zip else self.archive
            spellings = {}
            for count, member in enumerate(members, 1):
                if count > MAX_FILES:
                    raise ValidationError("SDK归档文件数越界")
                if self.zip:
                    mode = member.external_attr >> 16
                    directory = member.is_dir()
                    if member.flag_bits & 1 or stat.S_ISLNK(mode) or (stat.S_IFMT(mode) not in (0, stat.S_IFDIR, stat.S_IFREG)):
                        raise ValidationError("SDK ZIP包含链接、特殊文件或加密项")
                    if member.external_attr & 0x400:
                        raise ValidationError("SDK ZIP包含reparse属性")
                    size = member.file_size
                else:
                    directory = member.isdir()
                    if not directory and not member.isfile():
                        raise ValidationError("SDK TAR包含链接或特殊文件")
                    mode, size = member.mode, member.size
                name = normalized(member.filename if self.zip else member.name, directory=directory)
                if directory and size:
                    raise ValidationError("SDK归档目录带有数据体")
                for part in (PurePosixPath(name), *PurePosixPath(name).parents):
                    spelling = str(part)
                    previous = spellings.setdefault(spelling.casefold(), spelling)
                    if previous != spelling:
                        raise ValidationError("SDK归档隐含目录大小写冲突")
                folded = name.casefold()
                if folded in seen:
                    raise ValidationError("SDK归档成员重复或大小写冲突")
                seen.add(folded)
                total += size
                if size < 0 or size > MAX_FILE or total > MAX_TOTAL:
                    raise ValidationError("SDK归档解包体积越界")
                if name:
                    self.entries.append(Entry(name, directory, size, bool(mode & 0o111), member))
            paths = {entry.name.casefold(): entry.directory for entry in self.entries}
            for entry in self.entries:
                for parent in PurePosixPath(entry.name).parents:
                    if str(parent) != "." and paths.get(str(parent).casefold()) is False:
                        raise ValidationError("SDK归档文件被用作目录")
        except BaseException:
            self.archive.close()
            raise

    def content(self, entry):
        source = self.archive.open(entry.member) if self.zip else self.archive.extractfile(entry.member)
        with source:
            value = source.read(entry.size + 1)
        if len(value) != entry.size:
            raise ValidationError("SDK归档成员长度不符")
        return value

    def extract(self, tree):
        for entry in self.entries:
            path = tree.root / entry.name
            if entry.directory:
                tree.parents(path)
                try:
                    path.lstat()
                except FileNotFoundError:
                    tree.directory(path)
            else:
                tree.write(path, self.content(entry), executable=entry.executable)

    def verify(self, tree):
        expected = {entry.name for entry in self.entries if not entry.directory}
        expected_directories = {entry.name for entry in self.entries if entry.directory}
        for name in expected | expected_directories.copy():
            expected_directories.update(str(parent) for parent in PurePosixPath(name).parents if str(parent) != ".")
        actual, directories = set(), set()
        pending = [tree.root]
        while pending:
            parent = pending.pop()
            for path in parent.iterdir():
                info = path.lstat()
                if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                    raise ValidationError("既有SDK包含链接或reparse")
                if stat.S_ISDIR(info.st_mode):
                    tree.directory(path, existing=True)
                    directories.add(path.relative_to(tree.root).as_posix())
                    pending.append(path)
                else:
                    tree.check_file(path)
                    actual.add(path.relative_to(tree.root).as_posix())
        if actual != expected or directories != expected_directories:
            raise ValidationError("既有SDK多出或缺少文件/目录")
        for entry in self.entries:
            if not entry.directory:
                path = tree.check_file(tree.root / entry.name)
                if __import__("os").name != "nt" and stat.S_IMODE(path.stat().st_mode) != (0o700 if entry.executable else 0o600):
                    raise ValidationError("既有SDK执行权限与固定归档不符")
                if path.stat().st_size != entry.size or digest(path) != hashlib.sha256(self.content(entry)).hexdigest():
                    raise ValidationError("既有SDK文件与固定归档不符")
        return {"files": len(expected), "unpacked_bytes": sum(entry.size for entry in self.entries)}

    def close(self):
        self.archive.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
