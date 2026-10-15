"""由父探针以封闭环境、隔离 Python 和独立硬期限启动的内部入口。"""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))
from tools.suite.windows.gss_mit.probe import native_main


if __name__ == "__main__":
    raise SystemExit(native_main())
