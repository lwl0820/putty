"""独立准备子进程的固定补丁入口。"""
from pathlib import Path
import sys

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[4]))
from tools.suite.windows.gss_ssh.dependencies import verify_sources

if __name__ == "__main__":
    verify_sources(apply=True)
