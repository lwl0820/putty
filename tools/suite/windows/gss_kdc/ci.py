#!/usr/bin/env python3
"""Windows 原生 KDC 验收入口；用户目录快照仅在内存比较。"""
from __future__ import annotations

import argparse
from contextlib import contextmanager, ExitStack
import ctypes as C
from ctypes import wintypes as W
from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[4]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from tools.suite.windows.private_data import PrivateData
from tools.suite.windows.support import ValidationError, project_path
from tools.suite.windows.gss_kdc.prepare import LOCK, prepare
from tools.suite.windows.gss_kdc.security import PrivateTree, publish_report, utf8_streams
from tools.suite.windows.gss_kdc.verify import verify
from tools.suite.windows.gss_mit.prepare_kfw import validate_directory

MAX_ENTRIES = 32768
MAX_BYTES = 2 * 1024 * 1024 * 1024
MAX_FILE = 256 * 1024 * 1024
SNAPSHOT_SECONDS = 60
PROFILE_DIRECTORIES = (".dotnet", ".nuget", ".aspnet")


class _ProcessDeadline:
    """本进程超时即退出；原生 Job 随句柄关闭回收后代，不提交清理成功。"""

    def __init__(self, seconds):
        self.deadline = time.monotonic() + seconds
        self.finished = threading.Event()
        self.worker = threading.Thread(target=self._watch, daemon=True, name="windows-kdc-deadline")
        self.worker.start()

    def _watch(self):
        if not self.finished.wait(max(0, self.deadline - time.monotonic())):
            os._exit(124)

    def complete(self):
        if time.monotonic() >= self.deadline:
            os._exit(124)
        self.finished.set()


def native_windows():
    return sys.platform == "win32"


def actual_profile():
    """从当前进程令牌读取真实 profile，不能用可重定向的环境变量代替。"""
    native = PrivateData()
    token = W.HANDLE()
    try:
        native._ok(native.security.OpenProcessToken(native.kernel.GetCurrentProcess(), 8, C.byref(token)))
        userenv = C.WinDLL("userenv", use_last_error=True)
        function = userenv.GetUserProfileDirectoryW
        function.argtypes = [W.HANDLE, W.LPWSTR, C.POINTER(W.DWORD)]
        function.restype = W.BOOL
        size = W.DWORD()
        function(token, None, C.byref(size))
        if not 1 < size.value <= 32768:
            raise ValidationError("真实用户目录长度不符合边界")
        value = C.create_unicode_buffer(size.value)
        native._ok(function(token, value, C.byref(size)))
        result = Path(value.value)
        if not result.is_absolute():
            raise ValidationError("真实用户目录不是绝对路径")
        return result
    finally:
        try:
            if token:
                native._ok(native.kernel.CloseHandle(token))
        finally:
            native.close()


@dataclass
class ProfileSnapshot:
    records: dict
    files: int
    total_bytes: int


class _ReadOnlySnapshot:
    """仅此观察器接触真实用户目录；不创建文件、不修改 ACL、不记录路径。"""

    def __init__(self):
        self.native = PrivateData() if sys.platform == "win32" else None
        if self.native:
            self.native.kernel.ReadFile.argtypes = [W.HANDLE, C.c_void_p, W.DWORD, C.POINTER(W.DWORD), C.c_void_p]
            self.native.kernel.ReadFile.restype = W.BOOL
            self.native.security.GetSecurityDescriptorLength.argtypes = [C.c_void_p]
            self.native.security.GetSecurityDescriptorLength.restype = W.DWORD

    def close(self):
        if self.native:
            self.native.close()

    @contextmanager
    def opened(self, path, directory):
        info = path.lstat()
        if (stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400 or
                (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)) is not True or
                (not directory and info.st_nlink != 1)):
            raise ValidationError("用户目录观察拒绝链接、reparse 或特殊对象")
        if self.native:
            # 复用实际路径、类型和单链接检查；仅申请读取，不执行私有 ACL 收紧。
            handle = self.native._open(path, directory, 0x20080 | (0 if directory else 0x80000000), ancestor=True)
        else:
            handle = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | (os.O_DIRECTORY if directory else 0))
        try:
            yield handle
        finally:
            if self.native:
                self.native._ok(self.native.kernel.CloseHandle(handle))
            else:
                os.close(handle)

    def metadata(self, handle):
        if not self.native:
            info = os.fstat(handle)
            # 读取可能更新 atime；其余身份、权限、内容长度和修改时间必须保持不变。
            return (info.st_dev, info.st_ino, info.st_mode, info.st_nlink, info.st_uid,
                    info.st_gid, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
        information = (W.DWORD * 13)()
        self.native._ok(self.native.kernel.GetFileInformationByHandle(handle, information))
        # 排除两个 last-access DWORD，保留真实创建/写入时间、属性、卷、长度、链接数及文件身份。
        values = tuple(information[index] for index in (0, 1, 2, 5, 6, 7, 8, 9, 10, 11, 12))
        descriptor = C.c_void_p()
        if self.native.security.GetSecurityInfo(handle, 1, 7, None, None, None, None, C.byref(descriptor)):
            raise ValidationError("无法只读核验用户目录权限元数据")
        try:
            size = self.native.security.GetSecurityDescriptorLength(descriptor)
            if not 20 <= size <= 65536:
                raise ValidationError("用户目录权限元数据体积超限")
            return (*values, hashlib.sha256(C.string_at(descriptor, size)).digest())
        finally:
            self.native.kernel.LocalFree(descriptor)

    def content(self, handle, check_budget):
        value, length = hashlib.sha256(), 0
        buffer = C.create_string_buffer(65536) if self.native else None
        while True:
            check_budget()
            if self.native:
                count = W.DWORD()
                self.native._ok(self.native.kernel.ReadFile(handle, buffer, len(buffer), C.byref(count), None))
                part = buffer.raw[:count.value]
            else:
                part = os.read(handle, 65536)
            if not part:
                return length, value.digest()
            length += len(part)
            if length > MAX_FILE:
                raise ValidationError("用户目录单文件观察体积超限")
            value.update(part)


def snapshot_profile(profile):
    """只观察三个确切目录，任何缺失、替换或内容变化均体现在内存结果中。"""
    profile = Path(profile).absolute()
    observer = _ReadOnlySnapshot()
    records, files, total_bytes = {}, 0, 0
    deadline = time.monotonic() + SNAPSHOT_SECONDS

    def check_budget():
        if time.monotonic() >= deadline or len(records) > MAX_ENTRIES or total_bytes > MAX_BYTES:
            raise ValidationError("用户目录观察超出项数、体积或时间上限")

    def visit(path, relative, depth):
        nonlocal files, total_bytes
        check_budget()
        if depth > 64:
            raise ValidationError("用户目录观察深度超限")
        try:
            info = path.lstat()
        except FileNotFoundError:
            if depth != 0:
                raise ValidationError("用户目录观察期间对象消失") from None
            records[relative] = ("missing",)
            return
        directory = stat.S_ISDIR(info.st_mode)
        with observer.opened(path, directory) as handle:
            before = observer.metadata(handle)
            if directory:
                records[relative] = ("directory", before)
                # 逐项有界枚举，不能先将任意大目录 materialize 为列表。
                with os.scandir(path) as entries:
                    for entry in entries:
                        visit(path / entry.name, relative + "/" + entry.name, depth + 1)
            else:
                if info.st_size > MAX_FILE or total_bytes + info.st_size > MAX_BYTES:
                    raise ValidationError("用户目录观察声明体积超限")
                length, digest = observer.content(handle, check_budget)
                total_bytes += length
                files += 1
                records[relative] = ("file", before, length, digest)
            if observer.metadata(handle) != before:
                raise ValidationError("用户目录观察期间身份或元数据发生变化")
        check_budget()

    try:
        with ExitStack() as pins:
            for ancestor in (*reversed(profile.parents), profile):
                if ancestor != Path(ancestor.anchor):
                    pins.enter_context(observer.opened(ancestor, True))
            for name in PROFILE_DIRECTORIES:
                visit(profile / name, name, 0)
            check_budget()
        return ProfileSnapshot(records, files, total_bytes)
    finally:
        observer.close()


def protocol_summary(result):
    """只重建允许发布的固定布尔和有界数值，不转发内层任意字段。"""
    required = ("native_windows", "only_tgt_after_as", "service_keytab_decryption_verified",
                "forwardable_as_rejected", "forwarding_rejected", "cross_realm_rejected",
                "unknown_service_rejected", "reset_removes_service_ticket", "oversize_zero_malformed_rejected")
    if (not isinstance(result, dict) or type(result.get("schema")) is not int or
            result["schema"] != 1 or result.get("kind") != "real-mit-kdc-crosscheck" or
            result.get("passed") is not True or any(result.get(name) is not True for name in required)):
        raise ValidationError("KDC协议成功摘要缺少实际证据")
    output = {name: True for name in required}
    for name, expected in (("kvno", 1), ("network_policy_probes", 8), ("connection_budget", 64),
                           ("proxy_worker_budget", 8), ("proxy_socket_budget", 16)):
        if type(result.get(name)) is not int or result[name] != expected:
            raise ValidationError("KDC协议计数不符合固定契约")
        output[name] = expected
    for name, low, high in (("tgs_elapsed_seconds", 1.5, 30), ("inflight_close_seconds", 0, 6)):
        value = result.get(name)
        if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
            raise ValidationError("KDC协议时间不符合固定契约")
        output[name] = value
    for name in ("normal", "inflight"):
        item = result.get(name, {})
        if (not isinstance(item, dict) or not isinstance(item.get("kdc"), dict) or
                not isinstance(item.get("proxy"), dict) or
                item.get("passed") is not True or item.get("native_windows") is not True or
                item.get("private_data_removed") is not True or
                any(type(item.get("kdc", {}).get(key)) is not int or item["kdc"][key] != 0
                    for key in ("active_connections", "active_workers")) or
                any(type(item.get("proxy", {}).get(key)) is not int or item["proxy"][key] != 0
                    for key in ("active_connections", "live_workers"))):
            raise ValidationError("KDC协议缺少关闭与私有资料清理证据")
    output["private_data_removed"] = True
    return output


def pending_report():
    return {"schema": 1, "kind": "windows-mit-kdc", "passed": False, "stage": "pending",
            "native_windows": native_windows(), "gss_ssh_verified": False,
            "formal_plink_verified": False, "sspi_verified": False,
            "personal_metadata_before_observed": False, "personal_metadata_after_observed": False,
            "personal_metadata_unchanged": False, "private_parent_removed": False}


def run(tools, mit_directory, parent, report_path):
    """返回候选结果；CLI 刷新标准输出之后才发布最终文件。"""
    report_path = project_path(report_path)
    result = pending_report()
    publish_report(report_path, result)
    if not native_windows():
        result.update(stage="windows-required", skipped=True)
        return result
    before = profile = private_parent = None
    protocol = preparation = mit_lock = None
    failed_stage = None
    try:
        result["stage"] = "personal-metadata-before"
        profile = actual_profile()
        before = snapshot_profile(profile)
        result["personal_metadata_before_observed"] = True
        result["stage"] = "validate-mit-tools"
        mit_directory = project_path(mit_directory, must_exist=True)
        mit_lock = validate_directory(mit_directory)
        tools, parent = project_path(tools), project_path(parent)
        roots = (tools, mit_directory, parent)
        if (any(left == right or left.is_relative_to(right) or right.is_relative_to(left)
                for index, left in enumerate(roots) for right in roots[index + 1:]) or
                any(report_path == root or report_path.is_relative_to(root) for root in roots)):
            raise ValidationError("KDC运行根不能包含依赖或公开报告")
        result["stage"] = "private-parent"
        # 本轮独占新建父目录；不清理调用者预先存在或失败留下的未知资料。
        private_parent = PrivateTree(parent)
        result["stage"] = "prepare-kdc"
        preparation = prepare(tools)
        if (not isinstance(preparation, dict) or type(preparation.get("schema")) is not int or
                preparation["schema"] != 1 or preparation.get("kind") != "gss-kdc-preparation" or
                preparation.get("passed") is not True or preparation.get("sdk_version") != LOCK["dotnet_sdk_version"] or
                type(preparation.get("package_count")) is not int or
                preparation.get("package_count") != len(LOCK["nuget_packages"]) or
                type(preparation.get("sdk_files_verified")) is not int or
                not 1 <= preparation["sdk_files_verified"] <= MAX_ENTRIES or
                not re.fullmatch(r"[0-9a-f]{64}", preparation.get("source_sha256", ""))):
            raise ValidationError("KDC准备结果没有固定依赖核验证据")
        result["stage"] = "real-as-tgs"
        protocol = protocol_summary(verify(tools, parent, *(mit_directory / name for name in ("kinit.exe", "kvno.exe", "klist.exe"))))
    except Exception:
        failed_stage = result["stage"]
    finally:
        if private_parent:
            try:
                # verify 已清理各自拥有的随机子树；只允许删除本次确切的空父目录。
                if next(private_parent.root.iterdir(), None) is not None:
                    raise ValidationError("KDC运行父目录仍含未知或失败资料")
                private_parent.remove()
                result["private_parent_removed"] = True
            except Exception:
                failed_stage = failed_stage or "private-parent-cleanup"
            finally:
                try:
                    private_parent.close()
                except Exception:
                    failed_stage = failed_stage or "private-parent-cleanup"
        if profile is not None:
            try:
                after = snapshot_profile(profile)
                result["personal_metadata_after_observed"] = True
                if before is not None:
                    result["personal_metadata_unchanged"] = before == after
                    result["observed_file_count"] = after.files
                    result["observed_total_bytes"] = after.total_bytes
                if not result["personal_metadata_unchanged"]:
                    failed_stage = "personal-metadata-changed" if before is not None else (failed_stage or "personal-metadata-before")
            except Exception:
                failed_stage = "personal-metadata-after"
    if failed_stage is None and protocol is not None and result["private_parent_removed"]:
        result.update(passed=True, stage="complete", as_tgs_verified=True,
                      sdk_version=preparation["sdk_version"], package_count=preparation["package_count"],
                      sdk_files_verified=preparation["sdk_files_verified"], source_sha256=preparation["source_sha256"],
                      mit_version=mit_lock["version"], protocol=protocol)
    else:
        result["stage"] = failed_stage or "incomplete"
    return result


def main(*, deadline_seconds=18 * 60):
    utf8_streams()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tools", type=Path, required=True)
    parser.add_argument("--mit-directory", type=Path, required=True)
    parser.add_argument("--parent", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    arguments = parser.parse_args()
    try:
        # watchdog 启动前先使旧成功失效；后续整个执行、收尾、输出和提交都受硬期限保护。
        publish_report(arguments.report, pending_report())
    except Exception:
        print("Windows KDC 验收占位报告失败", file=sys.stderr, flush=True)
        return 1
    try:
        deadline = _ProcessDeadline(deadline_seconds)
    except Exception:
        print("Windows KDC 验收期限启动失败", file=sys.stderr, flush=True)
        return 1
    try:
        result = run(arguments.tools, arguments.mit_directory, arguments.parent, arguments.report)
        # 标准输出只是一份候选；刷新失败或挂起时不能先留下成功文件。
        print(json.dumps(result, ensure_ascii=False), flush=True)
        publish_report(arguments.report, result)
    except Exception:
        print("Windows KDC 验收入口失败", file=sys.stderr, flush=True)
        deadline.complete()
        return 1
    deadline.complete()
    return 0 if result["passed"] else (77 if result.get("skipped") else 1)


if __name__ == "__main__":
    raise SystemExit(main())
