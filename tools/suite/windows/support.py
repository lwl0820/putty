"""Windows 无存储测试的路径、子进程和报告契约；不开放个人注册表。"""
import hashlib
import json
import os
from pathlib import Path
import re
import stat

ROOT = Path(__file__).resolve().parents[3]
TEMPORARY_ROOT = ROOT / ".tmp"
CHECKS = {
    "handles": {"manual-reset", "auto-reset", "queued-same-value", "self-cancel"},
    "events": {"qt-manual-auto", "hwnd-tcp", "fd-oob", "close-reopen", "pending-close", "owner-thread"},
}
BINARIES = {
    "handles": "suite-windows-handle-tests.exe",
    "events": "suite-windows-runtime-tests.exe",
}


class ValidationError(RuntimeError):
    """证据、路径或执行条件不满足要求，必须失败而非跳过。"""


def project_path(value, *, temporary_root=TEMPORARY_ROOT, must_exist=False):
    path = Path(value)
    if ".." in path.parts:
        raise ValidationError("路径不能包含上级跳转")
    path = Path(os.path.abspath(path))
    root = Path(os.path.abspath(temporary_root))
    if path == root or not path.is_relative_to(root):
        raise ValidationError("产物路径必须在项目 .tmp 的子目录内")
    # 从卷根逐级检查，拒绝 junction、其它 reparse point 和符号链接。
    for component in (*reversed(path.parents), path):
        try:
            info = component.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ValidationError("产物路径不能经过链接或 Windows reparse point")
    if must_exist and not path.exists():
        raise ValidationError("预期产物不存在")
    return path


def file_digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def native_environment(root, base_environment=None):
    environment = dict(os.environ if base_environment is None else base_environment)
    for key in tuple(environment):
        if key.upper() in {
            "SSH_AUTH_SOCK", "SSH_AGENT_PID", "SSH_ASKPASS", "SSH_ASKPASS_REQUIRE",
            "GPG_AGENT_INFO", "GPG_TTY", "PUTTY_RUNTIME_ISOLATED_ROOT",
        } or key.upper().startswith("PUTTY_SUITE_"):
            environment.pop(key)
    for name in ("TMP", "TEMP", "TMPDIR"):
        environment[name] = str(root)
    environment["PUTTY_SUITE_NATIVE_NO_STORAGE"] = "1"
    # 不修改 HOME、USERPROFILE、APPDATA；环境值不是 HKCU 或种子隔离证据。
    return environment


def decode_native_report(output, kind):
    try:
        report = json.loads(output.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValidationError("原生宿主没有返回唯一合法 JSON 报告") from error
    if (not isinstance(report, dict) or report.get("schema") != 1 or
            report.get("kind") != kind or report.get("passed") is not True or
            report.get("storage") != "not-run" or not isinstance(report.get("checks"), list)):
        raise ValidationError("原生宿主报告契约不符")
    checks = report["checks"]
    if any(not isinstance(item, str) for item in checks) or set(checks) != CHECKS[kind] or len(checks) != len(CHECKS[kind]):
        raise ValidationError("原生宿主缺少必需的实际检查或重复检查")
    return report


def audit_link_map(path):
    path = project_path(path, must_exist=True)
    content = path.read_text(encoding="utf-8", errors="replace")
    # MSVC map 中对象名可能带库前缀；边界匹配避免 no_storage.c.obj 的误报。
    forbidden = re.findall(r"(?<![\w])(?:sshrand|storage|noise)(?:\.c)?\.obj\b", content, re.IGNORECASE)
    if forbidden or re.search(r"\b_?(?:random_ref|random_read|random_reseed)\b", content):
        raise ValidationError("无存储宿主链接了真实 PRNG、持久化或重噪声对象")
    for required in ("read_random_seed", "write_random_seed", "open_settings_w", "open_settings_r"):
        if not re.search(r"\b_?" + required + r"\b", content):
            raise ValidationError("链接图缺少持久化致命拒绝入口")
    return {"prng_objects_absent": True, "storage_tripwires_linked": True}


def write_report(directory, name, report):
    directory = project_path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    destination = project_path(directory / name)
    if destination.exists() and (not destination.is_file() or destination.stat().st_nlink != 1):
        raise ValidationError("报告目标必须是普通无硬链接文件")
    destination.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
