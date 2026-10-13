"""同时注册一个模型工具和一个用户命令。"""

from pathlib import Path
from registry import CommandResult


def setup(api):
    def describe(arguments):
        cwd = Path.cwd()
        names = sorted(p.name + ("/" if p.is_dir() else "") for p in cwd.iterdir())
        return f"工作目录：{cwd}\n" + "\n".join(names[:30])

    api.register_tool({
        "name": "project_info",
        "description": "列出当前项目目录的前 30 个条目。",
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    }, describe)
    api.register_command("project", "查看项目目录，不调用模型",
                         lambda args, agent: CommandResult(describe({})))
