"""第 6 章把第 5 章 src 加入 sys.path：Skill 系统复用它的 Agent、注册表与 read 工具。"""

import sys
from pathlib import Path

CORE = Path(__file__).resolve().parents[2] / "05-extensions" / "src"
if str(CORE) not in sys.path:
    sys.path.insert(0, str(CORE))
