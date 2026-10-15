#!/usr/bin/env python3
"""以项目私有目录和完整存储覆盖运行共享核心测试。"""
import argparse
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tools" / "suite"))
from isolation import IsolationEnvironment, IsolationError  # noqa: E402


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--parent", type=Path, required=True)
    parser.add_argument("--kind", choices=("lifecycle", "core-link", "agent-core"), default="lifecycle")
    arguments = parser.parse_args()
    if sys.platform != "linux":
        print("Windows 运行时测试缺少已验证的注册表及随机种子隔离，明确跳过")
        return 77
    try:
        with IsolationEnvironment(arguments.parent) as isolation:
            isolation.probe()
            binary = arguments.binary.resolve(strict=True)
            isolation.guard_program([str(binary)])
            environment = isolation.environment()
            environment["PUTTY_RUNTIME_ISOLATED_ROOT"] = str(isolation.root)
            command = [str(binary)]
            if arguments.kind == "core-link":
                command.append(str(isolation.root))
            result = subprocess.run(command, cwd=isolation.root,
                                    env=environment, timeout=25, umask=0o077)
            if result.returncode == 0:
                isolation.validate()
                if arguments.kind == "lifecycle":
                    seed = isolation.root / "putty" / "randomseed"
                    session = isolation.root / "putty" / "sessions" / "runtime-isolation"
                    if seed.stat().st_size == 0 or not session.is_file():
                        raise IsolationError("上游随机种子或会话没有写入预期私有目录")
                    print("上游真实存储落点：随机种子及会话写读均在项目私有目录")
                elif arguments.kind == "core-link":
                    session = isolation.root / "putty" / "sessions" / "core-link-smoke"
                    log = isolation.root / "logs" / "core-link.log"
                    if not session.is_file() or log.read_bytes() != "suite-log-中文\n".encode("utf-8"):
                        raise IsolationError("上游配置或日志没有写入预期私有目录")
                    print("完整客户端实际链接、原版配置往返与中文日志验证通过")
                else:
                    if (isolation.root / "putty" / "randomseed").stat().st_size:
                        raise IsolationError("代理核心不应初始化客户端随机种子")
                    print("代理核心独立随机符号边界、初始化与关闭验证通过")
            return result.returncode
    except (IsolationError, OSError, subprocess.SubprocessError) as error:
        print(f"运行时隔离失败：{error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
