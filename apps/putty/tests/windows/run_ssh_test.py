#!/usr/bin/env python3
"""Windows 真实 PPK／SSH 终端纵切；只运行受控宿主，不开放产品存储。"""
import argparse
import base64
import json
import math
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import sys
import time
import uuid

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools/suite/windows"))
sys.path.insert(0, str(ROOT / "tools/suite/windows/storage"))
from support import (TEMPORARY_ROOT, ValidationError, file_digest, native_environment,
                     project_path, write_report)
from run_storage_test import audit_storage_source
from tools.suite.windows.private_data import PrivateData


def safe_diagnostics(output, *, keygen=False):
    """只转录固定宿主阶段及数字故障定位；丢弃任意日志、路径和命令。"""
    if not isinstance(output, bytes):
        return []
    fixed = {"测试密钥生成或参数校准失败"} if keygen else {
        "必须通过项目私有隔离运行器启动终端测试", "终端纵切失败：检查未通过",
        *("终端测试阶段：" + stage for stage in (
            "私有 hive 创建", "Qt 终端入口", "Qt 偏好隔离", "Qt 初始化完成", "主机密钥拒绝",
            "真实密钥负载与呈现", "密钥计算中关闭", "口令提示取消", "有序输入与背压",
            "终端摘要完成", "私有 hive 核验与清理", "全部完成"))}
    selected = []
    for raw in output[:16384].splitlines()[:128]:
        try:
            line = raw.decode("utf-8")
        except UnicodeError:
            continue
        accepted = line if line in fixed else None
        if not keygen:
            if re.fullmatch(r"终端测试检查失败：行=[1-9][0-9]{0,3}", line) or re.fullmatch(
                    r"终端 hive (?:初始化失败|清理或实际存储检查失败)，代码 [0-9]{1,10}", line):
                accepted = line
            exception = re.fullmatch(
                r"终端测试未处理异常：代码=(0x[0-9a-fA-F]{8})，模块=[^，\r\n]{1,100}，偏移=(0x[0-9a-fA-F]{1,16})", line)
            if exception:
                accepted = f"终端测试未处理异常：代码={exception[1]}，偏移={exception[2]}"
        if accepted is not None and accepted not in selected:
            selected.append(accepted)
    return selected[-12:]


def _run_child(arguments, *, cwd, environment, timeout, keygen=False):
    label = "独立 PPK 校准" if keygen else "受控终端纵切"
    try:
        result = subprocess.run(arguments, cwd=cwd, env=environment, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired as error:
        diagnosis = safe_diagnostics(error.stderr, keygen=keygen)
        suffix = "；" + "；".join(diagnosis) if diagnosis else ""
        raise ValidationError(f"{label}超出 {timeout} 秒上限{suffix}") from None
    if result.returncode or len(result.stdout) > 8192:
        diagnosis = safe_diagnostics(result.stderr, keygen=keygen)
        suffix = "；" + "；".join(diagnosis) if diagnosis else ""
        raise ValidationError(f"{label}失败，退出码 {result.returncode}{suffix}")
    return result


def audit_map(path, *, keygen=False):
    path = project_path(path, must_exist=True)
    if not path.is_file() or path.stat().st_nlink != 1:
        raise ValidationError("链接图必须是普通无硬链接文件")
    content = path.read_text(encoding="utf-8", errors="replace")
    if re.search(r"(?<![\w])storage(?:\.c)?\.obj\b", content, re.I):
        raise ValidationError("终端纵切不得链接生产 storage 对象")
    if keygen:
        required = ("pg_core", "pg_platform", "sshrand")
        symbols = ("pg_generate", "pg_save", "random_setup_special", "BCryptGenRandom")
        if re.search(r"(?<![\w])(?:noise|settings|guarded_terminal)(?:\.c)?\.obj\b", content, re.I):
            raise ValidationError("独立密钥工具混入了完整客户端或平台存储")
    else:
        if re.search(r"(?<![\w])pt_terminal(?:\.c)?\.obj\b", content, re.I):
            raise ValidationError("受控终端抽入了未保护的生产模块对象")
        required = ("guarded_terminal", "guarded_module", "hive_bootstrap", "storage_variant",
                    "seed_guard", "settings", "noise", "sshrand", "prng")
        symbols = ("pt_terminal_real_module", "pt_terminal_module", "pt_publish", "random_ref", "random_unref",
                   "random_clear", "prng_free", "read_random_seed", "write_random_seed", "noise_get_heavy",
                   "suite_seed_CreateFileA", "suite_hive_release_owner", "suite_hive_claim_owner")
    for name in required:
        if not re.search(r"\b" + name + r"(?:\.c)?\.obj\b", content, re.I):
            raise ValidationError("链接图缺少必需的受控或正式核心对象：" + name)
    for symbol in symbols:
        if not re.search(r"\b(?:__imp_)?_?" + symbol + r"\b", content):
            raise ValidationError("链接图缺少实际入口：" + symbol)
    return {"guarded_variant_only": not keygen, "independent_keygen": keygen}


def _json(output, kind):
    try:
        value = json.loads(output.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ValidationError("宿主没有生成唯一 UTF-8 JSON 摘要") from error
    if not isinstance(value, dict) or value.get("schema") != 1 or value.get("kind") != kind or value.get("passed") is not True:
        raise ValidationError("宿主摘要类型或通过状态不符")
    return value


def decode_keygen(output):
    result = _json(output, "terminal-keygen")
    if (result.get("argon2_target_ms") != 1500 or result.get("memory_kib") != 8192 or
            result.get("parallelism") != 1 or result.get("build_config") not in
            {"Debug", "Release", "RelWithDebInfo", "MinSizeRel"} or result.get("compiler") != "MSVC"):
        raise ValidationError("密钥校准工具的目标、配置或 Argon2 参数不符")
    public = result.get("public_text")
    if not isinstance(public, str) or not re.fullmatch(r"ssh-ed25519 [A-Za-z0-9+/=]{40,200} suite-terminal", public):
        raise ValidationError("密钥工具没有返回唯一预期公开身份")
    try:
        blob = base64.b64decode(public.split()[1], validate=True)
    except ValueError as error:
        raise ValidationError("公钥编码不规范") from error
    return result, blob


def ppk_parameters(path, public_blob):
    content = path.read_bytes()
    if len(content) > 16384:
        raise ValidationError("PPK 超出固定测试密钥限额")
    header = content.decode("ascii").split("Private-Lines:", 1)[0]
    if not header.startswith("PuTTY-User-Key-File-3: ssh-ed25519\n") and not header.startswith(
            "PuTTY-User-Key-File-3: ssh-ed25519\r\n"):
        raise ValidationError("测试密钥不是 PPKv3 Ed25519")
    lines = header.splitlines()
    expected = {"Encryption": "aes256-cbc", "Key-Derivation": "Argon2id", "Argon2-Memory": "8192",
                "Argon2-Parallelism": "1"}
    for key, value in expected.items():
        if lines.count(f"{key}: {value}") != 1:
            raise ValidationError("实际 PPK 加密／Argon2 参数不符")
    passes = [line.removeprefix("Argon2-Passes: ") for line in lines if line.startswith("Argon2-Passes: ")]
    if len(passes) != 1 or not passes[0].isdigit() or not 1 <= int(passes[0]) <= 1_000_000:
        raise ValidationError("实际 PPK 没有有效的校准轮数")
    try:
        index = lines.index("Public-Lines: 2")
        blob = base64.b64decode("".join(lines[index + 1:index + 3]), validate=True)
    except (ValueError, IndexError) as error:
        raise ValidationError("PPK 公钥头不规范") from error
    if blob != public_blob:
        raise ValidationError("PPK 与对端准许的公开身份不一致")
    return {**expected, "Argon2-Passes": int(passes[0]), "target_ms": 1500}


def _number(value, minimum, maximum):
    return type(value) in (int, float) and math.isfinite(value) and minimum <= value <= maximum


def validate_terminal(report, keygen):
    if (not isinstance(report, dict) or report.get("passed") is not True or
            report.get("windows_guarded_ppk") is not True or report.get("non_bmp_screen_verified") is not True or
            report.get("gss_verified") is not False or report.get("windows_native_verified") is not False or
            report.get("host_key_rejection") is not True or report.get("prompt_cancellation") is not True or
            report.get("client_build_config") != keygen["build_config"] or
            report.get("client_compiler") != keygen["compiler"] or report.get("platform") != "offscreen" or
            report.get("qt") != "6.8.3"):
        raise ValidationError("终端没有完成同目标／同配置的受控 PPK 纵切，或越权标记原生桌面／GSS")
    workload = report.get("workload", {})
    close = report.get("authentication_close", {})
    ordered = report.get("ordered_input", {})
    if (not isinstance(workload, dict) or not isinstance(close, dict) or not isinstance(ordered, dict) or
            not _number(workload.get("duration_ms"), 10000, 60000) or
            not _number(workload.get("samples"), 180, 2000) or
            not _number(workload.get("maximum_heartbeat_ms"), 1, 250) or
            not _number(workload.get("real_auth_work_ms"), 500, 15000) or
            workload.get("frame_generation") != 2 or not _number(workload.get("metric_queries"), 1, 100000) or
            not isinstance(workload.get("font_families"), list) or
            any(not isinstance(family, str) or not family or len(family) > 256 for family in workload["font_families"]) or
            len(set(workload["font_families"])) < 2 or
            not _number(workload.get("pixel_width"), 1, 16384) or
            not _number(workload.get("pixel_height"), 1, 16384) or
            workload["pixel_width"] * workload["pixel_height"] > 4 * 1024 * 1024 or
            not _number(workload.get("event_high_water"), 1, 4) or
            not _number(workload.get("command_high_water"), 1, 16) or
            not _number(workload.get("input_high_water"), 1, 65536) or
            not _number(close.get("close_call_ms"), 0, 24) or
            not _number(close.get("safe_cleanup_ms"), 300, 15000) or
            not _number(close.get("heartbeats_while_closing"), 4, 2000) or
            ordered.get("accepted_wire_bytes") != 642 or ordered.get("command_high_water") != 16 or
            ordered.get("capacity_refusal_observed") is not True or
            ordered.get("order_verified_by_remote") is not True or
            not _number(ordered.get("input_backpressure_retries"), 1, 100000)):
        raise ValidationError("终端实际心跳、KDF、帧、异步关闭或背压证据不满足阈值")
    return report


def validate_hive(output):
    report = _json(output, "terminal-hive")
    if (any(report.get(key) is not True for key in ("continuous_mapping", "cleanup", "host_key_in_private_hive")) or
            report.get("cores_created") != 5 or report.get("cores_destroyed") != 5 or
            not _number(report.get("seed_reads"), 5, 100) or
            not _number(report.get("seed_writes"), 10, 100) or
            not _number(report.get("registry_accesses"), 1, 100000)):
        raise ValidationError("终端核心、HKCU 或随机种子未完整受控清理")
    return report


def validate_peer(report):
    if (not isinstance(report, dict) or report.get("paramiko") != "5.0.0" or report.get("bind_host") != "127.0.0.1" or
            report.get("closed") is not True or report.get("live_threads") != 0 or
            report.get("active_connections") != 0 or report.get("connection_errors") != 0 or
            report.get("accepted_connections") != 5 or report.get("authenticated_connections") != 2 or
            report.get("completed_commands") != 2 or report.get("capacity_refusals") != 0):
        raise ValidationError("真实 SSH 对端的认证、完成计数或资源回收不符")
    commands = report.get("command_results")
    if not isinstance(commands, list) or len(commands) != 2 or any(not isinstance(item, dict) for item in commands):
        raise ValidationError("SSH 对端缺少两个实际完成命令")
    by_name = {item.get("command"): item for item in commands}
    if len(by_name) != 2 or set(by_name) != {"suite-terminal-workload-v1", "suite-terminal-ordered-input-v1"}:
        raise ValidationError("SSH 对端命令重复或越界")
    for name, item in by_name.items():
        expected = 22 if name == "suite-terminal-workload-v1" else 642
        if item.get("exit_status") != 0 or item.get("received_bytes") != expected:
            raise ValidationError("对端未核对实际 SSH 输入和成功退出")
        if (not _number(item.get("output_bytes"), 1, 512 * 1024) or
                not _number(item.get("duration_seconds"), 0, 20) or
                not _number(item.get("samples"), 0, 10000)):
            raise ValidationError("对端缺少有界负载计数或耗时")
    load = by_name["suite-terminal-workload-v1"]
    if not _number(load.get("duration_seconds"), 10.2, 20) or not _number(load.get("samples"), 500, 10000):
        raise ValidationError("SSH 持续 Unicode 输出没有达到真实时间／负载要求")
    return report


def run(binary, keygen, parent):
    if sys.platform != "win32":
        raise ValidationError("此纵切只能在原生 Windows 执行，不能以跳过冒充通过")
    binary = project_path(binary, must_exist=True)
    keygen = project_path(keygen, must_exist=True)
    if (binary.name.casefold() != "putty-terminal-windows-tests.exe" or not binary.is_file() or
            binary.stat().st_nlink != 1):
        raise ValidationError("只允许运行私有存储保护的终端测试宿主")
    if (keygen.name.casefold() != "suite-terminal-keygen.exe" or not keygen.is_file() or
            keygen.stat().st_nlink != 1):
        raise ValidationError("只允许独立 pg-core 校准工具")
    parent = project_path(parent)
    if parent.parent != TEMPORARY_ROOT:
        raise ValidationError("测试父目录必须是项目 .tmp 的直接子目录")
    source_sha256 = audit_storage_source()
    link = audit_map(Path(str(binary) + ".map"))
    keygen_link = audit_map(Path(str(keygen) + ".map"), keygen=True)
    # 依赖只在原生平台与所有拒绝门通过后加载；产品不依赖此测试 SSH 实现。
    from tools.suite.windows.ssh_peer import RestrictedSSHPeer, WORKLOAD_COMMAND
    private = PrivateData()
    data = None
    completed = False
    try:
        private.directory(parent, allow_existing=True)
        before = {path.name for path in parent.iterdir()}
        data = private.directory(parent / ("data-" + uuid.uuid4().hex))
        for name in ("logs", "runtime", "config"):
            private.directory(data / name)
        passphrase = private.write(data / "passphrase", secrets.token_hex(32).encode("ascii") + b"\n")
        key = data / "终端 密钥.ppk"
        environment = native_environment(data / "runtime")
        environment.pop("PUTTY_SUITE_NATIVE_NO_STORAGE", None)
        environment.update(QT_QPA_PLATFORM="offscreen", PUTTY_TERMINAL_ISOLATED_ROOT=str(data),
                           SUITE_TERMINAL_HIVE_PARENT=str(parent))
        generated = _run_child([str(keygen), str(key), str(passphrase)], cwd=data,
                               environment=environment, timeout=30, keygen=True)
        keygen_report, public_blob = decode_keygen(generated.stdout)
        private.check_file(key)
        ppk = ppk_parameters(key, public_blob)
        with RestrictedSSHPeer(public_blob) as peer:
            result = _run_child([str(binary), "--port", str(peer.port), "--key", str(key),
                "--remote-command", WORKLOAD_COMMAND.decode("ascii"), "--passphrase-file", str(passphrase)],
                cwd=data, environment=environment, timeout=70)
            hive = validate_hive(result.stdout)
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                state = peer.statistics()
                if state["active_connections"] == 0 and state["completed_commands"] == 2:
                    break
                time.sleep(0.02)
        peer_report = validate_peer(peer.statistics())
        produced = project_path(data / "logs/terminal-result.json", must_exist=True)
        screenshot = project_path(data / "logs/terminal-final.png", must_exist=True)
        private.check_file(produced, harden=True)
        private.check_file(screenshot, harden=True)
        if produced.stat().st_size > 16384 or screenshot.stat().st_size > 16 * 1024 * 1024:
            raise ValidationError("终端结果超出固定摘要／截图限额")
        terminal = validate_terminal(json.loads(produced.read_text(encoding="utf-8")), keygen_report)
        if not screenshot.read_bytes().startswith(b"\x89PNG\r\n\x1a\n"):
            raise ValidationError("终端没有保存实际离屏图像")
        expected_entries = before | {data.name}
        if {path.name for path in parent.iterdir()} != expected_entries:
            raise ValidationError("所有核心关闭后仍有新的 hive 运行根残留")
        if {path.name for path in data.iterdir()} != {"logs", "runtime", "config", "passphrase", key.name}:
            raise ValidationError("私有数据根出现未登记产物")
        if {path.name for path in (data / "logs").iterdir()} != {"terminal-result.json", "terminal-final.png"}:
            raise ValidationError("终端日志目录出现未登记产物")
        if any((data / "runtime").iterdir()) or any((data / "config").iterdir()):
            raise ValidationError("离屏测试产生了未登记的临时文件或偏好")
        private.check_file(passphrase)
        # 汇总只含计数、版本、阈值、摘要；不复制口令、PPK、截图或进程原始日志。
        terminal_summary = {name: terminal[name] for name in (
            "passed", "qt", "platform",
            "host_key_rejection", "prompt_cancellation", "windows_guarded_ppk", "non_bmp_screen_verified",
            "client_build_config", "client_compiler", "gss_verified", "windows_native_verified")}
        for group, fields in {
            "workload": ("duration_ms", "samples", "maximum_heartbeat_ms", "real_auth_work_ms", "frame_generation",
                         "pixel_width", "pixel_height", "metric_queries", "font_families", "event_high_water",
                         "command_high_water", "input_high_water"),
            "authentication_close": ("close_call_ms", "safe_cleanup_ms", "heartbeats_while_closing"),
            "ordered_input": ("accepted_wire_bytes", "command_high_water", "input_backpressure_retries",
                              "capacity_refusal_observed", "order_verified_by_remote"),
        }.items():
            terminal_summary[group] = {name: terminal[group][name] for name in fields}
        hive = {name: hive[name] for name in ("schema", "kind", "passed", "continuous_mapping", "cleanup",
            "cores_created", "cores_destroyed", "seed_reads", "seed_writes", "registry_accesses", "host_key_in_private_hive")}
        peer_summary = {name: peer_report[name] for name in ("paramiko", "bind_host", "closed", "live_threads",
            "active_connections", "connection_errors", "accepted_connections", "authenticated_connections",
            "completed_commands", "capacity_refusals")}
        peer_summary["command_results"] = [{name: item[name] for name in (
            "command", "exit_status", "received_bytes", "output_bytes", "samples", "duration_seconds")}
            for item in peer_report["command_results"]]
        report = {"schema": 1, "kind": "windows-terminal-ssh", "passed": True,
            "platform": sys.platform, "native_ppk_verified": True, "native_sspi_verified": False,
            "desktop_verified": False, "terminal": terminal_summary, "hive": hive, "peer": peer_summary,
            "ppk_parameters": ppk, "keygen": {name: keygen_report[name] for name in (
                "schema", "kind", "passed", "argon2_target_ms", "memory_kib", "parallelism", "build_config", "compiler")},
            "binary_sha256": file_digest(binary), "keygen_sha256": file_digest(keygen),
            "storage_source_sha256": source_sha256, "link_audit": link, "keygen_link_audit": keygen_link,
            "unverified": ["native-sspi-authentication", "windows-10-desktop", "windows-11-desktop",
                           "ime-clipboard-mouse", "full-terminal-configuration", "production-seed-fallback",
                           "upstream-cli-storage"]}
        completed = True
        return report
    finally:
        private.close()
        if data is not None and completed:
            project_path(data, must_exist=True)
            shutil.rmtree(data)
            if data.exists():
                raise ValidationError("私有数据清理失败")


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", required=True, type=Path)
    parser.add_argument("--keygen", required=True, type=Path)
    parser.add_argument("--parent", required=True, type=Path)
    parser.add_argument("--report-dir", type=Path, default=TEMPORARY_ROOT / "windows-ci/reports")
    arguments = parser.parse_args()
    try:
        report = run(arguments.binary, arguments.keygen, arguments.parent)
        report["private_data_cleanup"] = True
        write_report(arguments.report_dir, "terminal-ssh.json", report)
        print("Windows 受控终端：真实 SSH／PPK、十秒心跳、Unicode／DPI、背压及关闭通过")
        return 0
    except (ValidationError, OSError, subprocess.SubprocessError, ValueError, RuntimeError) as error:
        write_report(arguments.report_dir, "terminal-ssh.json", {"schema": 1, "kind": "windows-terminal-ssh",
                     "passed": False, "error": str(error), "native_sspi_verified": False})
        print(f"Windows 终端纵切失败：{error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
