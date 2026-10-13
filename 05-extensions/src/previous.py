"""把已有章节当作包加载，避免多个 main.py / tools.py 互相覆盖。"""

import importlib
import sys
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[2]


def chapter_module(chapter: str, module: str):
    package_name = "_mini_pi_" + chapter.replace("-", "_")
    if package_name not in sys.modules:
        package = ModuleType(package_name)
        package.__path__ = [str(ROOT / chapter / "src")]
        package.__package__ = package_name
        sys.modules[package_name] = package
    return importlib.import_module(f"{package_name}.{module}")


messages = chapter_module("02-providers", "messages")
file_tools = chapter_module("03-tools", "tools")
sessions = chapter_module("04-sessions", "session")
compaction = chapter_module("04-sessions", "compaction")
tokens = chapter_module("04-sessions", "tokens")

Message = messages.Message
ToolCall = messages.ToolCall
Provider = messages.Provider
SessionManager = sessions.SessionManager
CompactionSettings = compaction.CompactionSettings
