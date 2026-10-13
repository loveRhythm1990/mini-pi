"""连接第 2 章统一消息与第 4 章的 OpenAI 格式会话记录。"""

import json
import os
import uuid
from types import SimpleNamespace

from previous import Message, ToolCall, Provider, ROOT, chapter_module


def to_message(record: dict) -> Message:
    calls = []
    for tc in record.get("tool_calls") or []:
        arguments = tc["function"]["arguments"]
        calls.append(ToolCall(tc["id"], tc["function"]["name"], json.loads(arguments)))
    return Message(role=record["role"], content=record.get("content"),
                   tool_calls=calls or None, tool_call_id=record.get("tool_call_id"))


def to_record(message: Message) -> dict:
    record = {"role": message.role, "content": message.content or ""}
    if message.tool_calls:
        record["tool_calls"] = [{"id": tc.id, "type": "function", "function": {
            "name": tc.name, "arguments": json.dumps(tc.arguments, ensure_ascii=False)}}
            for tc in message.tool_calls]
    if message.tool_call_id:
        record["tool_call_id"] = message.tool_call_id
    return record


class SummaryClient:
    """只适配摘要调用所需的接口，复用第 4 章切点和提示词。"""

    def __init__(self, provider: Provider):
        self.provider = provider
        self.chat = SimpleNamespace(completions=self)

    def create(self, *, model, messages, max_tokens):
        message = self.provider.create_completion(
            [to_message(m) for m in messages], [], max_tokens=max_tokens)
        if not message.content:
            raise RuntimeError("摘要为空，未保存压缩检查点")
        reason = "length" if message.stop_reason in ("length", "max_tokens") else message.stop_reason
        return SimpleNamespace(choices=[SimpleNamespace(
            message=SimpleNamespace(content=message.content), finish_reason=reason)])


class DemoProvider(Provider):
    """确定性的离线流程演示，不具备语言理解或真实摘要能力。"""

    model = "offline-demo"

    def create_completion(self, messages, tools, stream=False, max_tokens=None):
        names = {tool["name"] for tool in tools}
        if not tools:
            message = Message("assistant", "离线演示摘要：真实模型会在这里总结历史。", stop_reason="stop")
        elif messages[-1].role == "user":
            name = "project_info" if "project_info" in names else "read"
            args = {} if name == "project_info" else {"path": str(ROOT / "README.md")}
            message = Message("assistant", "先调用工具查看项目。", [
                ToolCall(uuid.uuid4().hex[:12], name, args)], stop_reason="tool_calls")
        else:
            result = messages[-1].content or ""
            message = Message("assistant", f"离线演示完成：\n{result[:500]}", stop_reason="stop")
        if not stream:
            return message

        def events():
            for start in range(0, len(message.content or ""), 12):
                yield {"type": "text_delta", "text": message.content[start:start + 12]}
            yield {"type": "done", "message": message}
        return events()

    def to_native_messages(self, messages):
        return [to_record(m) for m in messages]

    def from_native_message(self, native_msg):
        return to_message(native_msg)


def make_provider(name: str, model: str | None = None, demo: bool = False) -> Provider:
    if demo:
        return DemoProvider()
    env_name = "OPENAI_API_KEY" if name == "openai" else "ANTHROPIC_API_KEY"
    key = os.environ.get(env_name)
    if not key:
        raise ValueError(f"请设置 {env_name}，或使用 --demo 运行离线演示")
    if name == "openai":
        adapter = chapter_module("02-providers", "openai_adapter").OpenAIAdapter
        return adapter(key, model or os.environ.get("OPENAI_MODEL", "gpt-4o-mini"),
                       os.environ.get("OPENAI_BASE_URL") or None)
    if name != "anthropic":
        raise ValueError(f"未知 Provider：{name}")
    model = model or os.environ.get("ANTHROPIC_MODEL")
    if not model:
        raise ValueError("使用 Anthropic 时请通过 --model 或 ANTHROPIC_MODEL 指定模型")
    adapter = chapter_module("02-providers", "anthropic_adapter").AnthropicAdapter
    return adapter(key, model)
