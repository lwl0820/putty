#!/usr/bin/env python3
"""只执行无默认凭据访问的原生 SSPI 负例宿主，不声明 Kerberos 认证通过。"""
import argparse
import ctypes
import json
from pathlib import Path, PureWindowsPath
import re
import shutil
import stat
import subprocess
import sys
import uuid

from support import (TEMPORARY_ROOT, ValidationError, file_digest, native_environment,
                     project_path, write_report)

CHECKS = {"system-library", "eight-exports", "kerberos-package", "mic-invalid-context",
          "make-invalid-context", "verify-invalid-context", "init-invalid-credential",
          "failure-time-outputs", "no-default-credentials", "failure-cleanup", "repeated-handle-balance"}
REPORT_DIRECTORY = TEMPORARY_ROOT / "windows-ci/reports"
BINARY_NAME = "putty-sspi-native-negative-tests.exe"
INVALID_HANDLE = -2146893055  # SDK 的 SEC_E_INVALID_HANDLE，不使用网页误写的常量。
STATUS_FIELDS = {"package", "package_free", "mic", "make", "verify", "initialize",
                 "credentials_free", "context_delete"}
REPEATED_CALLS = {"package", "initialize", "query", "make", "verify", "free_buffer",
                  "free_credentials", "delete_context"}
ZERO_CALLS = {"acquire", "time_conversion", "allocations", "releases"}
REPORT_FIELDS = {"schema", "kind", "passed", "storage", "prng", "checks", "secur32_path",
                 "native_api", "kerberos_authentication_verified", "pointer_bits", "processor_architecture",
                 "warmup_iterations", "measured_iterations", "handles_before", "handles_after", "statuses", "calls"}

# 只公开宿主编译时固定的判断式，不回传任意 stderr 内容、路径或环境值。
DIAGNOSTIC_EXPRESSIONS = {
    "package_valid && statuses.package_free == SEC_E_OK",
    "!SecIsValidHandle(&context.cred_handle) && !SecIsValidHandle(&context.context)",
    "statuses.mic == SEC_E_INVALID_HANDLE && mic_empty", "!allocations && !releases",
    "statuses.make == SEC_E_INVALID_HANDLE && statuses.verify == SEC_E_INVALID_HANDLE",
    "result == SSH_GSS_FAILURE && statuses.initialize == SEC_E_INVALID_HANDLE",
    "expiry == GSS_NO_EXPIRATION && lifetime == 0",
    "conversion_calls == conversions_before && !unexpected_output && !unexpected_context",
    "output_free == SEC_E_OK",
    "statuses.context_delete == SEC_E_INVALID_HANDLE && statuses.credentials_free == SEC_E_INVALID_HANDLE",
    "count > 0 && count < MAX_PATH - 13",
    'swprintf(requested_path, MAX_PATH, L"%ls\\\\secur32.dll", system_directory) > 0',
    "module != NULL", "count > 0 && count < MAX_PATH",
    "CompareStringOrdinal(requested_path, -1, loaded_path, -1, TRUE) == CSTR_EQUAL",
    "WideCharToMultiByte(CP_UTF8, WC_ERR_INVALID_CHARS, loaded_path, -1, path_utf8, sizeof(path_utf8), NULL, NULL) > 0",
    "real_initialize != NULL", "real_query != NULL", "real_make != NULL", "real_verify != NULL",
    "real_free_buffer != NULL", "real_free_credentials != NULL", "real_delete_context != NULL",
    "real_package_info != NULL", 'GetProcAddress(module, "AcquireCredentialsHandleA") != NULL',
    "run_iteration()", "GetProcessHandleCount(GetCurrentProcess(), &handles_before)",
    "GetProcessHandleCount(GetCurrentProcess(), &handles_after)", "handles_after == handles_before",
    "initialize_calls == 136 && query_calls == 136 && make_calls == 136 && verify_calls == 136",
    "package_info_calls == 136 && free_buffer_calls == 136 && free_credentials_calls == 136 && delete_context_calls == 136",
    "!conversion_calls && !allocations && !releases", "FreeLibrary(module)",
}


def ordinary_file(value):
    path = project_path(value, must_exist=True)
    information = path.stat()
    if not stat.S_ISREG(information.st_mode) or information.st_nlink != 1:
        raise ValidationError("SSPI 宿主与链接图必须是普通无硬链接文件")
    return path


def audit_map(path):
    content = ordinary_file(path).read_text(encoding="utf-8", errors="strict")
    if re.search(r"\bsuite[-_]putty\b|(?<![\w])(?:sshrand|storage|noise|prng)(?:\.c)?\.obj\b", content, re.I):
        raise ValidationError("SSPI 负例宿主引入了完整核心、存储或 PRNG 对象")
    if re.search(r"\bQt[56][\w.-]*\.(?:lib|dll|obj)\b|\bQt[56]::", content, re.I):
        raise ValidationError("SSPI 负例宿主引入了 Qt")
    if re.search(r"\b_?(?:random_ref|random_read|random_reseed|noise_get_heavy|read_random_seed|write_random_seed|open_settings_[rw])\b", content):
        raise ValidationError("SSPI 负例宿主引入了持久化或随机源入口")
    public_symbols = set(re.findall(r"^\s*[0-9a-f]{4}:[0-9a-f]+\s+(\S+)", content, re.I | re.M))
    normalized = {symbol.removeprefix("_").split("@", 1)[0] for symbol in public_symbols}
    if any(re.search(r"(?:^|_)(?:imp_+)?AcquireCredentialsHandle[AW]?(?:@\d+)?$", symbol)
           for symbol in public_symbols):
        raise ValidationError("SSPI 负例宿主静态导入了默认凭据获取入口")
    if re.search(r"\b__imp_+AcquireCredentialsHandle[AW]?\b", content):
        raise ValidationError("SSPI 负例宿主静态导入了默认凭据获取入口")
    if not re.search(r"\bwindows_sspi_negative_test(?:\.c)?\.obj\b", content, re.I):
        raise ValidationError("链接图缺少专用原生 SSPI 宿主对象")
    for symbol in ("main", "safemalloc", "safefree", "p_InitializeSecurityContextA",
                   "p_QueryContextAttributesA", "p_MakeSignature", "p_VerifySignature"):
        if symbol not in normalized:
            raise ValidationError("链接图缺少必需的真实透传入口：" + symbol)
    for symbol in ("GetProcAddress", "LoadLibraryExW", "GetSystemDirectoryW", "GetModuleFileNameW",
                   "GetProcessHandleCount", "FreeLibrary"):
        if not re.search(r"\b(?:__imp_+)?" + symbol + r"(?:@\d+)?\b", content):
            raise ValidationError("链接图缺少系统库加载或资源检查入口：" + symbol)
    return {"native_host_only": True, "storage_prng_qt_absent": True, "acquire_static_import_absent": True}


def unique_object(pairs):
    result = {}
    for name, value in pairs:
        if name in result:
            raise ValidationError("SSPI 摘要含重复 JSON 字段")
        result[name] = value
    return result


def decode(output):
    if len(output) > 65536:
        raise ValidationError("SSPI 原生摘要超过容量上限")
    try:
        report = json.loads(output.decode("utf-8"), object_pairs_hook=unique_object)
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ValidationError("SSPI 宿主没有返回唯一 UTF-8 JSON 摘要") from error
    if (not isinstance(report, dict) or set(report) != REPORT_FIELDS or
            type(report["schema"]) is not int or report["schema"] != 1 or
            report["kind"] != "sspi-negative" or report["passed"] is not True or
            report["storage"] != "not-run" or report["prng"] != "not-run" or
            report["native_api"] is not True or report["kerberos_authentication_verified"] is not False):
        raise ValidationError("SSPI 原生负例报告契约不符，不能声明正向认证或存储通过")
    checks = report["checks"]
    if (not isinstance(checks, list) or any(not isinstance(check, str) for check in checks) or
            set(checks) != CHECKS or len(checks) != len(CHECKS)):
        raise ValidationError("SSPI 实际检查缺失或重复")
    path = report["secur32_path"]
    if (not isinstance(path, str) or len(path) > 1024 or any(ord(character) < 32 for character in path) or
            not PureWindowsPath(path).is_absolute() or ".." in PureWindowsPath(path).parts or
            PureWindowsPath(path).name.casefold() != "secur32.dll" or
            PureWindowsPath(path).parent.name.casefold() != "system32"):
        raise ValidationError("SSPI 摘要未给出系统目录中的 secur32 路径")
    for field, expected in (("pointer_bits", 64), ("processor_architecture", 9),
                            ("warmup_iterations", 8), ("measured_iterations", 128)):
        if type(report[field]) is not int or report[field] != expected:
            raise ValidationError("SSPI 原生架构或实际循环数不符：" + field)
    before, after = report["handles_before"], report["handles_after"]
    if type(before) is not int or type(after) is not int or not 0 < before <= 0xffffffff or before != after:
        raise ValidationError("SSPI 重复负例的真实进程句柄未守恒")
    statuses = report["statuses"]
    if not isinstance(statuses, dict) or set(statuses) != STATUS_FIELDS:
        raise ValidationError("SSPI 原生状态集合缺失或不符")
    for name, status in statuses.items():
        expected = 0 if name in {"package", "package_free"} else INVALID_HANDLE
        if type(status) is not int or status != expected:
            raise ValidationError("SSPI 实际 SDK 状态码不符：" + name)
    calls = report["calls"]
    if not isinstance(calls, dict) or set(calls) != REPEATED_CALLS | ZERO_CALLS:
        raise ValidationError("SSPI 实际调用计数集合缺失或不符")
    for name, count in calls.items():
        if type(count) is not int or count != (136 if name in REPEATED_CALLS else 0):
            raise ValidationError("SSPI 实际调用或资源计数不符：" + name)
    # 字段集合严格固定，不能把宿主额外返回的凭据、令牌或错误输出写入报告。
    return report


def safe_diagnostics(stderr):
    messages = []
    for line in stderr[:8192].decode("utf-8", errors="replace").splitlines():
        match = re.fullmatch(r"(原生 SSPI (?:负例|宿主)失败，第 [1-9][0-9]{0,3} 行：)(.+)", line)
        if match and match[2] in DIAGNOSTIC_EXPRESSIONS:
            messages.append(match[1] + match[2])
    return "；".join(messages)[:1536]


def system_secur32():
    kernel = ctypes.WinDLL("kernel32.dll", use_last_error=True)
    function = kernel.GetSystemDirectoryW
    function.argtypes = [ctypes.POINTER(ctypes.c_wchar), ctypes.c_uint]
    function.restype = ctypes.c_uint
    buffer = ctypes.create_unicode_buffer(32768)
    length = function(buffer, len(buffer))
    if not 0 < length < len(buffer):
        raise ValidationError("运行器无法核对真实 Windows 系统目录")
    return PureWindowsPath(buffer.value) / "secur32.dll"


def run(binary):
    if sys.platform != "win32":
        raise ValidationError("SSPI 原生负例必须在原生 Windows 执行，CI 标记不能开放其它系统")
    binary = ordinary_file(binary)
    if binary.name.casefold() != BINARY_NAME:
        raise ValidationError("只允许专用 SSPI 原生负例宿主，禁止原版 CLI 与产品程序")
    link = audit_map(Path(str(binary) + ".map"))
    expected_library = system_secur32()
    parent = project_path(TEMPORARY_ROOT / "windows-sspi-negative")
    parent.mkdir(parents=True, exist_ok=True)
    root = project_path(parent / ("run-" + uuid.uuid4().hex))
    root.mkdir()
    try:
        environment = native_environment(root)
        for name in tuple(environment):
            if name.upper().startswith("KRB5"):
                environment.pop(name)
        result = subprocess.run([str(binary)], cwd=root, env=environment, capture_output=True, timeout=20)
        if result.returncode:
            diagnostic = safe_diagnostics(result.stderr)
            raise ValidationError(f"原生 SSPI 负例宿主失败，退出码 {result.returncode}" +
                                  ("：" + diagnostic if diagnostic else "（无可公开的固定诊断）"))
        report = decode(result.stdout)
        if PureWindowsPath(report["secur32_path"]) != expected_library:
            raise ValidationError("SSPI 宿主加载路径与运行器实际系统目录不符")
        if any(root.iterdir()):
            raise ValidationError("无存储 SSPI 宿主意外创建了运行产物")
        report.update({"binary_sha256": file_digest(binary), "platform": sys.platform,
                       "link_audit": link, "negative_api_calls": 136 * 6,
                       "unverified": ["kerberos-authentication", "valid-context-signatures",
                                      "terminal-long-sspi-call", "windows-10-desktop", "windows-11-desktop"]})
        return report
    finally:
        project_path(root, must_exist=True)
        shutil.rmtree(root)


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    arguments = parser.parse_args()
    try:
        report = run(arguments.binary)
        write_report(REPORT_DIRECTORY, "sspi-negative.json", report)
        print(f"Windows SSPI：{len(CHECKS)} 项原生负例通过；正向 Kerberos 认证与长调用尚未验证")
        return 0
    except (ValidationError, OSError, UnicodeError, subprocess.SubprocessError) as error:
        message = str(error)[:2048]
        write_report(REPORT_DIRECTORY, "sspi-negative.json", {
            "schema": 1, "kind": "sspi-negative", "passed": False,
            "storage": "not-run", "prng": "not-run", "kerberos_authentication_verified": False,
            "error": message})
        print("Windows SSPI 负例失败：" + message, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
