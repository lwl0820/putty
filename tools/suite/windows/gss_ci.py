#!/usr/bin/env python3
"""Windows MIT GSS/SSH 的独立准备、真实执行与最终证据入口。"""
from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from tools.suite.windows.gss_kdc.ci import actual_profile, snapshot_profile
from tools.suite.windows.gss_kdc.prepare import LOCK, prepare as prepare_kdc, source_hash
from tools.suite.windows.gss_kdc.process import BoundedProcess
from tools.suite.windows.gss_kdc.security import PrivateTree, closed_environment, publish_report, utf8_streams
from tools.suite.windows.gss_mit.common import read_regular
from tools.suite.windows.gss_mit.prepare_kfw import validate_directory
from tools.suite.windows.gss_ssh.dependencies import ORIGINAL
from tools.suite.windows.gss_ssh.prepare import prepare as prepare_python
from tools.suite.windows.gss_ssh.run import SCENARIOS, RunFailure, validate_child
from tools.suite.windows.support import ValidationError, project_path

GSS_DIRECTORY = Path(__file__).resolve().parent / "gss_ssh"
MAX_REPORT = 65536


class _ProcessDeadline:
    """期限覆盖准备、原生进程、清理、标准输出与最终文件提交。"""

    def __init__(self, seconds):
        self.limit = time.monotonic() + seconds
        self.done = threading.Event()
        self.worker = threading.Thread(target=self._watch, daemon=True, name="windows-gss-ci-deadline")
        self.worker.start()

    def _watch(self):
        if not self.done.wait(max(0, self.limit - time.monotonic())):
            os._exit(124)

    def complete(self):
        if time.monotonic() >= self.limit:
            os._exit(124)
        self.done.set()


class _InnerFailure(ValidationError):
    def __init__(self, stage, major=None, minor=None):
        self.stage, self.major, self.minor = stage, major, minor
        super().__init__("内部 GSS 验收失败")


def native_windows():
    return sys.platform == "win32"


def decode_report(content):
    """拒绝嵌套重复字段、非有限数、超长整数及超额 JSON。"""
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("报告字段重复")
            result[key] = value
        return result

    def invalid(_):
        raise ValueError("报告数值非法")

    def finite(value):
        number = float(value)
        if not math.isfinite(number):
            raise ValueError("报告数值越界")
        return number

    try:
        if not isinstance(content, bytes) or len(content) > MAX_REPORT:
            raise ValueError("报告长度非法")
        result = json.loads(content.decode("utf-8"), object_pairs_hook=unique, parse_constant=invalid,
                            parse_float=finite)
        if not isinstance(result, dict):
            raise ValueError("报告必须为对象")
        return result
    except (ValueError, UnicodeDecodeError, RecursionError):
        raise ValidationError("GSS 报告不是有界唯一 JSON 对象") from None


def expected_sources():
    # 这里只读取仓库中的固定锁，不从主 Python 环境导入 Paramiko。
    with (GSS_DIRECTORY / "source-lock.json").open("rb") as source:
        content = source.read(16385)
    if len(content) > 16384:
        raise ValidationError("GSS 源码锁体积超限")
    lock = decode_report(content)
    if (set(lock) != {"schema", "paramiko_version", "original", "patched_auth_sha256", "patch_sha256"} or
            type(lock["schema"]) is not int or lock["schema"] != 1 or
            lock["paramiko_version"] != "4.0.0" or lock["original"] != ORIGINAL):
        raise ValidationError("GSS 源码锁不符合固定契约")
    for name in ("patched_auth_sha256", "patch_sha256"):
        value = lock[name]
        if not isinstance(value, str) or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
            raise ValidationError("GSS 源码锁摘要非法")
    return {**ORIGINAL, "auth_handler.py": lock["patched_auth_sha256"]}


def _integer(value, low, high):
    return type(value) is int and low <= value <= high


def _seconds(value, low, high):
    return type(value) in (int, float) and math.isfinite(value) and low <= value <= high


def preparation_summary(kdc, python):
    if (not isinstance(kdc, dict) or set(kdc) != {"schema", "kind", "passed", "sdk_version", "package_count",
            "sdk_files_verified", "source_sha256"} or type(kdc["schema"]) is not int or kdc["schema"] != 1 or
            kdc["kind"] != "gss-kdc-preparation" or kdc["passed"] is not True or
            kdc["sdk_version"] != LOCK["dotnet_sdk_version"] or
            not _integer(kdc["package_count"], len(LOCK["nuget_packages"]), len(LOCK["nuget_packages"])) or
            not _integer(kdc["sdk_files_verified"], 1, 32768) or kdc["source_sha256"] != source_hash()):
        raise ValidationError("KDC 准备缺少固定内容核验证据")
    expected = {"schema": 1, "kind": "gss-ssh-preparation", "passed": True, "wheel_count": 7,
                "paramiko_version": "4.0.0", "source_patch_verified": True, "authentication_verified": False}
    if (not isinstance(python, dict) or set(python) != set(expected) or
            any(type(python[key]) is not type(value) or python[key] != value for key, value in expected.items())):
        raise ValidationError("独立 Python 准备缺少七个固定 wheel 和源码补丁证据")
    return {"sdk_version": kdc["sdk_version"], "package_count": kdc["package_count"],
            "sdk_files_verified": kdc["sdk_files_verified"], "kdc_source_sha256": kdc["source_sha256"],
            "wheel_count": 7, "paramiko_version": "4.0.0", "source_patch_verified": True}


def kdc_summary(result):
    fields = {"schema", "kind", "passed", "realm", "encryption", "private_data_removed", "native_windows", "kdc", "proxy"}
    if (not isinstance(result, dict) or set(result) != fields or type(result["schema"]) is not int or
            result["schema"] != 1 or result["kind"] != "isolated-real-kdc" or result["passed"] is not True or
            result["realm"] != "SUITE.TEST" or result["encryption"] != "aes256-cts-hmac-sha1-96" or
            result["private_data_removed"] is not True or result["native_windows"] is not True):
        raise ValidationError("GSS KDC 摘要不符合原生清理契约")
    counters = result["kdc"]
    names = {"accepted", "completed", "rejected", "malformed", "as_requests", "tgs_requests", "active_connections", "active_workers"}
    if (not isinstance(counters, dict) or set(counters) != names | {"schema", "stopped"} or
            type(counters["schema"]) is not int or counters["schema"] != 1 or counters["stopped"] is not True or
            any(not _integer(counters[name], 0, 64) for name in names) or
            any(counters[name] for name in ("active_connections", "active_workers", "rejected", "malformed")) or
            not 5 <= counters["as_requests"] <= 32 or not 3 <= counters["tgs_requests"] <= 32 or
            counters["accepted"] != counters["completed"] or
            counters["completed"] != counters["as_requests"] + counters["tgs_requests"]):
        raise ValidationError("GSS KDC 缺少真实票据与零资源证据")
    proxy = result["proxy"]
    fields = {"requests", "responses", "configured_delay_seconds", "observed_delay_seconds",
              "active_connections", "accepted_connections", "live_workers"}
    if not isinstance(proxy, dict) or set(proxy) != fields:
        raise ValidationError("GSS 代理摘要字段非法")
    for label in ("requests", "responses"):
        item = proxy[label]
        expected = {"as", "tgs"} if label == "requests" else {"as", "tgs", "error"}
        if (not isinstance(item, dict) or set(item) != expected or
                any(not _integer(item[name], 0, 64) for name in item) or
                item["as"] != 0 or not 1 <= item["tgs"] <= 4 or item.get("error", 0)):
            raise ValidationError("GSS 代理缺少最后真实 TGS 证据")
    delays = proxy["observed_delay_seconds"]
    # reset_client 通过直接 KDC 获取 TGT，再清零代理计数；最后一轮只经代理 TGS。
    if (proxy["requests"]["tgs"] != proxy["responses"]["tgs"] or
            not _seconds(proxy["configured_delay_seconds"], 1.6, 1.6) or
            not isinstance(delays, list) or len(delays) != proxy["responses"]["tgs"] or
            any(not _seconds(value, 1.5, 30) for value in delays) or
            not _integer(proxy["accepted_connections"], 3, 64) or
            any(not _integer(proxy[name], 0, 0) for name in ("active_connections", "live_workers"))):
        raise ValidationError("GSS 代理缺少延迟或零资源证据")
    return {"private_data_removed": True, "kdc": {name: counters[name] for name in sorted(names)},
            "proxy": {"active_connections": 0, "live_workers": 0,
                      "accepted_connections": proxy["accepted_connections"],
                      "requests": {name: proxy["requests"][name] for name in ("as", "tgs")},
                      "responses": {name: proxy["responses"][name] for name in ("as", "tgs", "error")},
                      "configured_delay_seconds": 1.6, "observed_delay_seconds": list(delays)}}


def protocol_summary(result, sources):
    failed = {"schema", "kind", "passed", "stage", "kerberos_authentication_verified", "ssh_login_verified",
              "formal_plink_verified", "sspi_verified"}
    if isinstance(result, dict) and result.get("passed") is False:
        stages = {"pending", "validate-input", "dependencies", "prepared-kdc", "kdc-start", "kdc-cleanup",
                  "child-protocol", "real-tgs-not-observed", "replay-cache-not-created"}
        native_stages = set()
        for case in SCENARIOS:
            stages.update(("reset-" + case, "case-" + case, "child-" + case))
            for stage in ("dependencies", "native-load", "native-cleanup", "token-mic" if case == "token-mic" else "ssh-authentication"):
                value = "child-" + case + "-" + stage
                stages.add(value)
                if stage != "dependencies":
                    native_stages.add(value)
        with_codes = set(result) == failed | {"native_major", "native_minor"}
        if (set(result) not in (failed, failed | {"native_major", "native_minor"}) or
                type(result["schema"]) is not int or result["schema"] != 1 or result["kind"] != "mit-gss-ssh" or
                not isinstance(result["stage"], str) or result["stage"] not in stages or
                any(result[name] is not False for name in ("kerberos_authentication_verified", "ssh_login_verified", "formal_plink_verified", "sspi_verified")) or
                (with_codes and (result["stage"] not in native_stages or not _integer(result["native_major"], 1, 2**32 - 1) or
                                 not _integer(result["native_minor"], 0, 2**32 - 1)))):
            raise ValidationError("GSS 失败报告阶段或原生状态非法")
        raise _InnerFailure(result["stage"], result.get("native_major"), result.get("native_minor"))
    flags = {"credentials_acquired", "kerberos_authentication_verified", "ssh_login_verified", "private_data_removed",
             "all_native_objects_released", "all_threads_closed", "native_windows"}
    fields = flags | {"schema", "kind", "passed", "paramiko_version", "sspi_verified", "formal_plink_verified", "source_sha256", "checks", "kdc"}
    if (not isinstance(result, dict) or set(result) != fields or type(result["schema"]) is not int or
            result["schema"] != 1 or result["kind"] != "mit-gss-ssh" or result["passed"] is not True or
            result["paramiko_version"] != "4.0.0" or result["sspi_verified"] is not False or
            result["formal_plink_verified"] is not False or any(result[name] is not True for name in flags) or
            result["source_sha256"] != sources or not isinstance(result["checks"], dict) or set(result["checks"]) != set(SCENARIOS)):
        raise ValidationError("GSS 成功报告缺少固定五场景证据")
    checks = {}
    for case in SCENARIOS:
        # 细节协议由已有 GSS 边界统一核验，避免复制认证状态机的判定规则。
        child = {"schema": 1, "kind": "mit-gss-ssh-child", "passed": True, "scenario": case,
                 "native_windows": os.name == "nt", "sspi_verified": False, "all_native_objects_released": True,
                 "source_sha256": sources, "detail": result["checks"][case]}
        try:
            detail = validate_child(json.dumps(child, allow_nan=False).encode("utf-8"), case, sources)
        except (RunFailure, ValueError, TypeError):
            raise ValidationError("GSS 场景细节缺少对应认证证据") from None
        checks[case] = {name: detail[name] for name in sorted(detail)}
    return {"checks": checks, "source_sha256": dict(sources), "kdc": kdc_summary(result["kdc"]),
            **{name: True for name in flags}}


def invoke(tools, python_tools, mit_directory, parent, invocation):
    inner_report = invocation.expect_file(invocation.root / "inner-report.json")
    environment = closed_environment(invocation)
    python = project_path(python_tools / "venv" / "Scripts" / "python.exe", must_exist=True)
    command = [python, "-I", "-B", GSS_DIRECTORY / "run.py", "--prepared-kdc", tools,
               "--parent", parent, "--mit-directory", mit_directory, "--report", inner_report]
    child = BoundedProcess(command, cwd=invocation.root, environment=environment,
                           timeout=190, output_limit=MAX_REPORT, line_protocol=False)
    failed = False
    try:
        try:
            child.finish()
        except Exception:
            failed = True
        # 外部 Python 的原子报告继承目录 ACL；它是本次夹具文件，先固定并收紧再读。
        invocation.check_file(inner_report, harden=True)
        candidate = decode_report(bytes(child.stdout))
        final = decode_report(read_regular(inner_report, maximum=MAX_REPORT))
        # Python 对 bool/int 的等号会混淆类型；规范 JSON 比较保留这种差异。
        if json.dumps(candidate, sort_keys=True, allow_nan=False) != json.dumps(final, sort_keys=True, allow_nan=False):
            raise ValidationError("内部候选与最终文件不一致")
        summary = protocol_summary(final, expected_sources())
        if failed or child.process.returncode != 0:
            raise ValidationError("内部进程未以零状态完整收尾")
        return summary
    finally:
        child.abort()


def pending_report():
    return {"schema": 1, "kind": "windows-mit-gss-ssh", "passed": False, "stage": "pending",
            "native_windows": native_windows(), "gss_ssh_verified": False, "formal_plink_verified": False,
            "sspi_verified": False, "personal_metadata_before_observed": False,
            "personal_metadata_after_observed": False, "personal_metadata_unchanged": False,
            "private_parent_removed": False, "private_invocation_removed": False}


def run(tools, python_tools, mit_directory, parent, invocation_root, report_path):
    """只返回白名单候选；成功文件由入口在 stdout 刷新后提交。"""
    report_path = project_path(report_path)
    result = pending_report()
    publish_report(report_path, result)
    if not native_windows():
        result.update(stage="windows-required", skipped=True)
        return result
    before = profile = private_parent = invocation = preparation = protocol = mit = None
    failed_stage = None
    try:
        result["stage"] = "personal-metadata-before"
        profile = actual_profile()
        before = snapshot_profile(profile)
        result["personal_metadata_before_observed"] = True
        result["stage"] = "validate-input"
        tools, python_tools, mit_directory, parent, invocation_root = (
            project_path(value) for value in (tools, python_tools, mit_directory, parent, invocation_root))
        roots = (tools, python_tools, mit_directory, parent, invocation_root)
        if (any(left == right or left.is_relative_to(right) or right.is_relative_to(left)
                for index, left in enumerate(roots) for right in roots[index + 1:]) or
                any(report_path == root or report_path.is_relative_to(root) or root.is_relative_to(report_path) for root in roots)):
            raise ValidationError("公开报告、依赖、调用环境和运行资料必须互相独立")
        if not all(str(root).isascii() for root in roots):
            raise ValidationError("固定 KfW 窄字符夹具只接受已证明的 ASCII 路径")
        mit = validate_directory(project_path(mit_directory, must_exist=True))
        result["stage"] = "private-parent"
        private_parent = PrivateTree(parent)
        result["stage"] = "private-invocation"
        invocation = PrivateTree(invocation_root)
        result["stage"] = "prepare-kdc"
        kdc = prepare_kdc(tools)
        result["stage"] = "prepare-python"
        python = prepare_python(python_tools)
        result["stage"] = "validate-preparation"
        preparation = preparation_summary(kdc, python)
        result["stage"] = "real-gss-ssh"
        protocol = invoke(tools, python_tools, mit_directory, parent, invocation)
    except _InnerFailure as error:
        failed_stage = "inner-failed"
        result["inner_stage"] = error.stage
        if error.major is not None:
            result.update(native_major=error.major, native_minor=error.minor)
    except Exception:
        failed_stage = result["stage"]
    finally:
        for tree, label in ((invocation, "private-invocation"), (private_parent, "private-parent")):
            if tree is None:
                continue
            try:
                # invocation 仅含已登记环境目录与单一 inner 文件；parent 必须为空。
                if label == "private-parent" and next(tree.root.iterdir(), None) is not None:
                    raise ValidationError("运行父目录仍含未知或失败资料")
                tree.remove()
                result[label.replace("-", "_") + "_removed"] = True
            except Exception:
                failed_stage = failed_stage or label + "-cleanup"
            finally:
                try:
                    tree.close()
                except Exception:
                    failed_stage = failed_stage or label + "-cleanup"
        if profile is not None:
            try:
                after = snapshot_profile(profile)
                result["personal_metadata_after_observed"] = True
                if before is not None:
                    result["personal_metadata_unchanged"] = before == after
                    result["observed_file_count"] = after.files
                    result["observed_total_bytes"] = after.total_bytes
                if not result["personal_metadata_unchanged"]:
                    failed_stage = "personal-metadata-changed" if before is not None else failed_stage or "personal-metadata-before"
            except Exception:
                failed_stage = "personal-metadata-after"
    if (failed_stage is None and protocol is not None and preparation is not None and
            result["private_parent_removed"] and result["private_invocation_removed"] and result["personal_metadata_unchanged"]):
        result.update(passed=True, stage="complete", gss_ssh_verified=True,
                      mit_version=mit["version"], preparation=preparation, protocol=protocol)
    else:
        result["stage"] = failed_stage or "incomplete"
    return result


def main(*, deadline_seconds=18 * 60):
    utf8_streams()
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("tools", "python-tools", "mit-directory", "parent", "invocation", "report"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    try:
        publish_report(args.report, pending_report())
    except Exception:
        print("Windows GSS 验收占位报告失败", file=sys.stderr, flush=True)
        return 1
    try:
        deadline = _ProcessDeadline(deadline_seconds)
    except Exception:
        print("Windows GSS 验收期限启动失败", file=sys.stderr, flush=True)
        return 1
    try:
        result = run(args.tools, args.python_tools, args.mit_directory, args.parent, args.invocation, args.report)
        print(json.dumps(result, ensure_ascii=False, allow_nan=False), flush=True)
        publish_report(args.report, result)
    except Exception:
        print("Windows GSS 验收入口失败", file=sys.stderr, flush=True)
        deadline.complete()
        return 1
    deadline.complete()
    return 0 if result["passed"] else 77 if result.get("skipped") else 1


if __name__ == "__main__":
    raise SystemExit(main())
