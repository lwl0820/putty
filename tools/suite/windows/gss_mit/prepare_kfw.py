"""下载固定官方 MSI，逐层纯提取十个已锁定文件；不运行安装器。"""
import argparse
import json
from pathlib import Path
import re
import sys
import time
import urllib.request

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[4]))
from tools.suite.windows.gss_mit.common import (
    LOCK_PATH, ValidationError, WorkspaceFiles, closed_environment, digest,
    project_path, read_regular, regular_file, run_bounded, utf8_streams,
)
from tools.suite.windows.gss_mit.pe import pe_summary

SYSTEM_IMPORTS = {"kernel32.dll", "user32.dll", "advapi32.dll", "ws2_32.dll",
                  "shell32.dll", "gdi32.dll", "dnsapi.dll", "secur32.dll"}
NAMES = {"gssapi64.dll", "krb5_64.dll", "comerr64.dll", "k5sprt64.dll", "wshelp64.dll",
         "kinit.exe", "klist.exe", "kvno.exe", "kdestroy.exe", "msvcr100.dll"}
GSS_EXPORTS = {"gss_indicate_mechs", "gss_release_oid_set"}
KRB5_EXPORTS = {"krb5_init_context", "krb5_free_context", "krb5_cc_default_name",
                "krb5_get_default_realm", "krb5_free_default_realm"}
OFFICIAL_URL = "https://web.mit.edu/Kerberos/dist/kfw/4.1/kfw-4.1-amd64.msi"


class PreparationFailure(ValidationError):
    """只公开固定阶段名，内部异常及其路径留在当前进程。"""

    def __init__(self, stage):
        self.stage = stage
        super().__init__("MIT 依赖准备失败")


def load_lock():
    lock = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    files = lock.get("files", [])
    if (lock.get("schema") != 1 or lock.get("vendor") != "MIT" or lock.get("url") != OFFICIAL_URL or
            len(files) != 10 or {record["name"] for record in files} != NAMES or
            len({record["member"].casefold() for record in files}) != 10):
        raise ValidationError("MIT 内容锁的固定范围无效")
    for record in [lock, lock["cab"], *files]:
        if (not isinstance(record["size"], int) or not 0 < record["size"] <= 12 * 1024 * 1024 or
                not re.fullmatch(r"[a-f0-9]{64}", record["sha256"])):
            raise ValidationError("MIT 内容锁的长度或摘要无效")
    return lock


def verify_content(content, record):
    if len(content) != record["size"] or digest(content) != record["sha256"]:
        raise ValidationError("文件长度或 SHA256 与 MIT 固定内容锁不符")


def validate_binary(content, record, names=NAMES):
    verify_content(content, record)
    result = pe_summary(content)
    if result["imports"] != sorted(record["imports"]) or result["delay_imports"] != sorted(record["delay_imports"]):
        raise ValidationError("PE 普通或延迟依赖与内容锁不符")
    if (set(result["imports"]) | set(result["delay_imports"])) - names - SYSTEM_IMPORTS:
        raise ValidationError("PE 包含未闭合的第三方依赖")
    required = GSS_EXPORTS if record["name"] == "gssapi64.dll" else (
        KRB5_EXPORTS if record["name"] == "krb5_64.dll" else set())
    if not required <= set(result["exports"]):
        raise ValidationError("MIT DLL 缺少无凭据探针所需导出")
    return result


def validate_directory(directory, lock=None):
    lock = load_lock() if lock is None else lock
    directory = project_path(directory, must_exist=True)
    entries = list(directory.iterdir())
    if len(entries) != 10 or {entry.name for entry in entries} != NAMES:
        raise ValidationError("便携目录必须恰有十个白名单文件，不能增加 DLL 或其它文件")
    for record in lock["files"]:
        validate_binary(read_regular(directory / record["name"], maximum=record["size"]), record)
    return lock


def parse_listing(content, *, count, expanded_size, selected):
    """只接受扁平、唯一、普通成员；7z 不向文件系统写入归档路径。"""
    try:
        text = content.decode("utf-8").replace("\r\n", "\n")
    except UnicodeDecodeError:
        raise ValidationError("归档目录清单不是 UTF-8") from None
    records = {}
    for block in text.strip("\n").split("\n\n"):
        record = {}
        for line in block.splitlines():
            key, separator, value = line.partition(" = ")
            if not separator or key in record:
                raise ValidationError("归档目录清单字段无效或重复")
            record[key] = value
        name = record.get("Path", "")
        if (not re.fullmatch(r"[A-Za-z0-9_.!\[\]-]{1,180}", name) or name in {".", ".."} or
                name.startswith("-") or name.casefold() in records):
            raise ValidationError("归档含不安全路径、大小写冲突或重复成员")
        if any("link" in key.casefold() or "reparse" in key.casefold() for key in record):
            raise ValidationError("归档不能包含链接成员")
        if record.get("Folder", "-") != "-" or any(char in record.get("Attributes", "") for char in "DL"):
            raise ValidationError("归档不能包含目录或链接属性")
        if not re.fullmatch(r"[0-9]{1,8}", record.get("Size", "")):
            raise ValidationError("归档成员长度无效")
        size = int(record["Size"])
        if size > 12 * 1024 * 1024:
            raise ValidationError("归档成员体积超限")
        records[name.casefold()] = size
    if len(records) != count or sum(records.values()) != expanded_size:
        raise ValidationError("归档成员数或展开体积与内容锁不符")
    for name, size in selected.items():
        if records.get(name.casefold()) != size:
            raise ValidationError("归档缺少固定成员或其长度不符")
    return len(records)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *unused):
        raise ValidationError("固定官方 MIT 下载不接受重定向")


def download(lock):
    # HTTPS 校验由标准库默认信任库完成；不继承代理环境或改用其它镜像。
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
    request = urllib.request.Request(lock["url"], headers={"User-Agent": "PuTTY-suite-fixture/1", "Accept-Encoding": "identity"})
    with opener.open(request, timeout=45) as response:
        if response.status != 200 or response.geturl() != OFFICIAL_URL or response.headers.get("Content-Encoding"):
            raise ValidationError("MIT 下载响应来源或传输编码不符")
        length = response.headers.get("Content-Length")
        if length is not None and length != str(lock["size"]):
            raise ValidationError("MIT 下载声明长度与内容锁不符")
        content, deadline = bytearray(), time.monotonic() + 45
        while len(content) <= lock["size"]:
            if time.monotonic() >= deadline:
                raise ValidationError("MIT 下载超出总时限")
            # read1 每轮只做一次底层读取；同时保留 socket 的独立超时。
            part = response.read1(min(65536, lock["size"] + 1 - len(content)))
            if not part:
                break
            content.extend(part)
        content = bytes(content)
    verify_content(content, lock)
    return content


def prepare(parent, sevenzip, archive=None):
    lock = load_lock()
    sevenzip = Path(sevenzip)
    if not sevenzip.is_absolute():
        raise ValidationError("解包器必须为已有的绝对路径")
    sevenzip = regular_file(sevenzip, maximum=64 * 1024 * 1024, project=False)
    if sys.platform == "win32" and sevenzip.name.casefold() != "7z.exe":
        raise ValidationError("Windows 解包器必须为已有 7z.exe")
    parent = project_path(parent)
    workspace = WorkspaceFiles()
    stage = "private-directories"
    try:
        workspace.directory(parent, existing=True)
        source = workspace.directory(parent / "source")
        work = workspace.directory(parent / "work")
        portable = workspace.directory(parent / "portable")
        output = workspace.directory(portable / "bin")
        environment = closed_environment(work)
        stage = "sevenzip-version"
        version_output = run_bounded([str(sevenzip), "i"], maximum=262144, cwd=work, environment=environment)
        version = re.search(rb"7-Zip ([0-9]{2}\.[0-9]{2})", version_output)
        if not version:
            raise ValidationError("解包器没有报告可识别的 7-Zip 版本")
        stage = "official-archive"
        content = download(lock) if archive is None else read_regular(archive, maximum=lock["size"])
        verify_content(content, lock)
        msi = workspace.write(source / "kfw-4.1-amd64.msi", content)

        def listing(path, count, expanded, members):
            data = run_bounded([str(sevenzip), "l", "-slt", "-ba", "-sccUTF-8", str(path)],
                               maximum=262144, cwd=work, environment=environment)
            return parse_listing(data, count=count, expanded_size=expanded, selected=members)

        def extract(path, member, record):
            data = run_bounded([str(sevenzip), "e", "-so", "-bd", "-y", "-spd", str(path), member],
                               maximum=record["size"], cwd=work, environment=environment)
            verify_content(data, record)
            return data

        stage = "msi-catalog"
        listing(msi, lock["archive_entries"], lock["archive_expanded_size"],
                {lock["cab"]["member"]: lock["cab"]["size"]})
        stage = "embedded-cabinet"
        cabinet = workspace.write(work / "payload.cab", extract(msi, lock["cab"]["member"], lock["cab"]))
        stage = "cabinet-catalog"
        listing(cabinet, lock["cab"]["entries"], lock["cab"]["expanded_size"],
                {record["member"]: record["size"] for record in lock["files"]})
        for record in lock["files"]:
            stage = "locked-member-" + record["name"]
            content = extract(cabinet, record["member"], record)
            validate_binary(content, record)
            workspace.write(output / record["name"], content)
        stage = "final-directory"
        validate_directory(output, lock)
        return {"schema": 1, "kind": "mit-gss-preparation", "passed": True, "version": lock["version"],
                "archive_sha256": lock["sha256"], "sevenzip_version": version.group(1).decode("ascii"),
                "file_count": 10, "dll_count": 6, "total_bytes": sum(item["size"] for item in lock["files"]),
                "pe_dependency_closure_verified": True, "installer_executed": False,
                "windows_loader_verified": False, "credentials_acquired": False,
                "kerberos_authentication_verified": False, "sspi_verified": False}
    except Exception as error:
        raise PreparationFailure(stage) from error
    finally:
        workspace.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--parent", type=Path, required=True)
    parser.add_argument("--sevenzip", type=Path, required=True)
    parser.add_argument("--archive", type=Path, help="可选的项目 .tmp 内离线 MSI；仍须完整核验内容锁")
    arguments = parser.parse_args()
    try:
        report = prepare(arguments.parent, arguments.sevenzip, arguments.archive)
    except Exception as error:
        # 失败不输出内部异常中的私有路径、HTTP 诊断或解包器完整日志。
        report = {"schema": 1, "kind": "mit-gss-preparation", "passed": False, "error": "preparation-failed",
                  "stage": error.stage if isinstance(error, PreparationFailure) else "validate-input"}
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    utf8_streams()
    raise SystemExit(main())
