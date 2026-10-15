"""Windows x64 无凭据探针：内容锁、真实 DLL 路径、ABI 与私有配置选择。"""
import argparse
import json
import os
from pathlib import Path
import re
import sys
import threading
import time
import uuid

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[4]))
from tools.suite.windows.gss_mit.common import (
    ValidationError, WorkspaceFiles, closed_environment, digest, project_path,
    publish_report, run_bounded, utf8_streams,
)
from tools.suite.windows.gss_mit.mit_gss import DefaultsFailure, MitGss
from tools.suite.windows.gss_mit.prepare_kfw import validate_directory

NATIVE_SCRIPT = Path(__file__).resolve().with_name("native_probe.py")
NATIVE_STAGES = frozenset(("validate-input", "closed-environment", "native-dll-load", "mechanism-enumeration",
                         "configuration-defaults", "configuration-context", "configuration-realm-read",
                         "configuration-realm-match", "configuration-cache-read", "configuration-cache-match",
                         "absent-credentials", "final-directory"))
CHILD_CHECKS = ("windows_loader_verified", "abi_layout_verified", "private_configuration_verified",
                "inherited_kerberos_environment_removed", "configuration_file_cache_selected")
NATIVE_STATUS_STAGES = frozenset(("configuration-context", "configuration-realm-read"))


class ProbeFailure(ValidationError):
    """只向报告公开固定阶段名及可选的原生数值状态。"""

    def __init__(self, stage, status=None):
        self.stage, self.status = stage, status
        super().__init__("MIT 原生 ABI 探针失败")


class _ProcessDeadline:
    """原生调用无法由 Python 取消；超时直接结束本探针进程。"""

    def __init__(self, seconds):
        self.deadline = time.monotonic() + seconds
        self.finished = threading.Event()
        self.worker = threading.Thread(target=self._watch, daemon=True, name="mit-abi-deadline")
        self.worker.start()

    def _watch(self):
        if not self.finished.wait(max(0, self.deadline - time.monotonic())):
            # 不在超时分支清理或写成功；尚未提交结果时保留落盘的失败占位。
            os._exit(124)

    def complete(self):
        # 不能因 watchdog 尚未获调度，就允许超过固定期限的尾部收尾成功。
        if time.monotonic() >= self.deadline:
            os._exit(124)
        self.finished.set()


def cleanup_run(root):
    """仅删除本次已知配置与空目录；出现未知内容时失败，不递归删除。"""
    root = project_path(root, must_exist=True)
    expected = {"krb5.ini", "replay", "plugins"}
    if {entry.name for entry in root.iterdir()} - expected:
        raise ValidationError("本次运行目录出现未知内容，拒绝递归清理")
    for name in ("krb5.ini", "replay", "plugins"):
        path = project_path(root / name)
        try:
            path.lstat()
        except FileNotFoundError:
            continue
        if name == "krb5.ini":
            from tools.suite.windows.gss_mit.common import regular_file
            regular_file(path, maximum=65536)
            path.unlink()
        else:
            path.rmdir()
    root.rmdir()
    try:
        root.lstat()
    except FileNotFoundError:
        return
    raise ValidationError("本次私有运行目录清理未完成")


def runtime_environment(root):
    """此字典交给 CreateProcess；原生子进程从不修改 CRT 的环境副本。"""
    environment = closed_environment(root)
    environment.update({"KRB5_CONFIG": (root / "krb5.ini").as_posix(),
                        "KRB5_KTNAME": "FILE:" + (root / "absent-server.keytab").as_posix(),
                        "KRB5_CLIENT_KTNAME": "FILE:" + (root / "absent-client.keytab").as_posix(),
                        "KRB5RCACHEDIR": (root / "replay").as_posix(), "KRB5RCACHETYPE": "dfl"})
    return environment


def _native_probe(directory, root, realm):
    """只在隔离启动的子进程调用；返回值没有私有配置、路径或随机 realm。"""
    stage = "validate-input"
    try:
        if sys.platform != "win32":
            raise ValidationError("原生子进程需要 Windows")
        directory, root = project_path(directory, must_exist=True), project_path(root, must_exist=True)
        if (directory != root.parent / "portable" / "bin" or not str(root).isascii() or
                not re.fullmatch(r"abi-[0-9a-f]{32}", root.name) or
                not re.fullmatch(r"ABI-[0-9A-F]{32}\.TEST", realm)):
            raise ValidationError("原生子进程输入不符合固定运行根")
        stage = "closed-environment"
        expected = {key.upper(): value for key, value in runtime_environment(root).items()}
        if {key.upper(): value for key, value in os.environ.items()} != expected or Path.cwd() != root:
            raise ValidationError("原生子进程必须在创建时收到封闭环境和工作目录")
        lock = validate_directory(directory)
        stage = "native-dll-load"
        api = MitGss(directory)
        stage = "mechanism-enumeration"
        mechanism_count = api.mechanism_count()
        stage = "configuration-defaults"
        api.verify_defaults(realm, "FILE:" + (root / "absent-profile.ccache").as_posix())
        stage = "absent-credentials"
        for name in ("absent-profile.ccache", "absent-server.keytab", "absent-client.keytab"):
            try:
                (root / name).lstat()
            except FileNotFoundError:
                continue
            raise ValidationError("无凭据探针不应产生缓存或 keytab")
        stage = "final-directory"
        validate_directory(directory, lock)
        return {"passed": True, "loaded_dll_count": len(api.loaded), "mechanism_count": mechanism_count,
                **dict.fromkeys(CHILD_CHECKS, True)}
    except Exception as error:
        result = {"passed": False, "stage": error.stage if isinstance(error, DefaultsFailure) else stage}
        if isinstance(error, DefaultsFailure) and error.status is not None:
            result["native_status"] = error.status
        return result


def native_main():
    """内部子进程协议：退出 0 仅表示固定 JSON 已送达，最终成败由父进程决定。"""
    parser = argparse.ArgumentParser(description="MIT 原生内部子进程")
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--realm", required=True)
    parser.add_argument("--deadline", type=float, required=True)
    args = parser.parse_args()
    remaining = args.deadline - time.monotonic()
    if not 0 < remaining <= 60:
        return 1
    # 父进程已经落盘失败占位；此处超时不写成功，也不声称运行根已清理。
    deadline = _ProcessDeadline(remaining)
    result = _native_probe(args.directory, args.root, args.realm)
    print(json.dumps(result), flush=True)
    deadline.complete()
    return 0


def _invoke_native(directory, root, realm, deadline):
    # 为父进程的管道收尾和精确清理保留七秒；子进程还有独立原生调用硬期限。
    budget = min(45, deadline - time.monotonic() - 7)
    if budget <= 0:
        raise ProbeFailure("native-child-budget")
    try:
        output = run_bounded([sys.executable, "-I", "-S", "-B", str(NATIVE_SCRIPT), "--directory", str(directory),
                              "--root", str(root), "--realm", realm,
                              "--deadline", str(time.monotonic() + budget)], maximum=16384,
                             cwd=root, environment=runtime_environment(root), timeout=budget + 1)
    except Exception as error:
        raise ProbeFailure("native-child-process") from error
    try:
        # 重复键不能由 json.loads 的末值覆盖掩盖矛盾成败或未知阶段。
        def unique_fields(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValidationError("原生子进程 JSON 字段重复")
                result[key] = value
            return result

        result = json.loads(output, object_pairs_hook=unique_fields)
    except Exception as error:
        raise ProbeFailure("native-child-protocol") from error
    if not isinstance(result, dict) or type(result.get("passed")) is not bool:
        raise ProbeFailure("native-child-protocol")
    if not result["passed"]:
        if (set(result) - {"passed", "stage", "native_status"} or type(result.get("stage")) is not str or
                result["stage"] not in NATIVE_STAGES or
                ("native_status" in result) != (result["stage"] in NATIVE_STATUS_STAGES) or
                ("native_status" in result and (type(result["native_status"]) is not int or
                                               not -(2 ** 31) <= result["native_status"] < 2 ** 31))):
            raise ProbeFailure("native-child-protocol")
        raise ProbeFailure(result["stage"], result.get("native_status"))
    if (set(result) != {"passed", "loaded_dll_count", "mechanism_count", *CHILD_CHECKS} or
            type(result["loaded_dll_count"]) is not int or result["loaded_dll_count"] != 6 or
            type(result["mechanism_count"]) is not int or not 1 <= result["mechanism_count"] <= 32 or
            any(result[key] is not True for key in CHILD_CHECKS)):
        raise ProbeFailure("native-child-protocol")
    return result


def probe(directory, parent, *, deadline=None):
    if sys.platform != "win32":
        raise ValidationError("必须由原生 Windows x64 执行，不能用 Linux 结果代替")
    directory, parent = project_path(directory, must_exist=True), project_path(parent, must_exist=True)
    if directory != parent / "portable" / "bin":
        raise ValidationError("探针只接受本次准备目录中的固定 portable/bin")
    if not str(parent).isascii():
        raise ValidationError("固定 KfW 4.1 的窄字符路径探针要求 ASCII 项目路径")
    lock = validate_directory(directory)
    workspace = WorkspaceFiles()
    stage = "private-acl"
    root = None
    try:
        workspace.directory(parent, existing=True)
        workspace.directory(parent / "portable", existing=True)
        workspace.directory(directory, existing=True)
        for record in lock["files"]:
            workspace.private.check_file(directory / record["name"])
        root = workspace.directory(parent / ("abi-" + uuid.uuid4().hex))
        workspace.directory(root / "replay")
        plugins = workspace.directory(root / "plugins")
        realm = "ABI-" + uuid.uuid4().hex.upper() + ".TEST"
        cache = root / "absent-profile.ccache"
        server_keytab, client_keytab = root / "absent-server.keytab", root / "absent-client.keytab"
        cache_name = "FILE:" + cache.as_posix()
        config = ("[libdefaults]\n default_realm = " + realm + "\n"
                  " default_ccache_name = " + cache_name + "\n"
                  " default_keytab_name = FILE:" + server_keytab.as_posix() + "\n"
                  " default_client_keytab_name = FILE:" + client_keytab.as_posix() + "\n"
                  " plugin_base_dir = " + plugins.as_posix() + "\n"
                  " dns_lookup_kdc = false\n dns_lookup_realm = false\n rdns = false\n"
                  "[plugins]\n ccselect = {\n  disable = k5identity\n }\n")
        workspace.write(root / "krb5.ini", config.encode("ascii"))
        stage = "native-child-process"
        native = _invoke_native(directory, root, realm, time.monotonic() + 60 if deadline is None else deadline)
        stage = "absent-credentials"
        for absent in (cache, server_keytab, client_keytab):
            try:
                absent.lstat()
            except FileNotFoundError:
                continue
            raise ValidationError("无凭据探针不应产生缓存或 keytab")
        stage = "final-directory"
        validate_directory(directory, lock)
        result = {"schema": 1, "kind": "mit-gss-abi", "passed": True, "platform": "win32", "architecture": "x64",
                "version": lock["version"], "archive_sha256": lock["sha256"],
                "lock_sha256": digest(json.dumps(lock, sort_keys=True).encode("utf-8")),
                "file_count": 10, **native, "credentials_acquired": False,
                "kerberos_authentication_verified": False, "sspi_verified": False}
    except Exception as error:
        if isinstance(error, ProbeFailure):
            raise
        raise ProbeFailure(stage) from error
    finally:
        try:
            workspace.close()
        finally:
            if root is not None:
                try:
                    cleanup_run(root)
                except Exception as error:
                    raise ProbeFailure("private-run-cleanup") from error
    result["private_run_removed"] = True
    return result


def main(*, deadline_seconds=60):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--parent", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    pending = {"schema": 1, "kind": "mit-gss-abi", "passed": False, "error": "native-abi-incomplete",
               "credentials_acquired": False, "kerberos_authentication_verified": False, "sspi_verified": False}
    try:
        # 即使后续原生代码崩溃，也不能留下上一轮的成功报告。
        publish_report(args.report, pending)
    except Exception:
        print(json.dumps({"schema": 1, "kind": "mit-gss-abi", "passed": False, "error": "report-write-failed"}))
        return 1
    # CLI 固定使用 60 秒；短期限只供无原生 API 的子进程回归注入。
    deadline = _ProcessDeadline(deadline_seconds)
    try:
        report = probe(args.directory, args.parent, deadline=deadline.deadline)
    except Exception as error:
        report = {"schema": 1, "kind": "mit-gss-abi", "passed": False, "error": "native-abi-failed",
                  "stage": error.stage if isinstance(error, ProbeFailure) else "validate-input",
                  "credentials_acquired": False, "kerberos_authentication_verified": False, "sspi_verified": False}
        if isinstance(error, ProbeFailure) and error.status is not None:
            report["native_status"] = error.status
    try:
        # 标准输出只是候选结果；刷新挂起时最终文件仍为已落盘的失败占位。
        print(json.dumps(report, ensure_ascii=False), flush=True)
    except Exception:
        deadline.complete()
        return 1
    try:
        # 唯一权威结果在候选输出之后提交，仍须与本进程退出 0 一起验收。
        publish_report(args.report, report)
    except Exception:
        deadline.complete()
        return 1
    deadline.complete()
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    utf8_streams()
    raise SystemExit(main())
