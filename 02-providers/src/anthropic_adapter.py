"""Anthropic 适配器：system 单独传递，同一批工具结果合并。"""

from typing import Iterator, Any
from anthropic import Anthropic

try:
    from .messages import Provider, Message, ToolCall
except ImportError:
    from messages import Provider, Message, ToolCall


class AnthropicAdapter(Provider):
    def __init__(self, api_key: str, model: str = "claude-3-5-sonnet-20241022"):
        self.client = Anthropic(api_key=api_key)
        self.model = model

    def create_completion(self, messages: list[Message], tools: list[dict],
                          stream: bool = False, max_tokens: int | None = None) -> Message | Iterator[dict]:
        request = {"model": self.model, "max_tokens": max_tokens or 4096,
                   "messages": self.to_native_messages(messages)}
        system = "\n\n".join(m.content or "" for m in messages if m.role == "system")
        if system:
            request["system"] = system
        if tools:
            request["tools"] = tools
        if stream:
            return self._stream_completion(request)
        return self.from_native_message(self.client.messages.create(**request))

    def _stream_completion(self, request: dict) -> Iterator[dict]:
        with self.client.messages.stream(**request) as stream:
            for event in stream:
                if event.type == "content_block_delta":
                    if event.delta.type == "text_delta":
                        yield {"type": "text_delta", "text": event.delta.text}
                    elif event.delta.type == "input_json_delta":
                        yield {"type": "tool_call_delta", "json": event.delta.partial_json}
            yield {"type": "done", "message": self.from_native_message(stream.get_final_message())}

    def to_native_messages(self, messages: list[Message]) -> list[dict]:
        native = []
        for msg in messages:
            if msg.role == "system":
                continue
            content = []
            if msg.role == "tool":
                content.append({"type": "tool_result", "tool_use_id": msg.tool_call_id,
                                "content": msg.content or ""})
                # 一个 assistant 批次的结果必须放在同一个后继 user 消息中。
                if native and native[-1]["role"] == "user" and isinstance(native[-1]["content"], list) \
                        and all(b["type"] == "tool_result" for b in native[-1]["content"]):
                    native[-1]["content"].extend(content)
                    continue
                native.append({"role": "user", "content": content})
            elif msg.tool_calls:
                if msg.content:
                    content.append({"type": "text", "text": msg.content})
                content.extend({"type": "tool_use", "id": tc.id, "name": tc.name, "input": tc.arguments}
                               for tc in msg.tool_calls)
                native.append({"role": "assistant", "content": content})
            else:
                native.append({"role": msg.role, "content": msg.content or ""})
        return native

    def from_native_message(self, native_msg: Any) -> Message:
        text = [b.text for b in native_msg.content if b.type == "text"]
        calls = [ToolCall(b.id, b.name, b.input) for b in native_msg.content if b.type == "tool_use"]
        return Message(role="assistant", content="\n".join(text) or None, tool_calls=calls or None,
                       stop_reason=getattr(native_msg, "stop_reason", None))
