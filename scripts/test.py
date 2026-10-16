#!/usr/bin/env python3
"""各章有独立的 main.py，在独立进程中运行测试，避免同名模块冲突。"""

import subprocess
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
chapters = ["01-loop", "02-providers", "03-tools", "04-sessions", "05-extensions", "06-skills", "07-cli"]
for chapter in chapters:
    print(f"\n{chapter}", flush=True)
    result = subprocess.run([sys.executable, "-m", "pytest", f"{chapter}/src", "-q"], cwd=root)
    if result.returncode:
        raise SystemExit(result.returncode)
