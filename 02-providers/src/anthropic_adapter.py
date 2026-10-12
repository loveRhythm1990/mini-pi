"""
Anthropic 适配器

将 Anthropic API 格式转换为统一的 Message 格式
"""

from typing import Iterator, Any
from anthropic import Anthropic
from main import Provider, Message, ToolCall


class AnthropicAdapter(Provider):
    """Anthropic Claude 模型适配器"""
    
    def __init__(self, api_key: str, model: str = "claude-3-5-sonnet-20241022"):
        self.client = Anthropic(api_key=api_key)
        self.model = model
    
    def create_completion(
        self,
        messages: list[Message],
        tools: list[dict],
        stream: bool = False
    ) -> Message | Iterator[dict]:
        """调用 Anthropic API"""
        native_messages = self.to_native_messages(messages)
        
        if stream:
            return self._stream_completion(native_messages, tools)
        else:
            return self._blocking_completion(native_messages, tools)
    
    def _blocking_completion(self, native_messages: list[dict], tools: list[dict]) -> Message:
        """非流式调用"""
        response = self.client.messages.create(
            model=self.model,
            max_tokens=4096,
            messages=native_messages,
            tools=tools if tools else None,
            temperature=0.7
        )
        
        return self.from_native_message(response)
    
    def _stream_completion(self, native_messages: list[dict], tools: list[dict]) -> Iterator[dict]:
        """流式调用"""
        with self.client.messages.stream(
            model=self.model,
            max_tokens=4096,
            messages=native_messages,
            tools=tools if tools else None,
            temperature=0.7
        ) as stream:
            for event in stream:
                if event.type == "content_block_delta":
                    if event.delta.type == "text_delta":
                        yield {"type": "text_delta", "text": event.delta.text}
                    elif event.delta.type == "input_json_delta":
                        yield {"type": "tool_call_delta", "json": event.delta.partial_json}
            
            # Anthropic SDK 自动累积完整消息
            final_message = stream.get_final_message()
            yield {"type": "done", "message": self.from_native_message(final_message)}
    
    def to_native_messages(self, messages: list[Message]) -> list[dict]:
        """将统一格式转换为 Anthropic 格式"""
        native = []
        
        for msg in messages:
            if msg.role == "tool":
                # Anthropic: 工具结果放在 user 消息的 content 数组中
                native.append({
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": msg.tool_call_id,
                            "content": msg.content or ""
                        }
                    ]
                })
            elif msg.tool_calls:
                # Anthropic: content 是混合数组（文本 + 工具调用）
                content = []
                
                if msg.content:
                    content.append({"type": "text", "text": msg.content})
                
                for tc in msg.tool_calls:
                    content.append({
                        "type": "tool_use",
                        "id": tc.id,
                        "name": tc.name,
                        "input": tc.arguments
                    })
                
                native.append({
                    "role": "assistant",
                    "content": content
                })
            else:
                # 普通消息
                native.append({
                    "role": msg.role,
                    "content": msg.content or ""
                })
        
        return native
    
    def from_native_message(self, native_msg: Any) -> Message:
        """将 Anthropic 格式转换为统一格式"""
        text_parts = []
        tool_calls = []
        
        for block in native_msg.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                tool_calls.append(ToolCall(
                    id=block.id,
                    name=block.name,
                    arguments=block.input
                ))
        
        content = "\n".join(text_parts) if text_parts else None
        
        return Message(
            role="assistant",
            content=content,
            tool_calls=tool_calls if tool_calls else None
        )
