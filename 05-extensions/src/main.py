#!/usr/bin/env python3
"""第 5 章演示入口；第 6 章会用更完整的 CLI 复用这些模块。"""

import argparse
import sys
from pathlib import Path

from agent import Agent
from commands import register_commands
from extensions import ExtensionAPI
from previous import SessionManager
from providers import make_provider
from registry import CommandRegistry, builtin_tools

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


def main(argv=None):
    parser = argparse.ArgumentParser(description="第 5 章：扩展机制")
    parser.add_argument("prompt", nargs="?", help="给出任务后执行一次并退出")
    parser.add_argument("--demo", action="store_true", help="使用离线假模型")
    parser.add_argument("--provider", choices=["openai", "anthropic"], default="openai")
    parser.add_argument("--model")
    parser.add_argument("--session-dir", default=str(Path.home() / ".mini-pi" / "chapter05"))
    parser.add_argument("-e", "--extension", action="append", default=[])
    args = parser.parse_args(argv)
    try:
        provider = make_provider(args.provider, args.model, args.demo)
        commands = CommandRegistry()
        register_commands(commands)
        api = ExtensionAPI(builtin_tools(), commands, report=lambda text: print(text, file=sys.stderr))
        for name in ("project_info.py", "log_calls.py", "guard_bash.py"):
            api.load(str(EXAMPLES / name))
        for path in args.extension:
            api.load(path)
        session = SessionManager.create(args.session_dir)

        def notify(event):
            if event["type"] in ("message_end", "notice", "tool_end"):
                print(event["content"])

        agent = Agent(provider, session, api, notify=notify)
        print(f"会话：{session.path}", file=sys.stderr)
        if args.prompt:
            command = commands.dispatch(args.prompt, agent)
            if command is not None:
                print(command.content)
                return 1 if command.error else 0
            result = agent.prompt(args.prompt)
            return 0 if result.status == "completed" else 1
        print("输入任务或 /help；/quit 退出。")
        while True:
            try:
                text = input("\n> ").strip()
                if not text:
                    continue
                command = commands.dispatch(text, agent)
                if command is not None:
                    if command.exit:
                        break
                    print(command.content)
                else:
                    agent.prompt(text)
            except KeyboardInterrupt:
                print("\n本轮已中断；会话已保存。")
            except EOFError:
                break
            except Exception as error:
                print(f"错误: {error}", file=sys.stderr)
        return 0
    except KeyboardInterrupt:
        return 130
    except Exception as error:
        print(f"错误: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
