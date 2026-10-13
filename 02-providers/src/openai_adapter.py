"""OpenAI 适配器：消息转换、流式累积与停止原因。"""

import json
from typing import Iterator, Any
from openai import OpenAI

try:
    from .messages import Provider, Message, ToolCall
except ImportError:
    from messages import Provider, Message, ToolCall


class OpenAIAdapter(Provider):
    def __init__(self, api_key: str, model: str = "gpt-4o-mini", base_url: str | None = None):
        self.client = OpenAI(api_key=api_key, base_url=base_url)
        self.model = model

    def create_completion(self, messages: list[Message], tools: list[dict],
                          stream: bool = False, max_tokens: int | None = None) -> Message | Iterator[dict]:
        request = {"model": self.model, "messages": self.to_native_messages(messages)}
        if tools:
            request["tools"] = self._convert_tools(tools)
        if max_tokens is not None:
            request["max_tokens"] = max_tokens
        if stream:
            return self._stream_completion(request)
        response = self.client.chat.completions.create(**request)
        message = self.from_native_message(response.choices[0].message)
        message.stop_reason = response.choices[0].finish_reason
        return message

    def _stream_completion(self, request: dict) -> Iterator[dict]:
        response = self.client.chat.completions.create(**request, stream=True)
        accumulated = {"content": "", "tool_calls": {}, "stop_reason": None}
        try:
            for chunk in response:
                if not chunk.choices:
                    continue
                choice = chunk.choices[0]
                delta = choice.delta
                if choice.finish_reason:
                    accumulated["stop_reason"] = choice.finish_reason
                if delta.content:
                    accumulated["content"] += delta.content
                    yield {"type": "text_delta", "text": delta.content}
                for part in delta.tool_calls or []:
                    tc = accumulated["tool_calls"].setdefault(part.index,
                        {"id": "", "name": "", "arguments": ""})
                    if part.id:
                        tc["id"] = part.id
                    if part.function:
                        tc["name"] += part.function.name or ""
                        tc["arguments"] += part.function.arguments or ""
                    yield {"type": "tool_call_delta", "index": part.index}
            if accumulated["stop_reason"] is None:
                raise RuntimeError("OpenAI 流在结束标记前断开，响应未完成")
            yield {"type": "done", "message": self._build_final_message(accumulated)}
        finally:
            response.close()

    @staticmethod
    def _arguments(raw: str) -> dict:
        try:
            value = json.loads(raw or "{}")
            return value if isinstance(value, dict) else {"_raw": raw}
        except json.JSONDecodeError:
            return {"_raw": raw}

    def _build_final_message(self, accumulated: dict) -> Message:
        calls = [ToolCall(tc["id"], tc["name"], self._arguments(tc["arguments"]))
                 for _, tc in sorted(accumulated["tool_calls"].items())]
        return Message(role="assistant", content=accumulated["content"] or None,
                       tool_calls=calls or None, stop_reason=accumulated.get("stop_reason"))

    def to_native_messages(self, messages: list[Message]) -> list[dict]:
        native = []
        for msg in messages:
            item = {"role": msg.role, "content": msg.content or ""}
            if msg.role == "tool":
                item["tool_call_id"] = msg.tool_call_id
            if msg.tool_calls:
                item["tool_calls"] = [{"id": tc.id, "type": "function", "function": {
                    "name": tc.name, "arguments": json.dumps(tc.arguments, ensure_ascii=False)}}
                    for tc in msg.tool_calls]
            native.append(item)
        return native

    def from_native_message(self, native_msg: Any) -> Message:
        calls = [ToolCall(tc.id, tc.function.name, self._arguments(tc.function.arguments))
                 for tc in native_msg.tool_calls or []]
        return Message(role="assistant", content=native_msg.content or None, tool_calls=calls or None)

    def _convert_tools(self, tools: list[dict]) -> list[dict]:
        return [{"type": "function", "function": {"name": t["name"],
                 "description": t["description"], "parameters": t["input_schema"]}} for t in tools]
