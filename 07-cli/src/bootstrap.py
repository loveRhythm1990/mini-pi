"""第 7 章导入第 5 章核心与第 6 章 Skill 模块，不再复制一份 Agent 循环。"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for name in ("05-extensions", "06-skills"):
    directory = ROOT / name / "src"
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))
