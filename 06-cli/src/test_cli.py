"""真实启动 CLI，另外注入假 Agent 验证中断后的交互行为。"""

import io
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from cli import interactive_loop, parse_args, project_session_dir, run_turn
from registry import CommandRegistry
from types import SimpleNamespace
from ui import Renderer

ROOT = Path(__file__).resolve().parents[2]
ENTRY = ROOT / "06-cli" / "src" / "main.py"


def run_cli(tmp_path, *args, text=None):
    env = {k: v for k, v in os.environ.items() if k not in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY")}
    return subprocess.run([sys.executable, str(ENTRY), "--session-dir", str(tmp_path / "sessions"), *args],
                          cwd=tmp_path, env=env, input=text or "", text=True, capture_output=True, timeout=15)


def records(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_offline_print_keeps_progress_out_of_stdout(tmp_path):
    result = run_cli(tmp_path, "--demo", "--print", "介绍这个项目")
    assert result.returncode == 0 and "离线演示完成" in result.stdout
    assert "先调用工具" not in result.stdout and "扩展日志" not in result.stdout
    assert "project_info" in result.stderr and "会话：" in result.stderr
    path = next((tmp_path / "sessions").rglob("*.jsonl"))
    assert [e["message"]["role"] for e in records(path)[1:]] == ["user", "assistant", "tool", "assistant"]


def test_continue_appends_to_same_project_session(tmp_path):
    assert run_cli(tmp_path, "--demo", "第一轮").returncode == 0
    path = next((tmp_path / "sessions").rglob("*.jsonl"))
    original_id = records(path)[0]["id"]
    assert run_cli(tmp_path, "--demo", "-c", "第二轮").returncode == 0
    assert len(list((tmp_path / "sessions").rglob("*.jsonl"))) == 1
    saved = records(path)
    assert saved[0]["id"] == original_id
    assert [e["message"]["content"] for e in saved[1:] if e["message"]["role"] == "user"] == ["第一轮", "第二轮"]


def test_explicit_session_resume(tmp_path):
    run_cli(tmp_path, "--demo", "第一轮")
    path = next((tmp_path / "sessions").rglob("*.jsonl"))
    result = run_cli(tmp_path, "--demo", "--session", str(path), "第二轮")
    assert result.returncode == 0 and len(records(path)) == 9


def test_missing_explicit_session_does_not_create_it(tmp_path):
    missing = tmp_path / "missing.jsonl"
    result = run_cli(tmp_path, "--demo", "--session", str(missing), "任务")
    assert result.returncode == 1 and not missing.exists()


def test_stdin_task_is_processed_once(tmp_path):
    result = run_cli(tmp_path, "--demo", "-p", text="从 stdin 输入任务")
    assert result.returncode == 0 and "离线演示完成" in result.stdout
    assert "mini-pi>" not in result.stdout


def test_streaming_output_is_not_duplicated(tmp_path):
    result = run_cli(tmp_path, "--demo", "--stream", "任务")
    assert result.returncode == 0
    assert result.stdout.count("先调用工具查看项目。") == 1
    assert result.stdout.count("离线演示完成") == 1


def test_interactive_commands_and_eof(tmp_path):
    result = run_cli(tmp_path, "--demo", "-i", "--plain", text="/project\n/help\n/quit\n")
    assert result.returncode == 0 and "工作目录" in result.stdout and "/compact" in result.stdout
    path = next((tmp_path / "sessions").rglob("*.jsonl"))
    assert len(records(path)) == 1  # 斜杠命令没有写入模型历史。
    eof = run_cli(tmp_path, "--demo", "-i", "--plain", text="/tokens\n")
    assert eof.returncode == 0


def test_max_steps_reports_failure(tmp_path):
    result = run_cli(tmp_path, "--demo", "-p", "--max-steps", "1", "任务")
    assert result.returncode == 1 and "尚未确认完成" in result.stderr
    assert result.stdout == ""


def test_unknown_command_is_not_sent_to_model(tmp_path):
    result = run_cli(tmp_path, "--demo", "/does_not_exist")
    assert result.returncode == 1 and "未知命令" in result.stderr
    path = next((tmp_path / "sessions").rglob("*.jsonl"))
    assert len(records(path)) == 1


def test_missing_key_fails_before_creating_session(tmp_path):
    result = run_cli(tmp_path, "任务")
    assert result.returncode == 1 and "OPENAI_API_KEY" in result.stderr
    assert not (tmp_path / "sessions").exists()


def test_custom_extension_is_loaded(tmp_path):
    extension = tmp_path / "greeting.py"
    extension.write_text('from registry import CommandResult\n'
                         'def setup(api):\n'
                         '    api.register_command("hello", "问候", lambda args, agent: CommandResult("你好 " + args))\n')
    result = run_cli(tmp_path, "--demo", "-e", str(extension), "/hello 世界")
    assert result.returncode == 0 and result.stdout.strip() == "你好 世界"


def test_invalid_extension_stops_startup(tmp_path):
    extension = tmp_path / "broken.py"
    extension.write_text('def setup(api):\n    raise RuntimeError("加载失败")\n')
    result = run_cli(tmp_path, "--demo", "-e", str(extension), "任务")
    assert result.returncode == 1 and "加载失败" in result.stderr
    assert not (tmp_path / "sessions").exists()


def test_project_sessions_do_not_mix(tmp_path):
    assert project_session_dir(tmp_path / "sessions", tmp_path / "a") != project_session_dir(
        tmp_path / "sessions", tmp_path / "b")


@pytest.mark.parametrize("args", [["--max-steps", "0"], ["--context-window", "20000"],
                                    ["--print", "--stream"], ["--continue", "--session", "x"]])
def test_invalid_arguments_fail_before_side_effects(args):
    with pytest.raises(SystemExit) as error:
        parse_args(args)
    assert error.value.code == 2


def test_run_turn_maps_interrupt_to_130_and_finishes_partial_line():
    output, errors = io.StringIO(), io.StringIO()
    renderer = Renderer(stream=True, output=output, errors=errors)
    renderer.event({"type": "text_delta", "text": "半截"})
    def prompt(*args, **kwargs):
        raise KeyboardInterrupt()
    agent = SimpleNamespace(extensions=SimpleNamespace(commands=CommandRegistry()), prompt=prompt)
    assert run_turn(agent, "任务", renderer, 20, True) == (130, False)
    assert output.getvalue() == "半截\n" and "未完成的工具不会自动重放" in errors.getvalue()


def test_interactive_loop_survives_input_interrupt_and_task_error():
    events = iter([KeyboardInterrupt(), "错误任务", "正常任务", EOFError()])
    tasks = []
    def read():
        value = next(events)
        if isinstance(value, BaseException):
            raise value
        return value
    def prompt(text, **kwargs):
        tasks.append(text)
        if text == "错误任务":
            raise RuntimeError("API 错误")
        return SimpleNamespace(content="完成", status="completed")
    agent = SimpleNamespace(extensions=SimpleNamespace(commands=CommandRegistry()), prompt=prompt)
    output, errors = io.StringIO(), io.StringIO()
    renderer = Renderer(interactive=True, output=output, errors=errors)
    assert interactive_loop(agent, read, renderer, max_steps=20, stream=False) == 0
    assert tasks == ["错误任务", "正常任务"]
    assert "输入已取消" in errors.getvalue() and "API 错误" in errors.getvalue()


def test_rich_markup_is_rendered_as_literal_text():
    output = io.StringIO()
    renderer = Renderer(stream=True, output=output, errors=io.StringIO())
    renderer.event({"type": "text_delta", "text": "[red]普通模型文本[/red]"})
    renderer.finish_partial()
    assert output.getvalue() == "[red]普通模型文本[/red]\n"
