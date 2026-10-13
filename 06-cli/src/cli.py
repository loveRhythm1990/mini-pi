"""参数解析、会话选择和交互流程；模型与工具逻辑留在核心中。"""

import argparse
import hashlib
import sys
from pathlib import Path

import bootstrap  # noqa: F401
from agent import Agent
from commands import register_commands
from extensions import ExtensionAPI
from previous import CompactionSettings, ROOT, SessionManager
from providers import make_provider
from registry import CommandRegistry, builtin_tools
from ui import Renderer, make_reader


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="mini-pi：从教程走向可用的编程 Agent")
    parser.add_argument("prompt", nargs="?", help="执行一次任务并退出")
    parser.add_argument("--demo", action="store_true", help="假模型离线演示，不调用 API")
    parser.add_argument("--provider", choices=["openai", "anthropic"], default="openai")
    parser.add_argument("--model", help="模型名称，也可通过厂商对应的环境变量设置")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("-p", "--print", dest="print_mode", action="store_true", help="执行一次，只输出最终回答")
    mode.add_argument("-i", "--interactive", action="store_true", help="强制交互模式")
    resume = parser.add_mutually_exclusive_group()
    resume.add_argument("-c", "--continue", dest="cont", action="store_true", help="恢复当前目录最近的会话")
    resume.add_argument("--session", help="恢复指定 JSONL 文件")
    parser.add_argument("--session-dir", type=Path, default=Path.home() / ".mini-pi" / "cli-sessions")
    streaming = parser.add_mutually_exclusive_group()
    streaming.add_argument("--stream", dest="stream", action="store_true", help="逐段显示模型文本")
    streaming.add_argument("--no-stream", dest="stream", action="store_false", help="等待完整回复")
    parser.set_defaults(stream=None)
    parser.add_argument("--plain", action="store_true", help="使用普通 input，关闭输入历史和补全")
    parser.add_argument("--max-steps", type=int, default=20)
    parser.add_argument("--context-window", type=int, default=128000)
    parser.add_argument("--reserve-tokens", type=int, default=16384)
    parser.add_argument("--keep-recent-tokens", type=int, default=20000)
    parser.add_argument("-e", "--extension", action="append", default=[], help="加载 Python 扩展，可重复")
    parser.add_argument("--no-extensions", action="store_true", help="不加载教程附带的三个示例扩展")
    args = parser.parse_args(argv)
    if args.max_steps <= 0 or args.context_window <= 0 or args.reserve_tokens < 2 or args.keep_recent_tokens <= 0:
        parser.error("步数、窗口和保留预算必须为正数，预留预算至少为 2")
    if args.reserve_tokens + args.keep_recent_tokens >= args.context_window:
        parser.error("reserve-tokens + keep-recent-tokens 必须小于 context-window")
    if args.print_mode and args.stream:
        parser.error("--print 只输出最终回答，不能与 --stream 同时使用")
    return args


def project_session_dir(base, cwd):
    # 同名目录也可能是不同项目，用绝对路径的哈希分开。
    digest = hashlib.sha256(str(Path(cwd).resolve()).encode()).hexdigest()[:12]
    return Path(base).expanduser() / digest


def open_session(args, cwd):
    directory = project_session_dir(args.session_dir, cwd)
    if args.session:
        path = Path(args.session).expanduser().resolve(strict=True)
        return SessionManager.open(str(path))
    if args.cont:
        return SessionManager.continue_recent(str(directory), cwd=str(cwd))
    return SessionManager.create(str(directory), cwd=str(cwd))


def run_turn(agent, text, renderer, max_steps, stream):
    try:
        command = agent.extensions.commands.dispatch(text, agent)
        if command is not None:
            if command.content:
                if command.error:
                    renderer.note(command.content)
                else:
                    renderer.output.print(command.content)
            return (1 if command.error else 0), command.exit
        result = agent.prompt(text, max_steps=max_steps, stream=stream)
        if not renderer.interactive and not stream and result.status == "completed" and result.content:
            renderer.output.print(result.content)
        return (0 if result.status == "completed" else 1), False
    except KeyboardInterrupt:
        renderer.note("本轮已中断；会话已保存，未完成的工具不会自动重放。")
        return 130, False
    except Exception as error:
        renderer.note(f"错误: {error}")
        return 1, False
    finally:
        renderer.finish_partial()


def interactive_loop(agent, reader, renderer, *, max_steps, stream):
    while True:
        try:
            text = reader().strip()
        except EOFError:
            return 0
        except KeyboardInterrupt:
            renderer.note("输入已取消；/quit 或 Ctrl+D 退出。")
            continue
        if not text:
            continue
        _, exit_requested = run_turn(agent, text, renderer, max_steps, stream)
        if exit_requested:
            return 0


def main(argv=None):
    args = parse_args(argv)
    interactive = args.interactive or (args.prompt is None and not args.print_mode and sys.stdin.isatty())
    task = args.prompt
    if not interactive and task is None:
        task = sys.stdin.read().strip()
        if not task:
            return 0
    stream = args.stream if args.stream is not None else interactive
    renderer = Renderer(interactive=interactive, stream=stream)
    try:
        provider = make_provider(args.provider, args.model, args.demo)
        commands = CommandRegistry()
        register_commands(commands)
        api = ExtensionAPI(builtin_tools(), commands, report=renderer.note)
        if not args.no_extensions:
            for name in ("project_info.py", "log_calls.py", "guard_bash.py"):
                api.load(str(ROOT / "05-extensions" / "examples" / name))
        for path in args.extension:
            api.load(path)
        session = open_session(args, Path.cwd())
        agent = Agent(provider, session, api, context_window=args.context_window,
                      settings=CompactionSettings(reserve_tokens=args.reserve_tokens,
                                                  keep_recent_tokens=args.keep_recent_tokens),
                      notify=renderer.event)
        renderer.note(f"会话：{session.path} | 模型：{provider.model}")
        if not interactive:
            return run_turn(agent, task, renderer, args.max_steps, stream)[0]
        renderer.note("输入任务或 /help；Ctrl+C 中断当前轮次，Ctrl+D 退出。")
        reader = make_reader(Path(session.path).parent / "input.history", commands,
                             plain=args.plain or not sys.stdin.isatty())
        if task:
            _, exit_requested = run_turn(agent, task, renderer, args.max_steps, stream)
            if exit_requested:
                return 0
        return interactive_loop(agent, reader, renderer, max_steps=args.max_steps, stream=stream)
    except KeyboardInterrupt:
        return 130
    except Exception as error:
        renderer.note(f"错误: {error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
