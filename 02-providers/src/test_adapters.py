"""验证后续章节依赖的 system、批量结果、摘要预算和流关闭行为。"""

from types import SimpleNamespace as NS

import pytest

from messages import Message, ToolCall
from openai_adapter import OpenAIAdapter
from anthropic_adapter import AnthropicAdapter


def test_anthropic_system_and_batched_tool_results():
    calls = []
    adapter = AnthropicAdapter.__new__(AnthropicAdapter)
    adapter.model = "test"
    adapter.client = NS(messages=NS(create=lambda **kwargs: calls.append(kwargs) or NS(
        content=[NS(type="text", text="ok")], stop_reason="end_turn")))
    adapter.create_completion([
        Message("system", "系统提示"), Message("user", "任务"),
        Message("assistant", tool_calls=[ToolCall("a", "read", {"path": "a"}), ToolCall("b", "read", {"path": "b"})]),
        Message("tool", "A", tool_call_id="a"), Message("tool", "B", tool_call_id="b"),
    ], [], max_tokens=1234)
    assert calls[0]["system"] == "系统提示" and calls[0]["max_tokens"] == 1234
    assert "tools" not in calls[0]
    assert all(m["role"] != "system" for m in calls[0]["messages"])
    assert [b["tool_use_id"] for b in calls[0]["messages"][-1]["content"]] == ["a", "b"]


def test_openai_budget_and_finish_reason():
    calls = []
    adapter = OpenAIAdapter.__new__(OpenAIAdapter)
    adapter.model = "test"
    adapter.client = NS(chat=NS(completions=NS(create=lambda **kwargs: calls.append(kwargs) or NS(
        choices=[NS(message=NS(content="半截", tool_calls=None), finish_reason="length")]))))
    message = adapter.create_completion([Message("user", "历史")], [], max_tokens=800)
    assert calls[0]["max_tokens"] == 800 and "tools" not in calls[0]
    assert message.stop_reason == "length"


class FakeStream:
    def __init__(self, chunks):
        self.chunks = chunks
        self.closed = False

    def __iter__(self):
        return iter(self.chunks)

    def close(self):
        self.closed = True


def chunk(*, text=None, call=None, reason=None):
    return NS(choices=[NS(delta=NS(content=text, tool_calls=[call] if call else None), finish_reason=reason)])


def stream_adapter(stream):
    adapter = OpenAIAdapter.__new__(OpenAIAdapter)
    adapter.model = "test"
    adapter.client = NS(chat=NS(completions=NS(create=lambda **kwargs: stream)))
    return adapter


def test_openai_stream_assembles_arguments_and_closes():
    stream = FakeStream([
        chunk(call=NS(index=0, id="c1", function=NS(name="read", arguments='{"path":'))),
        chunk(call=NS(index=0, id=None, function=NS(name=None, arguments='"a.py"}'))),
        chunk(reason="tool_calls"),
    ])
    events = list(stream_adapter(stream).create_completion([Message("user", "任务")], [], stream=True))
    assert events[-1]["message"].tool_calls[0].arguments == {"path": "a.py"}
    assert events[-1]["message"].stop_reason == "tool_calls" and stream.closed


def test_openai_unfinished_stream_is_rejected_and_closed():
    stream = FakeStream([chunk(text="partial")])
    with pytest.raises(RuntimeError, match="结束标记"):
        list(stream_adapter(stream).create_completion([Message("user", "任务")], [], stream=True))
    assert stream.closed


def test_openai_stream_closes_when_consumer_interrupts():
    stream = FakeStream([chunk(text="partial"), chunk(reason="stop")])
    events = stream_adapter(stream).create_completion([Message("user", "任务")], [], stream=True)
    assert next(events)["type"] == "text_delta"
    events.close()
    assert stream.closed
