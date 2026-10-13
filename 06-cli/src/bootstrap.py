"""第 6 章只导入第 5 章核心，不再复制一份 Agent 循环。"""

import sys
from pathlib import Path

CORE = Path(__file__).resolve().parents[2] / "05-extensions" / "src"
if str(CORE) not in sys.path:
    sys.path.insert(0, str(CORE))
