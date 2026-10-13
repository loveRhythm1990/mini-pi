"""验证扩展改变实际 Agent 行为；所有模型调用均为假的。"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from agent import Agent
from commands import register_commands
from extensions import CompactDecision, ExtensionAPI, ResultDecision, ToolDecision
from previous import CompactionSettings, Message, SessionManager, ToolCall
from providers import SummaryClient, to_record
from registry import CommandRegistry, CommandResult, ToolRegistry, builtin_tools


class ScriptedProvider:
    model = "test-model"

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def create_completion(self, messages, tools, stream=False, max_tokens=None):
        self.calls.append({"messages": messages, "tools": tools, "max_tokens": max_tokens})
        reply = self.replies.pop(0)
        if isinstance(reply, BaseException):
            raise reply
        if not stream:
            return reply

        def events():
            yield {"type": "text_delta", "text": reply.content or ""}
            yield {"type": "done", "message": reply}
        return events()


def call_message(*calls, reason="tool_calls"):
    return Message("assistant", tool_calls=list(calls), stop_reason=reason)


def text_message(text="完成"):
    return Message("assistant", text, stop_reason="stop")


def setup_agent(tmp_path, replies):
    provider = ScriptedProvider(replies)
    session = SessionManager.create(str(tmp_path))
    commands = CommandRegistry()
    register_commands(commands)
    errors, events = [], []
    api = ExtensionAPI(builtin_tools(), commands, report=errors.append)
    agent = Agent(provider, session, api, notify=events.append)
    return agent, errors, events


def register_echo(api, execute=lambda args: args["text"]):
    api.register_tool({"name": "echo", "description": "回显", "input_schema": {
        "type": "object", "properties": {"text": {"type": "string"}},
        "required": ["text"], "additionalProperties": False}}, execute)


def test_dynamic_tool_is_sent_to_model_and_persisted(tmp_path):
    agent, _, _ = setup_agent(tmp_path, [call_message(ToolCall("c1", "echo", {"text": "你好"})), text_message()])
    register_echo(agent.extensions)
    lifecycle = []
    agent.extensions.on("turn_start", lambda event, ctx: lifecycle.append("start"))
    agent.extensions.on("turn_end", lambda event, ctx: lifecycle.append(event["status"]))
    result = agent.prompt("请回显")
    assert result.status == "completed" and lifecycle == ["start", "completed"]
    assert "echo" in [tool["name"] for tool in agent.provider.calls[0]["tools"]]
    restored = SessionManager.open(agent.session.path).build_context()
    assert [m["role"] for m in restored] == ["user", "assistant", "tool", "assistant"]
    assert restored[2]["tool_call_id"] == "c1" and restored[2]["content"] == "你好"


def test_duplicate_tool_and_command_are_rejected():
    api = ExtensionAPI(ToolRegistry(), CommandRegistry())
    register_echo(api)
    with pytest.raises(ValueError, match="工具已注册"):
        register_echo(api)
    api.register_command("hello", "问候", lambda args, ctx: CommandResult(args))
    with pytest.raises(ValueError, match="命令已注册"):
        api.register_command("hello", "覆盖", lambda args, ctx: CommandResult())


@pytest.mark.parametrize("args", [{}, {"text": 1}, {"text": "x", "extra": True}, ["x"]])
def test_invalid_arguments_never_reach_executor(args):
    api = ExtensionAPI(ToolRegistry(), CommandRegistry())
    executed = []
    register_echo(api, lambda args: executed.append(args) or "执行了")
    assert "参数验证失败" in api.tools.execute("echo", args)
    assert executed == []


def test_tool_exception_and_unknown_tool_are_results():
    def fail(args):
        raise OSError("磁盘错误")
    api = ExtensionAPI(ToolRegistry(), CommandRegistry())
    register_echo(api, fail)
    assert "磁盘错误" in api.tools.execute("echo", {"text": "x"})
    assert "未知工具" in api.tools.execute("missing", {})


def test_blocked_call_still_has_matching_result(tmp_path):
    agent, _, _ = setup_agent(tmp_path, [call_message(ToolCall("c1", "echo", {"text": "x"})), text_message()])
    executed, later = [], []
    register_echo(agent.extensions, lambda args: executed.append(args) or "执行了")
    agent.extensions.on("before_tool_call", lambda event, ctx: ToolDecision(block=True, reason="禁止"))
    agent.extensions.on("before_tool_call", lambda event, ctx: later.append(True))
    agent.prompt("测试拦截")
    result = agent.session.build_context()[2]
    assert result["tool_call_id"] == "c1" and "禁止" in result["content"]
    assert executed == later == []


def test_hook_failure_prevents_execution(tmp_path):
    agent, errors, _ = setup_agent(tmp_path, [call_message(ToolCall("c1", "echo", {"text": "x"})), text_message()])
    executed = []
    register_echo(agent.extensions, lambda args: executed.append(args) or "执行了")
    def fail(event, ctx):
        raise RuntimeError("规则加载失败")
    agent.extensions.on("before_tool_call", fail)
    agent.prompt("执行")
    assert not executed and errors
    assert "工具未执行" in agent.session.build_context()[2]["content"]


def test_argument_and_result_changes_follow_registration_order(tmp_path):
    agent, _, _ = setup_agent(tmp_path, [call_message(ToolCall("c1", "echo", {"text": "old"})), text_message()])
    register_echo(agent.extensions)
    agent.extensions.on("before_tool_call", lambda event, ctx: ToolDecision(arguments={"text": "new"}))
    agent.extensions.on("after_tool_call", lambda event, ctx: ResultDecision(event["content"] + "-a"))
    agent.extensions.on("after_tool_call", lambda event, ctx: ResultDecision(event["content"] + "-b"))
    agent.prompt("执行")
    assert agent.session.build_context()[2]["content"] == "new-a-b"
    # 实际参数允许改写，原始模型请求保留在会话里，便于解释。
    assert 'old' in agent.session.build_context()[1]["tool_calls"][0]["function"]["arguments"]


def test_event_mutation_does_not_silently_change_arguments(tmp_path):
    agent, _, _ = setup_agent(tmp_path, [call_message(ToolCall("c1", "echo", {"text": "original"})), text_message()])
    register_echo(agent.extensions)
    def mutate(event, ctx):
        event["arguments"]["text"] = "changed"
    agent.extensions.on("before_tool_call", mutate)
    agent.prompt("执行")
    assert agent.session.build_context()[2]["content"] == "original"


def test_observer_failure_preserves_original_tool_result(tmp_path):
    agent, errors, _ = setup_agent(tmp_path, [call_message(ToolCall("c1", "echo", {"text": "ok"})), text_message()])
    register_echo(agent.extensions)
    def fail(event, ctx):
        raise RuntimeError("日志失败")
    agent.extensions.on("turn_start", fail)
    agent.extensions.on("after_tool_call", fail)
    assert agent.prompt("执行").status == "completed"
    assert agent.session.build_context()[2]["content"] == "ok" and len(errors) == 2


def test_command_and_unknown_command_do_not_call_model(tmp_path):
    agent, _, _ = setup_agent(tmp_path, [])
    agent.extensions.register_command("hello", "问候", lambda args, ctx: CommandResult(f"hello {args}"))
    assert agent.extensions.commands.dispatch("/hello 中文名字", agent).content == "hello 中文名字"
    assert "未知命令" in agent.extensions.commands.dispatch("/missing", agent).content
    assert agent.extensions.commands.dispatch("普通输入", agent) is None
    assert agent.provider.calls == [] and agent.session.entries == []


def test_compaction_can_be_cancelled(tmp_path):
    agent, _, _ = setup_agent(tmp_path, [])
    agent.extensions.on("before_compact", lambda event, ctx: CompactDecision(cancel=True, reason="先保存快照"))
    assert agent.compact() is None and agent.provider.calls == []


def append_history(agent):
    for _ in range(4):
        agent.session.append_message({"role": "user", "content": "x" * 240})
        agent.session.append_message({"role": "assistant", "content": "ok"})


def test_threshold_compaction_uses_current_provider(tmp_path):
    agent, _, _ = setup_agent(tmp_path, [text_message("历史摘要"), text_message("最终回答")])
    append_history(agent)
    agent.context_window = 200
    agent.settings = CompactionSettings(reserve_tokens=100, keep_recent_tokens=30)
    assert agent.prompt("继续").content == "最终回答"
    assert agent.provider.calls[0]["tools"] == [] and agent.provider.calls[0]["max_tokens"] == 80
    assert any(e["type"] == "compaction" for e in agent.session.entries)


def test_overflow_compacts_and_retries_once(tmp_path):
    agent, _, _ = setup_agent(tmp_path, [RuntimeError("maximum context length"),
        text_message("历史摘要"), text_message("重试成功")])
    append_history(agent)
    agent.settings = CompactionSettings(reserve_tokens=100, keep_recent_tokens=30)
    assert agent.prompt("继续").content == "重试成功"
    assert len(agent.provider.calls) == 3


def test_summary_bridge_preserves_budget_and_rejects_truncation():
    provider = ScriptedProvider([Message("assistant", "半截", stop_reason="max_tokens")])
    reply = SummaryClient(provider).create(model="test", messages=[{"role": "user", "content": "历史"}], max_tokens=123)
    assert provider.calls[0]["max_tokens"] == 123 and provider.calls[0]["tools"] == []
    assert reply.choices[0].finish_reason == "length"


def test_empty_summary_is_rejected():
    with pytest.raises(RuntimeError, match="摘要为空"):
        SummaryClient(ScriptedProvider([Message("assistant")])).create(
            model="test", messages=[{"role": "user", "content": "历史"}], max_tokens=123)


def test_interrupted_batch_is_paired_without_replay(tmp_path):
    agent, _, _ = setup_agent(tmp_path, [call_message(
        ToolCall("c1", "echo", {"text": "first"}), ToolCall("c2", "echo", {"text": "second"}))])
    executed = []
    def interrupt(args):
        executed.append(args["text"])
        raise KeyboardInterrupt()
    register_echo(agent.extensions, interrupt)
    with pytest.raises(KeyboardInterrupt):
        agent.prompt("执行两个工具")
    restored = SessionManager.open(agent.session.path)
    results = [m for m in restored.build_context() if m["role"] == "tool"]
    assert [m["tool_call_id"] for m in results] == ["c1", "c2"]
    assert all("可能已有副作用" in m["content"] for m in results)
    assert executed == ["first"]
    resumed = Agent(ScriptedProvider([text_message()]), restored, agent.extensions)
    assert resumed.prompt("继续").status == "completed" and executed == ["first"]


def test_crash_recovery_adds_missing_result(tmp_path):
    agent, _, _ = setup_agent(tmp_path, [])
    agent.session.append_message({"role": "user", "content": "执行"})
    agent.session.append_message(to_record(call_message(ToolCall("c1", "write", {"path": "x", "content": "x"}))))
    Agent(ScriptedProvider([]), SessionManager.open(agent.session.path), agent.extensions)
    context = SessionManager.open(agent.session.path).build_context()
    assert context[-1]["role"] == "tool" and "未自动重放" in context[-1]["content"]
    assert not (tmp_path / "x").exists()


def test_truncated_response_never_executes_tools(tmp_path):
    agent, _, _ = setup_agent(tmp_path, [call_message(ToolCall("c1", "echo", {"text": "x"}), reason="length")])
    executed = []
    register_echo(agent.extensions, lambda args: executed.append(True) or "ok")
    with pytest.raises(RuntimeError, match="被截断"):
        agent.prompt("执行")
    assert not executed and "模型输出被截断" in agent.session.build_context()[-1]["content"]


@pytest.mark.parametrize("ids", [[""], ["same", "same"]])
def test_invalid_call_ids_are_not_persisted_or_executed(tmp_path, ids):
    agent, _, _ = setup_agent(tmp_path, [call_message(*[ToolCall(i, "echo", {"text": "x"}) for i in ids])])
    executed = []
    register_echo(agent.extensions, lambda args: executed.append(True) or "ok")
    with pytest.raises(RuntimeError, match="调用 id"):
        agent.prompt("执行")
    assert not executed and len(agent.session.build_context()) == 1


def test_corrupt_mid_history_is_rejected(tmp_path):
    agent, _, _ = setup_agent(tmp_path, [])
    agent.session.append_message(to_record(call_message(ToolCall("c1", "echo", {"text": "x"}))))
    agent.session.append_message(to_record(call_message(ToolCall("c2", "echo", {"text": "x"}))))
    with pytest.raises(ValueError, match="未配对"):
        Agent(ScriptedProvider([]), agent.session, agent.extensions)


def test_step_limit_is_not_reported_as_completed(tmp_path):
    agent, _, _ = setup_agent(tmp_path, [call_message(ToolCall("c1", "echo", {"text": "x"}))])
    register_echo(agent.extensions)
    assert agent.prompt("继续调用", max_steps=1).status == "max_steps"
    assert agent.session.build_context()[-1]["role"] == "tool"


def test_streaming_notifies_text_and_executes_final_calls(tmp_path):
    agent, _, events = setup_agent(tmp_path, [call_message(ToolCall("c1", "echo", {"text": "x"})), text_message("最终回答")])
    register_echo(agent.extensions)
    result = agent.prompt("执行", stream=True)
    assert result.content == "最终回答"
    assert any(e["type"] == "text_delta" and e["text"] == "最终回答" for e in events)
    assert agent.session.build_context()[2]["content"] == "x"


def test_missing_stream_done_does_not_persist_partial_message(tmp_path):
    agent, _, _ = setup_agent(tmp_path, [])
    agent.provider.create_completion = lambda *args, **kwargs: iter([{"type": "text_delta", "text": "半截"}])
    with pytest.raises(RuntimeError, match="未返回 done"):
        agent.prompt("执行", stream=True)
    assert [m["role"] for m in agent.session.build_context()] == ["user"]


def test_file_tools_are_integrated_with_the_loop(tmp_path):
    path = str(tmp_path / "hello.txt")
    agent, _, _ = setup_agent(tmp_path, [call_message(ToolCall("c1", "write", {"path": path, "content": "Hello World"})),
        call_message(ToolCall("c2", "edit", {"path": path, "old_str": "World", "new_str": "mini-pi"})),
        call_message(ToolCall("c3", "read", {"path": path})), text_message()])
    assert agent.prompt("创建并修改文件").status == "completed"
    assert Path(path).read_text() == "Hello mini-pi"
    assert agent.session.build_context()[-2]["content"] == "Hello mini-pi"


def test_branch_rejects_incomplete_tool_batch(tmp_path):
    agent, _, _ = setup_agent(tmp_path, [])
    user_id = agent.session.append_message({"role": "user", "content": "执行"})
    call_id = agent.session.append_message(to_record(call_message(ToolCall("c1", "echo", {"text": "x"}))))
    result_id = agent.session.append_message({"role": "tool", "tool_call_id": "c1", "content": "x"})
    assert "未完成" in agent.extensions.commands.dispatch(f"/branch {call_id}", agent).content
    assert agent.session.leaf_id == result_id
    agent.extensions.commands.dispatch(f"/branch {user_id}", agent)
    assert agent.session.leaf_id == user_id


def test_load_examples_and_invalid_extension(tmp_path):
    api = ExtensionAPI(builtin_tools(), CommandRegistry())
    example = Path(__file__).resolve().parents[1] / "examples" / "project_info.py"
    api.load(str(example))
    assert "工作目录" in api.commands.dispatch("/project", None).content
    invalid = tmp_path / "invalid.py"
    invalid.write_text("value = 1\n")
    with pytest.raises(ValueError, match="setup"):
        api.load(str(invalid))
