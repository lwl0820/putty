"""Windows x64 无凭据探针：内容锁、真实 DLL 路径、ABI 与私有配置选择。"""
import argparse
import json
import os
from pathlib import Path
import sys
import threading
import time
import uuid

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[4]))
from tools.suite.windows.gss_mit.common import (
    ValidationError, WorkspaceFiles, closed_environment, digest, project_path,
    publish_report, utf8_streams,
)
from tools.suite.windows.gss_mit.mit_gss import MitGss
from tools.suite.windows.gss_mit.prepare_kfw import validate_directory


class ProbeFailure(ValidationError):
    """只向报告公开固定阶段名。"""

    def __init__(self, stage):
        self.stage = stage
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


def probe(directory, parent):
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
        replay = workspace.directory(root / "replay")
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
        configuration = workspace.write(root / "krb5.ini", config.encode("ascii"))
        environment = closed_environment(root)
        environment.update({"KRB5_CONFIG": configuration.as_posix(),
                            "KRB5_KTNAME": "FILE:" + server_keytab.as_posix(),
                            "KRB5_CLIENT_KTNAME": "FILE:" + client_keytab.as_posix(),
                            "KRB5RCACHEDIR": replay.as_posix(), "KRB5RCACHETYPE": "dfl"})
        # 本 CLI 是独立进程；在首次加载 DLL 前封闭环境，不启动并行认证线程。
        stage = "closed-environment"
        os.environ.clear()
        os.environ.update(environment)
        stage = "native-dll-load"
        api = MitGss(directory)
        stage = "mechanism-enumeration"
        mechanism_count = api.mechanism_count()
        # 不设 KRB5CCNAME，真实默认名必须来自本次配置的 default_ccache_name。
        # 此处只读名称，不 resolve、open、遍历或申请缓存中的任何凭据。
        stage = "configuration-defaults"
        api.verify_defaults(realm, cache_name)
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
                "file_count": 10, "loaded_dll_count": len(api.loaded), "mechanism_count": mechanism_count,
                "windows_loader_verified": True, "abi_layout_verified": True,
                "private_configuration_verified": True, "inherited_kerberos_environment_removed": True,
                "configuration_file_cache_selected": True, "credentials_acquired": False,
                "kerberos_authentication_verified": False, "sspi_verified": False}
    except Exception as error:
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
        report = probe(args.directory, args.parent)
    except Exception as error:
        report = {"schema": 1, "kind": "mit-gss-abi", "passed": False, "error": "native-abi-failed",
                  "stage": error.stage if isinstance(error, ProbeFailure) else "validate-input",
                  "credentials_acquired": False, "kerberos_authentication_verified": False, "sspi_verified": False}
    try:
        publish_report(args.report, report)
    except Exception:
        print(json.dumps({"schema": 1, "kind": "mit-gss-abi", "passed": False, "error": "report-write-failed"}),
              flush=True)
        deadline.complete()
        return 1
    print(json.dumps(report, ensure_ascii=False), flush=True)
    deadline.complete()
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    utf8_streams()
    raise SystemExit(main())
