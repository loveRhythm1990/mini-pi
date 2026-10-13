#!/usr/bin/env python3
"""
第 4 章：带会话持久化和上下文压缩的 agent

用法：
  python3 04-sessions/src/main.py            # 新会话
  python3 04-sessions/src/main.py -c         # 恢复最近的会话
  python3 04-sessions/src/main.py --session path/to/file.jsonl

交互命令：
  /compact [说明]   手动压缩，可附加关注点（Additional focus）
  /tree             打印会话树
  /branch <id>      回到某个 entry，从那里开新分支
  /tokens           查看当前上下文估算 token
  /quit             退出
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from typing import Any, Optional

from compaction import CompactionSettings, compact, is_context_overflow, should_compact
from session import SessionManager
from tokens import estimate_messages_tokens

SYSTEM_PROMPT = "You are a helpful coding assistant. Use the bash tool when you need to inspect or change files."

TOOLS = [{
    "type": "function",
    "function": {
        "name": "bash",
        "description": "Execute a bash command and return stdout/stderr.",
        "parameters": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]},
    },
}]

DEFAULT_SESSION_DIR = os.path.expanduser("~/.mini-pi/sessions")


def run_bash(command: str) -> str:
    try:
        r = subprocess.run(command, shell=True, capture_output=True, text=True, timeout=60)
        out = (r.stdout + r.stderr).strip()
        return out[-4000:] if out else f"(exit code {r.returncode})"
    except subprocess.TimeoutExpired:
        return "错误: 命令超时"


class Agent:
    def __init__(self, client: Any, model: str, session: SessionManager,
                 context_window: int = 128000, settings: Optional[CompactionSettings] = None,
                 tool_runner=run_bash, log=print):
        self.client = client
        self.model = model
        self.session = session
        self.context_window = context_window
        self.settings = settings or CompactionSettings()
        self.tool_runner = tool_runner
        self.log = log

    def context_messages(self) -> list[dict]:
        return [{"role": "system", "content": SYSTEM_PROMPT}] + self.session.build_context()

    def maybe_compact(self) -> bool:
        """阈值触发：每次调用模型前检查"""
        tokens = estimate_messages_tokens(self.context_messages())
        if should_compact(tokens, self.context_window, self.settings):
            self.log(f"📦 上下文约 {tokens} tokens，超过阈值，自动压缩…")
            return self.compact() is not None
        return False

    def compact(self, custom_instructions: Optional[str] = None) -> Optional[dict]:
        result = compact(self.session, self.client, self.model, self.settings, custom_instructions)
        if result:
            self.log(f"📦 压缩完成：{result['tokensBefore']} → {result['tokensAfter']} tokens")
        else:
            self.log("📦 没有可压缩的内容")
        return result

    def _call_llm(self) -> Any:
        """溢出触发：API 报上下文超限 → 压缩 → 重试一次"""
        try:
            return self.client.chat.completions.create(
                model=self.model, messages=self.context_messages(), tools=TOOLS)
        except Exception as e:  # noqa: BLE001
            if not is_context_overflow(e):
                raise
            self.log("⚠️ 上下文溢出，压缩后重试…")
            if self.compact() is None:
                raise
            return self.client.chat.completions.create(
                model=self.model, messages=self.context_messages(), tools=TOOLS)

    def prompt(self, text: str, max_steps: int = 20) -> str:
        self.session.append_message({"role": "user", "content": text})
        final = ""
        for _ in range(max_steps):
            self.maybe_compact()
            resp = self._call_llm()
            msg = resp.choices[0].message
            assistant: dict = {"role": "assistant", "content": msg.content or ""}
            if msg.tool_calls:
                assistant["tool_calls"] = [{
                    "id": tc.id, "type": "function",
                    "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                } for tc in msg.tool_calls]
            self.session.append_message(assistant)
            if msg.content:
                final = msg.content
                self.log(f"💬 {msg.content}")
            if not msg.tool_calls:
                break
            for tc in msg.tool_calls:
                try:
                    args = json.loads(tc.function.arguments or "{}")
                except json.JSONDecodeError:
                    args = {}
                self.log(f"🔧 {tc.function.name}: {args}")
                out = self.tool_runner(args.get("command", "")) if tc.function.name == "bash" \
                    else f"错误: 未知工具 {tc.function.name}"
                self.session.append_message({"role": "tool", "tool_call_id": tc.id, "content": out})
        return final


def make_client():
    from openai import OpenAI
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        print("错误: 请设置环境变量 OPENAI_API_KEY", file=sys.stderr)
        sys.exit(1)
    return OpenAI(api_key=key, base_url=os.environ.get("OPENAI_BASE_URL") or None)


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("-c", "--continue", dest="cont", action="store_true", help="恢复最近的会话")
    p.add_argument("--session", help="打开指定的会话文件")
    p.add_argument("--session-dir", default=DEFAULT_SESSION_DIR)
    p.add_argument("--context-window", type=int, default=128000)
    p.add_argument("--reserve-tokens", type=int, default=16384, help="摘要及模型输出的预留预算")
    p.add_argument("--keep-recent-tokens", type=int, default=20000, help="压缩时保留的最近消息预算")
    args = p.parse_args(argv)
    if args.context_window <= 0 or args.reserve_tokens < 2 or args.keep_recent_tokens <= 0:
        p.error("context-window 和 keep-recent-tokens 必须为正数，reserve-tokens 至少为 2")
    if args.reserve_tokens + args.keep_recent_tokens >= args.context_window:
        p.error("reserve-tokens + keep-recent-tokens 必须小于 context-window；缩小窗口时也要缩小预算")
    return args


def main() -> None:
    args = parse_args()

    if args.session:
        session = SessionManager.open(args.session)
    elif args.cont:
        session = SessionManager.continue_recent(args.session_dir)
    else:
        session = SessionManager.create(args.session_dir)
    print(f"📁 会话文件: {session.path}（已有 {len(session.entries)} 个 entry）")

    model = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
    settings = CompactionSettings(reserve_tokens=args.reserve_tokens,
                                  keep_recent_tokens=args.keep_recent_tokens)
    agent = Agent(make_client(), model, session, context_window=args.context_window, settings=settings)

    while True:
        try:
            text = input("\n> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not text:
            continue
        if text == "/quit":
            break
        if text == "/tree":
            print(session.render_tree())
        elif text == "/tokens":
            print(f"约 {estimate_messages_tokens(agent.context_messages())} tokens")
        elif text.startswith("/branch "):
            try:
                session.branch(text.split(maxsplit=1)[1])
                print("🌿 已切换 leaf，下一条消息将开启新分支")
            except KeyError as e:
                print(e)
        elif text == "/compact" or text.startswith("/compact "):
            agent.compact(text[9:].strip() or None)
        else:
            agent.prompt(text)


if __name__ == "__main__":
    main()
