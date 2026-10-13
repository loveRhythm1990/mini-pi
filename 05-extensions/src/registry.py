"""注册时检查定义，执行时检查参数；工具错误作为结果回给模型。"""

import copy
import re
from dataclasses import dataclass
from typing import Callable

from jsonschema import Draft202012Validator
from previous import file_tools


def check_name(name: str) -> None:
    if not isinstance(name, str) or not re.fullmatch(r"[a-z][a-z0-9_]*", name):
        raise ValueError("名称必须以小写字母开头，只包含小写字母、数字和下划线")


class ToolRegistry:
    def __init__(self):
        self._tools = {}

    def register(self, definition: dict, execute: Callable[[dict], str]) -> None:
        name = definition["name"]
        check_name(name)
        if name in self._tools:
            raise ValueError(f"工具已注册：{name}")
        if not callable(execute):
            raise TypeError("工具执行器必须可调用")
        Draft202012Validator.check_schema(definition["input_schema"])
        self._tools[name] = (copy.deepcopy(definition), execute)

    def definitions(self) -> list[dict]:
        return [copy.deepcopy(item[0]) for item in self._tools.values()]

    def execute(self, name: str, arguments: dict) -> str:
        if name not in self._tools:
            return f"错误: 未知工具 {name}"
        definition, execute = self._tools[name]
        errors = sorted(Draft202012Validator(definition["input_schema"]).iter_errors(arguments),
                        key=lambda e: str(e.path))
        if errors:
            return f"错误: 参数验证失败: {errors[0].message}"
        try:
            result = execute(arguments)
            if not isinstance(result, str):
                raise TypeError("本教程的工具必须返回字符串")
            return result
        except Exception as error:
            return f"错误: 工具 {name} 执行失败: {error}"


def builtin_tools() -> ToolRegistry:
    registry = ToolRegistry()
    for original in file_tools.TOOLS:
        definition = copy.deepcopy(original)
        definition["input_schema"]["additionalProperties"] = False
        if definition["name"] == "bash":
            definition["description"] = "执行 shell 命令，30 秒超时；只保留尾部 2000 行或 50KB，完整输出存入临时文件。"
        registry.register(definition,
                          lambda args, name=definition["name"]: file_tools.execute_tool(name, args))
    return registry


@dataclass
class CommandResult:
    content: str = ""
    exit: bool = False
    error: bool = False


class CommandRegistry:
    def __init__(self):
        self._commands = {}

    def register(self, name: str, description: str, handler: Callable) -> None:
        check_name(name)
        if name in self._commands:
            raise ValueError(f"命令已注册：/{name}")
        if not callable(handler):
            raise TypeError("命令处理器必须可调用")
        self._commands[name] = (description, handler)

    def help(self) -> str:
        return "\n".join(f"/{name:<12} {description}" for name, (description, _) in self._commands.items())

    def dispatch(self, text: str, agent) -> CommandResult | None:
        if not text.startswith("/"):
            return None  # 普通输入才交给模型。
        name, _, args = text[1:].partition(" ")
        if name not in self._commands:
            return CommandResult(f"未知命令 /{name}，输入 /help 查看命令", error=True)
        result = self._commands[name][1](args.strip(), agent)
        if not isinstance(result, CommandResult):
            raise TypeError("命令必须返回 CommandResult")
        return result
