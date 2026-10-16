#!/usr/bin/env python3
"""第 6 章演示入口。

用法：
  python3 06-skills/src/main.py --demo                 # 离线剧本：观察清单注入与按需读取
  python3 06-skills/src/main.py "按 tutorial-contrib 准备提交"   # 连接真实模型
  python3 06-skills/src/main.py --skills-dir ~/my-skills ...
"""

import argparse
import sys
from pathlib import Path

import bootstrap  # noqa: F401  # 先把第 5 章 src 加入 sys.path
from agent import Agent
from commands import register_commands
from extensions import ExtensionAPI
from previous import Message, SessionManager, ToolCall
from providers import make_provider
from registry import CommandRegistry, builtin_tools
from skills import load_skills, register_skill_command, skills_prompt

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


class DemoSkillProvider:
    """离线剧本：任务匹配 → 先 read 第一个 Skill 的全文 → 复述要点。"""

    model = "offline-demo"

    def __init__(self, skills):
        self.skills = skills

    def create_completion(self, messages, tools, stream=False, max_tokens=None):
        if not self.skills:
            return Message("assistant", "离线演示：没有发现任何 Skill。", stop_reason="stop")
        if messages[-1].role == "user":
            target = self.skills[0]
            call = ToolCall("demo_skill_read", "read", {"path": str(target.path)})
            return Message("assistant", "任务与 Skill 匹配，先读取它的完整指令。",
                           [call], stop_reason="tool_calls")
        body = messages[-1].content or ""
        heading = next((line.lstrip("# ").strip() for line in body.splitlines()
                        if line.startswith("# ")), "…")
        return Message("assistant",
                      f"已加载 Skill「{self.skills[0].name}」（{heading}）并按其指令工作。",
                      stop_reason="stop")

    def to_native_messages(self, messages):
        return []

    def from_native_message(self, native_msg):
        return native_msg


def main(argv=None):
    parser = argparse.ArgumentParser(description="第 6 章：Skill 系统")
    parser.add_argument("prompt", nargs="?", help="给出任务后执行一次并退出")
    parser.add_argument("--demo", action="store_true", help="使用离线剧本模型")
    parser.add_argument("--provider", choices=["openai", "anthropic"], default="openai")
    parser.add_argument("--model")
    parser.add_argument("--session-dir", default=str(Path.home() / ".mini-pi" / "skills"))
    parser.add_argument("--skills-dir", action="append", default=[],
                        help="追加 Skill 目录，可重复；默认还扫描本章 examples 目录")
    args = parser.parse_args(argv)

    try:
        skill_dirs = [EXAMPLES, *args.skills_dir]
        skills, warnings = load_skills(skill_dirs)
        prompt_block = skills_prompt(skills)

        provider = DemoSkillProvider(skills) if args.demo else make_provider(args.provider, args.model)
        commands = CommandRegistry()
        register_commands(commands)
        register_skill_command(commands, skills)
        api = ExtensionAPI(builtin_tools(), commands, report=lambda text: print(text, file=sys.stderr))
        session = SessionManager.create(args.session_dir)

        def notify(event):
            if event["type"] in ("message_end", "notice", "tool_end"):
                print(event["content"])

        agent = Agent(provider, session, api, notify=notify, system_suffix=prompt_block)

        print(f"发现 {len(skills)} 个 Skill：{', '.join(s.name for s in skills) or '（无）'}",
              file=sys.stderr)
        for warning in warnings:
            print(f"⚠️  {warning}", file=sys.stderr)
        if prompt_block:
            print("\n系统提示追加的 <skills> 清单：\n" + prompt_block + "\n", file=sys.stderr)
        print(f"会话：{session.path}", file=sys.stderr)

        if args.prompt:
            command = commands.dispatch(args.prompt, agent)
            if command is not None:
                print(command.content)
                return 1 if command.error else 0
            result = agent.prompt(args.prompt)
            return 0 if result.status == "completed" else 1

        print("输入任务，或 /skill <名字> [请求] 强制加载；/quit 退出。")
        while True:
            try:
                text = input("\n> ").strip()
                if not text:
                    continue
                command = commands.dispatch(text, agent)
                if command is not None:
                    if command.exit:
                        return 0
                    if command.content:
                        print(command.content)
                    continue
                agent.prompt(text)
            except KeyboardInterrupt:
                print("\n本轮已中断；会话已保存。")
            except EOFError:
                return 0
    except KeyboardInterrupt:
        return 130
    except ValueError as error:  # make_provider 的缺 key 报错等
        print(f"错误: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
