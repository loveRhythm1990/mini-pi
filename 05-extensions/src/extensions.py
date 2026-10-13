"""同步扩展宿主：按注册顺序调用；显式返回决定，不靠修改事件传递。"""

import copy
import importlib.util
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path

EVENTS = {"turn_start", "turn_end", "before_tool_call", "after_tool_call",
          "before_compact", "after_compact"}


@dataclass
class ToolDecision:
    block: bool = False
    reason: str = ""
    arguments: dict | None = None


@dataclass
class ResultDecision:
    content: str


@dataclass
class CompactDecision:
    cancel: bool = False
    reason: str = ""


class HookError(RuntimeError):
    pass


class ExtensionAPI:
    def __init__(self, tools, commands, report=print):
        self.tools = tools
        self.commands = commands
        self.report = report
        self._handlers = {}

    def on(self, event: str, handler) -> None:
        if event not in EVENTS:
            raise ValueError(f"未知事件：{event}")
        if not callable(handler):
            raise TypeError("事件处理器必须可调用")
        self._handlers.setdefault(event, []).append(handler)

    def register_tool(self, definition: dict, execute) -> None:
        self.tools.register(definition, execute)

    def register_command(self, name: str, description: str, handler) -> None:
        self.commands.register(name, description, handler)

    def results(self, name: str, event: dict, agent, strict: bool = False):
        # tuple 固定本次分发的列表；新注册的处理器下一次才生效。
        for handler in tuple(self._handlers.get(name, [])):
            try:
                yield handler(copy.deepcopy(event), agent)
            except Exception as error:
                message = f"扩展错误 [{name}]: {error}"
                self.report(message)
                if strict:
                    raise HookError(message) from error

    def emit(self, name: str, event: dict, agent) -> None:
        for _ in self.results(name, event, agent):
            pass

    def load(self, path: str) -> None:
        path = Path(path).expanduser().resolve(strict=True)
        name = "_mini_pi_extension_" + uuid.uuid4().hex
        spec = importlib.util.spec_from_file_location(name, path)
        if spec is None or spec.loader is None:
            raise ValueError(f"无法加载扩展：{path}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        try:
            spec.loader.exec_module(module)
            setup = getattr(module, "setup", None)
            if not callable(setup):
                raise ValueError(f"扩展缺少 setup(api)：{path}")
            setup(self)
        except BaseException:
            sys.modules.pop(name, None)
            raise  # 加载失败时入口退出，不继续使用部分注册的宿主。
